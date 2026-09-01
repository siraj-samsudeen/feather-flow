"""Control-plane status vocabulary (issue #204).

One place for the words written to `control.run.status`, `control.run.stop_reason`,
`control.event.status`, and `control.budget_usage.budget_name`, so every extractor speaks
the same language and the #206 cross-source health query never special-cases per source.

Vocabulary is a **blend** (locked 2026-06-24): API extractors (gofrugal, zakya) split the
source leg from the sink leg (`source_error` vs `sink_error`); DB extractors (sap, ptp,
stylehr) use the single fused `error` (read+load happen in one retry). The `run` lifecycle
states and budget `stop_reason` are standard across all five.
"""
from __future__ import annotations

# --- control.run.status (run lifecycle) ---
RUN_RUNNING = "running"   # inserted at start; live
RUN_OK = "ok"             # finished, every unit succeeded
RUN_PARTIAL = "partial"   # finished, some units errored (or budget cut it short)
RUN_FAILED = "failed"     # finished by an unhandled exception
RUN_DIED = "died"         # never finished itself; reconciled by a later run (lease lapsed)

RUN_STATES = frozenset({RUN_RUNNING, RUN_OK, RUN_PARTIAL, RUN_FAILED, RUN_DIED})

# --- control.run.stop_reason (why the run ended) ---
STOP_CLEAN = "clean"               # ran to natural completion
STOP_BUDGET_API = "budget_api"     # hit a per-day API quota
STOP_BUDGET_EXPORT = "budget_export"  # hit a per-hour export quota
STOP_BUDGET_TIME = "budget_time"   # walltime budget cut it short (still healthy)
STOP_ERROR = "error"               # unhandled exception
STOP_DIED = "died"                 # set by the reconciler on a dead run

STOP_REASONS = frozenset({
    STOP_CLEAN, STOP_BUDGET_API, STOP_BUDGET_EXPORT, STOP_BUDGET_TIME, STOP_ERROR, STOP_DIED,
})

# --- control.event.status (per unit/cell outcome) ---
EV_OK = "ok"                    # landed rows
EV_ZERO_ROWS = "zero_rows"      # source reachable, genuinely empty (http 200)
EV_SOURCE_ERROR = "source_error"  # source returned non-200 / bad payload (API extractors)
EV_TIMEOUT = "timeout"          # source did not respond at all (http=0, all attempts threw)
EV_SINK_ERROR = "sink_error"    # the load/sink (MotherDuck) failed after retries (API extractors)
EV_RATE_LIMITED = "rate_limited"  # source 429 (Zakya)
EV_OK_PARTIAL = "ok_partial"    # landed, but some records quarantined (Zakya, ADR 0021)
EV_SOURCE_NOT_SETTLED = "source_not_settled"  # source answered, but as-of a moment before the
                                # business date closed, so the answer cannot be complete —
                                # NOT terminal, retry once the source has caught up (#2957)
EV_ERROR = "error"              # DB extractors: fused read-or-load failure (single leg)

# --- control.budget_usage.budget_name ---
BUDGET_WALLTIME = "walltime"          # per-run wall-clock budget (seconds)
BUDGET_API_DAILY = "api_daily"        # Zakya 25k calls/day
BUDGET_EXPORT_HOURLY = "export_hourly"  # Zakya 10 exports/hour

# error_detail is truncated to this (matches #108).
ERROR_DETAIL_CAP = 2000
