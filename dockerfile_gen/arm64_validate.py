"""Validate unchanged task expectations in separate baseline and gold containers."""
from __future__ import annotations

import io
import re
import subprocess
import tarfile
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from .arm64 import task_spec
from .arm64_pipeline import ANSI, execute


def rust_results(text: str) -> dict[str, str]:
    return {name: {"ok": "PASSED", "FAILED": "FAILED", "ignored": "SKIPPED"}[state]
            for name, state in re.findall(r"^test (.+?) \.\.\. (ok|FAILED|ignored)\b",
                                          ANSI.sub("", text), re.MULTILINE)}


def junit_results(data: bytes) -> dict[str, str]:
    results = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
        for member in archive:
            if not member.isfile() or not member.name.endswith(".xml"):
                continue
            root = ET.fromstring(archive.extractfile(member).read())
            for case in root.iter("testcase"):
                state = ("FAILED" if case.find("failure") is not None or case.find("error") is not None
                         else "SKIPPED" if case.find("skipped") is not None else "PASSED")
                name = case.get("classname", "") + "#" + case.get("name", "")
                # A repeated failure cannot be hidden by a later successful invocation.
                if results.get(name) != "FAILED":
                    results[name] = state
    return results


def select_results(expected: list[str], observed: dict[str, str], language: str) -> dict:
    selected = {}
    for selector in expected:
        if selector in observed:
            selected[selector] = observed[selector]
            continue
        if language == "java" and "#" not in selector:
            matches = [state for name, state in observed.items()
                       if name.split("#")[0].split("$")[0].split(".")[-1] == selector
                       or name.split("#")[0] == selector]
            if matches:
                selected[selector] = ("FAILED" if "FAILED" in matches
                                      else "PASSED" if all(s == "PASSED" for s in matches)
                                      else "SKIPPED")
                continue
        selected[selector] = "MISSING"
    return selected



def expected_compile_failure(task: Path, text: str) -> dict | None:
    """Recognize one reviewed feature task; never treat arbitrary build failures as tests."""
    if task.name != "google__gson-2024":
        return None
    symbol = "UPPER_CASE_WITH_UNDERSCORES"
    gold_patch = (task / "gold.patch").read_text()
    test_patch = (task / "test.patch").read_text()
    if not any(line.startswith("+") and symbol in line for line in gold_patch.splitlines()):
        return None
    if not any(line.startswith("+") and symbol in line for line in test_patch.splitlines()):
        return None
    text = ANSI.sub("", text)
    if not all(part in text for part in ("COMPILATION ERROR", "maven-compiler-plugin", ":testCompile")):
        return None
    allowed = set(re.findall(r"diff --git a/\S+ b/(\S+)", test_patch))
    diagnostics = re.findall(r"^\[ERROR\] /testbed/(.+?\.java):\[[^\]]+\] (.+)$", text, re.MULTILINE)
    symbols = re.findall(r"symbol:\s+(?:static|variable)\s+(\w+)", text)
    if not diagnostics or not symbols or set(symbols) != {symbol}:
        return None
    if not all(filename in allowed and message.strip() == "cannot find symbol"
               for filename, message in diagnostics):
        return None
    return {"symbol": symbol, "files": sorted({name for name, _ in diagnostics}),
            "reason": "The original test patch references the enum constant introduced by the gold patch."}


def phase(task: Path, image: str, logs: Path, *, gold: bool) -> dict:
    instance, specs, language, _ = task_spec(task)
    name = "gold" if gold else "baseline"
    container = "sweb-arm64-" + uuid.uuid4().hex[:12]
    print(f"{task.name}: {name} tests (network disabled)", flush=True)
    create = ["docker", "create", "--name", container, "--network", "none",
              "--platform", "linux/arm64", "--cpus", "2", "--memory", "4g",
              "--entrypoint", "/bin/bash", image, "-c", "sleep infinity"]
    execute(create)
    observed, commands = {}, []

    def run(script: str, check=True, timeout=1800):
        return subprocess.run(["docker", "exec", "-w", "/testbed", container,
                               "/bin/bash", "-c", script], capture_output=True,
                              text=True, check=check, timeout=timeout)
    try:
        execute(["docker", "start", container])
        for filename in ("test.patch", "gold.patch"):
            execute(["docker", "cp", str(task / filename), container + ":/tmp/" + filename])
        if gold:
            run("git apply /tmp/gold.patch")
        run("git apply /tmp/test.patch")
        for command in specs.get("build", []):
            result = run(command, check=False)
            (logs / f"{name}-setup.log").write_text(result.stdout + result.stderr)
            if result.returncode:
                return {"setup_failed": True, "returncode": result.returncode,
                        "FAIL_TO_PASS": {}, "PASS_TO_PASS": {}, "commands": []}
        for index, command in enumerate(specs["test_cmd"]):
            if language == "java":
                run("find /testbed -path '*/surefire-reports/TEST-*.xml' -delete")
            result = run(command, check=False)
            output = result.stdout + result.stderr
            filename = f"{name}-{index:02d}.log"
            (logs / filename).write_text("$ " + command + "\n" + output)
            record = {"command": command, "returncode": result.returncode, "log": filename}
            if not gold and result.returncode and not instance["PASS_TO_PASS"]:
                evidence = expected_compile_failure(task, output)
                if evidence:
                    record["expected_compile_failure"] = evidence
            commands.append(record)
            if language == "rust":
                parsed = rust_results(output)
            else:
                archive = subprocess.check_output([
                    "docker", "exec", "-w", "/testbed", container, "/bin/bash", "-c",
                    "find . -path '*/surefire-reports/TEST-*.xml' -print0 | tar --null -T - -cf -",
                ], timeout=60)
                (logs / f"{name}-{index:02d}-junit.tar").write_bytes(archive)
                parsed = junit_results(archive)
            for key, value in parsed.items():
                if observed.get(key) != "FAILED":
                    observed[key] = value
        compile_blocked = (not gold and not instance["PASS_TO_PASS"] and not observed
                           and bool(commands) and all(c.get("expected_compile_failure") for c in commands))
        f2p = select_results(instance["FAIL_TO_PASS"], observed, language)
        if compile_blocked:
            f2p = {name: "COMPILE_BLOCKED" for name in f2p}
        return {
            "FAIL_TO_PASS": f2p,
            "PASS_TO_PASS": select_results(instance["PASS_TO_PASS"], observed, language),
            "observed_test_count": len(observed), "commands": commands,
            "expected_compile_failure": compile_blocked,
        }
    finally:
        subprocess.run(["docker", "rm", "-f", container],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def validate(task: Path, image: str, logs: Path) -> dict:
    baseline = phase(task, image, logs, gold=False)
    gold = phase(task, image, logs, gold=True)
    passed = (
        not baseline.get("setup_failed") and not gold.get("setup_failed")
        and bool(baseline["FAIL_TO_PASS"])
        and (all(s == "FAILED" for s in baseline["FAIL_TO_PASS"].values())
             or baseline.get("expected_compile_failure", False))
        and all(s == "PASSED" for s in baseline["PASS_TO_PASS"].values())
        and all(s == "PASSED" for s in gold["FAIL_TO_PASS"].values())
        and all(s == "PASSED" for s in gold["PASS_TO_PASS"].values())
        and all(c["returncode"] == 0 for c in gold["commands"])
    )
    return {"passed": passed, "network": "none",
            "method": "upstream commands executed individually; JUnit XML or Cargo test results",
            "baseline": baseline, "gold": gold}
