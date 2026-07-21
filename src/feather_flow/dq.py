"""Data quality checks for feather-flow (V9)."""

from __future__ import annotations

from dataclasses import dataclass

import duckdb


@dataclass
class DQResult:
    """Result of a single DQ check."""

    check_type: str
    column_name: str | None
    result: str  # "pass", "fail", "warn"
    details: str


def run_dq_checks(
    con: duckdb.DuckDBPyConnection,
    table_name: str,
    target_table: str,
    quality_checks: dict | None,
    run_id: str,
    *,
    primary_key: list[str] | None = None,
) -> list[DQResult]:
    """Run all configured DQ checks against a loaded table.

    Returns list of DQResult. Always includes row_count check.
    """
    results: list[DQResult] = []

    # row_count always runs (FR8.4)
    count = con.execute(f"SELECT COUNT(*) FROM {target_table}").fetchone()[0]
    if count == 0:
        results.append(DQResult("row_count", None, "warn", "0 rows loaded"))
    else:
        results.append(DQResult("row_count", None, "pass", f"{count} rows"))

    if quality_checks is None:
        quality_checks = {}

    # not_null checks (FR8.2)
    for col in quality_checks.get("not_null", []):
        null_count = con.execute(
            f'SELECT COUNT(*) FROM {target_table} WHERE "{col}" IS NULL'
        ).fetchone()[0]
        if null_count > 0:
            results.append(
                DQResult("not_null", col, "fail", f"{null_count} NULL values")
            )
        else:
            results.append(DQResult("not_null", col, "pass", "no NULLs"))

    # unique checks (FR8.3)
    for col in quality_checks.get("unique", []):
        dup_count = con.execute(
            f'SELECT COUNT(*) FROM (SELECT "{col}" FROM {target_table} '
            f'GROUP BY "{col}" HAVING COUNT(*) > 1)'
        ).fetchone()[0]
        if dup_count > 0:
            results.append(
                DQResult("unique", col, "fail", f"{dup_count} duplicate values")
            )
        else:
            results.append(DQResult("unique", col, "pass", "all unique"))

    # exact duplicate row check (config-driven)
    if quality_checks.get("duplicate"):
        total = con.execute(f"SELECT COUNT(*) FROM {target_table}").fetchone()[0]
        distinct = con.execute(
            f"SELECT COUNT(*) FROM (SELECT DISTINCT * FROM {target_table})"
        ).fetchone()[0]
        exact_dups = total - distinct
        if exact_dups > 0:
            results.append(
                DQResult(
                    "duplicate", None, "fail", f"{exact_dups} exact duplicate rows"
                )
            )
        else:
            results.append(DQResult("duplicate", None, "pass", "no exact duplicates"))

    # PK-based duplicate check (when primary_key configured)
    if primary_key:
        pk_cols = ", ".join(f'"{pk}"' for pk in primary_key)
        dup_count = con.execute(
            f"SELECT COUNT(*) FROM (SELECT {pk_cols} FROM {target_table} "
            f"GROUP BY {pk_cols} HAVING COUNT(*) > 1)"
        ).fetchone()[0]
        if dup_count > 0:
            results.append(
                DQResult(
                    "duplicate",
                    ",".join(primary_key),
                    "fail",
                    f"{dup_count} duplicate PK combinations",
                )
            )
        else:
            results.append(
                DQResult("duplicate", ",".join(primary_key), "pass", "no duplicates")
            )

    return results
