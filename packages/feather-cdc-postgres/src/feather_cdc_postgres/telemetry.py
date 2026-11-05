"""Control-plane telemetry for one registration (#3079 D11).

The `_slot` probe keeps the Sweep 2e contract byte-for-byte (entity `_slot`, unit `probe`,
extra carrying slot/active/wal_status/restart_lsn/confirmed_flush_lsn/lag_bytes) and adds the
D-7 fuse read from the server (`max_slot_wal_keep_size`), the selected/published/destination
counts and checkpoint age. Reconciliation counts skip tables above a row threshold rather than
pretending to compare them; a compared table that does not tie is an EV_ERROR, not an `ok`
carrying a delta in `extra`.
"""
from __future__ import annotations

import datetime as _dt
import fnmatch

import psycopg
from psycopg import sql

from control_plane import status as cp_status

RECONCILE_MAX_ROWS = 1_000_000


def slot_probe(dsn: str, slot: str) -> dict | None:
    """One row of slot telemetry, or None when the slot does not exist."""
    with psycopg.connect(dsn, connect_timeout=10) as conn:
        row = conn.execute(
            """
            select active, wal_status, restart_lsn::text, confirmed_flush_lsn::text,
                   pg_wal_lsn_diff(pg_current_wal_lsn(), confirmed_flush_lsn)::bigint as lag_bytes,
                   (confirmed_flush_lsn - '0/0')::bigint as confirmed_flush_int
            from pg_replication_slots where slot_name = %s
            """,
            (slot,),
        ).fetchone()
        fuse = conn.execute(
            "select setting, unit from pg_settings where name = 'max_slot_wal_keep_size'"
        ).fetchone()
    if row is None:
        return None
    return {
        "slot": slot,
        "active": row[0],
        "wal_status": row[1],
        "restart_lsn": row[2],
        "confirmed_flush_lsn": row[3],
        "lag_bytes": int(row[4]) if row[4] is not None else None,
        "confirmed_flush_int": int(row[5]) if row[5] is not None else None,
        "max_slot_wal_keep_size_bytes": _fuse_bytes(fuse),
    }


def _fuse_bytes(setting) -> int | None:
    """pg_settings gives a number plus a unit ('MB', '8kB', …). A negative setting means NO
    fuse, and it reports as None — the same spelling `discovery.slot_state` uses. Never -1:
    a consumer comparing `lag > 10% of fuse` reads that as a zero fuse and alarms forever."""
    if not setting:
        return None
    value, unit = setting
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    if n < 0:
        return None
    factors = {"B": 1, "kB": 1024, "8kB": 8192, "MB": 1024 ** 2, "GB": 1024 ** 3}
    return n * factors.get(unit or "B", 1)


def emit_probe(run, probe: dict | None, counts: dict | None = None, *,
               pull_ms: int | None = None) -> dict:
    """Sweep 2e reads exactly this event: entity `_slot`, unit `probe`."""
    extra = dict(probe or {})
    extra.update(counts or {})
    run.event(entity="_slot", status=cp_status.EV_OK, unit="probe",
              pull_ms=pull_ms, extra=extra)
    return extra


def checkpoint_age_s(committed_at) -> float | None:
    if committed_at is None:
        return None
    if committed_at.tzinfo is None:
        committed_at = committed_at.replace(tzinfo=_dt.timezone.utc)
    return (_dt.datetime.now(_dt.timezone.utc) - committed_at).total_seconds()


EXCLUDED_REASON = "excluded by manifest: self-written by this drainer"


APPEND_ONLY_REASON = ("append_only: the destination keeps every row version, so a row count "
                      "cannot tie against the source's current rows")


def reconcile_counts(dsn: str, destination, relations, targets,
                     *, threshold: int = RECONCILE_MAX_ROWS,
                     exclude: "list[str] | None" = None,
                     append_only: "set | None" = None) -> list[dict]:
    """Source vs destination row counts, per relation. A table whose source count exceeds
    `threshold`, which the manifest excludes, or which loads `append_only`, is reported as
    skipped with a reason — never as a silent pass. `exclude` exists because a relation this
    drainer WRITES DURING THE RUN (its own `control.run`/`control.event`) can never tie "now"
    against "now"; `append_only` because one source UPDATE leaves two destination row versions,
    which would pin that table to EV_ERROR for the rest of its life. `targets` maps
    (schema, table) -> destination schema; a relation not in it is counted where it came from.
    """
    out: list[dict] = []
    target = lambda s, t: targets.get((s, t), s)  # noqa: E731
    patterns = list(exclude or [])
    keyless = set(append_only or ())
    compared: list[tuple] = []   # (schema, table, source_rows), counted on one dest connection
    with psycopg.connect(dsn, connect_timeout=10) as conn:
        for schema, table in relations:
            if (schema, table) in keyless:
                out.append({"schema": schema, "table": table, "skipped": True,
                            "append_only": True, "reason": APPEND_ONLY_REASON})
                continue
            if any(fnmatch.fnmatchcase(f"{schema}.{table}", pat) for pat in patterns):
                out.append({"schema": schema, "table": table, "skipped": True,
                            "excluded": True, "reason": EXCLUDED_REASON})
                continue
            row = conn.execute(
                "select reltuples::bigint from pg_class c join pg_namespace n "
                "on n.oid = c.relnamespace where n.nspname = %s and c.relname = %s",
                (schema, table)).fetchone()
            estimate = int(row[0]) if row and row[0] is not None else 0
            if estimate > threshold:
                out.append({"schema": schema, "table": table, "skipped": True,
                            "reason": f"estimated {estimate} rows > threshold {threshold}"})
                continue
            src = conn.execute(sql.SQL("select count(*) from {}.{}").format(
                sql.Identifier(schema), sql.Identifier(table))).fetchone()[0]
            compared.append((schema, table, int(src)))
            out.append(None)   # placeholder keeps relation order
    dest_counts = destination.table_row_counts(
        [(target(s, t), t) for s, t, _ in compared]) if compared else {}
    for schema, table, src in compared:
        dest = dest_counts.get((target(schema, table), table))
        out[out.index(None)] = {
            "schema": schema, "table": table, "skipped": False,
            "source_rows": src, "destination_rows": dest,
            "delta": None if dest is None else src - int(dest),
            # dlt creates a table on its FIRST row, so "0 at the source, absent at the
            # destination" ties — reporting it as a mismatch would fire an error every tick
            # for every still-empty selected table.
            "match": (int(dest) == src) if dest is not None else src == 0}
    return out


def emit_reconcile(run, per_table_rows) -> None:
    for row in per_table_rows:
        entity = f"{row['schema']}.{row['table']}"
        if row.get("skipped"):
            extra = {"skipped": True, "reason": row.get("reason")}
            if row.get("excluded"):
                extra["excluded"] = True
            if row.get("append_only"):
                extra["append_only"] = True
            run.event(entity=entity, status=cp_status.EV_ZERO_ROWS, unit="reconcile",
                      extra=extra)
        else:
            # A count that does not tie is the whole point of reconciling: it is an error-class
            # event, never an `ok` with an interesting `extra` nobody queries.
            matched = bool(row.get("match"))
            run.event(entity=entity,
                      status=cp_status.EV_OK if matched else cp_status.EV_ERROR,
                      unit="reconcile", rows=row.get("source_rows"),
                      error_detail=None if matched else (
                          f"source {row.get('source_rows')} vs destination "
                          f"{row.get('destination_rows')} (delta {row.get('delta')})"),
                      extra={"source_rows": row.get("source_rows"),
                             "destination_rows": row.get("destination_rows"),
                             "delta": row.get("delta"), "match": matched})
