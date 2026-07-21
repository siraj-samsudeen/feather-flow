"""Integration: end-to-end pipeline scenarios against client and sample_erp
DuckDB fixtures.

Covers full-pipeline runs, error isolation, validation guards, CSV sources,
SQLite sources, and the open-bug regression suite. This is the largest
integration file — classes are preserved for semantic grouping across
the ~9 concerns covered (Rule M2 exception: multiple classes share a
per-class ``config`` fixture that would be awkward to inline across 33
methods; classes double as a TOC).

Fixture layout
--------------
tests/fixtures/sample_erp.duckdb  -- synthetic ERP (erp schema, 3 tables, 12 rows)
tests/fixtures/client.duckdb      -- real Icube ERP data (icube schema, 6 tables)
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
import yaml

from tests.helpers import make_curation_entry, write_curation
from tests.integration.conftest import ProjectFixture


# ---------------------------------------------------------------------------
# Local helpers
# ---------------------------------------------------------------------------


def _write_duckdb_config(
    project: ProjectFixture,
    source_db: Path,
    curation_tables: list[dict],
    source_name: str = "src",
) -> Path:
    """Write a DuckDB-source feather.yaml + curation.json into ``project``."""
    project.write_config(
        sources=[{"type": "duckdb", "name": source_name, "path": str(source_db)}],
        destination={"path": str(project.data_db_path)},
    )
    write_curation(project.root, curation_tables)
    return project.config_path


def _write_csv_config(
    project: ProjectFixture,
    csv_dir: Path,
    curation_tables: list[dict],
    source_name: str = "csvs",
) -> Path:
    project.write_config(
        sources=[{"type": "csv", "name": source_name, "path": str(csv_dir)}],
        destination={"path": str(project.data_db_path)},
    )
    write_curation(project.root, curation_tables)
    return project.config_path


def _write_sqlite_config(
    project: ProjectFixture,
    sqlite_path: Path,
    curation_tables: list[dict],
    source_name: str = "sqlitedb",
) -> Path:
    project.write_config(
        sources=[{"type": "sqlite", "name": source_name, "path": str(sqlite_path)}],
        destination={"path": str(project.data_db_path)},
    )
    write_curation(project.root, curation_tables)
    return project.config_path


# ---------------------------------------------------------------------------
# sample_erp fixture -- basic correctness
# ---------------------------------------------------------------------------


class TestSampleErpFixture:
    """Tests using the synthetic sample_erp fixture.

    These are meta-assertions about fixture data integrity — they don't
    import feather_flow but they guard every other test in this file.
    """

    @pytest.fixture
    def sample_erp(self, project: ProjectFixture) -> Path:
        return project.copy_fixture("sample_erp.duckdb")

    def test_fixture_has_expected_tables(self, sample_erp):
        con = duckdb.connect(str(sample_erp), read_only=True)
        tables = {
            (r[0], r[1])
            for r in con.execute(
                "SELECT table_schema, table_name FROM information_schema.tables "
                "WHERE table_schema NOT IN ('information_schema','pg_catalog')"
            ).fetchall()
        }
        con.close()
        assert ("erp", "orders") in tables
        assert ("erp", "customers") in tables
        assert ("erp", "products") in tables

    def test_fixture_row_counts(self, sample_erp):
        con = duckdb.connect(str(sample_erp), read_only=True)
        assert con.execute("SELECT COUNT(*) FROM erp.orders").fetchone()[0] == 5
        assert con.execute("SELECT COUNT(*) FROM erp.customers").fetchone()[0] == 4
        assert con.execute("SELECT COUNT(*) FROM erp.products").fetchone()[0] == 3
        con.close()

    def test_fixture_null_in_products(self, sample_erp):
        """stock_qty is NULL for the 'Service Pack' row -- tests NULL pass-through."""
        con = duckdb.connect(str(sample_erp), read_only=True)
        row = con.execute(
            "SELECT stock_qty FROM erp.products WHERE product_name = 'Service Pack'"
        ).fetchone()
        con.close()
        assert row is not None
        assert row[0] is None


class TestSampleErpFullPipeline:
    """Full pipeline run against the synthetic sample_erp fixture."""

    @pytest.fixture
    def config(self, project: ProjectFixture) -> Path:
        source_db = project.copy_fixture("sample_erp.duckdb")
        return _write_duckdb_config(
            project,
            source_db,
            [
                make_curation_entry("src", "erp.orders", "orders"),
                make_curation_entry("src", "erp.customers", "customers"),
                make_curation_entry("src", "erp.products", "products"),
            ],
        )

    def test_run_all_succeeds(self, config):
        from feather_flow.config import load_config
        from feather_flow.pipeline import run_all

        cfg = load_config(config)
        results = run_all(cfg)
        assert all(r.status == "success" for r in results)
        assert {r.table_name for r in results} == {
            "src_orders",
            "src_customers",
            "src_products",
        }

    def test_row_counts_in_bronze(self, config):
        from feather_flow.config import load_config
        from feather_flow.pipeline import run_all

        cfg = load_config(config)
        run_all(cfg)

        con = duckdb.connect(str(cfg.destination.path), read_only=True)
        assert con.execute("SELECT COUNT(*) FROM bronze.src_orders").fetchone()[0] == 5
        assert (
            con.execute("SELECT COUNT(*) FROM bronze.src_customers").fetchone()[0] == 4
        )
        assert (
            con.execute("SELECT COUNT(*) FROM bronze.src_products").fetchone()[0] == 3
        )
        con.close()

    def test_null_passthrough(self, config):
        """NULL in source (products.stock_qty) must survive the Arrow round-trip."""
        from feather_flow.config import load_config
        from feather_flow.pipeline import run_all

        cfg = load_config(config)
        run_all(cfg)

        con = duckdb.connect(str(cfg.destination.path), read_only=True)
        row = con.execute(
            "SELECT stock_qty FROM bronze.src_products WHERE product_name = 'Service Pack'"
        ).fetchone()
        con.close()
        assert row is not None, "Service Pack row missing from bronze.src_products"
        assert row[0] is None, f"Expected NULL stock_qty, got {row[0]!r}"

    def test_etl_metadata_columns_added(self, config):
        from feather_flow.config import load_config
        from feather_flow.pipeline import run_all

        cfg = load_config(config)
        run_all(cfg)

        con = duckdb.connect(str(cfg.destination.path), read_only=True)
        cols = {
            r[0]
            for r in con.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'bronze' AND table_name = 'src_orders'"
            ).fetchall()
        }
        con.close()
        assert "_etl_loaded_at" in cols
        assert "_etl_run_id" in cols

    def test_source_columns_preserved(self, config):
        """All source columns (minus added ETL ones) must appear in bronze."""
        from feather_flow.config import load_config
        from feather_flow.pipeline import run_all

        cfg = load_config(config)
        run_all(cfg)

        con = duckdb.connect(str(cfg.destination.path), read_only=True)
        bronze_cols = {
            r[0]
            for r in con.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'bronze' AND table_name = 'src_orders'"
            ).fetchall()
        }
        con.close()
        expected = {
            "order_id",
            "customer_id",
            "order_date",
            "total_amount",
            "status",
            "created_at",
        }
        assert expected.issubset(bronze_cols)

    def test_idempotency(self, config):
        """Running twice must give same row counts (full swap replaces, not appends)."""
        from feather_flow.config import load_config
        from feather_flow.pipeline import run_all

        cfg = load_config(config)
        run_all(cfg)
        run_all(cfg)

        con = duckdb.connect(str(cfg.destination.path), read_only=True)
        assert con.execute("SELECT COUNT(*) FROM bronze.src_orders").fetchone()[0] == 5
        con.close()

    def test_state_records_runs(self, config):
        from feather_flow.config import load_config
        from feather_flow.pipeline import run_all

        cfg = load_config(config)
        run_all(cfg)

        state_path = config.parent / "feather_state.duckdb"
        assert state_path.exists()
        con = duckdb.connect(str(state_path), read_only=True)
        runs = con.execute(
            "SELECT table_name, status, rows_loaded FROM _runs ORDER BY table_name"
        ).fetchall()
        con.close()
        run_map = {r[0]: (r[1], r[2]) for r in runs}
        assert run_map["src_orders"] == ("success", 5)
        assert run_map["src_customers"] == ("success", 4)
        assert run_map["src_products"] == ("success", 3)


# ---------------------------------------------------------------------------
# Icube client fixture -- edge cases
# ---------------------------------------------------------------------------


class TestClientFixtureEdgeCases:
    @pytest.fixture
    def client_db_copy(self, project: ProjectFixture) -> Path:
        return project.copy_fixture("client.duckdb")

    def test_salesinvoicemaster_column_with_space(
        self, project: ProjectFixture, client_db_copy: Path
    ):
        """SALESINVOICEMASTER has a column called 'Round Off' (space in name)."""
        from feather_flow.config import load_config
        from feather_flow.pipeline import run_all

        config = _write_duckdb_config(
            project,
            client_db_copy,
            [make_curation_entry("src", "icube.SALESINVOICEMASTER", "sim")],
        )
        cfg = load_config(config)
        results = run_all(cfg)
        assert results[0].status == "success"

        con = duckdb.connect(str(cfg.destination.path), read_only=True)
        cols = {
            r[0]
            for r in con.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='bronze' AND table_name='src_sim'"
            ).fetchall()
        }
        con.close()
        assert "Round Off" in cols, "'Round Off' column (space) should be preserved"

    def test_invitem_blob_columns(self, project: ProjectFixture, client_db_copy: Path):
        """INVITEM has BLOB columns (IMAGE, DivImage).  Should load without error."""
        from feather_flow.config import load_config
        from feather_flow.pipeline import run_all

        config = _write_duckdb_config(
            project,
            client_db_copy,
            [make_curation_entry("src", "icube.INVITEM", "invitem")],
        )
        cfg = load_config(config)
        results = run_all(cfg)
        assert results[0].status == "success"
        assert results[0].rows_loaded == 1058

    def test_all_six_icube_tables(self, project: ProjectFixture, client_db_copy: Path):
        """All six Icube tables load in a single run."""
        from feather_flow.config import load_config
        from feather_flow.pipeline import run_all

        config = _write_duckdb_config(
            project,
            client_db_copy,
            [
                make_curation_entry("src", "icube.SALESINVOICE", "sales_invoice"),
                make_curation_entry("src", "icube.CUSTOMERMASTER", "customer_master"),
                make_curation_entry("src", "icube.InventoryGroup", "inventory_group"),
                make_curation_entry("src", "icube.INVITEM", "inv_item"),
                make_curation_entry(
                    "src", "icube.SALESINVOICEMASTER", "sales_invoice_master"
                ),
                make_curation_entry("src", "icube.EmployeeForm", "employee"),
            ],
        )
        cfg = load_config(config)
        results = run_all(cfg)

        assert all(r.status == "success" for r in results), [
            (r.table_name, r.error_message) for r in results if r.status != "success"
        ]
        row_map = {r.table_name: r.rows_loaded for r in results}
        assert row_map["src_sales_invoice"] == 11676
        assert row_map["src_customer_master"] == 1339
        assert row_map["src_inventory_group"] == 66
        assert row_map["src_inv_item"] == 1058
        assert row_map["src_sales_invoice_master"] == 335
        assert row_map["src_employee"] == 55

    def test_discover_icube_source(self, client_db_copy: Path):
        """discover() returns all 6 Icube tables with column metadata."""
        from feather_flow.sources.duckdb_file import DuckDBFileSource

        src = DuckDBFileSource(client_db_copy)
        schemas = src.discover()
        names = {s.name for s in schemas}
        assert "icube.SALESINVOICE" in names
        assert "icube.CUSTOMERMASTER" in names
        assert len(schemas) == 6
        for s in schemas:
            assert len(s.columns) > 0, f"{s.name} has no columns"


# ---------------------------------------------------------------------------
# Error isolation -- a bad table must not stop good tables
# ---------------------------------------------------------------------------


class TestErrorIsolation:
    @pytest.fixture
    def sample_erp(self, project: ProjectFixture) -> Path:
        return project.copy_fixture("sample_erp.duckdb")

    def test_good_table_succeeds_despite_bad_table(
        self, project: ProjectFixture, sample_erp: Path
    ):
        from feather_flow.config import load_config
        from feather_flow.pipeline import run_all

        config = _write_duckdb_config(
            project,
            sample_erp,
            [
                make_curation_entry("src", "erp.orders", "good"),
                make_curation_entry("src", "erp.NOSUCH", "bad"),
            ],
        )
        cfg = load_config(config)
        results = run_all(cfg)

        good = next(r for r in results if r.table_name == "src_good")
        bad = next(r for r in results if r.table_name == "src_bad")
        assert good.status == "success", "good table should succeed"
        assert bad.status == "failure", "bad table should fail"
        assert good.rows_loaded == 5

    def test_failure_error_message_stored_in_state(
        self, project: ProjectFixture, sample_erp: Path
    ):
        from feather_flow.config import load_config
        from feather_flow.pipeline import run_all

        config = _write_duckdb_config(
            project,
            sample_erp,
            [make_curation_entry("src", "erp.NOSUCH", "bad")],
        )
        cfg = load_config(config)
        run_all(cfg)

        state_path = project.state_db_path
        con = duckdb.connect(str(state_path), read_only=True)
        row = con.execute(
            "SELECT status, error_message FROM _runs WHERE table_name = 'src_bad'"
        ).fetchone()
        con.close()
        assert row is not None
        assert row[0] == "failure"
        assert row[1] is not None and len(row[1]) > 0, (
            "error_message should be non-empty"
        )

    def test_partial_failure_still_writes_good_table_to_bronze(
        self, project: ProjectFixture, sample_erp: Path
    ):
        from feather_flow.config import load_config
        from feather_flow.pipeline import run_all

        config = _write_duckdb_config(
            project,
            sample_erp,
            [
                make_curation_entry("src", "erp.orders", "good"),
                make_curation_entry("src", "erp.NOSUCH", "bad"),
            ],
        )
        cfg = load_config(config)
        run_all(cfg)

        con = duckdb.connect(str(cfg.destination.path), read_only=True)
        assert con.execute("SELECT COUNT(*) FROM bronze.src_good").fetchone()[0] == 5
        tables = {
            r[0]
            for r in con.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='bronze'"
            ).fetchall()
        }
        con.close()
        assert "src_good" in tables
        assert "src_bad" not in tables


# ---------------------------------------------------------------------------
# Validation guards -- graduated from TestKnownBugs after fixes
# ---------------------------------------------------------------------------


class TestValidationGuards:
    """Tests that validate catches invalid config before runtime."""

    def test_csv_source_validates_with_valid_directory(self, project: ProjectFixture):
        """CSV source type with a valid directory passes validation."""
        from feather_flow.config import load_config

        csv_dir = project.root / "csv_data"
        csv_dir.mkdir()
        (csv_dir / "orders.csv").write_text("id,name\n1,foo\n")
        project.write_config(
            sources=[{"type": "csv", "name": "csvs", "path": str(csv_dir)}],
            destination={"path": str(project.data_db_path)},
        )
        write_curation(
            project.root,
            [make_curation_entry("csvs", "orders.csv", "orders")],
        )
        cfg = load_config(project.config_path)
        assert cfg.sources[0].type == "csv"

    def test_csv_source_rejects_file_path(self, project: ProjectFixture):
        """CSV source path must be a directory, not a file."""
        from feather_flow.config import load_config

        csv_file = project.root / "source.csv"
        csv_file.write_text("id,name\n1,foo\n")
        project.write_config(
            sources=[{"type": "csv", "name": "csvs", "path": str(csv_file)}],
            destination={"path": str(project.data_db_path)},
        )
        write_curation(
            project.root,
            [make_curation_entry("csvs", "orders.csv", "orders")],
        )
        with pytest.raises(ValueError, match="CSV source path must be a directory"):
            load_config(project.config_path)

    def test_csv_source_rejects_nonexistent_path(self, project: ProjectFixture):
        """CSV source path that doesn't exist at all fails validation."""
        from feather_flow.config import load_config

        project.write_config(
            sources=[
                {
                    "type": "csv",
                    "name": "csvs",
                    "path": str(project.root / "no_such_dir"),
                }
            ],
            destination={"path": str(project.data_db_path)},
        )
        write_curation(
            project.root,
            [make_curation_entry("csvs", "orders.csv", "orders")],
        )
        with pytest.raises(ValueError, match="CSV source path must be a directory"):
            load_config(project.config_path)

    def test_missing_source_section_raises_valueerror(self, project: ProjectFixture):
        from feather_flow.config import load_config

        # Bypass ``write_config``'s tolerant behaviour and write raw YAML with
        # no sources section so load_config can raise.
        project.config_path.write_text(
            yaml.dump({"destination": {"path": str(project.data_db_path)}})
        )
        with pytest.raises(ValueError, match="Missing required config section"):
            load_config(project.config_path)

    def test_target_table_requires_schema_prefix(self, project: ProjectFixture):
        """target_table without schema prefix is rejected (tested via _validate)."""
        from feather_flow.config import _validate, load_config

        sample_erp = project.copy_fixture("sample_erp.duckdb")
        config = _write_duckdb_config(
            project,
            sample_erp,
            [make_curation_entry("src", "erp.orders", "orders")],
        )
        cfg = load_config(config, validate=False)
        cfg.tables[0].target_table = "no_schema_table"
        errors = _validate(cfg)
        assert any("must include a schema prefix" in e for e in errors)

    def test_hyphenated_target_table_rejected(self, project: ProjectFixture):
        """Hyphens in target_table are caught at validate time (tested via _validate)."""
        from feather_flow.config import _validate, load_config

        sample_erp = project.copy_fixture("sample_erp.duckdb")
        config = _write_duckdb_config(
            project,
            sample_erp,
            [make_curation_entry("src", "erp.orders", "orders")],
        )
        cfg = load_config(config, validate=False)
        cfg.tables[0].target_table = "bronze.my-table"
        errors = _validate(cfg)
        assert any("invalid characters" in e for e in errors)


