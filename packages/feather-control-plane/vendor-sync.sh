#!/usr/bin/env bash
# Vendor the canonical `control_plane` package into each Railway service's src/.
#
# WHY: Railway builds each service from its own subdir (Root Directory), so a Dockerfile/Railpack
# build cannot import a repo-root `control_plane/`. Until the library is published to the public
# `feather-etl` package (#204, Path 1), we vendor a copy into each Railway service and guard it
# with control_plane/tests/test_vendored.py (a sameness check). The on-prem box services get the
# lib via their deploy rsync, not a vendored copy.
#
# Usage: edit the canonical control_plane/src/control_plane, then run this to refresh the copies.
set -euo pipefail
cd "$(dirname "$0")/.."   # repo root
SRC="control_plane/src/control_plane"

# Railway services that vendor a copy (extend as each is migrated to the control plane).
TARGETS=(
  "gofrugal_extract/src"
)

for dest in "${TARGETS[@]}"; do
  rm -rf "${dest:?}/control_plane"
  cp -r "$SRC" "$dest/control_plane"
  find "$dest/control_plane" -name __pycache__ -type d -exec rm -rf {} +
  echo "vendored -> $dest/control_plane"
done
echo "done. sameness is guarded by control_plane/tests/test_vendored.py"
