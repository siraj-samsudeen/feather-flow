"""Pre-flight runner for ``feather extract`` and ``feather run``.

Encapsulates the per-table sizing + planning + banner logic so both commands
call it identically.  Returns a list of ``(TableConfig, ExtractPlan | None)``
tuples where ``None`` means the table is a file source that should be extracted
normally without planner involvement.

Exit semantics
--------------
* Exits **3** if any unaccepted blockers remain after planning all DB-source
  tables.
* Exits **0** (cleanly) if the user answers ``N`` at the TTY prompt.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

import builtins

import typer

from feather_etl.curation import resolve_source
from feather_etl.planner import (
    auto_derive_int_pk_column,
    auto_derive_timestamp_column,
    pick_plan,
)
from feather_etl.preflight_banner import render as render_banner
from feather_etl.sources import StreamSchema
from feather_etl.transports.registry import TRANSPORT_CLASSES

if TYPE_CHECKING:
    from feather_etl.config import FeatherConfig, TableConfig
    from feather_etl.planner import ExtractPlan
    from feather_etl.state import StateManager

# Alias builtins.input so tests can monkeypatch feather_etl.commands._preflight.input.
input = builtins.input


def _count_projection_columns(config_dir: Path, table_name: str) -> int | None:
    """Return the number of columns in transforms/bronze/<table_name>.sql, or None.

    Counts SELECT-list items naively (commas at the top level of the SELECT).
    This is a best-effort estimate used only for the pre-flight banner.
    """
    sql_path = config_dir / "transforms" / "bronze" / f"{table_name}.sql"
    if not sql_path.is_file():
        return None
    sql = sql_path.read_text()
    # Find the SELECT clause — everything between SELECT and the first FROM.
    upper = sql.upper()
    select_idx = upper.find("SELECT")
    from_idx = upper.find("FROM", select_idx + 1)
    if select_idx == -1:
        return None
    select_clause = sql[select_idx + 6 : from_idx if from_idx != -1 else None]
    # Count comma-separated expressions at depth 0.
    depth = 0
    count = 1
    for ch in select_clause:
        if ch in ("(", "["):
            depth += 1
        elif ch in (")", "]"):
            depth -= 1
        elif ch == "," and depth == 0:
            count += 1
    return count


def _recent_rows_per_sec(state: "StateManager", table_name: str) -> float | None:
    """Return avg rows/sec from the last 5 successful runs, or None."""
    try:
        con = state._connect()
        try:
            rows = con.execute(
                """
                SELECT rows_loaded, duration_sec
                FROM _runs
                WHERE table_name = ?
                  AND status = 'success'
                  AND rows_loaded IS NOT NULL
                  AND duration_sec IS NOT NULL
                  AND duration_sec > 0
                ORDER BY ended_at DESC
                LIMIT 5
                """,
                [table_name],
            ).fetchall()
        finally:
            con.close()
        if not rows:
            return None
        total_rows = sum(r[0] for r in rows)
        total_sec = sum(r[1] for r in rows)
        return total_rows / total_sec if total_sec > 0 else None
    except Exception:  # pragma: no cover
        return None


def is_accepted(blocker, flags: dict[str, bool]) -> bool:
    """Return True if *blocker* is bypassed by one of the accept flags."""
    if blocker.kind == "wide_table":
        return flags["accept_wide"]
    if blocker.kind == "no_window_column":
        return flags["single_window"]
    if blocker.kind == "slow_transport":
        return flags["accept_slow_transport"]
    return False


def run_preflight(
    config: "FeatherConfig",
    state: "StateManager",
    *,
    table_filter: str | None,
    accept_wide: bool,
    single_window: bool,
    accept_slow_transport: bool,
    plan_only: bool,
    is_tty: bool | None = None,
    tables: "list[TableConfig] | None" = None,
) -> list[tuple["TableConfig", "ExtractPlan | None"]] | None:
    """Run pre-flight sizing for all DB-source tables in *config*.

    Parameters
    ----------
    config:
        Loaded FeatherConfig.
    state:
        Initialised StateManager for the working directory.
    table_filter:
        If set, only plan the table with this exact name.
    accept_wide, single_window, accept_slow_transport:
        Bypass flags for the three blocker kinds.
    plan_only:
        If True, return None after printing all banners (caller skips extract).
    is_tty:
        Override TTY detection.  None = auto-detect via sys.stdin.isatty().
    tables:
        Explicit list of TableConfig objects to plan.  When provided, this list
        is used directly and ``table_filter`` is ignored.  Callers that have
        already filtered ``config.tables`` should pass the result here to avoid
        mutating ``config.tables``.

    Returns
    -------
    list of (TableConfig, ExtractPlan | None)
        Each table from config that should be extracted.  DB-source tables carry
        their ``ExtractPlan``; file-source tables carry ``None``.
    Returns None if plan_only=True.
    Exits 3 if any unaccepted blockers remain; exits 0 if user cancels at TTY
    prompt.
    """
    registered_transports = list(TRANSPORT_CLASSES.keys())
    flags = {
        "accept_wide": accept_wide,
        "single_window": single_window,
        "accept_slow_transport": accept_slow_transport,
    }

    # Tables to plan: use explicit list if provided, else filter config.tables.
    if tables is not None:
        pass  # use as-is
    else:
        tables = config.tables
        if table_filter is not None:
            tables = [t for t in tables if t.name == table_filter]

    result_list: list[tuple] = []
    all_unaccepted: list[tuple[str, object]] = []  # (table_name, blocker)

    for table in tables:
        source_db = table.database or (table.source_name or "")

        try:
            source = resolve_source(source_db, config.sources)
        except ValueError:
            # If source can't be resolved, include table without a plan —
            # the extract loop will report the error properly.
            result_list.append((table, None))
            continue

        # Determine if this is a DB source by probing cheap_rowcount.
        # File sources either lack the method entirely (AttributeError) or raise
        # NotImplementedError per the Source protocol default.
        try:
            cheap_rows = source.cheap_rowcount(table.source_table)
        except (NotImplementedError, AttributeError):
            # File source — skip planner, include as-is.
            result_list.append((table, None))
            continue

        # Fetch StreamSchema for this table via source.discover().
        # Match by source_table name (last component) since discover() returns
        # all tables for the source.
        try:
            discovered = source.discover()
        except Exception:
            discovered = []

        source_table_short = table.source_table.split(".")[-1]
        schema: StreamSchema | None = None
        for s in discovered:
            # Match on full name or short name.
            if (
                s.name == table.source_table
                or s.name.split(".")[-1] == source_table_short
            ):
                schema = s
                break

        if schema is None:
            # Can't plan without schema — include without plan.
            result_list.append((table, None))
            continue

        # Override from state DB.
        override = state.get_override(table.name)

        # Window range: probe the auto-derived or override window column.
        auto_ts_col = auto_derive_timestamp_column(schema)
        auto_int_pk = auto_derive_int_pk_column(schema)
        effective_window_col = (
            override.window_column
            if override and override.window_column
            else auto_ts_col
        )

        window_range = None
        if effective_window_col or auto_int_pk:
            probe_col = effective_window_col or auto_int_pk
            try:
                window_range = source.get_window_range(table.source_table, probe_col)
            except (NotImplementedError, Exception):
                window_range = None

        # Projection column count.
        projection_col_count = _count_projection_columns(config.config_dir, table.name)
        projection_path = (
            f"transforms/bronze/{table.name}.sql"
            if projection_col_count is not None
            else None
        )

        # Transport partition support.
        transport_supports_partition_on = bool(
            getattr(source, "supports_partition_on", False)
        )

        # Windows already committed.
        target = f"bronze.{table.name}"
        committed = state.get_committed_windows(target)
        windows_done = len(committed)

        # Recent rows/sec.
        rps = _recent_rows_per_sec(state, table.name)

        # Rows already extracted (from committed windows' rows_loaded sum).
        rows_already = 0
        try:
            con = state._connect()
            try:
                row = con.execute(
                    "SELECT COALESCE(SUM(rows_loaded), 0) FROM _extract_windows "
                    "WHERE table_name = ?",
                    [target],
                ).fetchone()
                rows_already = int(row[0]) if row else 0
            finally:
                con.close()
        except Exception:
            rows_already = 0

        # Source dialect for window SQL generation.
        source_dialect = getattr(source, "type", "destination")

        plan = pick_plan(
            table,
            schema,
            cheap_rows,
            window_range,
            override,
            config.defaults.extract,
            registered_transports,
            transport_supports_partition_on,
            projection_col_count,
            source_dialect=source_dialect,
            recent_rows_per_sec=rps,
            rows_already_extracted=rows_already,
        )

        # Render banner.
        rowcount_source = getattr(source, "_rowcount_source", "estimate")

        window_range_start = None
        window_range_end = None
        if window_range is not None:
            try:
                start, end = window_range
                if hasattr(start, "date") and callable(start.date):
                    # datetime → "YYYY-MM-DD"
                    window_range_start = start.date().isoformat()
                    window_range_end = end.date().isoformat()
                elif hasattr(start, "isoformat"):
                    # date
                    window_range_start = start.isoformat()
                    window_range_end = end.isoformat()
                else:
                    window_range_start = str(start)
                    window_range_end = str(end)
            except Exception:
                pass

        banner = render_banner(
            plan,
            table.name,
            rowcount_source=rowcount_source,
            projection_path=projection_path,
            windows_done=windows_done,
            window_range_start=window_range_start,
            window_range_end=window_range_end,
            rows_per_sec=int(rps) if rps is not None else None,
        )
        typer.echo(banner)

        # Collect unaccepted blockers.
        for blocker in plan.blockers:
            if not is_accepted(blocker, flags):
                all_unaccepted.append((table.name, blocker))

        result_list.append((table, plan))

    # If any unaccepted blockers, emit error and exit 3.
    if all_unaccepted:
        typer.echo("\nBlocked — resolve the following before extracting:", err=False)
        for tname, blocker in all_unaccepted:
            typer.echo(f"  [{tname}] {blocker.message}")
            typer.echo(f"         bypass: {blocker.bypass_flag}")
        raise typer.Exit(code=3)

    # plan_only: skip actual extraction.
    if plan_only:
        return None

    # TTY Y/n prompt (only when there are DB-source tables that produced plans).
    db_plans = [p for _, p in result_list if p is not None]
    if db_plans:
        if is_tty is None:
            is_tty = sys.stdin.isatty()
        if is_tty:
            try:
                answer = input("Proceed? [Y/n] ").strip().lower()
            except EOFError:
                answer = "y"
            if answer in ("n", "no"):
                typer.echo("Cancelled.")
                raise typer.Exit(code=0)

    return result_list