# ---------------------------------------------------------------------------
# Error isolation (additional graduated tests)
# ---------------------------------------------------------------------------


class TestPipelineReturnsOnFailure:
    def test_run_all_returns_results_on_total_failure(self, project: ProjectFixture):
        from feather_flow.config import load_config
        from feather_flow.pipeline import run_all

        sample_erp = project.copy_fixture("sample_erp.duckdb")
        config = _write_duckdb_config(
            project,
            sample_erp,
            [make_curation_entry("src", "erp.NOSUCH", "bad")],
        )
        cfg = load_config(config)
        results = run_all(cfg)
        assert len(results) == 1
        assert results[0].status == "failure"


# ---------------------------------------------------------------------------
# Known bugs -- remaining issues not yet fixed
# ---------------------------------------------------------------------------


class TestKnownBugs:
    def test_M6_incremental_strategy_silently_does_full_load(
        self, project: ProjectFixture
    ):
        """M-6: strategy='incremental' silently does a full load."""
        from feather_flow.config import load_config
        from feather_flow.pipeline import run_all

        sample_erp = project.copy_fixture("sample_erp.duckdb")
        config = _write_duckdb_config(
            project,
            sample_erp,
            [
                make_curation_entry(
                    "src",
                    "erp.orders",
                    "orders",
                    strategy="incremental",
                    timestamp_column="created_at",
                    primary_key=["order_id"],
                ),
            ],
        )
        cfg = load_config(config)
        results = run_all(cfg)
        assert results[0].status == "success"
        con = duckdb.connect(str(cfg.destination.path), read_only=True)
        count = con.execute("SELECT COUNT(*) FROM bronze.src_orders").fetchone()[0]
        con.close()
        assert count == 5


