# feather-cdc-postgres 0.2.0 — catch up with rama_dw's runtime

Created: 2026-11-09
Status: VERIFIED
Approved: Yes
Slice: —
Type: Fix
Review: —

## Why

`feather-cdc-postgres` 0.1.0 was cut from rama_dw at `c58825be` (27-Oct-2026). rama_dw's
runtime then moved three days further, and the package did not. The gap is not cosmetic:

```
$ uv run --isolated --with "feather-cdc-postgres==0.1.0" python -m feather_cdc_postgres validate featherbase.yml
[error] unknown key(s) in destination.table_schemas[]: from_module
```

`from_module: true` is in production's `featherbase.yml` and in `featherbase_dev.yml`, so the
published wheel **cannot parse the registration the deployment it replaces is running**. That
makes rama_dw's "pg_md_cdc keeps its registrations and loses its runtime" change (its #4599)
unmergeable until this ships.

## The upstream base, and how the delta was measured

The module was renamed on the way into the package (`pg_md_cdc` → `feather_cdc_postgres`), so
every comparison normalises the rename first:

```sh
diff <(git show "$V:pg_md_cdc/src/pg_md_cdc/runner.py" | sed 's/pg_md_cdc/feather_cdc_postgres/g') \
     packages/feather-cdc-postgres/src/feather_cdc_postgres/runner.py
```

Walking `$V` back through rama_dw's history pins the cut exactly: at `c58825be` **every** source
file is byte-identical to the package except the five that carry a deliberate packaging delta.
That is what makes the rest of this a clean port rather than a merge — the packaging deltas are
a closed, enumerable set, and everything else can be taken wholesale.

Upstream head used: rama_dw `86c89266` (the commit before its #4599 deletes the vendored tree).

## What is ported, and what is deliberately kept

| File | Upstream feature (ported) | Packaging delta (kept) |
|---|---|---|
| `runner.py` | all of it — #4588's `_hold_tables_in_drift_review`, `_refuse_overwriting_held`, `_settle_moved_tables`, the `_Targets` fallback to where a table's rows actually are, `write_baseline_lsns(..., targets)` at all four call sites, `status` printing the landed schema | the two `[feather_cdc_postgres]` log prefixes; the `PG_MD_CDC_*` env namespace (untouched — upstream has no lowercase `pg_md_cdc` in this file beyond the log prefix) |
| `destination.py` | #4588's `baseline.target_schema` column + its `add column if not exists` migration, `read_baseline_targets`, `record_baseline_target`, `write_baseline_lsns`'s `targets` parameter | the `[feather_cdc_postgres]` retry log prefix |
| `manifest.py` | #4588's `module_schema()`, `TableSchema.from_module`, `target_schema(..., module)`, `from_module` accepted by `_reject_unknown`, the `deny_on_module_rule` error, `from_module` folded into `pipeline_name_ambiguous` | `owner_problem`'s text — it must not name rama_dw's seed CSV |
| `selectors.py` | #4588 — `compile` passes the relation's module into `target_schema` | — |
| `cli.py` | nothing | `--out` required, `REPO_ROOT` deleted |
| `generate_dbt.py` | nothing | `default_out_path` deleted, header line not rooted at a warehouse path |
| `ownership.py` | nothing | `KEY_PREFIX = "pg_md_cdc:"` — hashed into a SOURCE-side advisory lock, so both trees must agree during a rollover |
| `__init__.py` | nothing | docstring: registrations are the deployer's |

Attribution note: the task brief credited `from_module` to #4688. The *code* landed in rama_dw
`53886d380` under **#4588**; #4688 is the later commit that adopts the rule in production's
`featherbase.yml`. Both numbers are cited in the commits for that reason.

## Tests

| Upstream test change | Decision |
|---|---|
| `test_module_routing_pg.py` (new, 11 tests) | **ported** — self-contained (its own inline manifest and `featherbase` catalog fixture), so only the import line changes |
| `test_manifest.py` — the "schema from a module (#4588)" section | **ported** verbatim; it drives `_m()` inline manifests, not a shipped registration |
| `test_runner_cli_pg.py` — `test_a_streaming_table_moved_to_a_new_target_brings_its_rows_with_it` | **ported** — it uses the package's own `routed_registration` fixture unchanged |
| `test_e2e_pg.py` / `test_runner_cli_pg.py` — sort WAL files by `st_mtime_ns` | **not ported.** This repo already fixed the same bug, better, in #89/#91: `conftest.wal_runs` orders by the `finished_at` the run recorded and *asserts* no two runs share a stamp. Porting the mtime sort would be a regression. |
| `test_manifest.py` / `test_generate_dbt.py` / `test_featherbase_selection_pg.py` / `test_featherbase_dev_registration.py` — the `*.*` → `featherbase.* + from_module` re-routing | **not ported.** These assert rama_dw's *registration* content (#4662/#4688), which is deployer config. The package's `tests/fixtures/registrations/demo_app.yml` is a fixture, not a deployment; `from_module` is covered end-to-end by `test_module_routing_pg.py`. |
| `test_destination_create_database.py` — adding `featherbase_staging` / `petpooja_webhook` owners | **not ported** — rama_dw's registration inventory. |
| `test_featherbase_staging_registration.py`, `test_petpooja_webhook_registration_pg.py` (new upstream) | **not ported** — both assert rama_dw deployment identity. The package already has a generalized `test_staging_registration.py`. |

## Acceptance

1. `validate` parses production's `featherbase.yml` and `featherbase_dev.yml`, run against the
   **built 0.2.0 wheel** in an isolated environment (not the working tree).
2. Package suite green.
3. feather-flow core and `feather-control-plane` unchanged.
4. `uv build --package feather-cdc-postgres` emits 0.2.0; `uv lock --check` clean.

## Results

- Package suite: **527 → 550 passed**, 0 skipped (+11 `test_module_routing_pg.py`,
  +11 `test_manifest.py` from-module cases, +1 `test_runner_cli_pg.py`).
- feather-flow core: **1094 passed, 15 skipped** — unchanged.
- `feather-control-plane`: **48 passed** — unchanged.
- `validate` against both production registrations, from the wheel: `ok` / exit 0.
