"""Postgres control plane: schema, idempotent upserts, the liveness lease (issue #204).

This is the *control* plane — run lifecycle, the event log, budget telemetry, and the lease.
The *data* plane stays in MotherDuck. Control is a transactional/concurrent/locking workload
(the opposite of columnar analytics), and you cannot durably log a MotherDuck failure into
MotherDuck — hence a separate Postgres (supersedes ADR 0005 + #65).

All writes are idempotent so the WAL shipper can replay at-least-once and converge:
- `event` keyed `(extractor, run_id, seq)` → ON CONFLICT DO NOTHING
- `run` keyed `run_id` → start DO NOTHING, finish is an UPDATE
- `budget_usage` keyed `(run_id, extractor, budget_name)` → ON CONFLICT DO UPDATE

`ran_at` is the canonical run-health timestamp (ADR 0027). `limit_val` avoids the reserved
word `limit`.
"""
from __future__ import annotations

import psycopg

SCHEMA_DDL = """
CREATE SCHEMA IF NOT EXISTS control;

CREATE TABLE IF NOT EXISTS control.run (
    run_id        TEXT PRIMARY KEY,
    extractor     TEXT NOT NULL,
    mode          TEXT,
    host          TEXT,
    pid           INTEGER,
    started_at    TIMESTAMPTZ NOT NULL,
    finished_at   TIMESTAMPTZ,
    status        TEXT NOT NULL,
    stop_reason   TEXT,
    n_ok          INTEGER,
    n_err         INTEGER,
    n_skipped     INTEGER,
    reconciled_by TEXT,
    wal_path      TEXT
);
CREATE INDEX IF NOT EXISTS run_extractor_unfinished
    ON control.run (extractor) WHERE finished_at IS NULL;

CREATE TABLE IF NOT EXISTS control.lease (
    extractor        TEXT PRIMARY KEY,
    run_id           TEXT NOT NULL,
    lease_expires_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS control.event (
    extractor       TEXT NOT NULL,
    run_id          TEXT NOT NULL,
    seq             BIGINT NOT NULL,
    ran_at          TIMESTAMPTZ NOT NULL,
    entity          TEXT,
    unit            TEXT,
    business_date   DATE,
    status          TEXT NOT NULL,
    http_status     INTEGER,
    rows            BIGINT,
    source_attempts INTEGER,
    sink_attempts   INTEGER,
    pull_ms         INTEGER,
    load_ms         INTEGER,
    error_detail    TEXT,
    PRIMARY KEY (extractor, run_id, seq)
);
CREATE INDEX IF NOT EXISTS event_run ON control.event (run_id);

CREATE TABLE IF NOT EXISTS control.budget_usage (
    run_id      TEXT NOT NULL,
    extractor   TEXT NOT NULL,
    budget_name TEXT NOT NULL,
    limit_val   BIGINT,
    used        BIGINT,
    remaining   BIGINT,
    hit         BOOLEAN,
    ran_at      TIMESTAMPTZ,
    PRIMARY KEY (run_id, extractor, budget_name)
);
"""


