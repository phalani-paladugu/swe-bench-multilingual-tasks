import io
from contextlib import redirect_stderr
import json
import shutil
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dockerfile_gen.arm64 import SELECTED, generate, inputs, main, sha256, task_spec, validate_lock
from dockerfile_gen.arm64_pipeline import resolve
from dockerfile_gen.arm64_validate import expected_compile_failure, junit_results, rust_results, select_results
from dockerfile_gen.tasks import TASKS_DIR


class ValidationTests(unittest.TestCase):
    def test_missing_tests_are_not_successful(self):
        self.assertEqual(select_results(["expected"], {}, "rust"), {"expected": "MISSING"})
        self.assertEqual(select_results(["NodeTest"], {}, "java"), {"NodeTest": "MISSING"})

    def test_compile_exception_is_scoped_to_exact_task_symbol_and_test_files(self):
        task = TASKS_DIR / "google__gson-2024"
        log = ("[ERROR] COMPILATION ERROR\n"
               "[ERROR] /testbed/gson/src/test/java/com/google/gson/functional/FieldNamingTest.java:[24,1] cannot find symbol\n"
               "[ERROR]   symbol: static UPPER_CASE_WITH_UNDERSCORES\n"
               "[ERROR] maven-compiler-plugin:3.8.1:testCompile failed\n")
        self.assertIsNotNone(expected_compile_failure(task, log))
        self.assertIsNone(expected_compile_failure(task, log.replace("UPPER_CASE_WITH_UNDERSCORES", "UNRELATED")))
        self.assertIsNone(expected_compile_failure(task, log.replace("cannot find symbol", "syntax error")))
        self.assertIsNone(expected_compile_failure(TASKS_DIR / "google__gson-2479", log))

    def test_cargo_distinguishes_failures_and_ignored_tests(self):
        output = "test x::one ... ok\ntest x::two ... FAILED\ntest x::three ... ignored\n"
        self.assertEqual(rust_results(output), {
            "x::one": "PASSED", "x::two": "FAILED", "x::three": "SKIPPED"
        })

    def test_junit_failure_error_skip_and_class_aggregation(self):
        xml = b"""<testsuite>
          <testcase classname="pkg.NodeTest" name="ok"/>
          <testcase classname="pkg.NodeTest" name="bad"><failure/></testcase>
          <testcase classname="pkg.OtherTest" name="error"><error/></testcase>
          <testcase classname="pkg.OtherTest" name="skip"><skipped/></testcase>
        </testsuite>"""
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w") as archive:
            info = tarfile.TarInfo("TEST-results.xml")
            info.size = len(xml)
            archive.addfile(info, io.BytesIO(xml))
        result = junit_results(output.getvalue())
        self.assertEqual(result["pkg.NodeTest#ok"], "PASSED")
        self.assertEqual(result["pkg.OtherTest#error"], "FAILED")
        self.assertEqual(result["pkg.OtherTest#skip"], "SKIPPED")
        self.assertEqual(select_results(["NodeTest"], result, "java")["NodeTest"], "FAILED")


