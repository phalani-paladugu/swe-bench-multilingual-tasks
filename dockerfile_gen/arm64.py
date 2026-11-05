"""Generate Java/Rust ARM64 tasks using resolve -> freeze -> rebuild -> verify."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
import urllib.request
from functools import lru_cache
from pathlib import Path

from . import get_dockerfile_base, make_env_script_list, make_repo_script_list
from . import MAP_REPO_VERSION_TO_SPECS
from .tasks import TASKS_DIR, load_task
from .utils import make_heredoc_run_command

SELECTED = [
    "google__gson-2024", "google__gson-2061", "google__gson-2134",
    "google__gson-2158", "google__gson-2311", "google__gson-2479",
    "javaparser__javaparser-4538", "javaparser__javaparser-4561",
    "burntsushi__ripgrep-2209", "burntsushi__ripgrep-2576",
    "sharkdp__bat-2393", "sharkdp__bat-2650",
    "tokio-rs__tokio-6724", "tokio-rs__tokio-6838",
    "tokio-rs__tokio-4867", "tokio-rs__tokio-6603",
]
MVND_SHA256 = "6534ec4565f508e5af38de8bdb0b48e534965fa797591b3f57d88bef64222c4f"
MVND_URL = ("https://github.com/apache/maven-mvnd/releases/download/1.0.2/"
            "maven-mvnd-1.0.2-linux-amd64.zip")
INPUT_FILES = ["task.yaml", "tests.json", "test.patch", "gold.patch", "eval.sh"]
GENERATOR_FILES = [
    "__init__.py", "java.py", "rust.py", "utils.py", "constants.py",
    "arm64.py", "arm64_pipeline.py", "arm64_validate.py",
]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def inputs(task: Path) -> dict:
    result = {name: sha256((task / name).read_bytes()) for name in INPUT_FILES}
    result["generator"] = {
        name: sha256(Path(__file__).with_name(name).read_bytes())
        for name in GENERATOR_FILES
    }
    _, specs, _, _ = task_spec(task)
    if specs.get("cargo_lock"):
        fixture = Path(__file__).with_name("fixtures") / specs["cargo_lock"]
        result["cargo_fixture"] = sha256(fixture.read_bytes())
    return result


def task_spec(task: Path):
    instance = load_task(task)
    specs = copy.deepcopy(MAP_REPO_VERSION_TO_SPECS[instance["repo"]][instance["version"]])
    ds = specs.get("docker_specs", {})
    if "java_image" in ds:
        language, base = "java", ds["java_image"]
    elif "rust_version" in ds:
        language, base = "rust", "rust:" + ds["rust_version"]
    else:
        raise ValueError(f"{task.name}: only Java and Rust are supported")
    return instance, specs, language, base


@lru_cache(maxsize=None)
def arm_base(reference: str) -> str:
    """Pin the ARM64 child manifest, not a mutable tag."""
    repo, tag = reference.rsplit(":", 1)
    repo = repo if "/" in repo else "library/" + repo
    with urllib.request.urlopen(
        f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{repo}:pull",
        timeout=60,
    ) as response:
        token = json.load(response)["token"]
    request = urllib.request.Request(
        f"https://registry-1.docker.io/v2/{repo}/manifests/{tag}",
        headers={"Authorization": "Bearer " + token,
                 "Accept": "application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.list.v2+json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        manifest = json.load(response)
    for child in manifest.get("manifests", []):
        platform = child.get("platform", {})
        if platform.get("os") == "linux" and platform.get("architecture") == "arm64":
            return reference + "@" + child["digest"]
    raise ValueError(f"No linux/arm64 manifest for {reference}")


def validate_lock(task: Path, lock: dict, cargo_dir: Path | None = None,
                  *, check_inputs: bool = True) -> None:
    if lock.get("schema") != 1 or lock.get("architecture") != "aarch64":
        raise ValueError("Unsupported ARM64 lock")
    if check_inputs and lock.get("inputs") != inputs(task):
        raise ValueError(f"{task.name}: task or generator changed; use --refresh-lock")
    if not re.fullmatch(r".+@sha256:[a-f0-9]{64}", lock["base_image"]):
        raise ValueError("The ARM64 base image must be digest-pinned")
    archive = (cargo_dir or task) / "Dockerfile.amd"
    if lock.get("amd_dockerfile_sha256") and sha256(archive.read_bytes()) != lock["amd_dockerfile_sha256"]:
        raise ValueError("The archived AMD Dockerfile changed")
    if lock["language"] == "rust":
        text = ((cargo_dir or task) / "Cargo.lock.arm64").read_bytes()
        if sha256(text) != lock["cargo_lock_sha256"]:
            raise ValueError("Cargo.lock.arm64 does not match the verified lock")


def generate(task: Path, *, base_image: str, cargo_lock: str | None = None,
             maven_artifacts: dict | None = None) -> str:
    instance, specs, language, _ = task_spec(task)
    base = get_dockerfile_base(instance, specs["docker_specs"])
    base, count = re.subn(r"FROM --platform=linux/amd64 \S+",
                         f"FROM --platform=linux/arm64 {base_image}", base, count=1)
    if count != 1:
        raise ValueError("Expected exactly one upstream base image")
    if language == "java":
        start = base.index("RUN curl -fsSL -o mvnd.zip")
        end = base.index("\nENV MVND_HOME", start)
        base = base[:start] + (
            f"RUN curl --retry 3 -fsSL -o /tmp/mvnd.zip {MVND_URL} && \\\n"
            f"    echo '{MVND_SHA256}  /tmp/mvnd.zip' | sha256sum -c - && \\\n"
            "    unzip -q /tmp/mvnd.zip -d /tmp && \\\n"
            "    mv /tmp/maven-mvnd-1.0.2-linux-amd64 /usr/local/mvnd && \\\n"
            "    rm /tmp/mvnd.zip /usr/local/mvnd/bin/mvnd && \\\n"
            "    ln -s mvnd.sh /usr/local/mvnd/bin/mvnd\n"
            "# Same mvnd/Maven release; run the bundled JVM client on the ARM JDK.\n"
            "ENV MVND_CLIENT=jvm\n"
        ) + base[end:]
        base += "\nRUN mvnd --version\n"
    if cargo_lock is not None:
        specs.pop("cargo_lock", None)
        delimiter = "ARM64_CARGO_LOCK_" + sha256(cargo_lock.encode())[:12]
        specs["pre_install"] = [
            f"cat <<'{delimiter}' > Cargo.lock\n{cargo_lock.rstrip()}\n{delimiter}",
            *specs.get("pre_install", []),
        ]
        for key in ("install", "build"):
            specs[key] = [
                re.sub(r"\bcargo (test|build)\b(?! --locked)", r"cargo \1 --locked", command)
                for command in specs.get(key, [])
            ]
    env = make_env_script_list(specs)
    text = "# syntax=docker/dockerfile:1\n# Generated by dockerfile-gen-arm64.\n" + base
    # Bound compilation parallelism; runtime/test commands remain upstream.
    if language == "rust":
        text += "\nENV CARGO_BUILD_JOBS=2\n"
    text += "\nARG ARM64_REBUILD_TOKEN=stable\nRUN test -n \"$ARM64_REBUILD_TOKEN\"\n"
    if env:
        text += "\n" + make_heredoc_run_command(env) + "\n"
    repo_commands = make_repo_script_list(specs, instance["repo"], instance["base_commit"])
    if language == "java":
        # Exercise an existing test so Surefire providers are resolved before freezing.
        # With no P2P selector, use the pre-existing class containing the first F2P test.
        selectors = instance["PASS_TO_PASS"] or instance["FAIL_TO_PASS"]
        selector = selectors[0]
        warmup = next((command for command in specs["test_cmd"] if "-Dtest=" + selector in command), None)
        if warmup is None:
            raise ValueError("The Java adapter requires Maven commands with -Dtest selectors")
        if not instance["PASS_TO_PASS"]:
            warmup = warmup.replace("-Dtest=" + selector, "-Dtest=" + selector.split("#")[0])
        repo_commands.append(warmup)
    text += "\n" + make_heredoc_run_command(repo_commands) + "\n"
    if maven_artifacts:
        for name, digest in maven_artifacts.items():
            if (not re.fullmatch(r"[a-f0-9]{64}", digest) or Path(name).is_absolute()
                    or ".." in Path(name).parts or "\n" in name or "\r" in name):
                raise ValueError("Invalid Maven artifact checksum entry")
        checks = "\n".join(f"{digest}  /root/.m2/repository/{name}"
                           for name, digest in sorted(maven_artifacts.items()))
        text += "\n" + make_heredoc_run_command([
            "cat <<'ARM64_MAVEN_CHECKSUMS' > /tmp/arm64-maven.sha256\n"
            + checks + "\nARM64_MAVEN_CHECKSUMS",
            "sha256sum -c /tmp/arm64-maven.sha256",
            "rm /tmp/arm64-maven.sha256",
        ]) + "\n"
    if cargo_lock:
        text += f"\nRUN echo '{sha256(cargo_lock.encode())}  /testbed/Cargo.lock' | sha256sum -c -\n"
    return text + "\nWORKDIR /testbed\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance_ids", nargs="+", default=SELECTED)
    parser.add_argument("--draft", action="store_true", help="Generate unverified files under --output-dir only")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--refresh-lock", action="store_true")
    parser.add_argument("--keep-images", action="store_true")
    parser.add_argument("--logs-dir", type=Path, default=Path("logs/arm64"))
    parser.add_argument("--skip-tests", action="store_true", help="Explicitly omit baseline/gold tests; report as unvalidated")
    args = parser.parse_args()
    tasks = [TASKS_DIR / name for name in args.instance_ids]
    if any("/" in n or "\\" in n or not (p / "task.yaml").is_file()
           for n, p in zip(args.instance_ids, tasks)):
        parser.error("Unknown task ID")
    if args.draft and not args.output_dir:
        parser.error("--draft requires --output-dir; never overwrite verified tasks with drafts")
    if args.draft and args.output_dir.resolve().is_relative_to(TASKS_DIR.resolve()):
        parser.error("Draft output must be outside the source task tree")
    from .arm64_pipeline import resolve
    failed = []
    for task in tasks:
        try:
            _, _, _, base = task_spec(task)
            if args.draft:
                dest = args.output_dir / task.name
                dest.mkdir(parents=True, exist_ok=True)
                (dest / "Dockerfile").write_text(generate(task, base_image=arm_base(base)))
            else:
                resolve(task, args.logs_dir, refresh=args.refresh_lock,
                        keep_images=args.keep_images, run_tests=not args.skip_tests,
                        output_dir=args.output_dir)
        except Exception as error:
            print(f"{task.name}: FAILED: {error}", file=sys.stderr, flush=True)
            failed.append(task.name)
    if failed:
        print("Failed tasks: " + ", ".join(failed), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
