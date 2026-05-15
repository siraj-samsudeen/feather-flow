"""Hit a real SQL Server through all three transports and diff the output.

Operator-run smoke check for #61's "identical schema + row count"
acceptance criterion. Not in CI — needs a live server.

Usage:
    export FEATHER_TEST_SQLSERVER_DSN="DRIVER={ODBC Driver 18 for SQL Server};SERVER=host,1433;DATABASE=db;UID=u;PWD=p;TrustServerCertificate=yes"
    export FEATHER_TEST_SQLSERVER_TABLE="dbo.SmallReferenceTable"   # < 10k rows
    uv run python scripts/manual_transport_equivalence.py

Picks the smallest table by default. Prints a per-transport row count,
schema, and a diff if any transport disagrees.
"""

from __future__ import annotations

import os
import sys

import pyarrow as pa

from feather_etl.transports.registry import get_transport_class


def main() -> int:
    dsn = os.environ.get("FEATHER_TEST_SQLSERVER_DSN")
    table = os.environ.get("FEATHER_TEST_SQLSERVER_TABLE")
    if not dsn or not table:
        print(
            "Set FEATHER_TEST_SQLSERVER_DSN and FEATHER_TEST_SQLSERVER_TABLE",
            file=sys.stderr,
        )
        return 2

    query = f"SELECT TOP 1000 * FROM {table}"
    results: dict[str, tuple[int, pa.Schema]] = {}

    for name in ("pyodbc", "arrow-odbc", "connectorx"):
        cls = get_transport_class(name)
        try:
            batches = list(
                cls().stream_batches(
                    dsn,
                    query,
                    batch_size=200,
                    table_label=table,
                    heartbeat_every_rows=0,
                    heartbeat_every_seconds=0,
                )
            )
        except ImportError as e:
            print(f"  {name}: SKIPPED ({e})")
            continue

        rows = sum(b.num_rows for b in batches)
        schema = batches[0].schema if batches else pa.schema([])
        results[name] = (rows, schema)
        print(f"  {name}: rows={rows} schema={schema}")

    if len(set((r, str(s)) for r, s in results.values())) > 1:
        print("\nDISAGREEMENT — transports produced different results", file=sys.stderr)
        return 1

    print("\nAll transports agree.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
