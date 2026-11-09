#!/usr/bin/env python
"""What `Destination.write_registry_rows` costs, before and after #4708.

Why this shape. The production cost was never CPU or SQL: it was one network round trip to
MotherDuck per row. A local duckdb file has no round trip worth the name (microseconds), so
timing the write against a temp file proves nothing about MotherDuck. The quantity the fix
changed is the ROUND-TRIP COUNT, so that is what this counts, on the real code path — then it
shows wall clock tracking that count by injecting a fixed per-round-trip latency, and projects
to production using production's own observed latency.

Both implementations run against the same real `Destination` and the same real duckdb file, and
the row counts they leave behind are compared, so a faster number that wrote less is caught.

    uv run --package feather-cdc-postgres python scripts/bench_registry_write.py

Production calibration (#4708, rama_dw PR #4714's `stage_timing` events, run 20261109T072034Z):
`drift_write_schema_registry` took 575 s for a registry of about 1,400 rows.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import pathlib
import sys
import tempfile
import time

SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from feather_cdc_postgres import destination as dest_mod                      # noqa: E402
from feather_cdc_postgres.destination import (                                # noqa: E402
    RUNTIME_SCHEMA, Destination, DestinationSpec,
)

PRODUCTION_ROWS = 1400        # Featherbase's registry, as measured in #4708
PRODUCTION_SECONDS = 575.0    # what `drift_write_schema_registry` took for it


def rows_for(n_rows: int, n_tables: int) -> list[dict]:
    return [{"registration": "featherbase", "schema": "featherbase",
             "table": f"t{i % n_tables}", "column": f"c{i}",
             "type_oid": 23, "typmod": -1, "not_null": True, "key_role": None,
             "dest_type": "bigint", "mapping": "direct"} for i in range(n_rows)]


def write_registry_rows_pre_4708(dest: Destination, rows) -> int:
    """`Destination.write_registry_rows` as it stood before #4708 — rama_dw `4f537660c`,
    verbatim apart from `self.` becoming `dest.`. One delete per table, then `executemany`."""
    if not rows:
        return 0
    dest.ensure_runtime_tables()
    now = _dt.datetime.now(_dt.timezone.utc)
    pairs = sorted({(r["registration"], r["schema"], r["table"]) for r in rows})
    with dest.connect() as con:
        con.execute("begin transaction")
        try:
            for registration, schema, table in pairs:
                con.execute(f'delete from "{RUNTIME_SCHEMA}".schema_registry '
                            'where registration = ? and "schema" = ? and "table" = ?',
                            [registration, schema, table])
            con.executemany(
                f'insert into "{RUNTIME_SCHEMA}".schema_registry values (?,?,?,?,?,?,?,?,?,?,?)',
                [[r["registration"], r["schema"], r["table"], r["column"],
                  r.get("type_oid"), r.get("typmod"), r.get("not_null"),
                  r.get("key_role"), r.get("dest_type"), r.get("mapping"),
                  r.get("seen_at") or now] for r in rows])
            con.execute("commit")
        except Exception:
            con.execute("rollback")
            raise
    return len(rows)


class Metered:
    """Counts round trips and optionally pays `latency_s` for each, so wall clock on a local
    file behaves like a remote destination. `executemany(sql, params)` is `len(params)` round
    trips: that is what duckdb's MotherDuck extension charges for it."""

    def __init__(self, con, meter, latency_s: float) -> None:
        self._con, self._meter, self._latency = con, meter, latency_s

    def _charge(self, trips: int) -> None:
        self._meter[0] += trips
        if self._latency:
            time.sleep(self._latency * trips)

    def execute(self, sql, *args):
        self._charge(1)
        return self._con.execute(sql, *args)

    def executemany(self, sql, params, *args):
        self._charge(len(params))
        return self._con.executemany(sql, params, *args)

    def close(self):
        self._con.close()

    def __getattr__(self, name):
        return getattr(self._con, name)


def measure(label: str, write, rows, *, latency_s: float) -> dict:
    """One run of `write` into a fresh duckdb file, metered. Returns trips + wall clock."""
    with tempfile.TemporaryDirectory() as tmp:
        dest = Destination(DestinationSpec(kind="duckdb", database="bench",
                                           path=str(pathlib.Path(tmp) / "bench.duckdb")))
        dest.ensure_runtime_tables()          # outside the measurement, as in a real run
        meter = [0]
        real = dest_mod.duckdb.connect
        dest_mod.duckdb.connect = lambda *a, **k: Metered(real(*a, **k), meter, latency_s)
        try:
            started = time.monotonic()
            write(dest, rows)
            elapsed = time.monotonic() - started
        finally:
            dest_mod.duckdb.connect = real
        with dest.connect() as con:
            landed = con.execute(f'select count(*) from "{RUNTIME_SCHEMA}".schema_registry').fetchone()[0]
    return {"label": label, "trips": meter[0], "seconds": elapsed, "landed": landed}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", type=int, default=PRODUCTION_ROWS,
                    help="registry rows, i.e. columns across all selected tables")
    ap.add_argument("--tables", type=int, default=90, help="tables those columns belong to")
    ap.add_argument("--latency-ms", type=float, default=5.0,
                    help="injected per-round-trip latency for the metered run")
    args = ap.parse_args()

    rows = rows_for(args.rows, args.tables)
    impls = [("pre-#4708 (executemany)", write_registry_rows_pre_4708),
             ("0.3.0 (multi-row)", lambda d, r: d.write_registry_rows(r))]

    free = [measure(name, fn, rows, latency_s=0) for name, fn in impls]
    metered = [measure(name, fn, rows, latency_s=args.latency_ms / 1000) for name, fn in impls]

    print(f"\n{args.rows} registry rows over {args.tables} tables "
          f"(production Featherbase: about {PRODUCTION_ROWS} rows)\n")
    header = f"{'implementation':<26} {'round trips':>12} {'local (s)':>10} " \
             f"{f'at {args.latency_ms:g} ms (s)':>16} {'rows landed':>12}"
    print(header)
    print("-" * len(header))
    for f, m in zip(free, metered):
        assert f["landed"] == m["landed"] == args.rows, "an implementation wrote the wrong rows"
        print(f"{f['label']:<26} {f['trips']:>12,} {f['seconds']:>10.3f} "
              f"{m['seconds']:>16.2f} {f['landed']:>12,}")

    old, new = free[0]["trips"], free[1]["trips"]
    per_trip = PRODUCTION_SECONDS / (old * args.rows / PRODUCTION_ROWS)
    print(f"\nRound trips: {old:,} -> {new:,} ({old / new:.0f}x fewer)")
    print(f"Projection at production's own latency. {PRODUCTION_SECONDS:.0f} s was observed for "
          f"about {PRODUCTION_ROWS:,} rows, which the pre-#4708 write pays in about "
          f"{old * PRODUCTION_ROWS / args.rows:,.0f} round trips, so about "
          f"{per_trip * 1000:.0f} ms each.")
    print(f"  pre-#4708: {old * per_trip:>7.1f} s   (the measured 575 s, by construction)")
    print(f"  0.3.0:     {new * per_trip:>7.1f} s")
    print("\nA linear per-round-trip model. It does NOT cover the rest of a run — source drain,"
          "\ndrift compile, the slot probe and reconcile all need real databases.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
