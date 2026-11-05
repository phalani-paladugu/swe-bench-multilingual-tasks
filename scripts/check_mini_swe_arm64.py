"""Check mini-swe-agent's ARM Docker integration without making model calls."""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from datasets import load_dataset
from minisweagent import __version__
from minisweagent.config import get_config_from_spec
from minisweagent.run.benchmarks.swebench import get_sb_environment, get_swebench_docker_image_name
from minisweagent.utils.serialize import recursive_merge

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=ROOT / "runs" / "mini-swe-arm64")
    parser.add_argument("--instance-ids", nargs="+")
    parser.add_argument("--report", type=Path, default=ROOT / "logs" / "arm64" / "mini-swe-smoke.json")
    parser.add_argument("--images", type=Path, default=ROOT / "arm64-images.json")
    args = parser.parse_args()
    instances = list(load_dataset(str(args.dataset_dir), split="test"))
    images = json.loads(args.images.read_text())
    if {row["instance_id"] for row in instances} != set(images) or len(instances) != len(images):
        raise ValueError("Dataset must contain exactly the tasks in the selected image mapping")
    if args.instance_ids:
        if not set(args.instance_ids) <= set(images):
            parser.error("Unknown instance ID")
        instances = [row for row in instances if row["instance_id"] in args.instance_ids]
    config = recursive_merge(get_config_from_spec("swebench.yaml"),
                             get_config_from_spec(ROOT / "configs" / "mini-swe-arm64.yaml"))
    results = []
    for row in instances:
        name = row["instance_id"]
        if get_swebench_docker_image_name(row) != images[name]:
            raise ValueError(f"Wrong image selected for {name}")
        lock = json.loads((ROOT / "tasks" / name / "arm64-lock.json").read_text())
        toolchain = {
            "java": "java -version 2>&1 && mvnd --version",
            "rust": "rustc -Vv && cargo -V",
            "cpp": "g++ --version && cmake --version && ctest --version",
        }[lock["language"]]
        env = get_sb_environment(config, row)
        try:
            command = "set -e; uname -m; pwd; git rev-parse HEAD; " + toolchain
            result = env.execute({"command": command}, timeout=180)
            if result["returncode"] != 0:
                raise RuntimeError(f"Toolchain check failed for {name}: {result}")
            lines = result["output"].splitlines()
            if lines[:3] != ["aarch64", "/testbed", row["base_commit"]]:
                raise RuntimeError(f"Unexpected environment for {name}: {result}")
            container = json.loads(subprocess.check_output(["docker", "inspect", env.container_id], text=True))[0]
            if container["HostConfig"]["NetworkMode"] != "none":
                raise RuntimeError("Expected an offline container")
            results.append({"instance_id": name, "image": images[name], "image_id": container["Image"],
                            "architecture": "aarch64", "network": "none", "base_commit": row["base_commit"],
                            "toolchain_output": "\n".join(lines[3:]), "passed": True})
            print(f"PASS {name}: mini-swe-agent started the ARM image and executed its toolchain", flush=True)
        finally:
            subprocess.run(["docker", "rm", "-f", env.container_id], check=True, capture_output=True)
            env.container_id = None
    report = {"mini_swe_agent_version": __version__, "checked_at": datetime.now(timezone.utc).isoformat(),
              "model_calls": 0, "checks": results}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Saved {len(results)} integration checks to {args.report}")


if __name__ == "__main__":
    main()