# ---------------------------------------------------------------------------
# CSV source -- full pipeline integration
# ---------------------------------------------------------------------------


class TestCsvFullPipeline:
    """Full pipeline run using CSV source."""

    CSV_CURATION = [
        make_curation_entry("csvs", "orders.csv", "orders"),
        make_curation_entry("csvs", "customers.csv", "customers"),
        make_curation_entry("csvs", "products.csv", "products"),
    ]

    @pytest.fixture
    def config(self, project: ProjectFixture) -> Path:
        csv_dir = project.copy_fixture("csv_data")
        return _write_csv_config(project, csv_dir, self.CSV_CURATION)

    def test_run_all_succeeds(self, config):
        from feather_flow.config import load_config
        from feather_flow.pipeline import run_all

        cfg = load_config(config)
        results = run_all(cfg)
        assert all(r.status == "success" for r in results)
        assert {r.table_name for r in results} == {
            "csvs_orders",
            "csvs_customers",
            "csvs_products",
        }

    def test_row_counts_in_bronze(self, config):
        from feather_flow.config import load_config
        from feather_flow.pipeline import run_all

        cfg = load_config(config)
        run_all(cfg)

        con = duckdb.connect(str(cfg.destination.path), read_only=True)
        assert con.execute("SELECT COUNT(*) FROM bronze.csvs_orders").fetchone()[0] == 5
        assert (
            con.execute("SELECT COUNT(*) FROM bronze.csvs_customers").fetchone()[0] == 4
        )
        assert (
            con.execute("SELECT COUNT(*) FROM bronze.csvs_products").fetchone()[0] == 3
        )
        con.close()

    def test_null_passthrough(self, config):
        from feather_flow.config import load_config
        from feather_flow.pipeline import run_all

        cfg = load_config(config)
        run_all(cfg)

        con = duckdb.connect(str(cfg.destination.path), read_only=True)
        row = con.execute(
            "SELECT stock_qty FROM bronze.csvs_products WHERE product_name = 'Service Pack'"
        ).fetchone()
        con.close()
        assert row is not None
        assert row[0] is None

    def test_etl_metadata_columns(self, config):
        from feather_flow.config import load_config
        from feather_flow.pipeline import run_all

        cfg = load_config(config)
        run_all(cfg)

        con = duckdb.connect(str(cfg.destination.path), read_only=True)
        cols = {
            r[0]
            for r in con.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'bronze' AND table_name = 'csvs_orders'"
            ).fetchall()
        }
        con.close()
        assert "_etl_loaded_at" in cols
        assert "_etl_run_id" in cols


