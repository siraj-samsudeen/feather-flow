# feather-cdc-postgres

Mirror a PostgreSQL database into MotherDuck from one declaration file, with no extractor
written for it.

Point it at a source database and a destination, say which relations you want, and it does the
rest: creates the replication slot and publication, takes a consistent baseline of every
selected table, then drains the write-ahead log on a schedule. A table with a primary key is
merged; a table with none is kept as an append-only history. Schema changes, renames and
`TRUNCATE` are followed. Columns you name are withheld before they ever leave the process.

One code artifact, one **registration** per source database, one service instance per
registration. Adding a source is a reviewed YAML file, not a new codebase.

## Install

```bash
pip install feather-cdc-postgres
```

## Commands

```bash
python -m feather_cdc_postgres validate     <registration>.yml   # static + read-only checks
python -m feather_cdc_postgres plan         <registration>.yml   # every change a run would make
python -m feather_cdc_postgres generate-dbt <registration>.yml --out PATH
python -m feather_cdc_postgres run|status|resnapshot <registration>.yml
```

`feather-cdc-postgres <command> ...` is installed as a console script and does the same thing.

`validate` and `plan` **mutate nothing** — they open read-only connections and print.
`plan` is safe to run against production; `tests/test_cli_plan_pg.py` fingerprints the source
catalog before and after to keep that claim true. `--json` on any command gives machine output.
`run`/`status`/`resnapshot` apply the same static validation first and refuse — before opening
any connection — on an error-severity problem (an unknown `api_version`, say).

`generate-dbt --out` is **required**: the runtime has no opinion about where your dbt project
lives.

Exit codes: `0` clean, `1` at least one error-severity problem or an unexpected failure (printed
redacted, never as a raw traceback), `2` a malformed manifest.

## Running one instance

Registrations are **yours**, not this package's: none ship here. Each instance is selected
entirely by environment:

| var | set to |
|---|---|
| `<manifest source.dsn_env>` | the source PostgreSQL DSN |
| `<manifest destination.token_env>` | the MotherDuck token |
| `CDC_LOG_PG_DSN` | the PostgreSQL database this mirror's runs and events are logged to. A hard dependency unless `--dev`; `CONTROL_PG_DSN` is accepted as a fallback |
| `CONTROL_WAL_DIR` | a mounted (non-ephemeral) path — only needed when `control.event_durability: persistent_local_wal` |
| `RAILWAY_ENVIRONMENT_NAME` | where set, must equal the registration's `environment`, or `run`/`resnapshot` exit before any connection |

The source needs `wal_level = logical` and a role with `REPLICATION`.

## What a registration declares

