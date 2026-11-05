# feather-cdc-postgres — move the PostgreSQL CDC runtime into this repo as its own package

Created: 2026-11-05
Status: VERIFIED
Approved: Yes
Slice: —
Type: Refactor
Issue: siraj-samsudeen/feather-flow#80 (step 2)
Prior step: [2026-11-03-control-plane-package.md](2026-11-03-control-plane-package.md)
Review: —

## Why

`pg_md_cdc/` in `rama_dw` is a general-purpose thing living in a place that cannot ship it.
Point it at a PostgreSQL database and a registration file and it mirrors that database into
MotherDuck — logical decoding, consistent baselines, merge-or-append key policy, schema
evolution, column deny-lists — with no extractor written for the source. That is a product.
`rama_dw` has no package configuration, so today the only way to use it anywhere else is to
copy the tree, which is the exact failure step 1 existed to end for the control plane.

#80's framing is **lift and shift, not rewrite**. This plan honours it. Three things change
beyond the move, each because publishing would otherwise make a defect permanent, and each
argued below.

## What lands

```
packages/feather-cdc-postgres/
  pyproject.toml              distribution AND import name `feather_cdc_postgres`
  README.md
  src/feather_cdc_postgres/   cli manifest selectors discovery publication snapshot stream
                              runner destination drift lifecycle telemetry schema_registry
                              ownership generate_dbt adapters/
  src/pg_replication/         vendored, Apache-2.0, modified — LICENSE + attribution added
  tests/                      29 files, 522 tests
  tests/fixtures/registrations/   package-owned manifests, written here
```

`packages/*` already matched the root workspace, so no root `pyproject.toml` change was needed
beyond the lock.

## Decisions

### The import name is renamed, unlike step 1's

Step 1 kept `control_plane` because ~40 call sites across nine services already spelled it that
way, which made adoption a dependency substitution with no source edits. That argument does not
transfer: **nothing outside the tree imports `pg_md_cdc`.** The only external references are
twelve `python -m pg_md_cdc …` command strings in `rama_dw` — runbook, Dockerfile, docs — which
change in one edit at adoption time (data-warehouse#4599).

With no adoption cost, the name is judged on its merits and fails:
`docs/conventions/naming-conventions.md` rejects acronyms and house codes, and `pg_md_cdc` is
two of them welded together (`md` for MotherDuck, `cdc` for change data capture). The CLI
follows the module: `python -m feather_cdc_postgres`, plus a `feather-cdc-postgres` console
script.

**Two strings keep the old spelling, with comments in the code saying why.**

- `ownership.KEY_PREFIX = "pg_md_cdc:"` is hashed into a `pg_try_advisory_lock` key on the
  **source** database. Two drainers exclude each other only while they agree on that string, and
  during a cutover the old tree and this one run against one source. It changes when every
  drainer changes.
- The `PG_MD_CDC_*` environment namespace (`_DESTINATION`, `_PIPELINES_DIR`, `_TEST_DSN`,
  `_TEST_PORT`, `_TEST_TOKEN`) is read from a deployed environment. Renaming it is a coordinated
  change with whoever sets those variables. **Follow-up for the adoption change**, not for this
  one.

### It depends on `feather-control-plane`; the usage is not trimmed

This **deviates from the 03-Oct comment on #80**, which asked for the control-plane usage to be
trimmed into a private run-log module so the two packages stay independent. Stated here so it
can be overruled on the record:

- "Lift and shift, not rewrite" is the issue's own headline principle, and trimming is a rewrite
  of working, tested code.
- The usage is not incidental: ~50 `cp_status.*` string constants across `runner.py`,
  `drift.py`, `telemetry.py` and `lifecycle.py`, plus a `ControlPlane` factory with four call
  sites in `runner.py`. It genuinely writes run, event and budget rows. A private module would
  be a second implementation of them, in a second place, to drift.
- What data-warehouse#4078 decoupled was the **database** — the CDC logs to its own CDC log DB
  via `CDC_LOG_PG_DSN` — not the client library. A different DSN satisfies that. A duplicate
  run-log implementation is not required by it.
- Reversible for about four import lines if independence is wanted later.

It is a **workspace** dependency (`[tool.uv.sources] feather-control-plane = { workspace = true }`)
so the two version together in-repo, and it takes the `[postgres]` extra, which is the one thing
step 1 said adopters must act on.

The vendored `src/control_plane/` tree is deleted in the same commit.

#### One real finding from that swap

`rama_dw`'s vendored copy has **diverged ahead** of the published package. Its `control_plane`
carries data-warehouse#4614/#4616 — `new_run_id(extractor)` with a random suffix, and a
`RunIdClash` refusal when a run record meets another extractor's row of the same `run_id` —
which `feather-control-plane` does not. Nothing in this package uses either name, so the swap is
safe here, but **the published control plane is a release behind its own vendored copy** and
porting those two changes is work this step did not do.

### `generate-dbt --out` becomes required

It defaulted to `repo_root / "dbt_runner/dbt/models" / source_id / f"_{source_id}_sources.yml"`
with `repo_root = Path(__file__).resolve().parents[3]`. That is one warehouse's directory layout
compiled into a runtime, and four parents up from an installed wheel is somebody's virtualenv,
not a dbt project. `default_out_path` is deleted; a test asserts the attribute is gone and that
the command exits without the flag. `manifest.owner_problem` also stops naming that repo's seed
file in its error message.

`git grep dbt_runner` inside the package now returns only the comment explaining the removal.

### The registrations do not come across; the tests get fixtures

The five `registrations/*.yml` are instance config: their `cadence_minutes` is **generated** by
`rama_dw`'s `config/schedules.yml`, their `dsn_env` names Railway reference variables that exist
in one account, and their `destination.owner` values come from that warehouse's
`layer_access_registry.csv`. Shipping them would publish a second, unsynchronised copy of
somebody's production config. Same for `Dockerfile` and the four `railway.*.json` files, which
pin `Root Directory: pg_md_cdc`. `uv.lock` goes too — a workspace member does not own a lock.

Nine test files read those YAMLs, so `tests/fixtures/registrations/` is **written here**, not
copied:

| Fixture | Carries |
|---|---|
| `demo_app.yml` | a fictional Featherbase install: discovery adapter, deny tables, column deny-list, per-table target schemas, `replica_identity: full` |
| `demo_app_dev.yml` | the same, `environment: dev`, its own identity keys |
| `demo_app_staging.yml` | the same, `environment: staging` |
| `control_plane.yml` | `source_id: control_plane` is load-bearing — `selectors.table_class` special-cases it so `control.*` classifies `warehouse_control`. Also the only `append_only` key overrides and the only `reconcile_exclude` |

The **deployment** identity in them is this package's (`demo_app`, `DEMO_APP_PG_DSN`,
`bronze_demo_app`, `demo_app_cdc_service_account`, `demo_app_cdc_slot`). The **source-side**
names stay the adapter's (`featherbase.person`, `featherbase.table_def`), because that is what
`adapters/featherbase.py` and the SQL fixtures speak, and the adapter is package code.

