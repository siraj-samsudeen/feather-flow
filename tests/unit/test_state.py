"""Tests for feather.state module."""

import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import duckdb
import pytest


CURRENT_SCHEMA_VERSION = 2


class TestStateInit:
    def test_creates_all_tables(self, tmp_path: Path):
        from feather_etl.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()

        con = duckdb.connect(str(sm.path), read_only=True)
        tables = {
            r[0]
            for r in con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main'"
            ).fetchall()
        }
        con.close()
        expected = {
            "_state_meta",
            "_watermarks",
            "_runs",
            "_run_steps",
            "_dq_results",
            "_schema_snapshots",
        }
        assert tables == expected

    def test_watermarks_has_source_db_column(self, tmp_path: Path):
        """_watermarks includes source_db for extract-written rows."""
        from feather_etl.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()
        con = sm._connect()
        cols = {
            row[0]
            for row in con.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = '_watermarks' ORDER BY ordinal_position"
            ).fetchall()
        }
        con.close()
        assert "source_db" in cols

    def test_state_meta_version(self, tmp_path: Path):
        from feather_etl.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()

        con = duckdb.connect(str(sm.path), read_only=True)
        row = con.execute("SELECT schema_version FROM _state_meta").fetchone()
        con.close()
        assert row[0] == CURRENT_SCHEMA_VERSION

    def test_idempotent_init(self, tmp_path: Path):
        from feather_etl.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()
        sm.init_state()  # should not error or duplicate

        con = duckdb.connect(str(sm.path), read_only=True)
        count = con.execute("SELECT COUNT(*) FROM _state_meta").fetchone()[0]
        con.close()
        assert count == 1

    def test_v1_to_v2_migration(self, tmp_path: Path):
        """Upgrading a v1 state DB: _cache_watermarks rows land in _watermarks."""
        import feather_etl
        from datetime import datetime, timezone

        from feather_etl.state import StateManager

        state_path = tmp_path / "state.duckdb"
        con = duckdb.connect(str(state_path))

        # Build a v1-era schema manually (no source_db in _watermarks, separate _cache_watermarks)
        con.execute("""
            CREATE TABLE _state_meta (
                schema_version INTEGER PRIMARY KEY,
                created_at TIMESTAMP,
                feather_version VARCHAR
            )
        """)
        con.execute("INSERT INTO _state_meta VALUES (1, now(), '0.0.1')")

        con.execute("""
            CREATE TABLE _watermarks (
                table_name VARCHAR PRIMARY KEY,
                strategy VARCHAR,
                last_value VARCHAR,
                last_checksum VARCHAR,
                last_row_count INTEGER,
                last_file_mtime DOUBLE,
                last_file_hash VARCHAR,
                last_run_at TIMESTAMP,
                retry_count INTEGER DEFAULT 0,
                retry_after TIMESTAMP,
                boundary_hashes JSON
            )
        """)

        con.execute("""
            CREATE TABLE _cache_watermarks (
                table_name VARCHAR PRIMARY KEY,
                source_db VARCHAR,
                last_file_mtime DOUBLE,
                last_file_hash VARCHAR,
                last_checksum VARCHAR,
                last_row_count INTEGER,
                last_run_at TIMESTAMP
            )
        """)
        con.execute(
            "INSERT INTO _cache_watermarks VALUES (?, ?, ?, ?, ?, ?, ?)",
            ["orders", "erp", 1.0, "abc", None, 10, datetime.now(timezone.utc)],
        )
        con.close()

        # Run init_state — should trigger migration
        sm = StateManager(state_path)
        sm.init_state()

        # Verify migration outcome
        con2 = duckdb.connect(str(state_path))

        # _cache_watermarks is gone
        tables = {
            r[0]
            for r in con2.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
            ).fetchall()
        }
        assert "_cache_watermarks" not in tables

        # The row is now in _watermarks with source_db set
        row = con2.execute(
            "SELECT table_name, source_db FROM _watermarks WHERE table_name = 'orders'"
        ).fetchone()
        assert row is not None
        assert row[1] == "erp"

        # Schema version is now 2
        version = con2.execute("SELECT schema_version FROM _state_meta").fetchone()[0]
        assert version == 2

        con2.close()

    def test_downgrade_protection(self, tmp_path: Path):
        from feather_etl.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()

        # Simulate a future version writing version=99
        con = duckdb.connect(str(sm.path))
        con.execute("UPDATE _state_meta SET schema_version = 99")
        con.close()

        with pytest.raises(RuntimeError, match="newer than feather-etl"):
            sm.init_state()

    def test_new_state_db_gets_600_permissions(self, tmp_path: Path):
        from feather_etl.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()
        mode = stat.S_IMODE(os.stat(sm.path).st_mode)
        assert mode == 0o600

    def test_chmod_oserror_is_swallowed(self, tmp_path: Path):
        from feather_etl.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        with patch("feather_etl.state.os.chmod", side_effect=OSError("denied")):
            sm.init_state()  # should not raise
        assert sm.path.exists()


