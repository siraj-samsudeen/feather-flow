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
  "ptp_extract/src"        # Railway cron; three PTP instances (#2826)
  "stylehr_bronze/src"     # Railway cron; StyleHR bronze (#2826)
  "pg_md_cdc/src"          # Railway cron; generic PG→MD CDC runtime (#3079)
  "ax_retail_bronze/src"   # on-prem box (rides ssh_box.py put src/); AX Retail history (#3809)
  "petpooja_extract/src"   # Railway cron; Petpooja nightly (#36, on main since #4249)
)

for dest in "${TARGETS[@]}"; do
  rm -rf "${dest:?}/control_plane"
  cp -r "$SRC" "$dest/control_plane"
  find "$dest/control_plane" -name __pycache__ -type d -exec rm -rf {} +
  echo "vendored -> $dest/control_plane"
done

# business_day.py alone (#3854), for two consumers that must NOT take the whole package:
#   essl_stylehr_sync_audit — not a control-plane service; its pyproject declares pyodbc + duckdb
#     and nothing else, and control_plane/__init__.py imports psycopg. Vendoring a Postgres
#     dependency into a read-only LAN audit to gain one stdlib module is the wrong trade.
#   scripts/ — hand-run tools take sys.path[0] = scripts/, so `import business_day` resolves there;
#     the repo-root venv cannot import control_plane at all (it has no src/ on the path).
# Byte-identical, and guarded by tests/test_business_day_ist.py the same way the packages above
# are guarded by control_plane/tests/test_vendored.py.
SINGLE_FILE_TARGETS=(
  "essl_stylehr_sync_audit/src/essl_stylehr_sync_audit"
  "scripts"
)
for dest in "${SINGLE_FILE_TARGETS[@]}"; do
  cp "$SRC/business_day.py" "$dest/business_day.py"
  echo "vendored -> $dest/business_day.py"
done

echo "done. sameness is guarded by control_plane/tests/test_vendored.py"
echo "      and, for the single-file copies, tests/test_business_day_ist.py"
