#!/usr/bin/env bash
# Build and verify a Utah live ISO with tuna-os/tacklebox.
#
# Shared by `just iso-tacklebox` and the CI tacklebox-iso job: inject the image
# ref into a working recipe, install tacklebox if missing, build the ISO,
# verify it, and print its size. The live-boot gate is CI-only and lives in
# tacklebox-boot-gate.sh.
#
# Usage: tacklebox-iso.sh IMAGE_REF RECIPE OUTPUT_ISO
set -euo pipefail

IMAGE_REF="${1:?image ref is required}"
RECIPE="${2:?recipe path is required}"
OUTPUT_ISO="${3:?output ISO path is required}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RECIPE="$(realpath "${RECIPE}")"
OUTPUT_ISO="$(realpath "${OUTPUT_ISO}")"
OUTPUT_DIR="$(dirname "${OUTPUT_ISO}")"
BASENAME="$(basename "${OUTPUT_ISO}")"

# Inject the image ref into a working recipe beside the original. The committed
# utah.json names the testing tag; CI passes a digest-pinned ref because
# build_main publishes the digest, not the :testing tag, so only the pinned ref
# is pullable in post-testing-e2e. live_customize resolves relative to the
# recipe file's directory, so the working recipe sits beside the original.
WORK_RECIPE="$(mktemp "${RECIPE%/*}/.utah-tmp-XXXXXX.json")"
trap 'rm -f "${WORK_RECIPE}"' EXIT
python3 - "${RECIPE}" "${IMAGE_REF}" "${WORK_RECIPE}" <<'PY'
import json, sys
src, ref, dst = sys.argv[1], sys.argv[2], sys.argv[3]
r = json.load(open(src))
r["offline_payloads"] = [ref]
r["bootable_environments"][0]["image"] = ref
json.dump(r, open(dst, "w"), indent=2)
PY

# Tacklebox has no published releases and its Go module needs Go 1.26+, which a
# host may not have. Build it from the upstream Containerfile (a version-locked
# golang:1.27 stage) instead of trusting the host toolchain -- the same
# podman-first approach the rest of Utah uses.
TBX_IMAGE="utah-tacklebox"
TBX_SRC="/tmp/tacklebox-src"

build_tacklebox() {
  podman image exists "${TBX_IMAGE}" >/dev/null 2>&1 && return 0
  echo "Building tacklebox from upstream source (no published releases)"
  rm -rf "${TBX_SRC}"
  git clone --depth 1 https://github.com/tuna-os/tacklebox.git "${TBX_SRC}"
  podman build -t "${TBX_IMAGE}" "${TBX_SRC}"
}

# Run tacklebox with the repo mounted read-only (so the recipe and its
# live_customize scripts resolve against it) and the output dir mounted
# writable. --privileged: tacklebox mounts images and loops squashfs roots.
# GHCR login happens inside the same container, before tacklebox pulls the
# published Utah image to embed as the offline payload.
run_tacklebox() {
  build_tacklebox
  local args=("$@")
  # shellcheck disable=SC2069
  podman run --rm --privileged \
    -v "${ROOT}:/work:ro" \
    -v "${OUTPUT_DIR}:/out" \
    -e TBOX_CUSTOMIZE_CAPS=net_admin \
    -e GHCR_TOKEN="${GH_TOKEN:-${GITHUB_TOKEN:-}}" \
    -e GHCR_USER="${GITHUB_ACTOR:-ghcr}" \
    "${TBX_IMAGE}" sh -c '
      echo "${GHCR_TOKEN}" | podman login ghcr.io -u "${GHCR_USER}" --password-stdin
      exec tacklebox "$@"' _ "${args[@]}" 2>&1
}

echo "Building ${BASENAME} from ${IMAGE_REF} with tacklebox"
run_tacklebox build "${WORK_RECIPE}" --iso "/out/${BASENAME}"

echo "Verifying ${BASENAME}"
podman run --rm \
  -v "${OUTPUT_DIR}:/out" \
  "${TBX_IMAGE}" verify "/out/${BASENAME}"

echo "ISO ready: ${OUTPUT_ISO} ($(du -sh "${OUTPUT_ISO}" | cut -f1))"
