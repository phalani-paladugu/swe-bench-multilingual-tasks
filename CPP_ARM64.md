# C++ ARM64 task environments

The current SWE-bench Multilingual task tree contains **12 C++ tasks**:

| Repository | C++ tasks |
| --- | ---: |
| fmtlib/fmt | 11 |
| nlohmann/json | 1 |
| Total | 12 |

The generator groups C and C++ together in `dockerfile_gen/c.py`. That combined
group contains 42 tasks: the 12 C++ tasks above plus 12 Redis, nine jq, five
MicroPython, and four Valkey tasks written in C. A set of 16 strictly C++ tasks
cannot be selected from this repository.

## Current status

The C++ adapter, image mapping, dataset export, and parser/generator tests are
prepared. **C++ image builds and baseline/gold validation are pending.** Docker
Desktop currently rejects build requests because administrator policy requires
sign-in with an account belonging to the organization required by the administrator.

No C++ task Dockerfiles have been replaced or marked verified. Local unverified
drafts are under `runs/arm64-cpp-drafts/`. The existing 16 Java/Rust task
Dockerfiles and verification records are unchanged.

## Build and validate all 12

With Docker Desktop signed in and the native ARM64 engine available, run from
the repository root:

```bash
.venv/bin/python -m pip install -e .
.venv/bin/dockerfile-gen-arm64-cpp
```

For one task:

```bash
.venv/bin/dockerfile-gen-arm64-cpp --instance_ids fmtlib__fmt-3901
```

The adapter follows the existing resolve, freeze, rebuild, and offline-validate
workflow:

1. Pin the Ubuntu 22.04 ARM64 platform manifest.
2. Build the exact upstream repository base commit using the original CMake
   targets, with compiler parallelism limited to two workers.
3. Inspect the architecture, compiler target, repository commit, removed Git
   remotes, GCC/G++, CMake, CTest, Make, Python, and installed Debian packages
   with networking disabled.
4. Freeze the complete package/version inventory into the final Dockerfile and
   `apt-packages.arm64.txt`. The base commit pins the vendored test frameworks.
5. Independently rebuild the repository/dependency layers and compare the two
   inspection reports. A checksum in the final Dockerfile verifies the exact
   installed package inventory.
6. In separate offline containers, apply the original test patch to baseline,
   then the original gold and test patches to a fresh checkout. Rebuild each
   and check all declared FAIL_TO_PASS and PASS_TO_PASS selectors.
7. Only after validation passes, archive the original Dockerfile as
   `Dockerfile.amd`, save the ARM Dockerfile and `arm64-lock.json`, and retain
   `sweb.eval.arm64.<instance-id>:latest` in the local Docker engine.

The fmt tasks use GoogleTest output from CTest. The nlohmann/json task uses
doctest XML. Missing or skipped expected tests fail validation. Compilation
errors also fail validation; they are not silently counted as failing tests.
Original metadata, patches, test expectations, and `eval.sh` remain unchanged.

Package versions are frozen, but a future rebuild still needs those package
versions to remain available in the configured apt repositories. This does not
create an archival package mirror or make image builds offline.

Existing passing locks are reused for Dockerfile regeneration. Use
`--refresh-lock` to repeat builds and tests while retaining the saved base digest
and package versions. Detailed logs are saved under `logs/arm64/cpp/`.

## mini-swe-agent

The C++ set has its own mapping, [arm64-cpp-images.json](arm64-cpp-images.json).
The original Java/Rust mapping remains the default for existing commands.

Export the C++ inference dataset:

```bash
.venv-mini/bin/python scripts/prepare_mini_swe_arm64.py \
  --images arm64-cpp-images.json \
  --output-dir runs/mini-swe-arm64-cpp
```

After all images have passed validation, check mini-swe-agent's Docker integration
without making model calls:

```bash
HF_HUB_OFFLINE=1 HF_DATASETS_OFFLINE=1 \
  .venv-mini/bin/python scripts/check_mini_swe_arm64.py \
  --images arm64-cpp-images.json \
  --dataset-dir runs/mini-swe-arm64-cpp \
  --report logs/arm64/cpp/mini-swe-smoke.json
```

With your model/provider configured, run inference:

```bash
.venv-mini/bin/mini-extra swebench \
  --subset ./runs/mini-swe-arm64-cpp --split test \
  --model "$MINI_MODEL" --workers 1 \
  -c swebench.yaml -c configs/mini-swe-arm64.yaml \
  --output runs/mini-swe-cpp-results
```

The ARM overlay requires local images and prevents an x86 image fallback.
Inference generates candidate patches; grading those patches is a separate step.
These commands have not yet been integration-tested with the C++ images because
Docker Desktop sign-in is blocking the builds.
