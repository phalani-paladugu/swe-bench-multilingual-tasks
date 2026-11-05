import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dockerfile_gen.arm64_cpp import (
    CPP_REPOS, SELECTED_CPP, generate, generate_recipe, package_text,
    recipe_base, resolve, task_spec, write_recipe,
)
from dockerfile_gen.arm64_cpp_validate import doctest_results, googletest_results
from dockerfile_gen.tasks import TASKS_DIR, load_task, task_dirs


class CppParserTests(unittest.TestCase):
    def test_googletest_ctest_prefix_and_repeated_failure(self):
        text = """1: [       OK ] suite.pass (0 ms)
1: [  FAILED  ] suite.fail (1 ms)
1: [       OK ] suite.fail (0 ms)
1: [  SKIPPED ] typed/0.skip (0 ms)
[  FAILED  ] 1 test, listed below:
[  FAILED  ] suite.fail
"""
        self.assertEqual(googletest_results(text), {
            "suite.pass": "PASSED", "suite.fail": "FAILED", "typed/0.skip": "SKIPPED",
        })

    def test_doctest_nested_failures_empty_and_exception(self):
        text = """prefix<doctest><TestSuite><TestCase name="basic">
<SubCase name="parent"><SubCase name="child"><Expression success="false"/></SubCase></SubCase>
<SubCase name="pass"><Expression success="true"/></SubCase>
<SubCase name="empty"/>
<SubCase name="exception"><Exception>unexpected</Exception></SubCase>
</TestCase></TestSuite></doctest>suffix"""
        self.assertEqual(doctest_results(text), {
            "basic > parent": "FAILED", "basic > child": "FAILED",
            "basic > pass": "PASSED", "basic > empty": "SKIPPED",
            "basic > exception": "FAILED",
        })
        self.assertEqual(doctest_results("build failed"), {})

    def test_doctest_repeated_success_cannot_hide_failure(self):
        text = """<doctest><TestCase name="basic">
<SubCase name="one"><Expression success="false"/></SubCase>
<SubCase name="one"><Expression success="true"/></SubCase>
</TestCase></doctest>"""
        self.assertEqual(doctest_results(text), {"basic > one": "FAILED"})