Four test files are renamed, because what they test is the runtime's guard rather than one
deployment's file:

| Was | Is |
|---|---|
| `test_featherbase_dev_registration.py` | `test_dev_registration.py` |
| `test_featherbase_staging_registration.py` | `test_staging_registration.py` |
| `test_featherbase_dev_pg.py` | `test_dev_registration_pg.py` |
| `test_featherbase_selection_pg.py` | `test_adapter_selection_pg.py` (the adapter is the subject) |

### One test is dropped, and it is the only one

`tests/test_petpooja_webhook_registration_pg.py` `exec_module`s `petpooja_webhook/store.py` from
the `rama_dw` repo root to get the receiver's DDL and its `POSTGRES_ONLY_COLUMNS` list. Outside
that checkout there is nothing for it to test. It is a test of **that service's** contract with
this runtime and it belongs with the service, in `rama_dw`, where it still lives and still runs.

Nothing else was dropped. 522 of the original 524 tests came across (the two lost are that
file's).

### `pg_replication/` stays vendored — and gains the licence notice #80 asked for

Upstream is a `dlt init` template, not a distribution: there is no `pg_replication` on PyPI to
pin. The copy here is **modified** (column deny-list projection, reviewed-layout quarantine,
mid-transaction rename handling, consistent baseline boundaries, generated-column publication).

It carried **no attribution at all** in `rama_dw`. Internally that is untidy; publishing it that
way is a licence problem. It now ships Apache-2.0's full text as `src/pg_replication/LICENSE`
(section 4(a)) and a header on `src/pg_replication/README.md` naming dlt-hub/verified-sources as
the origin and stating the files are changed (section 4(b)).

## History

Moved with history: `git format-patch --root --relative=pg_md_cdc` in `rama_dw`, then
`git am --directory=packages/feather-cdc-postgres` here. **40 commits**, authors and dates
intact, from the runtime's first appearance to its last change. The resulting tree was verified
byte-identical to the source (`diff -r`, excluding `__pycache__`, `.venv`, `.pytest_cache`)
before anything was changed. Every later change is its own commit on top, so the diff that
turns the Jeyarama tree into a package is readable on its own.

Note for expectations: the same was done in step 1, and `git log -- packages/feather-control-plane`
shows two commits — the PR was squash-merged. The history survives on the branch and is
flattened at merge, the same as it will be here.

## CI

A `feather-cdc-postgres` job, modelled on the control plane's, with two differences the package
forces.

**It needs `wal_level = logical`.** On the default `replica` the source cannot create a
replication slot at all. GitHub service containers take no `command`, so the setting arrives
through `POSTGRES_INITDB_ARGS` — `initdb -c name=value` writes `postgresql.conf` (PostgreSQL 15+)
and the official image forwards that variable. Verified locally against both `postgres:16` and
`postgres:17` before being written into the workflow. `max_replication_slots` and
`max_wal_senders` go to 20; the default 10 is thin when every integration test takes its own
slot.

**It asserts the setting took, before running the suite.** A database-gated suite that cannot
find a database skips and exits 0: 247 of 522 tests vanish and the job is green. The job reads
`show wal_level` out of the container first and fails there, where the message says what is
wrong.

`postgres:17`, not the control plane's 16: the server version is part of what this package
tests, and 17 is what its sources run. Three tests need PostgreSQL 18's
`publish_generated_columns` and skip by name.

`working-directory: packages/feather-cdc-postgres`, not `uv run --package … pytest` from the
root — `--package` picks the environment, not the tests, and from the root pytest collects
feather-flow's 1094.

One assertion was widened on the way: an expired snapshot identifier is `42704 undefined_object`
on PostgreSQL 17+ and `22023 invalid_parameter_value` on 16. The test means "the snapshot did not
survive the exporter" either way; without the widening the suite is red on 16 for no reason.

## Verification

Run on macOS 15 / arm64, Python 3.13, against `postgres:17` in Docker unless stated.

| Check | Command | Result |
|---|---|---|
| tree identical after `git am` | `diff -r --exclude=__pycache__ --exclude=.venv --exclude=.pytest_cache rama_dw/pg_md_cdc packages/feather-cdc-postgres` | no output |
| lock | `uv lock --check` | `Resolved 68 packages`, clean |
| feather-flow core | `uv run pytest -q` | **1094 passed, 15 skipped** |
| feather-control-plane | `cd packages/feather-control-plane && uv run --package feather-control-plane pytest -q` | **44 passed** |
| this package, local `pg_ctl` (PostgreSQL 18) | `cd packages/feather-cdc-postgres && uv run --package feather-cdc-postgres pytest -q` | **522 passed** |
| this package, `postgres:17` | same, with `PG_MD_CDC_TEST_DSN` | **519 passed, 3 skipped** (PostgreSQL 18 only) |
| this package, `postgres:16` | same | **519 passed, 3 skipped** |
| this package, no server at all | `_pg_bin` stubbed to `None` | **275 passed, 247 skipped** — so 247 need a live PostgreSQL |
| build | `uv build --package feather-cdc-postgres` | wheel + sdist |
| wheel contents | `unzip -l` | `feather_cdc_postgres/` + `pg_replication/` (with `LICENSE`), no `tests/` |
| installed wheel, CLI | `uv run --isolated --no-project --with dist/…whl --with ./packages/feather-control-plane[postgres] python -m feather_cdc_postgres --help` | six subcommands listed |
| `--out` is required | `feather-cdc-postgres generate-dbt x.yml` on that install | exit 2, "the following arguments are required: --out" |
| no warehouse path left | `git grep dbt_runner` in the package | one comment explaining the removal |
| workflow | `actionlint` | clean |

**Not verified locally:** the CI job itself. The branch is not pushed, so the
`POSTGRES_INITDB_ARGS` route is proven against a hand-started `postgres:17` container rather
than a GitHub service container, and `actionlint` is a lint, not a run.

## Not done here (step 3+)

- **Adoption in `rama_dw`** (data-warehouse#4599): delete `pg_md_cdc/src/`, depend on
  `feather-cdc-postgres`, repoint the twelve `python -m pg_md_cdc` strings and the Dockerfile's
  Root Directory, and pass `--out` wherever `generate-dbt` is called.
- **The `PG_MD_CDC_*` environment namespace**, which should move with that change, not before it.
- **Porting data-warehouse#4614/#4616 into `feather-control-plane`**, which is a release behind
  the copy `rama_dw` runs.
- This repo was not pushed and no PR was opened.