class GenerationTests(unittest.TestCase):
    def test_draft_cannot_overwrite_source_task_tree(self):
        with patch("sys.argv", ["dockerfile-gen-arm64", "--draft", "--output-dir", str(TASKS_DIR)]), \
             patch("dockerfile_gen.arm64.arm_base") as network, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                main()
            self.assertEqual(error.exception.code, 2)
            network.assert_not_called()

    def test_selected_tasks_are_eight_java_eight_rust(self):
        languages = [task_spec(TASKS_DIR / name)[2] for name in SELECTED]
        self.assertEqual(len(set(SELECTED)), 16)
        self.assertEqual(languages.count("java"), 8)
        self.assertEqual(languages.count("rust"), 8)

    def test_java_uses_original_mvnd_jvm_and_keeps_eval_unchanged(self):
        task = TASKS_DIR / "google__gson-2479"
        before = (task / "eval.sh").read_bytes()
        text = generate(task, base_image="maven:tag@sha256:" + "a" * 64)
        self.assertIn("ENV MVND_CLIENT=jvm", text)
        self.assertIn("ln -s mvnd.sh", text)
        self.assertIn("/releases/download/1.0.2/", text)
        self.assertNotIn("/opt/miniconda3", text)
        self.assertEqual((task / "eval.sh").read_bytes(), before)

    def test_invalid_maven_artifact_path_is_rejected(self):
        task = TASKS_DIR / "google__gson-2479"
        for name in ["../escape.jar", "/tmp/escape.jar", "bad\nentry"]:
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Invalid Maven"):
                generate(task, base_image="maven:x@sha256:" + "a" * 64,
                         maven_artifacts={name: "b" * 64})

    def test_unsupported_java_runner_is_explicit(self):
        with self.assertRaisesRegex(ValueError, "Java adapter requires Maven"):
            generate(TASKS_DIR / "projectlombok__lombok-3602",
                     base_image="maven:x@sha256:" + "a" * 64)

    def test_rust_lock_precedes_build_and_is_enforced(self):
        task = TASKS_DIR / "tokio-rs__tokio-6724"
        lock = 'version = 3\n# unique frozen lock\n'
        text = generate(task, base_image="rust:1.85@sha256:" + "b" * 64, cargo_lock=lock)
        self.assertLess(text.index("# unique frozen lock"), text.index("cargo test --locked"))
        self.assertIn(sha256(lock.encode()), text)
        self.assertEqual(text.count(" > Cargo.lock"), 1)

    def test_changed_inputs_reject_lock_reuse(self):
        task = TASKS_DIR / "google__gson-2479"
        lock = {"schema": 1, "architecture": "aarch64", "inputs": {}}
        with self.assertRaisesRegex(ValueError, "changed"):
            validate_lock(task, lock)

    def test_corrupt_cargo_snapshot_rejected_in_output_dir(self):
        task = TASKS_DIR / "tokio-rs__tokio-6724"
        with tempfile.TemporaryDirectory() as directory:
            dest = Path(directory)
            (dest / "Cargo.lock.arm64").write_text("corrupt\n")
            lock = {"schema": 1, "architecture": "aarch64", "inputs": inputs(task),
                    "base_image": "rust:1.85@sha256:" + "a" * 64, "language": "rust",
                    "cargo_lock_sha256": "b" * 64}
            with self.assertRaisesRegex(ValueError, "does not match"):
                validate_lock(task, lock, cargo_dir=dest)

    def test_refresh_uses_saved_base_and_maven_pins(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = root / "google__gson-2479"
            shutil.copytree(TASKS_DIR / task.name, task)
            lock = {"schema": 1, "architecture": "aarch64", "language": "java",
                    "base_image": "maven:x@sha256:" + "a" * 64,
                    "maven_artifacts": {"a.jar": "b" * 64}}
            (task / "arm64-lock.json").write_text(json.dumps(lock))
            with patch("dockerfile_gen.arm64_pipeline.arm_base", side_effect=AssertionError("must not resolve a new tag")), \
                 patch("dockerfile_gen.arm64_pipeline.generate", wraps=generate) as render, \
                 patch("dockerfile_gen.arm64_pipeline.build", side_effect=RuntimeError("stopped before build")), \
                 patch("dockerfile_gen.arm64_pipeline.subprocess.run"):
                with self.assertRaisesRegex(RuntimeError, "stopped before build"):
                    resolve(task, root / "logs", refresh=True)
                self.assertEqual(render.call_args.kwargs["base_image"], lock["base_image"])
                self.assertEqual(render.call_args.kwargs["maven_artifacts"], lock["maven_artifacts"])

    def test_failed_rebuild_preserves_original_task_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = root / "google__gson-2479"
            shutil.copytree(TASKS_DIR / task.name, task)
            before = {p.name: p.read_bytes() for p in task.iterdir() if p.is_file()}
            commit = task_spec(task)[0]["base_commit"]
            report = {"base_commit": commit, "maven_artifacts": {"a.jar": "a" * 64}, "v": 1}
            changed = {**report, "v": 2}
            with patch("dockerfile_gen.arm64_pipeline.arm_base", return_value="maven:x@sha256:" + "a"*64), \
                 patch("dockerfile_gen.arm64_pipeline.build"), \
                 patch("dockerfile_gen.arm64_pipeline.inspect", side_effect=[(report, None), (changed, None)]), \
                 patch("dockerfile_gen.arm64_pipeline.subprocess.run"):
                with self.assertRaisesRegex(ValueError, "drift"):
                    resolve(task, root / "logs", refresh=True, run_tests=False)
            after = {p.name: p.read_bytes() for p in task.iterdir() if p.is_file()}
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
