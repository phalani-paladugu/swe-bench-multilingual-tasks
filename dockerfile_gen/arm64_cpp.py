"""Resolve, freeze, rebuild, validate, and retain native ARM64 C++ task images."""
from __future__ import annotations

import argparse
import copy
import json
import re
import shlex
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .arm64 import arm_base, sha256
from .arm64_pipeline import atomic_write, build, shell
from .c import MAP_REPO_VERSION_TO_SPECS_C
from .tasks import TASKS_DIR, load_task
from .utils import make_heredoc_run_command

CPP_REPOS = {"fmtlib/fmt", "nlohmann/json"}
SELECTED_CPP = [
    "fmtlib__fmt-1683", "fmtlib__fmt-2310", "fmtlib__fmt-2317",
    "fmtlib__fmt-2457", "fmtlib__fmt-3158", "fmtlib__fmt-3248",
    "fmtlib__fmt-3272", "fmtlib__fmt-3729", "fmtlib__fmt-3750",
    "fmtlib__fmt-3863", "fmtlib__fmt-3901", "nlohmann__json-4237",
]
BASE = "ubuntu:22.04"
INPUT_FILES = ["task.yaml", "tests.json", "test.patch", "gold.patch", "eval.sh", "problem_statement.md"]
GENERATOR_FILES = [
    "__init__.py", "c.py", "constants.py", "tasks.py", "utils.py",
    "arm64.py", "arm64_pipeline.py", "arm64_cpp.py", "arm64_cpp_validate.py",
]


def task_spec(task: Path):
    instance = load_task(task)
    if instance["repo"] not in CPP_REPOS:
        raise ValueError(f"{task.name}: this adapter supports C++ tasks only")
    specs = copy.deepcopy(MAP_REPO_VERSION_TO_SPECS_C[instance["repo"]][instance["version"]])
    return instance, specs


def inputs(task: Path) -> dict:
    return {
        **{name: sha256((task / name).read_bytes()) for name in INPUT_FILES},
        "generator": {name: sha256(Path(__file__).with_name(name).read_bytes())
                      for name in GENERATOR_FILES},
    }


def package_text(packages: dict[str, str]) -> str:
    return "".join(f"{name}\t{version}\n" for name, version in sorted(packages.items()))


def generate_recipe(task: Path, base_image: str) -> str:
    """Translate the archived x86 recipe, changing only its FROM instruction."""
    task_spec(task)
    if not re.fullmatch(r"ubuntu:22\.04@sha256:[a-f0-9]{64}", base_image):
        raise ValueError("Expected a pinned Ubuntu 22.04 ARM64 manifest")
    archive = task / "Dockerfile.amd"
    original = (archive if archive.exists() else task / "Dockerfile").read_text()
    if len(re.findall(r"^FROM ", original, re.MULTILINE)) != 1:
        raise ValueError("Expected exactly one upstream FROM instruction")
    recipe, count = re.subn(
        r"^FROM --platform=linux/amd64 ubuntu:22\.04$",
        f"FROM --platform=linux/arm64 {base_image}", original, flags=re.MULTILINE,
    )
    if count != 1:
        raise ValueError("Expected the original Ubuntu 22.04 AMD64 Dockerfile")
    return recipe


def recipe_base(task: Path) -> str:
    """Reuse the committed recipe's digest unless there is no ARM recipe yet."""
    path = task / "Dockerfile.arm64"
    if not path.exists():
        return arm_base(BASE)
    recipe = path.read_text()
    match = re.search(r"^FROM --platform=linux/arm64 (\S+)$", recipe, re.MULTILINE)
    if not match or recipe != generate_recipe(task, match[1]):
        raise ValueError("ARM recipe differs from the x86 source; regenerate with --base-image")
    return match[1]


def write_recipe(task: Path, base_image: str | None = None):
    recipe = generate_recipe(task, base_image or recipe_base(task))
    atomic_write(task / "Dockerfile.arm64", recipe)
    print(f"{task.name}: saved Dockerfile.arm64 (recipe only; not build-validated)", flush=True)