class TestWatermarks:
    def test_write_and_read(self, tmp_path: Path):
        from feather_etl.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()

        sm.write_watermark("sales_invoice", strategy="full")
        wm = sm.read_watermark("sales_invoice")
        assert wm is not None
        assert wm["table_name"] == "sales_invoice"
        assert wm["strategy"] == "full"

    def test_read_nonexistent(self, tmp_path: Path):
        from feather_etl.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()
        assert sm.read_watermark("nonexistent") is None

    def test_upsert_watermark(self, tmp_path: Path):
        from feather_etl.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()

        sm.write_watermark("test_table", strategy="full")
        sm.write_watermark("test_table", strategy="full")

        con = duckdb.connect(str(sm.path), read_only=True)
        count = con.execute(
            "SELECT COUNT(*) FROM _watermarks WHERE table_name = 'test_table'"
        ).fetchone()[0]
        con.close()
        assert count == 1

    def test_write_watermark_with_mtime_and_hash(self, tmp_path: Path):
        from feather_etl.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()

        sm.write_watermark(
            "test_table",
            strategy="full",
            last_file_mtime=1234567890.5,
            last_file_hash="abc123def456",
        )
        wm = sm.read_watermark("test_table")
        assert wm["last_file_mtime"] == 1234567890.5
        assert wm["last_file_hash"] == "abc123def456"

    def test_write_watermark_update_preserves_mtime_hash(self, tmp_path: Path):
        from feather_etl.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()

        sm.write_watermark(
            "test_table",
            strategy="full",
            last_file_mtime=100.0,
            last_file_hash="hash1",
        )
        sm.write_watermark(
            "test_table",
            strategy="full",
            last_file_mtime=200.0,
            last_file_hash="hash2",
        )
        wm = sm.read_watermark("test_table")
        assert wm["last_file_mtime"] == 200.0
        assert wm["last_file_hash"] == "hash2"

    def test_write_watermark_without_mtime_hash_stays_null(self, tmp_path: Path):
        from feather_etl.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()

        sm.write_watermark("test_table", strategy="full")
        wm = sm.read_watermark("test_table")
        assert wm["last_file_mtime"] is None
        assert wm["last_file_hash"] is None

    def test_write_watermark_preserves_last_value_on_update(self, tmp_path: Path):
        """H-1: write_watermark() without last_value must preserve existing last_value."""
        from feather_etl.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()

        # First write: set last_value
        sm.write_watermark(
            "test_table",
            strategy="incremental",
            last_value="2026-03-01T00:00:00",
            last_file_mtime=100.0,
            last_file_hash="hash1",
        )
        wm = sm.read_watermark("test_table")
        assert wm["last_value"] == "2026-03-01T00:00:00"

        # Second write: touch-skip path — no last_value provided
        sm.write_watermark(
            "test_table",
            strategy="incremental",
            last_file_mtime=200.0,
            last_file_hash="hash1",
        )
        wm = sm.read_watermark("test_table")
        # last_value must survive — not be nulled
        assert wm["last_value"] == "2026-03-01T00:00:00"

    def test_write_watermark_updates_last_value_when_provided(self, tmp_path: Path):
        """H-1: write_watermark() with explicit last_value should update it."""
        from feather_etl.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()

        sm.write_watermark(
            "test_table",
            strategy="incremental",
            last_value="2026-03-01T00:00:00",
        )
        sm.write_watermark(
            "test_table",
            strategy="incremental",
            last_value="2026-03-15T00:00:00",
        )
        wm = sm.read_watermark("test_table")
        assert wm["last_value"] == "2026-03-15T00:00:00"


