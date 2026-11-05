"""Build, inspect offline, freeze, independently rebuild, and validate ARM64 tasks."""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .arm64 import arm_base, generate, inputs, sha256, task_spec, validate_lock

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
MAVEN_INVENTORY = r"""
set -euo pipefail
cd /root/.m2/repository
find . -name _remote.repositories -print0 | while IFS= read -r -d '' marker; do
  while IFS= read -r file; do
    path="$(dirname "$marker")/$file"
    test -f "$path"
    sha256sum "$path"
  done < <(awk -F '[>=]' '!/^#/ && NF >= 3 && $2 != "" {print $1}' "$marker")
done | sort -u
"""


def execute(command, *, log: Path | None = None, timeout=3600):
    if log is None:
        return subprocess.check_output(command, text=True, timeout=timeout).strip()
    with log.open("w") as stream:
        stream.write(shlex.join(command) + "\n")
        stream.flush()
        result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}); see {log}")


def shell(image: str, command: str) -> str:
    return execute(["docker", "run", "--rm", "--network", "none", "--platform", "linux/arm64",
                    "--entrypoint", "/bin/bash", image, "-c", command], timeout=300)


def build(task: Path, dockerfile: str, image: str, logs: Path, stage: str):
    source = logs / f"{stage}.Dockerfile"
    source.write_text(dockerfile)
    print(f"{task.name}: {stage} build", flush=True)
    execute(["docker", "buildx", "build", "--platform", "linux/arm64", "--load",
             "--progress", "plain", "--build-arg", "ARM64_REBUILD_TOKEN=" + uuid.uuid4().hex,
             "--tag", image, "--file", str(source.resolve()), str(task.resolve())],
            log=logs / f"{stage}-build.log")


def inspect(image: str, language: str) -> tuple[dict, str | None]:
    common = shell(image, "set -e; uname -m; git -C /testbed rev-parse HEAD; "
                   "git -C /testbed remote; printf '\\n__DPKG__\\n'; dpkg-query -W | sort")
    git, dpkg = common.split("\n__DPKG__\n", 1)
    git_lines = git.strip().splitlines()
    report = {"architecture": git_lines[0], "base_commit": git_lines[1],
              "git_remotes": git_lines[2:],
              "system_packages": dict(line.split("\t", 1) for line in dpkg.splitlines())}
    if report["architecture"] != "aarch64" or report["git_remotes"]:
        raise ValueError("Expected native aarch64 execution and no Git remotes")
    if language == "java":
        report["java"] = shell(image, "java -version 2>&1")
        report["maven"] = ANSI.sub("", shell(image, "mvn --version")).splitlines()[:3]
        wrapper = shell(image, "if test -d /root/.m2/wrapper/dists && test -x /testbed/mvnw; "
                        "then cd /testbed && ./mvnw --version; fi")
        if wrapper:
            report["maven_wrapper"] = ANSI.sub("", wrapper).splitlines()[:3]
        report["mvnd"] = [line for line in ANSI.sub("", shell(image, "mvnd --version 2>/dev/null")).splitlines()
                          if "Maven Daemon" in line or line.startswith("Apache Maven ")]
        if not report["mvnd"] or not any("1.0.2" in line for line in report["mvnd"]):
            raise ValueError("Pinned mvnd 1.0.2 is unavailable")
        artifacts = {}
        for line in shell(image, MAVEN_INVENTORY).splitlines():
            digest, name = line.split(None, 1)
            artifacts[name.removeprefix("./")] = digest
        if not artifacts:
            raise ValueError("No resolved Maven artifacts found")
        report["maven_artifacts"] = artifacts
        return report, None
    report["rustc"] = shell(image, "rustc -Vv")
    report["cargo"] = shell(image, "cargo -V")
    cargo_lock = shell(image, "cat /testbed/Cargo.lock") + "\n"
    report["cargo_lock_sha256"] = sha256(cargo_lock.encode())
    return report, cargo_lock


