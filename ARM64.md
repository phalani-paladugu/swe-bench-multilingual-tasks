# ARM64 Java and Rust task images

This fork ports 16 SWE-bench Multilingual tasks to Linux ARM64: eight Java
tasks (Gson and JavaParser) and eight Rust tasks (ripgrep, bat, and Tokio).
The selected IDs are in `dockerfile_gen.arm64.SELECTED`. The Java adapter currently
supports Maven projects; Gradle and Ant projects need separate adapters.

The workflow follows the [original ARM64 task methodology](https://github.com/phalani-paladugu/swe-bench-tasks/blob/arm64-dockerfiles/ARM64.md):
resolve the environment, freeze dependencies, rebuild independently, inspect both
images offline, and save task files only after the requested checks pass.

## What is preserved

- The original Dockerfile is archived byte-for-byte as `Dockerfile.amd`.
- Repository base commits, problem statements, gold/test patches, `tests.json`,
  `eval.sh`, and `task.yaml` are unchanged.
- Java uses the upstream JDK and Maven image versions. Maven Daemon stays at
  **1.0.2**, including its bundled Maven **3.9.9**. The release archive contains
  the portable `mvnd.sh` client and an ARM JNI library. The ARM image removes
  the x86 native client, links `mvnd` to `mvnd.sh`, and sets `MVND_CLIENT=jvm`.
  The archive download is SHA-256 verified.
- Rust uses the upstream toolchain versions and Cargo lockfile fixtures.
  The resolved lock is saved as `Cargo.lock.arm64` and embedded into the final
  Dockerfile. Verification builds use `cargo ... --locked`.
- The upstream generator remains available as `dockerfile-gen`; the ARM workflow
  is a separate `dockerfile-gen-arm64` command.

## Resolve, freeze, rebuild, and validate

1. Resolve the base tag to its **ARM64 platform-manifest digest**.
2. Build a provisional ARM64 image, including the exact repository checkout.
3. Inspect architecture, base commit, removed Git remotes, system package
   inventory, and language toolchain versions with networking disabled.
4. Freeze dependencies. For Rust, capture and hash `Cargo.lock`. For Java,
   record SHA-256 hashes of downloaded Maven artifacts, excluding locally
   installed project artifacts whose generated JAR timestamps are not stable.
   An existing unpatched Java test warms Surefire provider dependencies before
   the inventory is captured.
5. Rebuild from the frozen inputs. A unique build argument invalidates the
   repository/dependency layer so this is an independent resolution, while
   retaining the reusable base layers. Maven artifacts and Cargo locks are
   checked inside the generated Dockerfile.
6. Compare the complete resolve and rebuild inspection reports.
7. In separate containers with **no network**, apply the original test patch
   to the baseline checkout, then the original gold and test patches to a fresh
   checkout. Check every declared FAIL_TO_PASS and PASS_TO_PASS expectation.
   Java results come from Surefire JUnit XML; Rust results come from Cargo's
   individual test results. Missing or skipped expected tests fail validation,
   except for the explicitly reviewed compilation case described below.
   Test commands run individually so an expected baseline failure does not
   short-circuit later commands. The committed `eval.sh` remains unchanged.
8. Save `Dockerfile`, `Dockerfile.amd`, `arm64-lock.json`, and, for Rust,
   `Cargo.lock.arm64` only after verification and the requested tests pass.

`arm64-lock.json` records the exact validation outcomes and dependency inventory.
Detailed build logs, test logs, and JUnit reports are under `logs/arm64/` and are
not committed.

## Expected baseline compilation failure

For **google__gson-2024**, the original test patch references
FieldNamingPolicy.UPPER_CASE_WITH_UNDERSCORES, which is introduced by the gold
patch. Baseline compilation therefore fails before JUnit runs. The validator
records these selectors as **COMPILE_BLOCKED**, not as executed test failures.
It accepts this specific baseline only when every invocation fails in the
patched test files with precisely that missing symbol; there are no declared
PASS_TO_PASS selectors for this task. All three gold-patched tests must execute
and pass. Other compilation errors do not receive this exception.

## Generate or refresh

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# Process the selected 16 tasks.
dockerfile-gen-arm64

# Process an individual task.
dockerfile-gen-arm64 --instance_ids google__gson-2479

# Rebuild and repeat all checks instead of reusing an existing verified lock.
dockerfile-gen-arm64 --instance_ids google__gson-2479 --refresh-lock

# Generate a draft outside the task tree. This does not prove buildability.
dockerfile-gen-arm64 --draft --output-dir /tmp/multilingual-arm64
```

Existing locks are reused only when their recorded inputs match. A refresh retains
the saved base-image digest, Cargo lock, Maven artifact hashes, and toolchain
versions; unexpected drift is rejected. To intentionally resolve a new environment,
review and remove the old lock first. Use
`--refresh-lock` after changing task inputs. `--keep-images` retains temporary
resolve/verify images for investigation. `--skip-tests` explicitly records
benchmark validation as not run; it must not be described as a validated task.

## Build and use an image

```bash
task=google__gson-2479
docker buildx build --platform linux/arm64 --load \
  --tag "sweb.eval.arm64.$task:latest" \
  --file "tasks/$task/Dockerfile" "tasks/$task"
```

The original `task.yaml` still references its upstream x86 image. Configure the
evaluation harness to use the ARM image, using the mappings in
`arm64-images.json`; do not let it silently pull the original x86 image.
This repository publishes Dockerfiles and verification records, not registry
images.

The base digest, Rust dependency graph, Maven artifact content, and toolchain
versions are checked. Apt package versions are inventoried and compared between
the two validation builds but are not separately pinned against future apt
repository changes. An offline inspection/test run does not mean the image can
be built offline: source repositories, container registries, apt, Maven Central,
and crates.io must be reachable during builds.

These checks validate the benchmark environments and reference patches. They do
not constitute an agent evaluation or an ARM-versus-x86 performance comparison.

## Generator checks

```bash
python -m unittest discover -s tests -v
```

The focused tests cover missing/skipped test detection, JUnit/Cargo result
parsing, architecture conversion, lock integrity, and preservation of existing
task files when verification fails.

## Verified tasks

All 16 tasks were built and independently rebuilt on native ARM64. Offline
baseline/gold validation passed for every task. The generator's 14 unit tests
passed, and final Dockerfiles were compared byte-for-byte with the Dockerfiles
used by the verification builds. Full task records are in
[arm64-validation.json](arm64-validation.json).

Counts below are the original benchmark selectors; JavaParser selectors represent
entire test classes, so they can contain multiple JUnit cases.

| Task | Language | FAIL_TO_PASS | PASS_TO_PASS | Result |
| --- | --- | ---: | ---: | --- |
| [google__gson-2024](tasks/google__gson-2024/arm64-lock.json) | Java | 3 | 0 | Verified; baseline compile failure |
| [google__gson-2061](tasks/google__gson-2061/arm64-lock.json) | Java | 2 | 4 | Verified |
| [google__gson-2134](tasks/google__gson-2134/arm64-lock.json) | Java | 2 | 1 | Verified |
| [google__gson-2158](tasks/google__gson-2158/arm64-lock.json) | Java | 6 | 4 | Verified |
| [google__gson-2311](tasks/google__gson-2311/arm64-lock.json) | Java | 1 | 2 | Verified |
| [google__gson-2479](tasks/google__gson-2479/arm64-lock.json) | Java | 2 | 1 | Verified |
| [javaparser__javaparser-4538](tasks/javaparser__javaparser-4538/arm64-lock.json) | Java | 1 | 1 | Verified |
| [javaparser__javaparser-4561](tasks/javaparser__javaparser-4561/arm64-lock.json) | Java | 1 | 1 | Verified |
| [burntsushi__ripgrep-2209](tasks/burntsushi__ripgrep-2209/arm64-lock.json) | Rust | 1 | 0 | Verified |
| [burntsushi__ripgrep-2576](tasks/burntsushi__ripgrep-2576/arm64-lock.json) | Rust | 1 | 85 | Verified |
| [sharkdp__bat-2393](tasks/sharkdp__bat-2393/arm64-lock.json) | Rust | 1 | 2 | Verified |
| [sharkdp__bat-2650](tasks/sharkdp__bat-2650/arm64-lock.json) | Rust | 1 | 1 | Verified |
| [tokio-rs__tokio-6724](tasks/tokio-rs__tokio-6724/arm64-lock.json) | Rust | 1 | 2 | Verified |
| [tokio-rs__tokio-6838](tasks/tokio-rs__tokio-6838/arm64-lock.json) | Rust | 1 | 7 | Verified |
| [tokio-rs__tokio-4867](tasks/tokio-rs__tokio-4867/arm64-lock.json) | Rust | 1 | 22 | Verified |
| [tokio-rs__tokio-6603](tasks/tokio-rs__tokio-6603/arm64-lock.json) | Rust | 1 | 84 | Verified |

The locks retain their build-time input snapshots as `verification_inputs` when
final CLI/provenance refinements changed generator source hashes. The `inputs`
field records the final generator after its output was confirmed identical to
the independently built Dockerfile; original verification timestamps and test
outcomes are preserved.