# ---------------------------------------------------------------------------
# SQLite source -- full pipeline integration
# ---------------------------------------------------------------------------


class TestSqliteFullPipeline:
    """Full pipeline run using SQLite source."""

    SQLITE_CURATION = [
        make_curation_entry("sqlitedb", "orders", "orders"),
        make_curation_entry("sqlitedb", "customers", "customers"),
        make_curation_entry("sqlitedb", "products", "products"),
    ]

    @pytest.fixture
    def config(self, project: ProjectFixture) -> Path:
        sqlite_db = project.copy_fixture("sample_erp.sqlite")
        return _write_sqlite_config(project, sqlite_db, self.SQLITE_CURATION)

    def test_run_all_succeeds(self, config):
        from feather_flow.config import load_config
        from feather_flow.pipeline import run_all

        cfg = load_config(config)
        results = run_all(cfg)
        assert all(r.status == "success" for r in results)
        assert {r.table_name for r in results} == {
            "sqlitedb_orders",
            "sqlitedb_customers",
            "sqlitedb_products",
        }

    def test_row_counts_in_bronze(self, config):
        from feather_flow.config import load_config
        from feather_flow.pipeline import run_all

        cfg = load_config(config)
        run_all(cfg)

        con = duckdb.connect(str(cfg.destination.path), read_only=True)
        assert (
            con.execute("SELECT COUNT(*) FROM bronze.sqlitedb_orders").fetchone()[0]
            == 5
        )
        assert (
            con.execute("SELECT COUNT(*) FROM bronze.sqlitedb_customers").fetchone()[0]
            == 4
        )
        assert (
            con.execute("SELECT COUNT(*) FROM bronze.sqlitedb_products").fetchone()[0]
            == 3
        )
        con.close()

    def test_null_passthrough(self, config):
        from feather_flow.config import load_config
        from feather_flow.pipeline import run_all

        cfg = load_config(config)
        run_all(cfg)

        con = duckdb.connect(str(cfg.destination.path), read_only=True)
        row = con.execute(
            "SELECT stock_qty FROM bronze.sqlitedb_products WHERE product_name = 'Service Pack'"
        ).fetchone()
        con.close()
        assert row is not None
        assert row[0] is None

    def test_etl_metadata_columns(self, config):
        from feather_flow.config import load_config
        from feather_flow.pipeline import run_all

        cfg = load_config(config)
        run_all(cfg)

        con = duckdb.connect(str(cfg.destination.path), read_only=True)
        cols = {
            r[0]
            for r in con.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'bronze' AND table_name = 'sqlitedb_orders'"
            ).fetchall()
        }
        con.close()
        assert "_etl_loaded_at" in cols
        assert "_etl_run_id" in cols
