"""MotherDuck / duckdb load contract + runtime metadata (#3079 Deviation D-2).

One dlt pipeline PER TARGET SCHEMA (= one dlt dataset; a relation's target comes from
`destination.table_schemas`, then `schema_mapping`), so two source schemas holding the same
table name never collide and one source schema can fan out into several bronze schemas (D-13). Checkpoint, lifecycle and schema registry
live in `<database>._cdc_runtime` written through a PLAIN duckdb connection — no DDL on the
shared control plane (#3091 deadlock) and no dependence on opaque dlt state.
duckdb and MotherDuck differ only by the credentials string.
"""
from __future__ import annotations

import datetime as _dt
import os
from dataclasses import dataclass
from typing import Iterable, Sequence

import dlt
import duckdb
from dlt.destinations.impl.motherduck.configuration import MotherDuckCredentials

RUNTIME_SCHEMA = "_cdc_runtime"


def q(identifier: str) -> str:
    """One duckdb identifier, quoted. An embedded double quote is doubled, never dropped."""
    return '"' + str(identifier).replace('"', '""') + '"'


@dataclass
class DestinationSpec:
    kind: str                      # "duckdb" | "motherduck"
    database: str                  # MotherDuck database name, or the logical name for duckdb
    token_env: str | None = None   # motherduck: env var NAME holding the token (never the value)
    path: str | None = None        # duckdb: file path
    pipelines_dir: str | None = None


