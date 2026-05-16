"""End-to-end pre-flight integration test against a stubbed SQL Server source.

Three scenarios exercising the full feather extract --plan-only path without
a live SQL Server connection.

Scenario 1 – SMALL table
    5 000 rows, 10 columns → SMALL bucket, single window, no blockers, exit 0.

Scenario 2 – LARGE table with connectorx available
    50 000 000 rows, 32 projection columns, connectorx registered, integer PK
    present for partition_on.  Banner shows bucket=LARGE, transport=connectorx,
    partition_on and partitions=8, 408 date windows, exit 0.

Scenario 3 – LARGE table without connectorx
    Same shape as Scenario 2 but TRANSPORT_CLASSES is monkeypatched to exclude
    connectorx → slow_transport blocker → exit 3, error names
    --accept-slow-transport.

All three tests use ``--plan-only`` so they never reach the real extract
pipeline; the stub source only needs to serve cheap_rowcount, discover(), and
get_window_range().
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import yaml
from typer.testing import CliRunner

from feather_etl.cli import app
from feather_etl.sources import StreamSchema
from feather_etl.state import StateManager

# ---------------------------------------------------------------------------
# Shared runner
# ---------------------------------------------------------------------------

runner = CliRunner()

# ---------------------------------------------------------------------------
# Stubbed SQL Server source
# ---------------------------------------------------------------------------

_SMALL_TABLE = "dbo.small_invoices"
_LARGE_TABLE = "dbo.large_invoices"

_SMALL_COLUMNS: list[tuple[str, str]] = [
    ("Invoice ID", "int"),
    ("customer_name", "varchar(100)"),
    ("amount", "decimal(18,2)"),
    ("status", "varchar(50)"),
    ("region", "varchar(50)"),
    ("product_code", "varchar(20)"),
    ("unit_price", "decimal(18,2)"),
    ("quantity", "int"),
    ("discount", "decimal(5,2)"),
    ("notes", "varchar(255)"),
]

# 32 columns: integer PK + 1 timestamp + 30 varchar.
# "Invoice ID" (int) = pk → partition_on, "created_at" = timestamp → window col.
_LARGE_COLUMNS: list[tuple[str, str]] = (
    [("Invoice ID", "int"), ("created_at", "datetime")]
    + [(f"field_{i}", "varchar(100)") for i in range(30)]
)

# Window range for LARGE scenarios: 2025-04-01 → 2026-05-13 → 408 date windows.
_WINDOW_START = datetime(2025, 4, 1)
_WINDOW_END = datetime(2026, 5, 13)


class StubbedSqlServerSource:
    """Minimal SQL Server stand-in: controlled rowcount / schema / window range.

    Registered as ``"stubbed_sqlserver"`` in the source registry for the
    duration of each test that uses it.  The ``from_yaml`` classmethod makes
    it compatible with feather_etl.sources.registry.get_source_class() and the
    YAML config ``type: stubbed_sqlserver`` pattern.
    """

    # Dialect used for SQL identifier quoting in windowing SQL.
    # Must be one of the dialects known to extract_windows._quote_for_dialect
    # ("sqlserver", "postgres", "mysql", "destination").
    # The source_type string "stubbed_sqlserver" is only used in the registry;
    # the planner reads source.type for SQL quoting.
    type = "sqlserver"
    name = "testsrc"
    database = "testdb"
    databases = None
    host = "localhost"
    port = 1433
    user = "sa"
    password = "pw"
    transport_name = "arrow-odbc"
    supports_partition_on = True  # exposes partition-on capability
    _rowcount_source = "estimate"

    def __init__(self, cheap_rows_map: dict[str, int]) -> None:
        self._cheap_rows_map = cheap_rows_map

    @classmethod
    def from_yaml(cls, entry: dict, config_dir: Path) -> "StubbedSqlServerSource":
        # Both small and large tables are always registered on this source;
        # cheap_rows_map is set by the fixture after instantiation.
        return cls(cheap_rows_map={_SMALL_TABLE: 5_000, _LARGE_TABLE: 50_000_000})

    # ------------------------------------------------------------------
    # Source protocol
    # ------------------------------------------------------------------

    def validate_source_table(self, table: str) -> list[str]:
        return []

    def check(self) -> bool:
        return True

    def discover(self) -> list[StreamSchema]:
        return [
            StreamSchema(
                name=_SMALL_TABLE,
                columns=_SMALL_COLUMNS,
                primary_key=["Invoice ID"],
                supports_incremental=True,
            ),
            StreamSchema(
                name=_LARGE_TABLE,
                columns=_LARGE_COLUMNS,
                primary_key=["Invoice ID"],
                supports_incremental=True,
            ),
        ]

    def cheap_rowcount(self, table: str) -> int:
        return self._cheap_rows_map[table]

    def get_window_range(self, table: str, column: str) -> tuple[datetime, datetime]:
        # Only meaningful for the large table.
        return (_WINDOW_START, _WINDOW_END)

    def detect_changes(self, table: str, last_state=None):
        from feather_etl.sources import ChangeResult

        return ChangeResult(changed=False, reason="unchanged")

    def extract(self, table: str, **kw):
        import pyarrow as pa

        return pa.table({"Invoice ID": [1]})

    def get_schema(self, table: str) -> list[tuple[str, str]]:
        if table == _SMALL_TABLE:
            return _SMALL_COLUMNS
        return _LARGE_COLUMNS


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_CURATION_ENTRY_DEFAULTS = {
    "decision": "include",
    "table_type": "fact",
    "group": "test",
    "classification_notes": None,
    "strategy": "full",
    "timestamp": None,
    "grain": None,
    "scd": None,
    "mapping": None,
    "dq_policy": None,
    "load_contract": None,
    "reason": "preflight integration test",
}


def _curation_entry(source_db: str, source_table: str, alias: str, pk: list[str]):
    return {
        "source_db": source_db,
        "source_table": source_table,
        "alias": alias,
        "primary_key": pk,
        **_CURATION_ENTRY_DEFAULTS,
    }


def _write_project(tmp_path: Path, tables: list[dict]) -> Path:
    """Write feather.yaml + curation.json + initialise state.  Return config path."""
    config = {
        "sources": [
            {
                "type": "stubbed_sqlserver",
                "name": "testsrc",
                "host": "localhost",
                "database": "testdb",
                "user": "sa",
                "password": "pw",
            }
        ],
        "destination": {
            "path": str(tmp_path / "feather_data.duckdb"),
        },
    }
    config_file = tmp_path / "feather.yaml"
    config_file.write_text(yaml.dump(config, default_flow_style=False))

    discovery_dir = tmp_path / "discovery"
    discovery_dir.mkdir(exist_ok=True)
    curation = {
        "version": 2,
        "updated_at": "2026-01-01T00:00:00Z",
        "notes": "preflight integration test",
        "source_systems": {},
        "policies": {"data_quality": {"default": "flag", "escalations": []}},
        "tables": tables,
    }
    (discovery_dir / "curation.json").write_text(json.dumps(curation))

    state = StateManager(tmp_path / "feather_state.duckdb")
    state.init_state()

    return config_file


def _register_stub_source():
    """Context manager: add StubbedSqlServerSource to SOURCE_CLASSES for the test."""
    from feather_etl.sources import registry as source_registry

    return patch.dict(
        source_registry.SOURCE_CLASSES,
        {"stubbed_sqlserver": "tests.integration.test_preflight_integration.StubbedSqlServerSource"},
    )


def _patch_resolve_stub(stub: StubbedSqlServerSource):
    """Patch both resolve_source call sites to return the stub."""
    return (
        patch(
            "feather_etl.commands._preflight.resolve_source",
            return_value=stub,
        ),
        patch(
            "feather_etl.extract.resolve_source",
            return_value=stub,
        ),
    )


# ---------------------------------------------------------------------------
# Scenario 1: SMALL table
# ---------------------------------------------------------------------------


def test_small_table_single_window_no_blockers(tmp_path: Path) -> None:
    """5 000-row / 10-col table → SMALL bucket, single window, exit 0."""
    tables = [
        _curation_entry("testdb", _SMALL_TABLE, "small_invoices", ["Invoice ID"]),
    ]
    config_file = _write_project(tmp_path, tables)

    stub = StubbedSqlServerSource(cheap_rows_map={_SMALL_TABLE: 5_000})

    preflight_patch, extract_patch = _patch_resolve_stub(stub)
    with _register_stub_source(), preflight_patch, extract_patch:
        result = runner.invoke(
            app,
            ["extract", "--config", str(config_file), "--plan-only"],
            catch_exceptions=False,
        )

    output = result.output
    assert result.exit_code == 0, f"Expected exit 0; got {result.exit_code}.\n{output}"
    assert "bucket" in output and "SMALL" in output, (
        f"Expected SMALL bucket in banner; got:\n{output}"
    )
    assert "single window" in output, (
        f"Expected 'single window' in banner; got:\n{output}"
    )
    # No blockers should appear.
    assert "Blocked" not in output, f"Unexpected blocker output:\n{output}"


# ---------------------------------------------------------------------------
# Scenario 2: LARGE table + connectorx available
# ---------------------------------------------------------------------------


def test_large_table_connectorx_available_banner(tmp_path: Path) -> None:
    """50 M-row / 32-col table with connectorx → banner shows connectorx + 8 partitions + 408 windows."""
    tables = [
        _curation_entry("testdb", _LARGE_TABLE, "large_invoices", ["Invoice ID"]),
    ]
    config_file = _write_project(tmp_path, tables)

    # Write a projection SQL with 32 columns so _count_projection_columns returns 32.
    bronze_dir = tmp_path / "transforms" / "bronze"
    bronze_dir.mkdir(parents=True)
    # Build SELECT with 32 comma-separated columns.
    projection_cols = ", ".join(col for col, _ in _LARGE_COLUMNS)
    (bronze_dir / "testdb_large_invoices.sql").write_text(
        f"SELECT {projection_cols}\nFROM large_invoices\n"
    )

    stub = StubbedSqlServerSource(cheap_rows_map={_LARGE_TABLE: 50_000_000})

    preflight_patch, extract_patch = _patch_resolve_stub(stub)

    # Ensure connectorx IS in the registered transports for this scenario.
    from feather_etl.transports import registry as transport_registry

    transports_with_connectorx = dict(transport_registry.TRANSPORT_CLASSES)
    if "connectorx" not in transports_with_connectorx:
        transports_with_connectorx["connectorx"] = (
            "feather_etl.transports.connectorx_transport.ConnectorxTransport"
        )

    with (
        _register_stub_source(),
        preflight_patch,
        extract_patch,
        patch.dict(transport_registry.TRANSPORT_CLASSES, transports_with_connectorx, clear=True),
    ):
        result = runner.invoke(
            app,
            ["extract", "--config", str(config_file), "--plan-only"],
            catch_exceptions=False,
        )

    output = result.output
    assert result.exit_code == 0, f"Expected exit 0; got {result.exit_code}.\n{output}"
    assert "LARGE" in output, f"Expected LARGE bucket; got:\n{output}"
    assert "connectorx" in output, f"Expected connectorx transport; got:\n{output}"
    # partition_on and partitions appear in the transport line.
    assert "partition_on=Invoice ID" in output, (
        f"Expected partition_on=Invoice ID; got:\n{output}"
    )
    assert "partitions=8" in output, f"Expected partitions=8; got:\n{output}"
    # 408 date windows for 2025-04-01 → 2026-05-13 with 1d grain.
    assert "408" in output, f"Expected 408 windows; got:\n{output}"
    assert "Blocked" not in output, f"Unexpected blocker output:\n{output}"


# ---------------------------------------------------------------------------
# Scenario 3: LARGE table without connectorx → slow_transport blocker → exit 3
# ---------------------------------------------------------------------------


def test_large_table_no_connectorx_exits_three(tmp_path: Path) -> None:
    """50 M-row table without connectorx → slow_transport blocker → exit 3."""
    tables = [
        _curation_entry("testdb", _LARGE_TABLE, "large_invoices", ["Invoice ID"]),
    ]
    config_file = _write_project(tmp_path, tables)

    stub = StubbedSqlServerSource(cheap_rows_map={_LARGE_TABLE: 50_000_000})

    preflight_patch, extract_patch = _patch_resolve_stub(stub)

    from feather_etl.transports import registry as transport_registry

    # Exclude connectorx so slow_transport blocker fires.
    transports_without_connectorx = {
        k: v
        for k, v in transport_registry.TRANSPORT_CLASSES.items()
        if k != "connectorx"
    }
    if not transports_without_connectorx:
        # Guarantee at least one transport so the planner has a default.
        transports_without_connectorx = {
            "arrow-odbc": "feather_etl.transports.arrow_odbc_transport.ArrowOdbcTransport"
        }

    with (
        _register_stub_source(),
        preflight_patch,
        extract_patch,
        patch.dict(transport_registry.TRANSPORT_CLASSES, transports_without_connectorx, clear=True),
    ):
        result = runner.invoke(
            app,
            ["extract", "--config", str(config_file)],
            catch_exceptions=False,
        )

    output = result.output
    assert result.exit_code == 3, (
        f"Expected exit 3 for slow_transport blocker; got {result.exit_code}.\n{output}"
    )
    assert "LARGE" in output, f"Expected LARGE bucket in banner; got:\n{output}"
    assert "--accept-slow-transport" in output, (
        f"Expected --accept-slow-transport in error output; got:\n{output}"
    )
