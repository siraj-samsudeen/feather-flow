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
from psycopg.types.json import Json

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
    wal_path      TEXT,
    extra         JSONB
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
    extra           JSONB,
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


# Advisory-lock key serializing the DDL path across processes (#3091). An arbitrary but FIXED
# 64-bit constant: every writer must pick the same number or the lock serializes nothing. Recorded
# here so a future advisory lock in this repo picks a different one.
_SCHEMA_ADVISORY_LOCK = 3_204_198_401

# The shape ensure_schema guarantees. Checked as a READ before any DDL runs, because DDL on this
# path is what deadlocked the fleet: `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` takes an
# AccessExclusiveLock *even when the column already exists*, so running it on every startup gave
# every pair of concurrently-starting writers a chance to deadlock (#3091).
_REQUIRED_TABLES = ("run", "lease", "event", "budget_usage")
_REQUIRED_COLUMNS = (("event", "extra"), ("run", "extra"))


def _schema_is_current(conn: psycopg.Connection) -> bool:
    """True when every table and migrated column already exists — a pure catalog read, no locks.

    This is the steady state on a live control plane, so the common path of ensure_schema() must
    reach exactly here and stop: no CREATE, no ALTER, nothing that can block another session."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'control'")
        tables = {r[0] for r in cur.fetchall()}
        if not set(_REQUIRED_TABLES) <= tables:
            return False
        cur.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'control' AND table_name = ANY(%s)",
            ([t for t, _ in _REQUIRED_COLUMNS],))
        columns = {(r[0], r[1]) for r in cur.fetchall()}
        return set(_REQUIRED_COLUMNS) <= columns


def ensure_schema(conn: psycopg.Connection) -> None:
    """Create the control schema if it is not already there. Safe to call concurrently.

    Called at the start of EVERY run by every writer, so its steady-state cost is the thing that
    matters: a single catalog read and no lock at all. Before #3091 it ran `CREATE TABLE IF NOT
    EXISTS` + two `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` unconditionally, and because the ALTER
    takes an AccessExclusiveLock whether or not the column is missing, two writers starting in the
    same moment deadlocked -- each holding the CREATE's weaker lock while requesting the exclusive
    one. Postgres killed them, and under ADR 0028's hard dependency that aborts the run BEFORE it
    reads its source: on 04-Sep-2026 two of PTP's three instances did not load at all, killed by
    the featherbase drainer's startup 27 seconds earlier. With featherbase ticking every 5 minutes
    that was a dice-roll at every startup, fleet-wide.
    """
    if _schema_is_current(conn):  # @spec schema_ensured_without_locks
        conn.rollback()   # end the read's implicit transaction; hold no locks on the way out
        return

    with conn.cursor() as cur:
        # First run, or a genuine migration. Serialize the DDL so concurrent first-runs queue
        # instead of deadlocking; the lock is released with the transaction below.
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_ADVISORY_LOCK,))
        cur.execute(SCHEMA_DDL)
        # Migration for control planes created before control.event.extra existed (e.g. the
        # live GoFrugal table): CREATE TABLE IF NOT EXISTS above won't add a column to it.
        cur.execute("ALTER TABLE control.event ADD COLUMN IF NOT EXISTS extra JSONB")
        # Same for control.run.extra, added for dbt (#2640): dbt's run row carries fields no
        # extractor has -- bronze_watermark, action, dbt_rc -- and bolting five dbt-specific
        # columns onto the table five writers share would be worse than one JSONB bag.
        cur.execute("ALTER TABLE control.run ADD COLUMN IF NOT EXISTS extra JSONB")
    conn.commit()


# --- WAL-record projection (the shipper calls apply() per record, in file order) ---

def apply(cur: psycopg.Cursor, rec: dict) -> None:  # @spec shipping_idempotent
    """Project one WAL record into Postgres. Idempotent; dispatched by `kind`."""
    kind = rec["kind"]
    if kind == "run_start":
        run_extra = rec.get("extra")
        cur.execute(
            "INSERT INTO control.run "
            "(run_id, extractor, mode, host, pid, started_at, status, wal_path, extra) "
            "VALUES (%s,%s,%s,%s,%s,%s::timestamptz,%s,%s,%s) ON CONFLICT (run_id) DO NOTHING",
            (rec["run_id"], rec["extractor"], rec.get("mode"), rec.get("host"),
             rec.get("pid"), rec["started_at"], rec["status"], rec.get("wal_path"),
             Json(run_extra) if run_extra is not None else None),
        )
    elif kind == "run_finish":
        # Upsert: if PG was down at start (no run_start row landed), still record the run.
        fin_extra = rec.get("extra")
        cur.execute(
            "INSERT INTO control.run "
            "(run_id, extractor, started_at, finished_at, status, stop_reason, n_ok, n_err, n_skipped, extra) "
            "VALUES (%s,%s,%s::timestamptz,%s::timestamptz,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (run_id) DO UPDATE SET "
            "finished_at=EXCLUDED.finished_at, status=EXCLUDED.status, "  # @spec died_overwritten_by_late_finish
            "stop_reason=EXCLUDED.stop_reason, n_ok=EXCLUDED.n_ok, n_err=EXCLUDED.n_err, "
            "n_skipped=EXCLUDED.n_skipped, "
            # COALESCE, not overwrite: a finish that carries no extra must never erase what
            # run_start recorded (dbt's bronze_watermark lands at start).
            "extra=COALESCE(EXCLUDED.extra, control.run.extra)",  # @spec run_extra_preserved_on_finish
            (rec["run_id"], rec["extractor"], rec.get("started_at", rec["finished_at"]),
             rec["finished_at"], rec["status"], rec.get("stop_reason"),
             rec.get("n_ok"), rec.get("n_err"), rec.get("n_skipped"),
             Json(fin_extra) if fin_extra is not None else None),
        )
    elif kind == "event":
        extra = rec.get("extra")
        cur.execute(
            "INSERT INTO control.event "
            "(extractor, run_id, seq, ran_at, entity, unit, business_date, status, "
            " http_status, rows, source_attempts, sink_attempts, pull_ms, load_ms, error_detail, extra) "
            "VALUES (%s,%s,%s,%s::timestamptz,%s,%s,%s::date,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (extractor, run_id, seq) DO NOTHING",
            (rec["extractor"], rec["run_id"], rec["seq"], rec["ran_at"], rec.get("entity"),
             rec.get("unit"), rec.get("business_date"), rec["status"], rec.get("http_status"),
             rec.get("rows"), rec.get("source_attempts"), rec.get("sink_attempts"),
             rec.get("pull_ms"), rec.get("load_ms"), rec.get("error_detail"),
             Json(extra) if extra is not None else None),
        )
    elif kind == "budget":
        cur.execute(
            "INSERT INTO control.budget_usage "
            "(run_id, extractor, budget_name, limit_val, used, remaining, hit, ran_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s::timestamptz) "
            "ON CONFLICT (run_id, extractor, budget_name) DO UPDATE SET "  # @spec budget_recorded_every_run
            "limit_val=EXCLUDED.limit_val, used=EXCLUDED.used, remaining=EXCLUDED.remaining, "
            "hit=EXCLUDED.hit, ran_at=EXCLUDED.ran_at",
            (rec["run_id"], rec["extractor"], rec["budget_name"], rec.get("limit_val"),
             rec.get("used"), rec.get("remaining"), rec.get("hit"), rec.get("ran_at")),
        )
    else:
        raise ValueError(f"unknown WAL record kind: {kind!r}")


def apply_events_batch(cur: psycopg.Cursor, recs: list[dict]) -> None:
    """Batch form of apply() for a homogeneous list of kind='event' records.

    Same SQL and same idempotency key as the per-record path -- executemany just sends it in
    one pipelined batch instead of one round-trip per row. Added for #2640: dbt's run_results
    projection can carry tens of thousands of nodes in a catch-up window, and at ~250ms RTT over
    the public proxy the per-record path turns 28k rows into two hours. executemany does it in
    seconds, and on Railway's private network the difference is merely large.
    """
    if not recs:
        return
    bad = [r["kind"] for r in recs if r.get("kind") != "event"]
    if bad:
        raise ValueError(f"apply_events_batch only takes kind='event' records, got {bad[:3]}")
    cur.executemany(
        "INSERT INTO control.event "
        "(extractor, run_id, seq, ran_at, entity, unit, business_date, status, "
        " http_status, rows, source_attempts, sink_attempts, pull_ms, load_ms, error_detail, extra) "
        "VALUES (%s,%s,%s,%s::timestamptz,%s,%s,%s::date,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (extractor, run_id, seq) DO NOTHING",
        [(r["extractor"], r["run_id"], r["seq"], r["ran_at"], r.get("entity"),
          r.get("unit"), r.get("business_date"), r["status"], r.get("http_status"),
          r.get("rows"), r.get("source_attempts"), r.get("sink_attempts"),
          r.get("pull_ms"), r.get("load_ms"), r.get("error_detail"),
          Json(r["extra"]) if r.get("extra") is not None else None)
         for r in recs],
    )


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
            "WHERE control.lease.lease_expires_at < now() "  # @spec lease_steal_only_if_expired
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
            "WHERE extractor = %s AND finished_at IS NULL AND run_id <> %s",  # @spec unfinished_prior_runs_marked_died
            (run_id, extractor, run_id),
        )
        n = cur.rowcount
    conn.commit()
    return n
