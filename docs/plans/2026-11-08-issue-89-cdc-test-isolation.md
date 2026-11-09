# feather-cdc-postgres — the flaky suite is a WAL-filename ordering bug, not leaked Postgres state

Created: 2026-11-08
Status: VERIFIED
Approved: Pending
Slice: —
Type: Fix
Issue: siraj-samsudeen/feather-flow#89
Review: —

## Why

`packages/feather-cdc-postgres`'s suite fails a different test on different full-suite runs.
Issue #89 suspects shared state in the session-scoped PostgreSQL — a leaked replication slot,
a leftover publication, a shared dlt working directory.

It is none of those. The nondeterminism is in the **test helpers**, and it is a coin flip.

## The real cause

A WAL-only `ControlPlane` writes one file per run:

```
<wal_dir>/<extractor>/<run_id>.jsonl
```

and `control_plane.run.new_run_id` mints

```
<UTC second>-<extractor>-<6 random hex>      e.g. 20261108T142016Z-demo_cdc-5233af
```

Four test modules then ask "what did the **last** run finish as?" by sorting those filenames:

| Site | Code |
|---|---|
| `tests/test_e2e_pg.py:955` (`finish_record`, imported by 4 other modules) | `for path in sorted(pathlib.Path(wal_dir, extractor).glob("*.jsonl"))` … `return records[-1]` |
| `tests/test_runner_cli_pg.py:221` | `for path in sorted((tmp_path / "wal" / "demo_cdc").glob("*.jsonl"))` … `finishes[-1]` |

Two runs inside one test almost always start **in the same UTC second**, so the timestamp
prefix is identical and the sort is decided by `secrets.token_hex(3)` — **a fair coin**.
Half the time the second run's file sorts first, `records[-1]` returns the *first* run's
`run_finish`, and the test reads `ok` where it arranged `failed`/`partial`.

### Evidence

`test_an_empty_compiled_selection_fails_closed_and_records_the_run`, run **alone**, 20 times,
with the WAL directory dumped after each run — 7 failures, and the correlation is total:

```
[WALDBG pass]  20261108T142008Z-demo_cdc-104b01.jsonl  ['run_start=running', 'run_finish=ok']
               20261108T142008Z-demo_cdc-a2dcd4.jsonl  ['run_start=running', 'run_finish=failed']
[WALDBG FAIL]  20261108T142016Z-demo_cdc-5233af.jsonl  ['run_start=running', 'run_finish=failed']
               20261108T142016Z-demo_cdc-edc051.jsonl  ['run_start=running', 'run_finish=ok']
```

Same second in every case. The test passes exactly when the `ok` hex sorts low and fails
exactly when the `failed` hex sorts low. Nothing about the full suite is required — it was
only ever reported as a full-suite failure because nobody looped the test on its own.

### What was ruled out, with evidence

- **Leaked replication slots.** A census after every test (finalised *after* `fresh_db` drops
  its database) across `test_e2e_pg.py`, `test_runner_cli_pg.py` and `test_stream_pg.py`:
  `tests ending with surviving slots: 0`. `max_replication_slots=20` is never approached.
- **Leaked publications / dlt pipeline dirs / `control.run` rows.** Every one of these is
  already unique per test: `fresh_db` is a brand-new database, `names` mints
  `slot`/`publication`/`registration` from a uuid, and both the dlt `pipelines_dir` and the
  `wal_dir` hang off pytest's per-test `tmp_path`. The isolation the issue asks for is
  already there.
- **Collection order.** Unchanged, as #89 says. The varying thing is 3 bytes of randomness
  in a filename.

## The fix

**Order runs by the time they recorded, never by their filename.**

`run_finish` carries `finished_at` — `datetime.now(timezone.utc).isoformat()`, microsecond
resolution. That is the recorded fact; the filename is an inference from it that silently
loses the sub-second part.

1. Move the WAL readers into `tests/conftest.py` (today `finish_record` lives in
   `test_e2e_pg.py` and four sibling modules `from test_e2e_pg import finish_record`) and
   make them sort by `finished_at`:
   - `wal_records(wal_dir, extractor)` — every record, for the one test that also wants events.
   - `wal_run_finishes(wal_dir, extractor)` — the `run_finish` records, chronological.
   - `finish_record(wal_dir, extractor="test_cdc")` — the last of those.
2. **Ties fail loudly.** Two `run_finish` records sharing a `finished_at` raise, naming both
   run ids, rather than picking one. A reintroduced ambiguity is then a hard error on the
   first run, not a 50% flake.
3. Point `test_runner_cli_pg.py:221` at the same helper instead of re-rolling the glob.
4. Guard the thing #89 suspected anyway: `fresh_db`'s teardown asserts the cluster has **no**
   replication slots left once the test's database is dropped. Cheap (it reuses the
   connection already open to drop the database) and it makes a genuine future leak fail on
   the test that caused it.

### Proving the fix fired

`tests/test_wal_reading.py` writes two WAL files by hand whose **filenames sort opposite to
their `finished_at`** — the exact shape the coin flip produces — and asserts `finish_record`
returns the chronologically-last one. It fails against the old filename sort every time, with
no looping.

## Not done here, and why

`new_run_id`'s second-resolution timestamp makes `<run_id>.jsonl` *look* chronologically
sortable when it is not. Widening it to milliseconds would be a real improvement, but:

- it changes a format already live in `rama_dw`'s production `control.run` (and in SAP's
  `run_id[:8]` backup names), which is an outward-facing decision for the owner, not a
  side-effect of a test fix; and
- it would only make the collision **rarer**, not impossible — a test that orders runs by
  name would still be relying on an accident.

The docstring now says so explicitly, so the next reader does not assume sortability.
Recommended as a separate issue.

## Verification

`cd packages/feather-cdc-postgres && uv run --package feather-cdc-postgres pytest -q`, six
consecutive full-suite runs each way.

| | Red runs | Failures | Which tests |
|---|---|---|---|
| Before | **5 of 6** | 7 | `test_a_drift_blocked_table_makes_the_run_partial_with_honest_counts` ×3, `test_an_empty_compiled_selection_fails_closed_and_records_the_run` ×2, `test_a_retired_relation_with_no_recorded_key_is_reported_not_merged_silently` ×2 |
| After | **0 of 6** | 0 | — |

All three are `finish_record`/sorted-glob consumers, and no other test ever failed.

- The three tests together, looped **20×**: 20/20 green (before, the empty-selection one alone
  failed **7 of 20**).
- `tests/test_wal_reading.py`: 5 passed. With `wal_runs` reverted to a filename sort, 3 of the
  5 fail every time — the fix is proved to have fired, deterministically, without looping.
- Counts: 522 → **527** (the 5 new reader tests); **no new skips** (0 skipped locally, before
  and after).
- Replication-slot census after every test, all six "before" runs:
  `tests ending with surviving slots: 0`.
- Siblings unaffected: `feather-control-plane` 48 passed; repo-root suite 1094 passed,
  15 skipped.
