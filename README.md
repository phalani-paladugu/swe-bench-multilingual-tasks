# SWE-bench Dockerfiles (Multilingual)

This fork includes [16 ARM64 Java/Rust task environments](ARM64.md), with dependency locks and baseline/gold validation records.

Dockerfile generator for SWE-bench Multilingual benchmarks (C, Go, Java, JavaScript, PHP, Ruby, Rust).

## Usage

```bash
# From HuggingFace dataset
dockerfile-gen

# From local JSON/JSONL file
dockerfile-gen --instance_ids google__gson-2479

# Specific instances
dockerfile-gen --instance_ids google__gson-2479
```

## Output

`Dockerfile` and `eval.sh` are regenerated in place under `tasks/<instance_id>/`, from that task's `task.json`.

## Install

```bash
pip install -e .
```
