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

## ARM64 Dockerfiles

All **12 native ARM64 images were built and validated on November 5, 2026**.
They are retained locally as `sweb.eval.arm64.<instance-id>:latest` and are
[published on Docker Hub](DOCKER_HUB.md) under `phalanipaladugu84963`.
Use [arm64-dockerhub-images.json](arm64-dockerhub-images.json) for digest-pinned
registry references.

Validation passed for all 14 FAIL_TO_PASS selectors (fail on baseline, pass with
the gold patch) and all 745 PASS_TO_PASS selectors (pass in both). Each image was
built twice, with an identical inspected package inventory, compiler/toolchain,
and source checkout. mini-swe-agent 2.4.6 also started all 12 images and executed
their native toolchains with networking disabled and zero model calls.

Each task directory contains:

- `Dockerfile`: the verified ARM64 recipe with pinned package versions and an
  installed-package checksum.
- `Dockerfile.arm64`: the minimal ARM translation of the original x86 recipe.
- `Dockerfile.amd`: the original x86 Dockerfile, preserved byte-for-byte.
- `apt-packages.arm64.txt` and `arm64-lock.json`: the package snapshot, source
  and generator hashes, toolchain details, and baseline/gold validation results.

The table links to the minimal recipes; use each task's `Dockerfile` to rebuild
the verified environment.

| Task | ARM64 Dockerfile | Build target |
| --- | --- | --- |
| fmtlib__fmt-1683 | [Dockerfile.arm64](tasks/fmtlib__fmt-1683/Dockerfile.arm64) | printf-test |
| fmtlib__fmt-2310 | [Dockerfile.arm64](tasks/fmtlib__fmt-2310/Dockerfile.arm64) | format-test |
| fmtlib__fmt-2317 | [Dockerfile.arm64](tasks/fmtlib__fmt-2317/Dockerfile.arm64) | format-test |
| fmtlib__fmt-2457 | [Dockerfile.arm64](tasks/fmtlib__fmt-2457/Dockerfile.arm64) | ranges-test |
| fmtlib__fmt-3158 | [Dockerfile.arm64](tasks/fmtlib__fmt-3158/Dockerfile.arm64) | ranges-test |
| fmtlib__fmt-3248 | [Dockerfile.arm64](tasks/fmtlib__fmt-3248/Dockerfile.arm64) | format-test |
| fmtlib__fmt-3272 | [Dockerfile.arm64](tasks/fmtlib__fmt-3272/Dockerfile.arm64) | xchar-test |
| fmtlib__fmt-3729 | [Dockerfile.arm64](tasks/fmtlib__fmt-3729/Dockerfile.arm64) | std-test |
| fmtlib__fmt-3750 | [Dockerfile.arm64](tasks/fmtlib__fmt-3750/Dockerfile.arm64) | format-test |
| fmtlib__fmt-3863 | [Dockerfile.arm64](tasks/fmtlib__fmt-3863/Dockerfile.arm64) | ranges-test |
| fmtlib__fmt-3901 | [Dockerfile.arm64](tasks/fmtlib__fmt-3901/Dockerfile.arm64) | format-test |
| nlohmann__json-4237 | [Dockerfile.arm64](tasks/nlohmann__json-4237/Dockerfile.arm64) | test-udt_cpp11 |

### Matching the x86 environment

Each `Dockerfile.arm64` is byte-for-byte identical to the archived x86
`Dockerfile.amd` **except for its `FROM` line**:

```dockerfile
FROM --platform=linux/arm64 ubuntu:22.04@sha256:a8fb2ba88181746111cbbe2969826d6546b71226785b196c93d2c3445a25000d
```

The digest is the official Ubuntu 22.04 Linux ARM64 platform manifest. This keeps
the same OS release, explicit apt package list, repository URL and base commit,
vendored test-framework source, CMake options, build targets, build parallelism
(`$(nproc)`), working directory, user setup, shell initialization, and Git-history
cleanup. The task metadata, test/gold patches, test expectations, and evaluation
scripts are unchanged.

This establishes **recipe parity**, not verified parity with a previously built
x86 image. The original recipe runs an unpinned `apt update && apt install`, so
today's packages may differ from those in the historical x86 image. ARM64 also
uses native ARM binaries, compiler targets, and Ubuntu archive endpoints.
Historical x86 package/toolchain versions have not been compared directly.
The ARM environments have passed the original baseline/gold benchmark checks;
their observed package versions are frozen in the verified Dockerfiles.

