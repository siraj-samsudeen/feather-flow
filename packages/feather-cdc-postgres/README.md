# `pg_md_cdc` — declarative PostgreSQL → MotherDuck CDC runtime (#3079)

One code artifact. One **registration** (`registrations/<source_id>.yml`) per source database,
one Railway service instance per registration. A reviewed declaration is sufficient to mirror a
PostgreSQL database into MotherDuck at bronze fidelity — no per-source extractor.

Plan: `docs/plans/issue_3079_pg_md_cdc_runtime.md`. It generalizes the #2851 Featherbase pilot
(`featherbase_extract/`) — cut over to this runtime on 05-Sep-2026 and deleted from the repo
the same day (#3156).
**Operator runbook (activation, status, pause, slot-fuse recovery, resnapshot, declaration
changes, rollback, decommission, the Featherbase cutover and control-plane activation
checklists): `docs/agents/deployment.md`'s `pg-md-cdc` row + `docs/agents/pg-md-cdc-runbook.md`.**
This README covers the code; the runbook covers running it.

## Deploying (one Railway service instance per registration)

One image serves every registration — the instance is selected entirely by env, Root Directory
is always `pg_md_cdc`. Env vars, matching `registrations/*.yml`:

| var | set to | source |
|---|---|---|
| `CDC_REGISTRATION` | the registration id, e.g. `featherbase` or `control_plane` | selects `registrations/<id>.yml` |
| `<manifest source.dsn_env>` | the source Postgres DSN (`FEATHERBASE_PG_DSN` / `CONTROL_PG_DSN`) | Railway reference var |
| `<manifest destination.token_env>` | the MotherDuck token (`MOTHERDUCK_TOKEN` in both shipped manifests) | Railway reference var |
| `CDC_LOG_PG_DSN` | the customer's **CDC log DB** (Railway `<customer>-cdc-log-db` in project `<customer>-cdc`, e.g. `jeyarama-cdc-log-db`) — where its runs and events are logged (#4078) | Railway reference var — hard dependency unless `--dev`. `CONTROL_PG_DSN` is accepted for one release; for the control-plane CDC it is also the source, so set `CDC_LOG_PG_DSN` |
| `CONTROL_WAL_DIR` | a mounted (non-ephemeral) path | only needed if `control.event_durability: persistent_local_wal`; neither shipped manifest uses it |

Cron: `*/5 * * * *`, set on the **service instance** (`railway api` `serviceInstanceUpdate`, or
the dashboard) — `railway.json` is NOT honoured on these instances and must not be cited as the
schedule (`docs/agents/gotchas/railway-status-stale-cron-schedule.md`). Details, health-check queries and the destructive-command
confirmations live in the runbook, not here.

## Commands

```bash
python -m pg_md_cdc validate     registrations/featherbase.yml   # static + read-only checks
python -m pg_md_cdc plan         registrations/featherbase.yml   # every change a run would make
python -m pg_md_cdc generate-dbt registrations/featherbase.yml [--out PATH]
python -m pg_md_cdc run|status|resnapshot registrations/featherbase.yml
```

`validate` and `plan` **mutate nothing** — they open read-only connections and print. `plan` is
safe to run against production; `tests/test_cli_plan_pg.py` fingerprints the source catalog
before and after to keep that claim true. `--json` on any command gives machine output.
`run`/`status`/`resnapshot` apply the same static validation first and refuse — before opening
any connection — on an error-severity problem (an unknown `api_version`, say).

Exit codes: `0` clean, `1` at least one error-severity problem or an unexpected failure (printed
redacted, never as a raw traceback), `2` a malformed manifest.

## What a registration declares

| Block | Decides |
|---|---|
| `source` | which relations: `include`/`exclude`/`deny` globs on `schema.table`, plus an optional catalog `discovery.adapter` |
| `destination` | MotherDuck database, `schema_mapping` (default: source schemas preserved), **`table_schemas`** (per-table target schema, `[{match: "schema.table" glob, schema: target}]`, first match wins, unmatched → `schema_mapping`; D-13), dlt pipeline name (only with a single target schema), retirement policy |
| `replication` | slot, publication, cadence, key policy for unkeyed tables, replica identity, batch size |
| `control` | the extractor identity, control-plane availability policy, event durability, `reconcile_exclude` |
| `classification` | optional first-match rules overriding the default `table_class` |

Secrets are **references**: `dsn_env` and `token_env` name environment variables. A manifest
carrying a value is rejected, and no environment value is ever printed or written into an event.

## Selection semantics

- A relation is `(schema, table)`, always together — two schemas may hold the same table name.
- Patterns are `fnmatch` globs over the qualified name (`public._dlt_*`). `include` is a
  conjunction (schema **and**, when given, table pattern); `exclude` and `deny` are disjunctions.
- **`deny` is evaluated on the compiled set**, after the adapter narrows it — a deny rule is a
  tripwire that fires only when something really would have been replicated.
- **`deny.columns`** (`{"schema.table": [column, ...]}`, exact names, #4346) withholds columns:
  they are left out of the baseline select list, dropped by name from every decoded record and
  from dlt's hints, and kept out of the drift signature. The deny follows the table's OID and
  the column's attnum, so a table or column rename cannot release it. Every decoded description
  of a table that carries a deny must match, exactly, the column layout its baseline recorded;
  any column DDL there (an added column included) quarantines the table until a resnapshot
  (runbook §f2). Every run checks the destination (and each `_staging` twin) and
  reports a denied column found there as an error. It is a projection *inside the CDC process*,
  not a publication column list: under `replica_identity: full` that list must name every
  column, or every UPDATE/DELETE of the table fails at the source.
- A discovery adapter supplies policy only ("which relations, and what does the source call
  them"). It never supplies types, keys or load behaviour. The Featherbase adapter's
  `options.system_tables` switches on the **two-way rule**: a `table_def."system" = true` table
  (Featherbase's own) is offered only when named there, a `"system" = false` (user-created)
  table always is — so a new user table flows with no manifest edit. `plan` prints each
  relation's `origin=` (`user_created` / `named_system`); user-created relations classify as
  `application` whatever they are called. Absent, the adapter offers everything (legacy).
- One dlt pipeline per **target** schema (`<source_id>_cdc__<target_schema>`), never per
  source schema: `_cdc_runtime.*`, lifecycle and `control.event` entities keep the **source**
  identity `schema.table`; the destination side is the relation's compiled `target_schema`.
- `selection_hash` covers the relation set **and** each table's key policy, so a table that
  silently dropped from merge to append-only changes the hash.

## Key policy (D6)

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
whose consistent point PG guarantees matches its snapshot ("in the snapshot" ⟺ "commit LSN <
boundary", with in-flight transactions waited out), stores that boundary per relation in
`_cdc_runtime.baseline`, and the drain drops that relation's transactions below it. That is what
makes the overlap exact for `append_only`, where no key could dedup it.

**`TRUNCATE`** is published and applied: a keyed mirror is emptied before the rows that followed
the truncate load (batch order preserved); an `append_only` history keeps every arrival. Both
emit a `truncate` control event. **Partitioned tables** replicate as their partitions (`relkind
'r'`); the parent is a named `partitioned_parent_skipped` warning, never a relation.

**Run outcome**: `control.run.status` is `ok` only when every selected relation drained with no
error-class event; `partial` when a table is drift-blocked or a reconcile count did not tie;
`failed` when nothing drained (slot lost under `slot_loss_policy: pause`, or a selection that
compiled to nothing — which `run` refuses rather than leaving an undrained slot behind).

`control.reconcile_exclude` names `schema.table` globs the reconcile counts skip — for relations
this drainer writes while it runs (`control.run`, `control.event`, `control.budget_usage`), which
can never tie source-now against destination-now. `append_only` relations are skipped too
(`extra.append_only`): their destination keeps every row version, so a count can never tie once
the source is edited. Both still emit a `reconcile` event with `skipped` and a reason, so the gap
is visible rather than silently passed.

Merging on a unique index needs the source to publish those columns on DELETE — set
`replica_identity: full` or `ALTER TABLE ... REPLICA IDENTITY USING INDEX`. The compiler warns
when neither is in place.

## Type matrix

`schema_registry.compile_column` maps every OID in use on the two live sources exactly, and
labels how faithful the mapping is: `exact`, `widened`, `exact_as_text` (uuid, name),
`lossy_text` (interval, timetz), `text_fallback`. Arrays and undeclared OIDs land as text and
**always** raise a warning naming `schema.table.column` and the OID — never silently.

The integer family (`int2/int4/int8/oid`) compiles to plain `BIGINT`. dlt renders
`{"data_type": "bigint", "precision": 32}` as `INTEGER` on duckdb while the snapshot path
reflects every integer as `BIGINT`; `bronze_featherbase.public.comment.position` carries both
spellings today. Dropping the precision hint pins one, in the widening direction.

## Tests

```bash
cd pg_md_cdc && .venv/bin/pytest tests -q
```

Integration tests start a throwaway Postgres 17 (`wal_level=logical`) and skip when no binary is
available. `PG_MD_CDC_TEST_PORT` picks the port (default 54331) so parallel sessions do not
collide; `PG_MD_CDC_TEST_DSN` points at a Postgres you own instead — never a production one, the
suite creates and drops slots, publications, schemas and tables.
