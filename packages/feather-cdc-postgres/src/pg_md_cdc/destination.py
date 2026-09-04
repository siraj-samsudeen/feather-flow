"""MotherDuck / duckdb load contract + runtime metadata (#3079 Deviation D-2).

One dlt pipeline PER SOURCE SCHEMA (dataset = the mapped target schema), so two source
schemas holding the same table name never collide. Checkpoint, lifecycle and schema registry
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
    def pipeline_for_schema(self, source_schema: str, target_schema: str,
                            pipeline_name: str | None = None, *, source_id: str = "pg") -> dlt.Pipeline:
        name = pipeline_name or f"{source_id}_cdc__{source_schema}"
        kwargs = {}
        if self.spec.pipelines_dir:
            kwargs["pipelines_dir"] = self.spec.pipelines_dir
        return dlt.pipeline(pipeline_name=name, destination=self._dlt_destination(),
                            dataset_name=target_schema, **kwargs)

    @staticmethod
    def load(pipeline: dlt.Pipeline, items, *, resource_name: str = "cdc"):
        """Run one batch through a pipeline. The caller checkpoints only after this returns."""
        return pipeline.run(dlt.resource(items, name=resource_name))

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
        self._ensured = True

    def read_checkpoint(self, registration: str) -> tuple[int, str | None] | None:
        self.ensure_runtime_tables()
        with self.connect() as con:
            row = con.execute(
                f'select last_commit_lsn, load_ids from "{RUNTIME_SCHEMA}".checkpoint '
                "where registration = ?", [registration]).fetchone()
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
        with self.connect() as con:
            rows = con.execute(
                f'select "schema", "table", state from "{RUNTIME_SCHEMA}".lifecycle '
                "where registration = ?", [registration]).fetchall()
        return {(r[0], r[1]): r[2] for r in rows}

    def write_registry_rows(self, rows: Sequence[dict]) -> int:
        """Called by schema_registry.py. Keys: registration, schema, table, column, type_oid,
        typmod, not_null, key_role, dest_type, mapping (seen_at is stamped here)."""
        if not rows:
            return 0
        self.ensure_runtime_tables()
        now = _dt.datetime.now(_dt.timezone.utc)
        with self.connect() as con:
            for r in rows:
                con.execute(
                    f'insert into "{RUNTIME_SCHEMA}".schema_registry values (?,?,?,?,?,?,?,?,?,?,?)',
                    [r["registration"], r["schema"], r["table"], r["column"],
                     r.get("type_oid"), r.get("typmod"), r.get("not_null"),
                     r.get("key_role"), r.get("dest_type"), r.get("mapping"),
                     r.get("seen_at") or now])
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
        with self.connect() as con:
            rows = con.execute(
                f'select "schema", "table", signature_json from "{RUNTIME_SCHEMA}".signature '
                "where registration = ?", [registration]).fetchall()
        return {(r[0], r[1]): json.loads(r[2]) for r in rows if r[2]}

    def destination_tables(self, schemas: Iterable[str]) -> set[tuple[str, str]]:
        with self.connect() as con:
            rows = con.execute(
                "select table_schema, table_name from information_schema.tables "
                "where table_schema = any(?)", [list(schemas)]).fetchall()
        return {(r[0], r[1]) for r in rows if not r[1].startswith("_dlt")}

    def table_row_count(self, schema: str, table: str) -> int | None:
        try:
            with self.connect() as con:
                return con.execute(f"select count(*) from {q(schema)}.{q(table)}").fetchone()[0]
        except Exception:  # noqa: BLE001 — table not there yet is not an error here
            return None

    def drop_table(self, schema: str, table: str) -> list[str]:
        """Resnapshot needs the destination table AND its dlt merge-staging twin gone —
        a stale staging table replays into the fresh one on the next merge."""
        dropped = []
        with self.connect() as con:
            for sch in (schema, f"{schema}_staging"):
                try:
                    con.execute(f"drop table if exists {q(sch)}.{q(table)}")
                    dropped.append(f"{sch}.{table}")
                except Exception:  # noqa: BLE001 — missing schema is fine
                    pass
        return dropped