def generate(task: Path, base_image: str, packages: dict | None = None) -> str:
    # The validation pipeline builds the same recipe, adding only package
    # freezing/checks and a cache-busting argument for independent rebuilds.
    recipe = generate_recipe(task, base_image)
    checks = ""
    if packages:
        pins = []
        for name, version in sorted(packages.items()):
            if not re.fullmatch(r"[a-z0-9][a-z0-9+.-]*(?::[a-z0-9]+)?", name):
                raise ValueError("Invalid Debian package name")
            if not re.fullmatch(r"[0-9A-Za-z.+:~_-]+", version):
                raise ValueError("Invalid Debian package version")
            pins.append(shlex.quote(name + "=" + version))
        start = recipe.index("    apt install -y ")
        end = recipe.index(" && \\", start)
        recipe = (recipe[:start] + "    apt install -y --allow-downgrades " +
                  " \\\n        ".join(pins) + recipe[end:])
        digest = sha256(package_text(packages).encode())
        checks = make_heredoc_run_command([
            "LC_ALL=C dpkg-query -W | LC_ALL=C sort > /tmp/arm64-packages.txt",
            f"echo '{digest}  /tmp/arm64-packages.txt' | sha256sum -c -",
            "rm /tmp/arm64-packages.txt",
        ]) + "\n"
    marker = "\nRUN <<"
    if recipe.count(marker) != 1:
        raise ValueError("Expected one upstream repository setup/build script")
    setup = recipe.index(marker)
    extra = '\nARG ARM64_REBUILD_TOKEN=stable\nRUN test -n "$ARM64_REBUILD_TOKEN"\n'
    return ("# syntax=docker/dockerfile:1\n# Generated by dockerfile-gen-arm64-cpp.\n" +
            recipe[:setup] + "\n" + checks + extra + recipe[setup:])


def inspect(image: str) -> dict:
    common = shell(image, "set -e; uname -m; gcc -dumpmachine; git -C /testbed rev-parse HEAD; "
                   "git -C /testbed remote; printf '\\n__DPKG__\\n'; "
                   "LC_ALL=C dpkg-query -W | LC_ALL=C sort")
    git, packages = common.split("\n__DPKG__\n", 1)
    lines = git.strip().splitlines()
    report = {
        "architecture": lines[0], "compiler_target": lines[1],
        "base_commit": lines[2], "git_remotes": lines[3:],
        "system_packages": dict(line.split("\t", 1) for line in packages.splitlines()),
    }
    if report["architecture"] != "aarch64" or report["compiler_target"] != "aarch64-linux-gnu":
        raise ValueError("Expected native ARM64 execution and compiler")
    if report["git_remotes"]:
        raise ValueError("Task checkout still has Git remotes")
    status = shell(image, "git -C /testbed -c core.fileMode=false status --porcelain --untracked-files=no")
    if status:
        raise ValueError(f"Build changed tracked source files: {status}")
    for name, command in {"gcc": "gcc --version", "g++": "g++ --version",
                          "cmake": "cmake --version", "ctest": "ctest --version",
                          "make": "make --version", "python": "python3 --version"}.items():
        report[name] = shell(image, command).splitlines()[0]
    return report


def check_lock(task: Path, lock: dict, *, check_inputs=True):
    if lock.get("schema") != 1 or lock.get("language") != "cpp" or lock.get("architecture") != "aarch64":
        raise ValueError("Unsupported C++ ARM64 lock")
    if check_inputs and lock.get("inputs") != inputs(task):
        raise ValueError("Task or generator inputs changed; use --refresh-lock")
    if sha256((task / "Dockerfile.amd").read_bytes()) != lock["amd_dockerfile_sha256"]:
        raise ValueError("Original AMD archive changed")
    if (task / "apt-packages.arm64.txt").read_text() != package_text(lock["system_packages"]):
        raise ValueError("Saved apt package snapshot changed")