The upstream `/root/.bashrc` line referencing Miniconda is preserved as part of
recipe parity; these Ubuntu C++ recipes do not install Conda. Build and validation
commands use non-interactive Bash and do not depend on that initialization.

### Rebuild a verified image

From the repository root, with an available Docker builder:

```bash
task=fmtlib__fmt-3901
docker buildx build --platform linux/arm64 --load \
  -f "tasks/$task/Dockerfile" \
  -t "sweb.eval.arm64.$task:latest" "tasks/$task"
```

To build all 12 sequentially:

```bash
.venv/bin/python -c 'import json; print("\n".join(json.load(open("arm64-cpp-images.json"))))' |
while IFS= read -r task; do
  docker buildx build --platform linux/arm64 --load \
    -f "tasks/$task/Dockerfile" \
    -t "sweb.eval.arm64.$task:latest" "tasks/$task" || exit 1
done
```

These direct builds use the committed package pins and inventory checksum,
but do not rerun benchmark validation or update the lock records. The pipeline
below rebuilds and validates the images. Building `Dockerfile.arm64` instead
uses the original unpinned apt recipe, so package versions may change over time.

### Regenerate the recipes without Docker

```bash
.venv/bin/dockerfile-gen-arm64-cpp --write-dockerfiles
```

Regeneration reuses each saved base digest and preserves the x86 Dockerfile. For
a deliberate base update, supply `--base-image ubuntu:22.04@sha256:<arm64-digest>`.
This mode does not create validation records or overwrite the task's `Dockerfile`.

## Build and validate all 12

With Docker Desktop signed in and the native ARM64 engine available, run from
the repository root:

```bash
.venv/bin/python -m pip install -e .
.venv/bin/dockerfile-gen-arm64-cpp --refresh-lock
```

For one task:

```bash
.venv/bin/dockerfile-gen-arm64-cpp --refresh-lock --instance_ids fmtlib__fmt-3901
```

The adapter follows the existing resolve, freeze, rebuild, and offline-validate
workflow:

1. Reuse the saved recipe's Ubuntu 22.04 ARM64 platform digest, or resolve it
   if no recipe exists.
2. Build the exact upstream recipe and repository base commit using the original
   CMake targets and build parallelism.
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
   and check all declared FAIL_TO_PASS and PASS_TO_PASS selectors. Only these
   validation rebuilds limit compiler parallelism to two workers; the saved
   ARM64 recipes keep the original `$(nproc)` command.
7. Only after validation passes, archive the original Dockerfile as
   `Dockerfile.amd`, save the ARM Dockerfile and `arm64-lock.json`, and retain
   `sweb.eval.arm64.<instance-id>:latest` in the local Docker engine.

After validation, `Dockerfile.arm64` remains the minimal architecture
translation. The verified `Dockerfile` additionally contains package pins, an
inventory checksum, and a rebuild-cache token.

The fmt tasks use GoogleTest output from CTest. The nlohmann/json task uses
doctest XML. Missing or skipped expected tests fail validation. Compilation
errors also fail validation; they are not silently counted as failing tests.
Original metadata, patches, test expectations, and `eval.sh` remain unchanged.

Package versions are frozen, but a future rebuild still needs those package
versions to remain available in the configured apt repositories. This does not
create an archival package mirror or make image builds offline.

Without `--refresh-lock`, existing passing locks are reused to regenerate
Dockerfiles without rebuilding images. `--refresh-lock` repeats builds and tests
while retaining the saved base digest and package versions. Detailed build/test
logs, `local-images.json`, and `mini-swe-smoke.json` are saved locally under
`logs/arm64/cpp/`; per-task validation records are committed in `arm64-lock.json`.

## mini-swe-agent

The C++ set has its own mapping, [arm64-cpp-images.json](arm64-cpp-images.json).
The original Java/Rust mapping remains the default for existing commands.

Export the C++ inference dataset:

```bash
.venv-mini/bin/python scripts/prepare_mini_swe_arm64.py \
  --images arm64-cpp-images.json \
  --output-dir runs/mini-swe-arm64-cpp
```

Repeat the completed mini-swe-agent Docker integration check without making
model calls:

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
Container startup, task selection, native toolchains, working directories,
base commits, and disabled networking were checked for all 12 C++ images.
Model-driven inference has not been run as part of these environment checks.