| Block | Decides |
|---|---|
| `environment` | `production` (default), `staging` or `dev` |
| `source` | which relations: `include`/`exclude`/`deny` globs on `schema.table`, plus an optional catalog `discovery.adapter` |
| `destination` | MotherDuck database, **`owner`** (required: the MotherDuck account the token must sign in as; `run`/`resnapshot` check `current_user()` against it before creating the database on a first run or writing anything), `schema_mapping` (default: source schemas preserved), **`table_schemas`** (per-table target schema, `[{match: "schema.table" glob, schema: target}]`, first match wins; add `from_module: true` to name the schema after the relation's catalog module instead, with `schema` as the fallback), dlt pipeline name (only with a single target schema), retirement policy |
| `replication` | slot, publication, cadence, key policy for unkeyed tables, replica identity, batch size |
| `control` | the extractor identity, run-log availability policy, event durability, `reconcile_exclude` |
| `classification` | optional first-match rules overriding the default `table_class` |

Secrets are **references**: `dsn_env` and `token_env` name environment variables. A manifest
carrying a value is rejected, and no environment value is ever printed or written into an event.

`tests/fixtures/registrations/` holds four worked examples.

## Selection semantics

- A relation is `(schema, table)`, always together — two schemas may hold the same table name.
- Patterns are `fnmatch` globs over the qualified name (`public._dlt_*`). `include` is a
  conjunction (schema **and**, when given, table pattern); `exclude` and `deny` are disjunctions.
- **`deny` is evaluated on the compiled set**, after the adapter narrows it — a deny rule is a
  tripwire that fires only when something really would have been replicated.
- **`deny.columns`** (`{"schema.table": [column, ...]}`, exact names) withholds columns:
  they are left out of the baseline select list, dropped by name from every decoded record and
  from dlt's hints, and kept out of the drift signature. The deny follows the table's OID and
  the column's attnum, so a table or column rename cannot release it. Every decoded description
  of a table that carries a deny must match, exactly, the column layout its baseline recorded;
  any column DDL there (an added column included) quarantines the table until a resnapshot.
  Every run checks the destination (and each `_staging` twin) and reports a denied column found
  there as an error. It is a projection *inside the CDC process*, not a publication column list:
  under `replica_identity: full` that list must name every column, or every UPDATE/DELETE of the
  table fails at the source.
- A discovery adapter supplies policy only ("which relations, and what does the source call
  them"). It never supplies types, keys or load behaviour. The Featherbase adapter's
  `options.system_tables` switches on the **two-way rule**: a `table_def."system" = true` table
  (the platform's own) is offered only when named there, a `"system" = false` (user-created)
  table always is — so a new user table flows with no manifest edit. `plan` prints each
  relation's `origin=` (`user_created` / `named_system`); user-created relations classify as
  `application` whatever they are called. Absent, the adapter offers everything.
- One dlt pipeline per **target** schema (`<source_id>_cdc__<target_schema>`), never per
  source schema: `_cdc_runtime.*`, lifecycle and control events keep the **source**
  identity `schema.table`; the destination side is the relation's compiled `target_schema`.
  A table whose target changes — a `table_schemas` edit, or a `from_module` rule following a
  module rename — is **re-baselined in the new schema** on the next run, so its existing rows
  move with it; the copy it left is frozen, like any retired mirror. A table waiting for a
  resnapshot (`drift_review`, `resnapshot_required`) stays where its rows are until the operator
  resnapshots it, and nothing else may land on that held copy.
- `selection_hash` covers the relation set **and** each table's key policy, so a table that
  silently dropped from merge to append-only changes the hash.

## Key policy

Primary key → merge. Otherwise a **NOT NULL** non-partial, non-expression unique index → merge on
its **key** columns (`INCLUDE` columns carry no uniqueness and are never part of the key;
`NULL = NULL` is never true, so a nullable key would insert a duplicate on every load).
Otherwise `replication.tables_without_key`: `reject` (default, an error naming the table) or
`append_only`. `key_overrides` can force `append_only` per table. `full_row` is **rejected**
(`full_row_unsupported`): an all-column merge key cannot mirror an UPDATE — pgoutput emits only
the new tuple, so the old row is never matched and both versions would stay.

`append_only` is a load contract, not "no key": on the CDC path the relation **merges on `lsn`**
(unique per WAL record), so a batch replayed after a crash lands exactly once. An UPDATE lands as
an additional row version and a DELETE record removes nothing — updates and deletes are recorded
as arrivals, not applied. A re-baseline (resnapshot, a resumed interrupted snapshot, a slot-loss
re-init) therefore REPLACES the destination copy: a snapshot row has no `lsn` to dedup on.

**Every baseline has a boundary.** The initial snapshot reads the slot's exported snapshot, so
nothing below its consistent point is ever streamed. Every later baseline — a table added
mid-life, a resumed snapshot, a resnapshot — reads a table the publication already carries, so
it overlaps the WAL; `snapshot.consistent_baseline` takes it from a throwaway slot `<slot>_bl`
whose consistent point PostgreSQL guarantees matches its snapshot ("in the snapshot" ⟺ "commit
LSN < boundary", with in-flight transactions waited out), stores that boundary per relation in
`_cdc_runtime.baseline`, and the drain drops that relation's transactions below it. That is what
makes the overlap exact for `append_only`, where no key could dedup it.

**`TRUNCATE`** is published and applied: a keyed mirror is emptied before the rows that followed
the truncate load (batch order preserved); an `append_only` history keeps every arrival. Both
emit a `truncate` control event. **Partitioned tables** replicate as their partitions (`relkind
'r'`); the parent is a named `partitioned_parent_skipped` warning, never a relation.

**Run outcome**: the run's status is `ok` only when every selected relation drained with no
error-class event; `partial` when a table is drift-blocked or a reconcile count did not tie;
`failed` when nothing drained (slot lost under `slot_loss_policy: pause`, or a selection that
compiled to nothing — which `run` refuses rather than leaving an undrained slot behind).

`control.reconcile_exclude` names `schema.table` globs the reconcile counts skip — for relations
written while this mirror runs, which can never tie source-now against destination-now.
`append_only` relations are skipped too (`extra.append_only`): their destination keeps every row
version, so a count can never tie once the source is edited. Both still emit a `reconcile` event
with `skipped` and a reason, so the gap is visible rather than silently passed.

Merging on a unique index needs the source to publish those columns on DELETE — set
`replica_identity: full` or `ALTER TABLE ... REPLICA IDENTITY USING INDEX`. The compiler warns
when neither is in place.

## Type matrix

`schema_registry.compile_column` maps every OID in use on the sources it has met exactly, and
labels how faithful the mapping is: `exact`, `widened`, `exact_as_text` (uuid, name),
`lossy_text` (interval, timetz), `text_fallback`. Arrays and undeclared OIDs land as text and
**always** raise a warning naming `schema.table.column` and the OID — never silently.

The integer family (`int2/int4/int8/oid`) compiles to plain `BIGINT`. dlt renders
`{"data_type": "bigint", "precision": 32}` as `INTEGER` on duckdb while the snapshot path
reflects every integer as `BIGINT`, so dropping the precision hint pins one spelling, in the
widening direction.

## The run log

Runs, per-relation events and budget telemetry go to
[`feather-control-plane`](../feather-control-plane), a hard dependency of this package, writing
to whatever `CDC_LOG_PG_DSN` points at — deliberately **not** the database being mirrored. Pass
`--dev` for a local, WAL-only run that writes no run rows at all.

## Vendored code

`src/pg_replication/` is the `pg_replication` verified source from
[dlt-hub/verified-sources](https://github.com/dlt-hub/verified-sources), Apache-2.0, **modified**.
Upstream is a `dlt init` template rather than a distribution, so there is nothing on PyPI to pin.
See `src/pg_replication/README.md` for the attribution and `src/pg_replication/LICENSE` for the
licence text.

## Tests

```bash
cd packages/feather-cdc-postgres && uv run --package feather-cdc-postgres pytest -q
```

`--package` picks the *environment*; it does not pick the tests. pytest takes its config from the
nearest `pyproject.toml` above the working directory, so a bare `pytest` run from the repo root
collects feather-flow's own suite no matter what `--package` says. Either `cd` in or name the
path.

522 tests. 275 are pure logic and run anywhere; **247 need a real PostgreSQL with
`wal_level = logical`** and are skipped without one — they create and drop replication slots,
publications, schemas and tables, so never point them at a database you care about. The suite
finds a server in this order:

1. `PG_MD_CDC_TEST_DSN` — a superuser DSN for a server you own.
2. a local PostgreSQL binary (`pg_ctl` on `PATH`, else Homebrew's keg-only `postgresql@N`),
   `initdb`'d into a temp directory on a throwaway port and torn down afterwards.
   `PG_MD_CDC_TEST_PORT` picks the port (default 54331) so parallel sessions do not collide.
3. otherwise skip.

A container works for (1):

```bash
docker run -d -p 55432:5432 -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=cdc_test \
  -e POSTGRES_INITDB_ARGS="-c wal_level=logical -c max_replication_slots=20 -c max_wal_senders=20" \
  postgres:17
PG_MD_CDC_TEST_DSN=postgresql://postgres:postgres@127.0.0.1:55432/cdc_test uv run pytest -q
```

Three tests additionally need PostgreSQL 18 (`publish_generated_columns`) and skip below it.

The destination in tests is dlt's `duckdb` destination (a temp file) — the same load path
MotherDuck takes minus credentials, so merge, hard-delete and schema-evolution behaviour is
exercised for real.