def ensure_schema(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(SCHEMA_DDL)
    conn.commit()


# --- WAL-record projection (the shipper calls apply() per record, in file order) ---

def apply(cur: psycopg.Cursor, rec: dict) -> None:
    """Project one WAL record into Postgres. Idempotent; dispatched by `kind`."""
    kind = rec["kind"]
    if kind == "run_start":
        cur.execute(
            "INSERT INTO control.run "
            "(run_id, extractor, mode, host, pid, started_at, status, wal_path) "
            "VALUES (%s,%s,%s,%s,%s,%s::timestamptz,%s,%s) ON CONFLICT (run_id) DO NOTHING",
            (rec["run_id"], rec["extractor"], rec.get("mode"), rec.get("host"),
             rec.get("pid"), rec["started_at"], rec["status"], rec.get("wal_path")),
        )
    elif kind == "run_finish":
        # Upsert: if PG was down at start (no run_start row landed), still record the run.
        cur.execute(
            "INSERT INTO control.run "
            "(run_id, extractor, started_at, finished_at, status, stop_reason, n_ok, n_err, n_skipped) "
            "VALUES (%s,%s,%s::timestamptz,%s::timestamptz,%s,%s,%s,%s,%s) "
            "ON CONFLICT (run_id) DO UPDATE SET "
            "finished_at=EXCLUDED.finished_at, status=EXCLUDED.status, "
            "stop_reason=EXCLUDED.stop_reason, n_ok=EXCLUDED.n_ok, n_err=EXCLUDED.n_err, "
            "n_skipped=EXCLUDED.n_skipped",
            (rec["run_id"], rec["extractor"], rec.get("started_at", rec["finished_at"]),
             rec["finished_at"], rec["status"], rec.get("stop_reason"),
             rec.get("n_ok"), rec.get("n_err"), rec.get("n_skipped")),
        )
    elif kind == "event":
        cur.execute(
            "INSERT INTO control.event "
            "(extractor, run_id, seq, ran_at, entity, unit, business_date, status, "
            " http_status, rows, source_attempts, sink_attempts, pull_ms, load_ms, error_detail) "
            "VALUES (%s,%s,%s,%s::timestamptz,%s,%s,%s::date,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (extractor, run_id, seq) DO NOTHING",
            (rec["extractor"], rec["run_id"], rec["seq"], rec["ran_at"], rec.get("entity"),
             rec.get("unit"), rec.get("business_date"), rec["status"], rec.get("http_status"),
             rec.get("rows"), rec.get("source_attempts"), rec.get("sink_attempts"),
             rec.get("pull_ms"), rec.get("load_ms"), rec.get("error_detail")),
        )
    elif kind == "budget":
        cur.execute(
            "INSERT INTO control.budget_usage "
            "(run_id, extractor, budget_name, limit_val, used, remaining, hit, ran_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s::timestamptz) "
            "ON CONFLICT (run_id, extractor, budget_name) DO UPDATE SET "
            "limit_val=EXCLUDED.limit_val, used=EXCLUDED.used, remaining=EXCLUDED.remaining, "
            "hit=EXCLUDED.hit, ran_at=EXCLUDED.ran_at",
            (rec["run_id"], rec["extractor"], rec["budget_name"], rec.get("limit_val"),
             rec.get("used"), rec.get("remaining"), rec.get("hit"), rec.get("ran_at")),
        )
    else:
        raise ValueError(f"unknown WAL record kind: {kind!r}")


# --- the liveness lease (the deterministic death-detection primitive) ---

def lease_acquire(conn: psycopg.Connection, extractor: str, run_id: str, ttl_s: int) -> bool:
    """Acquire this extractor's lease. Steal-only-if-expired: the ON CONFLICT UPDATE fires
    *only* when the existing lease has lapsed, so a genuinely-live run is never displaced.
    Returns True if acquired (no live prior run), False if another run holds a fresh lease.
    """
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO control.lease (extractor, run_id, lease_expires_at) "
            "VALUES (%s, %s, now() + make_interval(secs => %s)) "
            "ON CONFLICT (extractor) DO UPDATE SET "
            "run_id = EXCLUDED.run_id, lease_expires_at = EXCLUDED.lease_expires_at "
            "WHERE control.lease.lease_expires_at < now() "
            "RETURNING run_id",
            (extractor, run_id, ttl_s),
        )
        acquired = cur.fetchone() is not None
    conn.commit()
    return acquired


def lease_renew(conn: psycopg.Connection, extractor: str, run_id: str, ttl_s: int) -> bool:
    """Push the lease forward (renewer thread). Returns False if we no longer own it."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE control.lease SET lease_expires_at = now() + make_interval(secs => %s) "
            "WHERE extractor = %s AND run_id = %s",
            (ttl_s, extractor, run_id),
        )
        ok = cur.rowcount > 0
    conn.commit()
    return ok


def lease_release(conn: psycopg.Connection, extractor: str, run_id: str) -> None:
    """Expire our lease immediately so the next run can reclaim without waiting out the TTL."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE control.lease SET lease_expires_at = now() "
            "WHERE extractor = %s AND run_id = %s",
            (extractor, run_id),
        )
    conn.commit()


def reconcile_dead(conn: psycopg.Connection, extractor: str, run_id: str) -> int:
    """Mark every unfinished prior run of this extractor as `died`. Safe to call only after
    we hold the lease (⇒ no other run of this extractor is alive). The dead run's cause is
    already in control.event via WAL replay. Returns the number reconciled."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE control.run SET status = 'died', stop_reason = 'died', "
            "finished_at = now(), reconciled_by = %s "
            "WHERE extractor = %s AND finished_at IS NULL AND run_id <> %s",
            (run_id, extractor, run_id),
        )
        n = cur.rowcount
    conn.commit()
    return n
