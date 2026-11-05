# Run mini-swe-agent with the 16 ARM64 tasks

mini-swe-agent can run these Java and Rust tasks through its Docker environment.
The integration here is tested with **mini-swe-agent 2.4.6**. Each exported task
sets `image_name` to its local ARM tag. This matters because mini's default
SWE-bench image name uses x86_64, and its benchmark runner chooses the image from
the task record rather than a shared `environment.image` setting.
See the [upstream batch runner](https://github.com/SWE-agent/mini-swe-agent/blob/main/src/minisweagent/run/benchmarks/swebench.py)
and [SWE-bench usage documentation](https://mini-swe-agent.com/latest/usage/swebench/).

All 16 local images were built and their offline environment inspections matched
the saved dependency locks. mini-swe-agent 2.4.6 then started all 16 images and
passed the environment checks below with zero model calls. Local image IDs and
check results are recorded in `logs/arm64/local-images.json` and
`logs/arm64/mini-swe-smoke.json` on the build machine.

## Set up

Run these commands from this repository's root, with Docker running on an ARM64
machine:

```bash
python3 -m venv .venv-mini
.venv-mini/bin/python -m pip install 'mini-swe-agent==2.4.6'
.venv-mini/bin/python scripts/prepare_mini_swe_arm64.py
```

The export creates `runs/mini-swe-arm64/test.jsonl`, containing exactly the 16
selected tasks, their original issue text, base commits, and ARM image names.
Gold patches and test patches are kept out of the inference dataset. The
original benchmark files remain unchanged.

## Local images

All image names are listed in [arm64-images.json](arm64-images.json), for example:

```text
sweb.eval.arm64.google__gson-2479:latest
sweb.eval.arm64.tokio-rs__tokio-6724:latest
```

These tags are local to the Docker daemon where they are built. A GitHub fork
contains the Dockerfiles, not the image layers. On another ARM machine, build
all 16 images from the repository root:

```bash
python3 - <<'BUILD_IMAGES'
import json
import subprocess
from pathlib import Path

for instance_id, image in json.loads(Path('arm64-images.json').read_text()).items():
    task = Path('tasks') / instance_id
    subprocess.run([
        'docker', 'buildx', 'build', '--platform', 'linux/arm64', '--load',
        '--tag', image, '--file', str(task / 'Dockerfile'), str(task),
    ], check=True)
BUILD_IMAGES
```

Builds require network access. The locked Maven/Cargo content is checked during
builds; the [ARM64 methodology notes](ARM64.md) describe reproducibility limits.

## Check the integration without a model

```bash
HF_HUB_OFFLINE=1 HF_DATASETS_OFFLINE=1 \
  .venv-mini/bin/python scripts/check_mini_swe_arm64.py
```

This loads the exported dataset with the same dataset library used by mini,
uses mini's actual image selection and Docker environment, and checks native
`aarch64`, `/testbed`, the base commit, offline container networking, and the
Java/Rust toolchains. It makes **zero model calls** and writes
`logs/arm64/mini-swe-smoke.json`. This is an environment check, not a benchmark
solve-rate result.

## Run inference

Set `MINI_MODEL` to your provider/model identifier and configure that provider's
credentials as described in mini's documentation. Then run:

```bash
.venv-mini/bin/mini-extra swebench \
  --subset ./runs/mini-swe-arm64 \
  --split test \
  --model "$MINI_MODEL" \
  --workers 1 \
  -c swebench.yaml \
  -c configs/mini-swe-arm64.yaml \
  --output runs/mini-swe-results
```

To start with one task, add `--filter '^google__gson-2479$'`. Both config arguments
are needed: mini's bundled config supplies the agent prompts and model settings;
our overlay supplies the ARM environment settings. Review mini's model and cost
limits before launching your chosen run.

The overlay enforces `linux/arm64` and `--pull=never`, so a missing local image
fails immediately. Each container has two CPUs, a 4 GB memory limit, and no
network access. The model client runs on the host and can still reach its API.
One worker is appropriate for the current Docker allocation of about 8 GB RAM.
Increase workers only when the Docker host has sufficient resources.

mini writes candidate patches to `runs/mini-swe-results/preds.json` and saves
per-task trajectories. Grading those patches is a separate step. To report ARM
results, the evaluator must use these same ARM images; an evaluation that pulls
the default upstream x86 images measures a different environment. The existing
`arm64-lock.json` records validate the baseline and reference gold patches, not
future model-generated patches.
