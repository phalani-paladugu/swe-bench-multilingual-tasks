"""Offline baseline/gold validation for the original C++ benchmark selectors."""
from __future__ import annotations

import re
import subprocess
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from .arm64_cpp import task_spec
from .arm64_pipeline import ANSI, execute


def merge_result(results: dict, name: str, state: str):
    # Never let a later successful repetition hide a failure or skipped execution.
    rank = {"PASSED": 0, "SKIPPED": 1, "FAILED": 2}
    if name not in results or rank[state] > rank[results[name]]:
        results[name] = state


def googletest_results(text: str) -> dict[str, str]:
    results = {}
    for state, name in re.findall(
        r"^.*\[\s*(OK|FAILED|SKIPPED)\s*\]\s+(\S+)\s+\([^\n]*\)\s*$",
        ANSI.sub("", text), re.MULTILINE,
    ):
        merge_result(results, name, {"OK": "PASSED", "FAILED": "FAILED", "SKIPPED": "SKIPPED"}[state])
    return results


def doctest_results(text: str) -> dict[str, str]:
    start = text.find("<doctest")
    end = text.find("</doctest>", start)
    if start < 0 or end < 0:
        return {}
    root = ET.fromstring(text[start:end + len("</doctest>")])
    results = {}
    for case in root.findall(".//TestCase"):
        for subcase in case.findall(".//SubCase"):
            name = case.get("name", "") + " > " + subcase.get("name", "")
            expressions = subcase.findall(".//Expression")
            failures = subcase.findall(".//Exception") + subcase.findall(".//FatalErrorCondition")
            if failures or any(expr.get("success") != "true" for expr in expressions):
                state = "FAILED"
            elif expressions:
                state = "PASSED"
            else:
                state = "SKIPPED"
            merge_result(results, name, state)
    return results


def phase(task: Path, image: str, logs: Path, *, gold: bool):
    instance, specs = task_spec(task)
    phase_name = "gold" if gold else "baseline"
    container = "sweb-cpp-" + uuid.uuid4().hex[:12]
    print(f"{task.name}: {phase_name} build/tests (network disabled)", flush=True)
    execute(["docker", "create", "--name", container, "--network", "none",
             "--platform", "linux/arm64", "--cpus", "2", "--memory", "4g",
             "--entrypoint", "/bin/bash", image, "-c", "sleep infinity"])

    def run(script: str, check=True):
        return subprocess.run(["docker", "exec", "-w", "/testbed", container,
                               "/bin/bash", "-c", script], capture_output=True,
                              text=True, check=check, timeout=1800)
    try:
        execute(["docker", "start", container])
        for filename in ("test.patch", "gold.patch"):
            execute(["docker", "cp", str(task / filename), container + ":/tmp/" + filename])
        if gold:
            run("git apply /tmp/gold.patch")
        run("git apply /tmp/test.patch")
        # Upstream C++ build lists include cd commands; execute in one shell.
        build_script = "set -euo pipefail\n" + "\n".join(specs.get("build", []))
        setup = run(build_script, check=False)
        (logs / f"{phase_name}-build.log").write_text(build_script + "\n" + setup.stdout + setup.stderr)
        if setup.returncode:
            return {"setup_failed": True, "returncode": setup.returncode,
                    "FAIL_TO_PASS": {}, "PASS_TO_PASS": {}, "observed_test_count": 0}
        commands = specs["test_cmd"]
        script = "set -o pipefail\n" + "\n".join(commands)
        result = run(script, check=False)
        output = result.stdout + result.stderr
        (logs / f"{phase_name}-tests.log").write_text("$ " + script + "\n" + output)
        observed = (googletest_results(output) if instance["log_parser"] == "parse_log_googletest"
                    else doctest_results(output))
        return {
            "FAIL_TO_PASS": {name: observed.get(name, "MISSING") for name in instance["FAIL_TO_PASS"]},
            "PASS_TO_PASS": {name: observed.get(name, "MISSING") for name in instance["PASS_TO_PASS"]},
            "observed_test_count": len(observed), "returncode": result.returncode,
            "commands": commands,
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
        and all(state == "FAILED" for state in baseline["FAIL_TO_PASS"].values())
        and all(state == "PASSED" for state in baseline["PASS_TO_PASS"].values())
        and all(state == "PASSED" for state in gold["FAIL_TO_PASS"].values())
        and all(state == "PASSED" for state in gold["PASS_TO_PASS"].values())
        and baseline["returncode"] != 0 and gold["returncode"] == 0
    )
    return {"passed": passed, "network": "none", "baseline": baseline, "gold": gold,
            "method": "Original GoogleTest/doctest selectors; upstream build/test commands with two compiler workers"}