class TestConnectionCleanup:
    """M-2: DB connections must be closed even on exception paths."""

    def test_read_watermark_closes_on_error(self, tmp_path: Path):
        from unittest.mock import MagicMock

        from feather_etl.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()

        mock_con = MagicMock()
        mock_con.execute.side_effect = RuntimeError("db error")
        sm._connect = lambda: mock_con

        with pytest.raises(RuntimeError, match="db error"):
            sm.read_watermark("test")

        mock_con.close.assert_called_once()

    def test_write_watermark_closes_on_error(self, tmp_path: Path):
        from unittest.mock import MagicMock

        from feather_etl.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()

        mock_con = MagicMock()
        mock_con.execute.side_effect = RuntimeError("db error")
        sm._connect = lambda: mock_con

        with pytest.raises(RuntimeError, match="db error"):
            sm.write_watermark("test", strategy="full")

        mock_con.close.assert_called_once()

    def test_record_run_closes_on_error(self, tmp_path: Path):
        from unittest.mock import MagicMock

        from feather_etl.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()

        mock_con = MagicMock()
        mock_con.execute.side_effect = RuntimeError("db error")
        sm._connect = lambda: mock_con

        now = datetime.now(timezone.utc)
        with pytest.raises(RuntimeError, match="db error"):
            sm.record_run(
                run_id="r1",
                table_name="t1",
                started_at=now,
                ended_at=now,
                status="success",
            )

        mock_con.close.assert_called_once()

    def test_get_status_closes_on_error(self, tmp_path: Path):
        from unittest.mock import MagicMock

        from feather_etl.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()

        mock_con = MagicMock()
        mock_con.execute.side_effect = RuntimeError("db error")
        sm._connect = lambda: mock_con

        with pytest.raises(RuntimeError, match="db error"):
            sm.get_status()

        mock_con.close.assert_called_once()


class TestRuns:
    def test_record_and_get_status(self, tmp_path: Path):
        from feather_etl.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()

        now = datetime.now(timezone.utc)
        sm.record_run(
            run_id="sales_invoice_2025-01-01T00:00:00",
            table_name="sales_invoice",
            started_at=now,
            ended_at=now,
            status="success",
            rows_extracted=100,
            rows_loaded=100,
        )

        status = sm.get_status()
        assert len(status) == 1
        assert status[0]["table_name"] == "sales_invoice"
        assert status[0]["status"] == "success"
        assert status[0]["rows_loaded"] == 100


class TestCacheWatermarks:
    """Cache-scoped watermark methods — fully isolated from _watermarks."""

    def test_read_returns_none_when_absent(self, tmp_path: Path):
        from feather_etl.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()
        assert sm.read_cache_watermark("nonexistent") is None

    def test_write_then_read_roundtrip(self, tmp_path: Path):
        from datetime import datetime, timezone
        from feather_etl.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()
        now = datetime.now(timezone.utc)
        sm.write_cache_watermark(
            table_name="afans_sales",
            source_db="afans",
            last_run_at=now,
            last_file_mtime=1234567890.5,
            last_file_hash="deadbeef",
            last_checksum="abc123",
            last_row_count=42,
        )
        row = sm.read_cache_watermark("afans_sales")
        assert row is not None
        assert row["table_name"] == "afans_sales"
        assert row["source_db"] == "afans"
        assert row["last_file_mtime"] == 1234567890.5
        assert row["last_file_hash"] == "deadbeef"
        assert row["last_checksum"] == "abc123"
        assert row["last_row_count"] == 42

    def test_write_upserts_existing_row(self, tmp_path: Path):
        from datetime import datetime, timezone
        from feather_etl.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()
        t1 = datetime(2026, 4, 1, tzinfo=timezone.utc)
        t2 = datetime(2026, 4, 2, tzinfo=timezone.utc)

        sm.write_cache_watermark(
            table_name="t",
            source_db="db",
            last_run_at=t1,
            last_file_hash="old",
        )
        sm.write_cache_watermark(
            table_name="t",
            source_db="db",
            last_run_at=t2,
            last_file_hash="new",
        )
        row = sm.read_cache_watermark("t")
        assert row["last_file_hash"] == "new"
        # Only one row
        import duckdb

        con = duckdb.connect(str(sm.path), read_only=True)
        count = con.execute(
            "SELECT COUNT(*) FROM _watermarks WHERE table_name = 't'"
        ).fetchone()[0]
        con.close()
        assert count == 1

    def test_write_cache_watermark_writes_to_watermarks(self, tmp_path: Path):
        """write_cache_watermark delegates to write_watermark (unified table)."""
        from datetime import datetime, timezone
        from feather_etl.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()
        sm.write_cache_watermark(
            "orders", source_db="erp", last_run_at=datetime.now(timezone.utc)
        )
        row = sm.read_watermark("orders")
        assert row is not None
        assert row["source_db"] == "erp"

    def test_write_cache_watermark_normalizes_int_checksum_to_str(self, tmp_path: Path):
        """Pins the documented boundary: int checksums (SQL Server CHECKSUM_AGG)
        are stored as str so the VARCHAR column accepts them uniformly with
        Postgres md5() hex strings."""
        from datetime import datetime, timezone
        from feather_etl.state import StateManager

        sm = StateManager(tmp_path / "state.duckdb")
        sm.init_state()
        sm.write_cache_watermark(
            table_name="mssql_like",
            source_db="db",
            last_run_at=datetime.now(timezone.utc),
            last_checksum=12345,  # int, as SQL Server would emit
        )
        row = sm.read_cache_watermark("mssql_like")
        assert row["last_checksum"] == "12345"
        assert isinstance(row["last_checksum"], str)
