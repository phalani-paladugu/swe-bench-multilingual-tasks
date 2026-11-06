# ARM64 images on Docker Hub

All **20 images (12 C++ and 8 Java)** are published publicly under
[phalanipaladugu84963](https://hub.docker.com/u/phalanipaladugu84963).
Their platform is **linux/arm64**.

Every uploaded manifest was fetched with an anonymous registry pull token and
verified against its expected digest. The ARM64 image configuration and filesystem
layer hashes match the previously built and benchmark-validated local image.
Publication reused the validated images. All 20 digest-pinned references were
then pulled successfully, and mini-swe-agent 2.4.6 started each image and executed
its toolchain offline with zero model calls.

- [arm64-dockerhub-images.json](arm64-dockerhub-images.json) maps each task to an
  immutable Docker Hub image digest. Use this for reproducible runs.
- [arm64-dockerhub-manifests.json](arm64-dockerhub-manifests.json) records the
  published tags, index/platform/config digests, source commits, and verification
  timestamps.
- The `:latest` tags point to the same published images as of that record; the
  digest-pinned references continue selecting these exact images if tags change.

## Pull an image

```bash
# C++
docker pull --platform linux/arm64 \
  docker.io/phalanipaladugu84963/sweb.eval.arm64.fmtlib__fmt-3901:latest

# Java
docker pull --platform linux/arm64 \
  docker.io/phalanipaladugu84963/sweb.eval.arm64.google__gson-2024:latest
```

To pull all 20 by their recorded digests, run from the repository root:

```bash
.venv/bin/python -c 'import json; print("\n".join(json.load(open("arm64-dockerhub-images.json")).values()))' |
while IFS= read -r image; do
  docker pull --platform linux/arm64 "$image" || exit 1
done
```

## mini-swe-agent

Pull the images first: the ARM overlay uses `--pull=never` and runs containers
with networking disabled. Export the published 20-task dataset:

```bash
.venv-mini/bin/python scripts/prepare_mini_swe_arm64.py \
  --images arm64-dockerhub-images.json \
  --output-dir runs/mini-swe-dockerhub
```

Check image selection, architecture, base commits, native toolchains, and
container networking without making model calls:

```bash
HF_HUB_OFFLINE=1 HF_DATASETS_OFFLINE=1 \
  .venv-mini/bin/python scripts/check_mini_swe_arm64.py \
  --images arm64-dockerhub-images.json \
  --dataset-dir runs/mini-swe-dockerhub \
  --report logs/arm64/dockerhub/mini-swe-smoke.json
```

With your model/provider configured:

```bash
.venv-mini/bin/mini-extra swebench \
  --subset ./runs/mini-swe-dockerhub --split test \
  --model "$MINI_MODEL" --workers 1 \
  -c swebench.yaml -c configs/mini-swe-arm64.yaml \
  --output runs/mini-swe-dockerhub-results
```

Inference produces candidate patches; grading those patches is a separate step.
The original upstream task metadata is preserved, so use the explicit published
image mapping when configuring an evaluation harness.

## Published tasks

| Task | Language |
| --- | --- |
| [fmtlib__fmt-1683](https://hub.docker.com/r/phalanipaladugu84963/sweb.eval.arm64.fmtlib__fmt-1683) | C++ |
| [fmtlib__fmt-2310](https://hub.docker.com/r/phalanipaladugu84963/sweb.eval.arm64.fmtlib__fmt-2310) | C++ |
| [fmtlib__fmt-2317](https://hub.docker.com/r/phalanipaladugu84963/sweb.eval.arm64.fmtlib__fmt-2317) | C++ |
| [fmtlib__fmt-2457](https://hub.docker.com/r/phalanipaladugu84963/sweb.eval.arm64.fmtlib__fmt-2457) | C++ |
| [fmtlib__fmt-3158](https://hub.docker.com/r/phalanipaladugu84963/sweb.eval.arm64.fmtlib__fmt-3158) | C++ |
| [fmtlib__fmt-3248](https://hub.docker.com/r/phalanipaladugu84963/sweb.eval.arm64.fmtlib__fmt-3248) | C++ |
| [fmtlib__fmt-3272](https://hub.docker.com/r/phalanipaladugu84963/sweb.eval.arm64.fmtlib__fmt-3272) | C++ |
| [fmtlib__fmt-3729](https://hub.docker.com/r/phalanipaladugu84963/sweb.eval.arm64.fmtlib__fmt-3729) | C++ |
| [fmtlib__fmt-3750](https://hub.docker.com/r/phalanipaladugu84963/sweb.eval.arm64.fmtlib__fmt-3750) | C++ |
| [fmtlib__fmt-3863](https://hub.docker.com/r/phalanipaladugu84963/sweb.eval.arm64.fmtlib__fmt-3863) | C++ |
| [fmtlib__fmt-3901](https://hub.docker.com/r/phalanipaladugu84963/sweb.eval.arm64.fmtlib__fmt-3901) | C++ |
| [nlohmann__json-4237](https://hub.docker.com/r/phalanipaladugu84963/sweb.eval.arm64.nlohmann__json-4237) | C++ |
| [google__gson-2024](https://hub.docker.com/r/phalanipaladugu84963/sweb.eval.arm64.google__gson-2024) | Java |
| [google__gson-2061](https://hub.docker.com/r/phalanipaladugu84963/sweb.eval.arm64.google__gson-2061) | Java |
| [google__gson-2134](https://hub.docker.com/r/phalanipaladugu84963/sweb.eval.arm64.google__gson-2134) | Java |
| [google__gson-2158](https://hub.docker.com/r/phalanipaladugu84963/sweb.eval.arm64.google__gson-2158) | Java |
| [google__gson-2311](https://hub.docker.com/r/phalanipaladugu84963/sweb.eval.arm64.google__gson-2311) | Java |
| [google__gson-2479](https://hub.docker.com/r/phalanipaladugu84963/sweb.eval.arm64.google__gson-2479) | Java |
| [javaparser__javaparser-4538](https://hub.docker.com/r/phalanipaladugu84963/sweb.eval.arm64.javaparser__javaparser-4538) | Java |
| [javaparser__javaparser-4561](https://hub.docker.com/r/phalanipaladugu84963/sweb.eval.arm64.javaparser__javaparser-4561) | Java |
