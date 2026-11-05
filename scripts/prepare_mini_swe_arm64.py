"""Export only the issue text, task identity, and ARM image mapping for inference."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "runs" / "mini-swe-arm64")
    parser.add_argument("--images", type=Path, default=ROOT / "arm64-images.json")
    args = parser.parse_args()
    images = json.loads(args.images.read_text())
    rows = []
    for instance_id, image in images.items():
        task = ROOT / "tasks" / instance_id
        metadata = yaml.safe_load((task / "task.yaml").read_text())
        if not re.fullmatch(r"[0-9a-f]{40}", metadata["base_commit"]):
            raise ValueError(f"Invalid base commit for {instance_id}")
        if metadata["instance_id"] != instance_id or not image.startswith("sweb.eval.arm64."):
            raise ValueError(f"Invalid ARM task mapping for {instance_id}")
        rows.append({
            "instance_id": instance_id,
            "repo": metadata["repo"],
            "base_commit": metadata["base_commit"],
            "version": str(metadata["version"]),
            "problem_statement": (task / "problem_statement.md").read_text(),
            "image_name": image,
        })
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / "test.jsonl"
    output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
    print(f"Wrote {len(rows)} tasks to {output}")


if __name__ == "__main__":
    main()