class Destination:
    def __init__(self, spec: DestinationSpec) -> None:
        self.spec = spec
        if spec.kind not in ("duckdb", "motherduck"):
            raise ValueError(f"unsupported destination kind {spec.kind!r}")
        self._ensured = False

    # --- credentials ---
    def _token(self) -> str:
        token = os.environ.get(self.spec.token_env or "")
        if not token:
            raise ValueError(f"env var {self.spec.token_env!r} is not set (no token value is logged)")
        return token

    def credentials(self):
        """duckdb: the file path. motherduck: a typed `MotherDuckCredentials` whose `str()`
        masks the password — NEVER `md:<db>?motherduck_token=<token>`, because duckdb echoes a
        connection string back in its exception text and Railway logs it (#3079 review)."""
        if self.spec.kind == "duckdb":
            if not self.spec.path:
                raise ValueError("duckdb destination needs `path`")
            return self.spec.path
        creds = MotherDuckCredentials()
        creds.database = self.spec.database
        creds.password = self._token()
        return creds

    def _dlt_destination(self):
        if self.spec.kind == "duckdb":
            return dlt.destinations.duckdb(credentials=self.credentials())
        return dlt.destinations.motherduck(credentials=self.credentials())

    def connect(self) -> duckdb.DuckDBPyConnection:
        if self.spec.kind == "duckdb":
            return duckdb.connect(self.credentials())
        # config= keeps the token out of the connection string duckdb quotes in errors.
        return duckdb.connect(f"md:{self.spec.database}",
                              config={"motherduck_token": self._token()})

    # --- pipelines ---
    def pipeline_for_target(self, target_schema: str, pipeline_name: str | None = None, *,
                            source_id: str = "pg") -> dlt.Pipeline:
        """The pipeline whose dataset is `target_schema`; named after it unless pinned."""
        name = pipeline_name or f"{source_id}_cdc__{target_schema}"
        kwargs = {}
        if self.spec.pipelines_dir:
            kwargs["pipelines_dir"] = self.spec.pipelines_dir
        return dlt.pipeline(pipeline_name=name, destination=self._dlt_destination(),
                            dataset_name=target_schema, **kwargs)

    @staticmethod
    def table_of(item) -> str | None:
        """The table a marked item is bound for (TableNameMeta / HintsMeta), else None."""
        from dlt.extract.hints import HintsMeta
        from dlt.extract.items import DataItemWithMeta, TableNameMeta
        if isinstance(item, DataItemWithMeta):
            if isinstance(item.meta, TableNameMeta):
                return item.meta.table_name
            if isinstance(item.meta, HintsMeta):
                return item.meta.hints.get("table_name")
        return None

    @staticmethod
    def rounds(items) -> list[list]:
        """Split one batch into load ROUNDS so no dlt resource carries two descriptions of a
        table (#4345). A table renamed or altered mid-batch arrives as `hints, rows, hints,
        rows`; in one resource the LAST hints win for every row, so a column dropped and re-
        added with another type pushed the earlier rows into a variant column. Round k holds
        the k-th description group of every table, in WAL order — the per-description loads
        the stream made before it learnt to decode through a Relation message. A batch with
        one group per table is one round, as before."""
        from dlt.extract.hints import HintsMeta
        from dlt.extract.items import DataItemWithMeta
        groups: dict[str, list[list]] = {}
        for item in items:
            table = Destination.table_of(item)
            segments = groups.setdefault(table, [])
            starts = (isinstance(item, DataItemWithMeta) and isinstance(item.meta, HintsMeta))
            if not segments or (starts and segments[-1]):
                segments.append([])
            segments[-1].append(item)
        depth = max((len(s) for s in groups.values()), default=0)
        return [[item for segments in groups.values() if k < len(segments)
                 for item in segments[k]] for k in range(depth)]

    @staticmethod
    def finish_pending(pipeline: dlt.Pipeline) -> bool:
        """Load what a crashed run left extracted or normalized but unloaded. dlt's `run`
        loads such a PENDING package and returns WITHOUT extracting the data it was handed
        (#4345 Codex adversarial), so a replay must finish it first — before re-applying the
        batch's truncates, and before loading any round."""
        if not pipeline.has_pending_data:
            return False
        pipeline.run()
        return True

    @staticmethod
    def load(pipeline: dlt.Pipeline, items, *, resource_name: str = "cdc"):
        """Run one batch through a pipeline as ONE dlt resource PER TABLE, named after the
        table. dlt records the resource that last wrote a table in the schema's `resource`
        attribute, and `refresh="drop_resources"` finds tables by that attribute: a CDC batch
        loaded under a resource named after the slot flipped it, and every later re-baseline
        of that table (slot loss, re-widening) then dropped NOTHING and doubled an append_only
        copy (#3079 final review). `resource_name` only names items carrying no table mark.
        The caller checkpoints only after this returns."""
        by_table: dict[str, list] = {}
        for item in items:
            by_table.setdefault(Destination.table_of(item) or resource_name, []).append(item)
        Destination.finish_pending(pipeline)   # never let `run` skip the items handed to it
        return pipeline.run([dlt.resource(v, name=k) for k, v in by_table.items()])

    @staticmethod
    def row_counts(pipeline: dlt.Pipeline) -> dict[str, int]:
        try:
            trace = pipeline.last_trace
            counts = trace.last_normalize_info.row_counts if trace and trace.last_normalize_info else {}
            return {t: n for t, n in counts.items() if not t.startswith("_dlt")}
        except Exception:  # noqa: BLE001 — telemetry must never fail the drain
            return {}

    # --- runtime metadata ---
    def ensure_runtime_tables(self) -> None:
        if self._ensured:
            return
        with self.connect() as con:
            con.execute(f'create schema if not exists "{RUNTIME_SCHEMA}"')
            con.execute(f'''create table if not exists "{RUNTIME_SCHEMA}".checkpoint (
                registration varchar, slot varchar, last_commit_lsn bigint,
                load_ids varchar, committed_at timestamptz)''')
            con.execute(f'''create table if not exists "{RUNTIME_SCHEMA}".lifecycle (
                registration varchar, "schema" varchar, "table" varchar,
                state varchar, since timestamptz, detail varchar)''')
            con.execute(f'''create table if not exists "{RUNTIME_SCHEMA}".schema_registry (
                registration varchar, "schema" varchar, "table" varchar, "column" varchar,
                type_oid bigint, typmod bigint, not_null boolean, key_role varchar,
                dest_type varchar, mapping varchar, seen_at timestamptz)''')
            con.execute(f'''create table if not exists "{RUNTIME_SCHEMA}".signature (
                registration varchar, "schema" varchar, "table" varchar,
                signature_json varchar, seen_at timestamptz)''')
            # The consistent point each relation's CURRENT baseline was taken at: the drain
            # drops that relation's transactions with commit LSN below it (snapshot.py).
            con.execute(f'''create table if not exists "{RUNTIME_SCHEMA}".baseline (
                registration varchar, "schema" varchar, "table" varchar,
                boundary_lsn bigint, taken_at timestamptz)''')
        self._ensured = True

    def _read_metadata_with_retry(self, read_fn, *, max_wait_s: float = 40.0,
                                  backoff_s: float = 2.0):
        """Retry a `_cdc_runtime` metadata READ against MotherDuck's catalog lag
        (docs/agents/gotchas/motherduck-catalog-lags-a-write.md): a table created on one
        connection can be invisible to a fresh connection for ~25s. Retries ONLY a
        `duckdb.CatalogException` whose message names `_cdc_runtime` or "does not exist" —
        every other exception (e.g. `duckdb.BinderException`) propagates immediately, and
        WRITES never call this. Logs one line per retry."""
        import time
        deadline = time.monotonic() + max_wait_s
        attempt = 0
        while True:
            try:
                return read_fn()
            except duckdb.CatalogException as exc:
                msg = str(exc)
                if RUNTIME_SCHEMA not in msg and "does not exist" not in msg.lower():
                    raise
                if time.monotonic() >= deadline:
                    raise
                attempt += 1
                print(f"[pg_md_cdc] _cdc_runtime metadata read retry {attempt} "
                      f"(motherduck-catalog-lags-a-write): {msg}")
                time.sleep(backoff_s)

    def read_checkpoint(self, registration: str) -> tuple[int, str | None] | None:
        self.ensure_runtime_tables()

        def _read():
            with self.connect() as con:
                return con.execute(
                    f'select last_commit_lsn, load_ids from "{RUNTIME_SCHEMA}".checkpoint '
                    "where registration = ?", [registration]).fetchone()
        row = self._read_metadata_with_retry(_read)
        return (int(row[0]), row[1]) if row and row[0] is not None else None

    def write_checkpoint(self, registration: str, slot: str, lsn: int,
                         load_ids: Iterable[str] | None = None) -> None:
        self.ensure_runtime_tables()
        ids = ",".join(load_ids or [])
        with self.connect() as con:
            con.execute(f'delete from "{RUNTIME_SCHEMA}".checkpoint where registration = ?',
                        [registration])
            con.execute(f'insert into "{RUNTIME_SCHEMA}".checkpoint values (?, ?, ?, ?, ?)',
                        [registration, slot, int(lsn), ids, _dt.datetime.now(_dt.timezone.utc)])

    def write_baseline_lsns(self, registration: str, boundaries: dict) -> None:
        """{(schema, table): boundary_lsn} — one row per relation, replaced in place, one
        transaction (a MotherDuck round trip per relation would make a 50-table init slow)."""
        if not boundaries:
            return
        self.ensure_runtime_tables()
        now = _dt.datetime.now(_dt.timezone.utc)
        with self.connect() as con:
            con.execute("begin transaction")
            try:
                for (schema, table), lsn in sorted(boundaries.items()):
                    con.execute(f'delete from "{RUNTIME_SCHEMA}".baseline '
                                'where registration = ? and "schema" = ? and "table" = ?',
                                [registration, schema, table])
                    con.execute(f'insert into "{RUNTIME_SCHEMA}".baseline values (?, ?, ?, ?, ?)',
                                [registration, schema, table, int(lsn), now])
                con.execute("commit")
            except Exception:
                con.execute("rollback")
                raise

    def read_baseline_lsns(self, registration: str) -> dict[tuple[str, str], int]:
        self.ensure_runtime_tables()

        def _read():
            with self.connect() as con:
                return con.execute(
                    f'select "schema", "table", boundary_lsn from "{RUNTIME_SCHEMA}".baseline '
                    "where registration = ?", [registration]).fetchall()
        rows = self._read_metadata_with_retry(_read)
        return {(r[0], r[1]): int(r[2]) for r in rows if r[2] is not None}

    def write_lifecycle(self, registration: str, schema: str, table: str, state: str,
                        detail: str | None = None) -> None:
        self.ensure_runtime_tables()
        with self.connect() as con:
            con.execute(f'delete from "{RUNTIME_SCHEMA}".lifecycle '
                        'where registration = ? and "schema" = ? and "table" = ?',
                        [registration, schema, table])
            con.execute(f'insert into "{RUNTIME_SCHEMA}".lifecycle values (?, ?, ?, ?, ?, ?)',
                        [registration, schema, table, state,
                         _dt.datetime.now(_dt.timezone.utc), detail])

    def read_lifecycle(self, registration: str) -> dict[tuple[str, str], str]:
        self.ensure_runtime_tables()

        def _read():
            with self.connect() as con:
                return con.execute(
                    f'select "schema", "table", state from "{RUNTIME_SCHEMA}".lifecycle '
                    "where registration = ?", [registration]).fetchall()
        rows = self._read_metadata_with_retry(_read)
        return {(r[0], r[1]): r[2] for r in rows}

    def write_registry_rows(self, rows: Sequence[dict]) -> int:
        """Called by schema_registry.py. Keys: registration, schema, table, column, type_oid,
        typmod, not_null, key_role, dest_type, mapping (seen_at is stamped here).

        REPLACES each named relation's rows — the registry is current state, like `lifecycle`
        and `signature` beside it; the history of what CHANGED lives in `control.event`. Appending
        instead would add one row per column per tick: ~500 rows every 5 minutes for Featherbase,
        50M a year, in a table nothing prunes. One transaction, so a MotherDuck load pays one
        round trip rather than one per column."""
        if not rows:
            return 0
        self.ensure_runtime_tables()
        now = _dt.datetime.now(_dt.timezone.utc)
        pairs = sorted({(r["registration"], r["schema"], r["table"]) for r in rows})
        with self.connect() as con:
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

    def write_signature(self, registration: str, schema: str, table: str, signature: dict) -> None:
        """One row per relation, replaced in place: the ONLY thing the next run diffs against.
        A key_change deliberately leaves the old row standing (see drift.py)."""
        import json
        self.ensure_runtime_tables()
        with self.connect() as con:
            con.execute(f'delete from "{RUNTIME_SCHEMA}".signature '
                        'where registration = ? and "schema" = ? and "table" = ?',
                        [registration, schema, table])
            con.execute(f'insert into "{RUNTIME_SCHEMA}".signature values (?, ?, ?, ?, ?)',
                        [registration, schema, table, json.dumps(signature, sort_keys=True),
                         _dt.datetime.now(_dt.timezone.utc)])

    def read_signatures(self, registration: str) -> dict[tuple[str, str], dict]:
        import json
        self.ensure_runtime_tables()

        def _read():
            with self.connect() as con:
                return con.execute(
                    f'select "schema", "table", signature_json from "{RUNTIME_SCHEMA}".signature '
                    "where registration = ?", [registration]).fetchall()
        rows = self._read_metadata_with_retry(_read)
        return {(r[0], r[1]): json.loads(r[2]) for r in rows if r[2]}

    def destination_tables(self, schemas: Iterable[str]) -> set[tuple[str, str]]:
        """Tables in THIS database only. `md:<db>` attaches every MotherDuck database the token
        can see and `information_schema` spans all of them (probed live 04-Sep-2026: connected to
        bronze_featherbase it also lists bronze_sap.control, bronze_gofrugal.landing …), so an
        unfiltered count inflates the `_slot` probe's `destination_tables` with someone else's
        same-named schema. `current_database()` is the same predicate on a duckdb file."""
        with self.connect() as con:
            rows = con.execute(
                "select table_schema, table_name from information_schema.tables "
                "where table_catalog = current_database() and table_schema = any(?)",
                [list(schemas)]).fetchall()
        return {(r[0], r[1]) for r in rows if not r[1].startswith("_dlt")}

    def table_row_count(self, schema: str, table: str) -> int | None:
        try:
            with self.connect() as con:
                return con.execute(f"select count(*) from {q(schema)}.{q(table)}").fetchone()[0]
        except Exception:  # noqa: BLE001 — table not there yet is not an error here
            return None

    def table_row_counts(self, pairs) -> dict:
        """{(schema, table): rows | None} on ONE connection — `reconcile_counts` asked one
        connection per relation, fifty MotherDuck round trips every tick for Featherbase."""
        out = {}
        with self.connect() as con:
            for schema, table in pairs:
                try:
                    out[(schema, table)] = con.execute(
                        f"select count(*) from {q(schema)}.{q(table)}").fetchone()[0]
                except Exception:  # noqa: BLE001 — not there yet is not an error here
                    out[(schema, table)] = None
        return out

    def _schema_exists(self, con, schema: str) -> bool:
        return con.execute(
            "select 1 from information_schema.schemata "
            "where catalog_name = current_database() and schema_name = ?", [schema]
        ).fetchone() is not None

    def delete_all_rows(self, schema: str, table: str) -> bool:
        """A source TRUNCATE applied to a keyed mirror. False when the destination table does
        not exist yet (an empty-at-snapshot source table has no destination table until its
        first row, so there is nothing to empty)."""
        with self.connect() as con:
            if not self._schema_exists(con, schema):
                return False
            exists = con.execute(
                "select 1 from information_schema.tables where table_catalog = current_database() "
                "and table_schema = ? and table_name = ?", [schema, table]).fetchone()
            if exists is None:
                return False
            con.execute(f"delete from {q(schema)}.{q(table)}")
        return True

    def drop_table(self, schema: str, table: str) -> list[str]:
        """Resnapshot needs the destination table AND its dlt merge-staging twin gone —
        a stale staging table replays into the fresh one on the next merge. The ONE
        tolerated absence is a schema that was never created (a table dlt never merged has
        no `_staging` twin); every other failure — permissions, a MotherDuck error, a view in
        the way — propagates, because a resnapshot that then merges into the undropped table
        keeps rows the source deleted (#3079 review)."""
        dropped = []
        with self.connect() as con:
            for sch in (schema, f"{schema}_staging"):
                if not self._schema_exists(con, sch):
                    continue
                con.execute(f"drop table if exists {q(sch)}.{q(table)}")
                dropped.append(f"{sch}.{table}")
        return dropped