def resolve(task: Path, logs_root: Path, *, refresh=False):
    instance, _ = task_spec(task)
    saved = task / "arm64-lock.json"
    previous = json.loads(saved.read_text()) if saved.exists() else None
    if previous:
        check_lock(task, previous, check_inputs=not refresh)
    if previous and not refresh:
        if not previous.get("validation", {}).get("passed"):
            raise ValueError("Existing lock has no passing benchmark validation")
        final = generate(task, previous["base_image"], previous["system_packages"])
        if (task / "Dockerfile").read_text() != final:
            atomic_write(task / "Dockerfile", final)
        print(f"{task.name}: reused verified C++ lock", flush=True)
        return
    base_image = previous["base_image"] if previous else recipe_base(task)
    packages = previous["system_packages"] if previous else None
    logs = logs_root.resolve() / task.name
    logs.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex[:10]
    images = [f"sweb.arm64.{task.name}:{stage}-{run_id}" for stage in ("resolve", "verify")]
    tag = f"sweb.eval.arm64.{task.name}:latest"
    try:
        build(task, generate(task, base_image, packages), images[0], logs, "resolve")
        resolved = inspect(images[0])
        atomic_write(logs / "resolve.json", json.dumps(resolved, indent=2, sort_keys=True) + "\n")
        if resolved["base_commit"] != instance["base_commit"]:
            raise ValueError("Wrong task base commit")
        if previous and any(previous[key] != value for key, value in resolved.items()):
            raise ValueError("Frozen C++ environment drift during refresh")
        final = generate(task, base_image, resolved["system_packages"])
        build(task, final, images[1], logs, "verify")
        verified = inspect(images[1])
        atomic_write(logs / "verify.json", json.dumps(verified, indent=2, sort_keys=True) + "\n")
        if resolved != verified:
            raise ValueError("Independent rebuild changed package, toolchain, or checkout state")
        from .arm64_cpp_validate import validate
        validation = validate(task, images[1], logs)
        atomic_write(logs / "validation.json", json.dumps(validation, indent=2, sort_keys=True) + "\n")
        if not validation["passed"]:
            raise ValueError(f"Baseline/gold validation failed: {logs / 'validation.json'}")
        archive = task / "Dockerfile.amd"
        original = archive.read_bytes() if archive.exists() else (task / "Dockerfile").read_bytes()
        lock = {
            "schema": 1, "language": "cpp", "base_image": base_image,
            "upstream_base_image": BASE, "inputs": inputs(task),
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "amd_dockerfile_sha256": sha256(original), "image": tag,
            **verified, "validation": validation,
        }
        if not archive.exists():
            archive.write_bytes(original)
        atomic_write(task / "apt-packages.arm64.txt", package_text(verified["system_packages"]))
        atomic_write(task / "Dockerfile", final)
        atomic_write(saved, json.dumps(lock, indent=2, sort_keys=True) + "\n")
        subprocess.run(["docker", "tag", images[1], tag], check=True)
        print(f"{task.name}: verified, saved, and retained as {tag}", flush=True)
    finally:
        subprocess.run(["docker", "image", "rm", *images], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance_ids", nargs="+", default=SELECTED_CPP, choices=SELECTED_CPP)
    parser.add_argument("--refresh-lock", action="store_true")
    parser.add_argument("--logs-dir", type=Path, default=Path("logs/arm64/cpp"))
    parser.add_argument("--write-dockerfiles", action="store_true",
                        help="Write side-by-side ARM recipes without Docker; only FROM changes")
    parser.add_argument("--base-image",
                        help="Pinned Ubuntu 22.04 ARM64 image for --write-dockerfiles; "
                             "otherwise reuse the saved digest or resolve the current tag")
    args = parser.parse_args()
    if args.base_image and not args.write_dockerfiles:
        parser.error("--base-image requires --write-dockerfiles")
    if args.refresh_lock and args.write_dockerfiles:
        parser.error("--refresh-lock is for build validation, not recipe generation")
    failed = []
    for name in args.instance_ids:
        try:
            if args.write_dockerfiles:
                write_recipe(TASKS_DIR / name, args.base_image)
            else:
                resolve(TASKS_DIR / name, args.logs_dir, refresh=args.refresh_lock)
        except Exception as error:
            print(f"{name}: FAILED: {error}", flush=True)
            failed.append(name)
    if failed:
        raise SystemExit("Failed tasks: " + ", ".join(failed))


if __name__ == "__main__":
    main()