class CppGenerationTests(unittest.TestCase):
    def test_selection_is_all_twelve_cpp_tasks_and_excludes_c(self):
        actual = {task.name for task in task_dirs() if load_task(task)["repo"] in CPP_REPOS}
        self.assertEqual(set(SELECTED_CPP), actual)
        self.assertEqual(len(SELECTED_CPP), 12)
        with self.assertRaisesRegex(ValueError, "C\\+\\+ tasks only"):
            task_spec(TASKS_DIR / "redis__redis-13115")

    def test_frozen_packages_and_architecture_preserve_task(self):
        task = TASKS_DIR / "fmtlib__fmt-3901"
        before = {name: (task / name).read_bytes() for name in ("test.patch", "gold.patch", "eval.sh")}
        packages = {"cmake": "3.22.1-1ubuntu1.22.04.2", "g++": "4:11.2.0-1ubuntu1"}
        result = generate(task, "ubuntu:22.04@sha256:" + "a" * 64, packages)
        self.assertIn("FROM --platform=linux/arm64", result)
        self.assertIn("cmake=3.22.1-1ubuntu1.22.04.2", result)
        self.assertIn("g++=4:11.2.0-1ubuntu1", result)
        self.assertIn("--parallel $(nproc) --target format-test", result)
        self.assertIn("/opt/miniconda3", result)
        self.assertNotIn("CMAKE_BUILD_PARALLEL_LEVEL", result)
        self.assertEqual(before, {name: (task / name).read_bytes() for name in before})

    def test_all_committed_recipes_change_only_the_from_instruction(self):
        for name in SELECTED_CPP:
            with self.subTest(task=name):
                task = TASKS_DIR / name
                base = recipe_base(task)
                archive = task / "Dockerfile.amd"
                original = (archive if archive.exists() else task / "Dockerfile").read_bytes()
                expected = original.replace(
                    b"FROM --platform=linux/amd64 ubuntu:22.04\n",
                    f"FROM --platform=linux/arm64 {base}\n".encode(),
                )
                self.assertNotEqual(expected, original)
                self.assertEqual((task / "Dockerfile.arm64").read_bytes(), expected)
                self.assertEqual(generate_recipe(task, base).encode(), expected)

    def test_recipe_regeneration_preserves_inputs_and_reuses_saved_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            task = Path(directory) / "fmtlib__fmt-3901"
            shutil.copytree(TASKS_DIR / task.name, task)
            before = {p.name: p.read_bytes() for p in task.iterdir() if p.is_file()}
            with patch("dockerfile_gen.arm64_cpp.arm_base", side_effect=AssertionError("No tag lookup")):
                write_recipe(task)
            after = {p.name: p.read_bytes() for p in task.iterdir() if p.is_file()}
            self.assertEqual(before, after)
            path = task / "Dockerfile.arm64"
            path.write_text(path.read_text().replace("--target format-test", "--target wrong-test"))
            with self.assertRaisesRegex(ValueError, "ARM recipe differs"):
                recipe_base(task)

    def test_recipe_uses_x86_archive_and_rejects_unexpected_bases(self):
        with tempfile.TemporaryDirectory() as directory:
            task = Path(directory) / "fmtlib__fmt-3901"
            shutil.copytree(TASKS_DIR / task.name, task)
            original = (task / "Dockerfile").read_text()
            (task / "Dockerfile.amd").write_text(original)
            (task / "Dockerfile").write_text((task / "Dockerfile.arm64").read_text())
            base = "ubuntu:22.04@sha256:" + "a" * 64
            self.assertIn("--target format-test", generate_recipe(task, base))
            for invalid in ("ubuntu:22.04", "ubuntu:24.04@sha256:" + "a" * 64):
                with self.assertRaisesRegex(ValueError, "pinned Ubuntu 22.04"):
                    generate_recipe(task, invalid)
            (task / "Dockerfile.amd").write_text(original.replace("ubuntu:22.04", "ubuntu:24.04"))
            with self.assertRaisesRegex(ValueError, "original Ubuntu 22.04"):
                generate_recipe(task, base)
            (task / "Dockerfile.amd").write_text(original + "\nFROM scratch\n")
            with self.assertRaisesRegex(ValueError, "exactly one"):
                generate_recipe(task, base)

    def test_package_snapshot_is_sorted_and_invalid_pins_rejected(self):
        self.assertEqual(package_text({"z": "2", "a": "1"}), "a\t1\nz\t2\n")
        task = TASKS_DIR / "fmtlib__fmt-3901"
        for packages in ({"bad;name": "1"}, {"cmake": "1\nbad"}):
            with self.assertRaisesRegex(ValueError, "Invalid Debian"):
                generate(task, "ubuntu:22.04@sha256:" + "a" * 64, packages)

    def test_failed_validation_preserves_task_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = root / "fmtlib__fmt-1683"
            shutil.copytree(TASKS_DIR / task.name, task)
            (task / "arm64-lock.json").unlink(missing_ok=True)
            before = {p.name: p.read_bytes() for p in task.iterdir() if p.is_file()}
            report = {"base_commit": load_task(task)["base_commit"], "system_packages": {"cmake": "1"}}
            with patch("dockerfile_gen.arm64_cpp.arm_base", return_value="ubuntu:22.04@sha256:" + "a" * 64), \
                 patch("dockerfile_gen.arm64_cpp.build"), \
                 patch("dockerfile_gen.arm64_cpp.inspect", return_value=report), \
                 patch("dockerfile_gen.arm64_cpp_validate.validate", return_value={"passed": False}), \
                 patch("dockerfile_gen.arm64_cpp.subprocess.run"):
                with self.assertRaisesRegex(ValueError, "Baseline/gold validation failed"):
                    resolve(task, root / "logs")
            after = {p.name: p.read_bytes() for p in task.iterdir() if p.is_file()}
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
