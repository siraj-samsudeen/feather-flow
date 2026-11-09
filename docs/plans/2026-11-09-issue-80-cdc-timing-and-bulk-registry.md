# feather-cdc-postgres 0.3.0 — stage timing + a bulk schema-registry write

Created: 2026-11-09
Status: IN PROGRESS
Approved: Yes
Slice: —
Type: Fix
Review: —

## Why

0.2.0 shipped on 09-Nov-2026 and was behind within hours. rama_dw's #4708 found that
production Featherbase CDC spends about 670 s per run moving zero rows, instrumented the run
to find out where (its #4714), and then fixed the one stage that was 86% of it (its #4717).
Both landed on rama_dw `origin/main` on top of `a4e3800d1`, i.e. on top of the exact tree
0.2.0 was cut from.

rama_dw's PR #4714 says it plainly: *"PR #4706 moves this runtime to PyPI. If it merges later,
this instrumentation has to go across to feather-flow, or it is lost."* This is that crossing.

Production numbers, from `stage_timing` events written by #4714 on run `20261109T072034Z`:

| Stage | Featherbase (0 rows) | `control_plane_cdc` |
|---|---|---|
| `drift_write_schema_registry` | **575 s** | 16–18 s |
| `reconcile_counts` | 56 s | 1–6 s |
| Everything else | ~43 s | ~8–20 s |
| Whole run | **674 s** | 26–46 s |

The cost was `Destination.write_registry_rows` using `executemany` over ~1,400 rows: duckdb's
MotherDuck extension pays a round trip per row.

## The upstream base, and how the delta was measured

Same technique as the 0.2.0 catch-up: the module was renamed on the way into the package
(`pg_md_cdc` → `feather_cdc_postgres`), so every comparison normalises the rename first.

```sh
cd rama_dw
FF=.../feather-flow/packages/feather-cdc-postgres/src/feather_cdc_postgres
diff <(git show "$V:pg_md_cdc/src/pg_md_cdc/destination.py" | sed 's/pg_md_cdc/feather_cdc_postgres/g') \
     "$FF/destination.py"
```

**Base pinned at rama_dw `86c892668`** — the commit before its #4599 deletes the vendored tree,
and the tree 0.2.0 was cut from. At that commit, per-file diff line counts against the package:

```
0  destination.py   0  drift.py   0  runner.py   0  stream.py   0  schema_registry.py
0  discovery.py     0  lifecycle.py  0  publication.py  0  selectors.py  0  snapshot.py
0  telemetry.py     0  __main__.py
6  __init__.py     11  cli.py      6  generate_dbt.py   2  manifest.py   7  ownership.py
```

Every file this port touches is **byte-identical modulo the rename**. That turns the port from a
merge into a copy: `git show <upstream>:<path> | sed 's/pg_md_cdc/feather_cdc_postgres/g' > <dest>`
reproduces both the upstream feature and the packaging delta, because the only packaging delta in
those four files *is* the rename (the `[pg_md_cdc]` log prefix is lowercase, so the same `sed`
converts it; the `PG_MD_CDC_*` env namespace is uppercase, so the same `sed` leaves it alone).

The five files with a non-zero delta are not touched by either upstream commit, so their deltas
survive untouched: `--out` required / `default_out_path` deleted (`cli.py`, `generate_dbt.py`),
`manifest.owner_problem` not naming rama_dw's seed CSV, `ownership.KEY_PREFIX == "pg_md_cdc:"`,
and `__init__.py`'s docstring.

## What is ported

### rama_dw `4f537660c` — per-stage timing (its #4714)

| File | Upstream feature (ported) | Packaging delta (kept) |
|---|---|---|
| `timing.py` (new, 134 lines) | `StageClock`, `NO_CLOCK`, `TimedConnection`, `CONNECTIONS` — whole file | `_log` prefix becomes `[feather_cdc_postgres]`; `from control_plane import status` is already the import name feather-control-plane kept |
| `destination.py` | `connect()` returns a `TimedConnection`; `_with_account` times its connection | rename only |
| `drift.py` | `scan(..., clock=NO_CLOCK)` + three `drift_*` stages | rename only |
| `runner.py` | `clock` threaded through `run` → `execute` → `run_relations` → `_drain`; 16 named stages | rename only |
| `stream.py` | `drain(..., note_time=None)` reporting `peek_slot_s` and `read_wal_s` | rename only |

Tests: `tests/test_timing.py` (new, 7 tests) and one e2e in `tests/test_e2e_pg.py` asserting the
exact stage sequence. `tests/test_destination_create_database.py`'s `fake_execute` grows
`clock=None` to match `execute`'s new keyword.

**Not ported:** `4f537660c` also touches
`.claude/skills/daily-loading/scripts/tests/test_system_health.py`. That is rama_dw's own skill,
not the package's.

**Not brought back:** the upstream tests do not touch `conftest.py`, so feather-flow's own
WAL-ordering fix from #89/#91 (`wal_runs` / `finish_record` ordering on recorded `finished_at`
rather than `st_mtime_ns`) is untouched. The package's `test_e2e_pg.py` imports `finish_record`
from `conftest`; upstream still defines it inline with an `st_mtime_ns` sort. The appended test
uses neither.

### rama_dw `765771456` — bulk schema-registry write (its #4717)

| File | Upstream feature (ported) | Packaging delta (kept) |
|---|---|---|
| `destination.py` | `_chunks(items, size=500)`; `write_registry_rows` replaces `executemany` + per-table `delete` with multi-row `insert` and one OR-ed `delete` per 500-pair chunk, in the same transaction | rename only |

Tests: `tests/test_schema_registry_write.py` (new, 6 tests), including the statement counter.

## Proving the performance claim

A local suite cannot reach production MotherDuck, and must not. Three things are built instead,
and the gap is stated rather than papered over.

1. **A behavioural guard that fails against the old code.**
   `test_statements_do_not_grow_with_the_number_of_columns` spies on the duckdb connection,
   counts `execute` calls and asserts exactly 6 for 1,203 rows over 90 tables
   (begin, 1 delete, 3 inserts, commit). Its `Spy.executemany` raises. Proof it is not
   vacuous: run it against the pre-#4717 `write_registry_rows` and watch it fail.

2. **A measurement, on the real code path, against a latency-realistic destination.**
   Production's cost is per-round-trip latency, which a local duckdb file does not have
   (its round trip is ~microseconds). So the benchmark wraps the connection in a shim that
   sleeps a fixed per-statement latency, runs the real `write_registry_rows` at production's
   row count, and reports round-trip counts for both implementations. Round trips, not
   wall-clock on a local file, are what the fix changed.

3. **What is not covered.** The end-to-end ~87 s includes source drain, drift compile, the slot
   probe and reconcile against a real Postgres and real MotherDuck. None of that is reproduced
   locally. Nothing here measures an end-to-end run.

## Verification

- Package suite, `uv run --package feather-cdc-postgres pytest -q` from `packages/feather-cdc-postgres/`.
- feather-flow core suite and `feather-control-plane` suite — unchanged by this work, run to prove it.
- `uv build --package feather-cdc-postgres` and `uv lock --check`.
- `validate` against production's `featherbase.yml` and `featherbase_dev.yml`, copied out of
  rama_dw into a temp dir, run against the built 0.3.0 wheel in an isolated environment. This is
  the check that caught 0.1.0's `from_module` regression; it is not optional.

## Results

Filled in below once run.