def atomic_write(path: Path, content: str):
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    try:
        temporary.write_text(content)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def resolve(task: Path, logs_root: Path, *, refresh=False, keep_images=False,
            run_tests=True, output_dir: Path | None = None):
    instance, _, language, base = task_spec(task)
    dest = output_dir / task.name if output_dir else task
    saved = dest / "arm64-lock.json"
    saved_lock = json.loads(saved.read_text()) if saved.exists() else None
    if saved_lock is not None and not refresh:
        lock = saved_lock
        if lock.get("inputs") != inputs(task):
            raise ValueError("Inputs changed; use --refresh-lock")
        validate_lock(task, lock, cargo_dir=dest)
        if run_tests and not lock.get("validation", {}).get("passed"):
            raise ValueError("Lock has no passing baseline/gold validation; use --refresh-lock")
        cargo = (dest / "Cargo.lock.arm64").read_text() if language == "rust" else None
        text = generate(task, base_image=lock["base_image"], cargo_lock=cargo,
                        maven_artifacts=lock.get("maven_artifacts"))
        if (dest / "Dockerfile").read_text() != text:
            atomic_write(dest / "Dockerfile", text)
        print(f"{task.name}: reused verified lock", flush=True)
        return

    logs = logs_root.resolve() / task.name
    logs.mkdir(parents=True, exist_ok=True)
    if saved_lock is not None:
        validate_lock(task, saved_lock, cargo_dir=dest, check_inputs=False)
        pinned = saved_lock["base_image"]
        seed_cargo = (dest / "Cargo.lock.arm64").read_text() if language == "rust" else None
        seed_maven = saved_lock.get("maven_artifacts")
    else:
        pinned, seed_cargo, seed_maven = arm_base(base), None, None
    run_id = uuid.uuid4().hex[:10]
    images = [f"sweb.arm64.{task.name}:{stage}-{run_id}" for stage in ("resolve", "verify")]
    try:
        provisional = generate(task, base_image=pinned, cargo_lock=seed_cargo,
                               maven_artifacts=seed_maven)
        build(task, provisional, images[0], logs, "resolve")
        resolved, cargo = inspect(images[0], language)
        atomic_write(logs / "resolve.json", json.dumps(resolved, indent=2, sort_keys=True) + "\n")
        if resolved["base_commit"] != instance["base_commit"]:
            raise ValueError("Resolved checkout is not the task base commit")
        if saved_lock is not None:
            for key in ("java", "maven", "maven_wrapper", "mvnd", "maven_artifacts",
                        "rustc", "cargo", "cargo_lock_sha256"):
                if saved_lock.get(key) != resolved.get(key):
                    raise ValueError(f"Locked {key} drift during refresh; compare reports in {logs}")
        final = generate(task, base_image=pinned, cargo_lock=cargo,
                         maven_artifacts=resolved.get("maven_artifacts"))
        build(task, final, images[1], logs, "verify")
        verified, verified_cargo = inspect(images[1], language)
        atomic_write(logs / "verify.json", json.dumps(verified, indent=2, sort_keys=True) + "\n")
        if resolved != verified or cargo != verified_cargo:
            raise ValueError(f"Dependency/toolchain/checkout drift; compare reports in {logs}")
        validation = {"passed": False, "status": "not_run"}
        if run_tests:
            from .arm64_validate import validate
            validation = validate(task, images[1], logs)
            atomic_write(logs / "validation.json", json.dumps(validation, indent=2, sort_keys=True) + "\n")
            if not validation["passed"]:
                raise ValueError(f"Baseline/gold validation failed; see {logs / 'validation.json'}")
        lock = {
            "schema": 1, "language": language, "base_image": pinned,
            "upstream_base_image": base, "inputs": inputs(task),
            "verified_at": datetime.now(timezone.utc).isoformat(),
            **verified, "validation": validation,
        }
        dest.mkdir(parents=True, exist_ok=True)
        original = task / "Dockerfile.amd"
        original_bytes = original.read_bytes() if original.exists() else (task / "Dockerfile").read_bytes()
        archive = dest / "Dockerfile.amd"
        if not archive.exists():
            archive.write_bytes(original_bytes)
        elif archive.read_bytes() != original_bytes:
            raise ValueError("Existing AMD archive differs; refusing to overwrite")
        lock["amd_dockerfile_sha256"] = sha256(original_bytes)
        if cargo:
            atomic_write(dest / "Cargo.lock.arm64", cargo)
        atomic_write(dest / "Dockerfile", final)
        atomic_write(saved, json.dumps(lock, indent=2, sort_keys=True) + "\n")
        print(f"{task.name}: verified and saved", flush=True)
    finally:
        if keep_images:
            print(f"{task.name}: images retained: {' '.join(images)}", flush=True)
        else:
            subprocess.run(["docker", "image", "rm", *images],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
