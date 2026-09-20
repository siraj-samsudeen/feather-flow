"""The warehouse's business day, in IST (#3854).

Two clocks, and confusing them is the bug this module exists to prevent:

  * a **business day** — a load window, a grid key, an audit window, a report "as of" —
    is a partition of the *business*, and the business runs on IST;
  * an **instant** — `_loaded_at`, a run id, a log stamp, an elapsed/deadline comparison,
    a cron hour compared against `REPAIR_UTC_HOURS` — is UTC (ADR 0027).

This module is the first half only. Never reach for it to stamp an instant.

Import it rather than writing `ZoneInfo("Asia/Kolkata")` inline: this docstring is the one
place the reason is recorded, and a literal carries no reason with it.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def ist_today() -> dt.date:
    """The calendar date it is *now in India* — the warehouse's business day.

    Never `date.today()`. No service sets `TZ`, so every container runs UTC, and between
    00:00 and 05:30 IST `date.today()` returns the PREVIOUS business day. Every window
    derived from it then shifts a day, and the run still reports success: a GoFrugal
    nightly started at 04:48 IST on 20-Sep-2026 (23:18 UTC on the 19th) pulled 18/17/16-Sep
    and lost 19-Sep entirely, with a clean `control.run` row and an all-`ok` grid (#3854).
    """
    return dt.datetime.now(IST).date()


def ist_yesterday() -> dt.date:
    """The business day before `ist_today()` — the usual edge / nightly anchor.

    Plain calendar arithmetic, deliberately: the group's stores trade seven days a week, so
    there is no trading-day calendar to skip weekends or holidays against.
    """
    return ist_today() - dt.timedelta(days=1)
