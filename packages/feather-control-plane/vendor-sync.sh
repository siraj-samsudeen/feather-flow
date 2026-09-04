#!/usr/bin/env bash
# Vendor the canonical `control_plane` package into each Railway service's src/.
#
# WHY: Railway builds each service from its own subdir (Root Directory), so a Dockerfile/Railpack
# build cannot import a repo-root `control_plane/`. Until the library is published to the public
# `feather-etl` package (#204, Path 1), we vendor a copy into each service's src/ and guard it
# with control_plane/tests/test_vendored.py (a sameness check). The on-prem box services
# (sap_bronze, #221) vendor the same way: their deploy is `ssh_box.py put src/` and run.sh uses
# `PYTHONPATH=src`, so the vendored src/control_plane rides the existing rsync — no deploy change.
#
# Usage: edit the canonical control_plane/src/control_plane, then run this to refresh the copies.
set -euo pipefail
cd "$(dirname "$0")/.."   # repo root
SRC="control_plane/src/control_plane"

# Services that vendor a copy (extend as each is migrated to the control plane).
TARGETS=(
  "gofrugal_extract/src"   # Railway
  "zakya_extract/src"      # Railway
  "sap_bronze/src"         # on-prem box (rides ssh_box.py put src/)
  "dbt_runner/src"         # Railway cron; uses store.py only (#2640, see dbt_control_pg.py)
  "featherbase_extract/src"  # Railway cron; CDC drainer (#2851)
  "ptp_extract/src"        # Railway cron; three PTP instances (#2826)
  "stylehr_bronze/src"     # Railway cron; StyleHR bronze (#2826)
)

for dest in "${TARGETS[@]}"; do
  rm -rf "${dest:?}/control_plane"
  cp -r "$SRC" "$dest/control_plane"
  find "$dest/control_plane" -name __pycache__ -type d -exec rm -rf {} +
  echo "vendored -> $dest/control_plane"
done
echo "done. sameness is guarded by control_plane/tests/test_vendored.py"
