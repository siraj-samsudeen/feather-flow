"""Integration: feather_etl.extract.run_extract — bronze extractor.

Exercises extract + config + state + destinations cross-module behavior.
All tests invoke extract.run_extract directly; the CLI counterpart lives
in tests/e2e/test_12_extract.py.

Renamed from test_cache_pipeline.py per the feather-transform change
(verb `feather cache` -> `feather extract`).
"""

from __future__ import annotations

import duckdb
import pytest

from feather_etl.config import load_config
from feather_etl.extract import run_extract
from tests.db_bootstrap import MYSQL_ADMIN_KWARGS, mysql_marker
from tests.helpers import make_curation_entry, write_curation


def _setup_project(project):
    """Build a minimal feather project: 1 DuckDB source, 1 curated table."""
    project.copy_fixture("client.duckdb")
    project.write_config(
        sources=[
            {
                "type": "duckdb",
                "name": "icube",
                "path": str(project.root / "client.duckdb"),
            }
        ],
        destination={"path": str(project.root / "feather_data.duckdb")},
    )
    project.write_curation([("icube", "icube.InventoryGroup", "inventory_group")])


def test_extracts_all_columns_into_bronze(project):
    _setup_project(project)
    cfg = load_config(project.config_path)
    results = run_extract(cfg, cfg.tables, project.root)

    assert len(results) == 1
    assert results[0].status == "success"
    assert results[0].table_name == "icube_inventory_group"
    assert results[0].rows_loaded > 0

    # Verify bronze table exists with all source columns
    con = duckdb.connect(str(cfg.destination.path), read_only=True)
    cols = {
        r[0]
        for r in con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'bronze' "
            "AND table_name = 'icube_inventory_group'"
        ).fetchall()
    }
    con.close()
    # Must include _etl_* metadata columns AND specific source columns
    # (known subset of InventoryGroup). Asserting specific source columns
    # (not just len > 2) catches silent column dropping.
    required = {
        "_etl_loaded_at",
        "_etl_run_id",
        "GRPCODE",
        "GRPNAME",
        "Alias",
    }
    assert required.issubset(cols), f"Missing expected columns. Got: {sorted(cols)}"


def test_writes_only_to_cache_watermarks(project):
    """After run_extract, _watermarks must be empty and _cache_watermarks populated.

    Note: the underlying state table is still ``_cache_watermarks`` — only the
    verb-facing API was renamed by the feather-transform change. State schema
    is out of scope for this rename.

    ``_runs`` IS written (one row per table, ``trigger='extract'``) per the
    transform-command spec — observability requirement, not a state-machine
    split.
    """
    _setup_project(project)
    cfg = load_config(project.config_path)
    run_extract(cfg, cfg.tables, project.root)

    con = duckdb.connect(str(project.state_db_path), read_only=True)
    prod = con.execute("SELECT COUNT(*) FROM _watermarks").fetchone()[0]
    cache = con.execute("SELECT COUNT(*) FROM _cache_watermarks").fetchone()[0]
    runs = con.execute(
        "SELECT COUNT(*), MIN(trigger), MAX(trigger) FROM _runs"
    ).fetchone()
    con.close()

    assert prod == 0, "run_extract must not write to _watermarks"
    assert cache == 1, "run_extract must write to _cache_watermarks"
    assert runs[0] == 1, "run_extract must write one _runs row per table"
    assert runs[1] == "extract" and runs[2] == "extract", (
        "run_extract rows must have trigger='extract'"
    )


def test_skips_unchanged_on_second_run(project):
    _setup_project(project)
    cfg = load_config(project.config_path)

    first = run_extract(cfg, cfg.tables, project.root)
    assert first[0].status == "success"

    second = run_extract(cfg, cfg.tables, project.root)
    assert second[0].status == "cached"
    assert second[0].rows_loaded == 0


def test_refresh_forces_repull(project):
    _setup_project(project)
    cfg = load_config(project.config_path)

    run_extract(cfg, cfg.tables, project.root)
    second = run_extract(cfg, cfg.tables, project.root, refresh=True)

    assert second[0].status == "success"
    assert second[0].rows_loaded > 0


def test_one_failure_does_not_block_others(project):
    """One bad table should not prevent other tables from being extracted."""
    project.copy_fixture("client.duckdb")
    project.write_config(
        sources=[
            {
                "type": "duckdb",
                "name": "icube",
                "path": str(project.root / "client.duckdb"),
            }
        ],
        destination={"path": str(project.root / "feather_data.duckdb")},
    )
    write_curation(
        project.root,
        [
            make_curation_entry("icube", "icube.InventoryGroup", "good_table"),
            make_curation_entry("icube", "icube.DOES_NOT_EXIST", "bad_table"),
        ],
    )

    cfg = load_config(project.config_path)
    results = run_extract(cfg, cfg.tables, project.root)

    statuses = {r.table_name: r.status for r in results}
    assert statuses["icube_good_table"] == "success"
    assert statuses["icube_bad_table"] == "failure"


def test_unresolvable_source_db_records_failure_without_raising(project):
    """A curation entry whose ``database`` doesn't match any configured
    source produces an ``ExtractResult(status='failure')`` with the
    resolve_source error message — no exception propagates.
    (extract.py)"""
    project.copy_fixture("client.duckdb")
    project.write_config(
        sources=[
            {
                "type": "duckdb",
                "name": "icube",
                "path": str(project.root / "client.duckdb"),
            }
        ],
        destination={"path": str(project.root / "feather_data.duckdb")},
    )
    entry = make_curation_entry("icube", "icube.InventoryGroup", "orphan_table")
    # Override database to a name that doesn't exist anywhere in cfg.sources
    entry["source_db"] = "nonexistent_system"
    write_curation(project.root, [entry])

    cfg = load_config(project.config_path)
    # load_config's validator tolerates this (config-level check is
    # informational); run_extract must not raise either.
    results = run_extract(cfg, cfg.tables, project.root)

    assert len(results) == 1
    assert results[0].status == "failure"
    assert results[0].source_db == "nonexistent_system"
    assert results[0].error_message is not None
    assert "nonexistent_system" in results[0].error_message
    # Extract shouldn't mutate bronze for a source-resolution failure.
    assert results[0].rows_loaded == 0


# ---------------------------------------------------------------------------
# MySQL multi-DB form regression — issues #51 (1046 no database selected) +
# #52 (VARCHAR/TEXT landing as BLOB). Both bugs surface only when the source
# is declared with the ``databases: [...]`` form rather than the singular
# ``database:`` form. Gated on a live local MySQL (same gate as
# ``TestMySQL*Integration`` in tests/unit/sources/test_mysql.py).
# ---------------------------------------------------------------------------


@pytest.fixture
def _mysql_blob_text_fixture():
    """Seed (idempotently) a table on ``feather_test`` whose columns
    exercise the BLOB/TEXT type-code overload (issue #52).

    mysql-connector returns the same FieldType code (252) for both ``BLOB``
    and ``TEXT``; the only discriminator is the ``FieldFlag.BINARY`` bit
    on ``cursor.description[i][7]``. Without flag-aware mapping, the OLD
    code mapped 252 → ``pa.binary()`` unconditionally, so TEXT columns
    landed in DuckDB as ``BLOB``.

    Table shape:
      - ``id INT``                  — sanity, must remain BIGINT in DuckDB
      - ``body TEXT``               — must land as DuckDB VARCHAR (the #52 case)
      - ``raw BLOB``                — must land as DuckDB BLOB (binary preserved)

    Returns the table name. Skips silently if MySQL isn't reachable —
    the surrounding ``@mysql_marker()`` on the test handles that gate.
    """
    import mysql.connector

    table_name = "issue_52_blob_vs_text"
    conn = mysql.connector.connect(**MYSQL_ADMIN_KWARGS, database="feather_test")
    try:
        cur = conn.cursor()
        cur.execute(f"DROP TABLE IF EXISTS `{table_name}`")
        cur.execute(
            f"CREATE TABLE `{table_name}` ("
            "id INT PRIMARY KEY, "
            "body TEXT, "
            "raw BLOB"
            ") CHARACTER SET utf8mb4"
        )
        cur.execute(
            f"INSERT INTO `{table_name}` VALUES "
            "(1, 'hello-text', UNHEX('48656C6C6F')), "  # 'Hello' raw bytes
            "(2, 'second row', UNHEX('776F726C64'))"  # 'world' raw bytes
        )
        conn.commit()
        cur.close()
        yield table_name
    finally:
        # Best-effort cleanup; the next run's CREATE-after-DROP also handles
        # leftover rows from a crashed prior run. Catch only mysql-connector's
        # error type so unrelated bugs (typos, attribute errors) propagate.
        try:
            cleanup = mysql.connector.connect(
                **MYSQL_ADMIN_KWARGS, database="feather_test"
            )
            c = cleanup.cursor()
            c.execute(f"DROP TABLE IF EXISTS `{table_name}`")
            cleanup.commit()
            c.close()
            cleanup.close()
        except mysql.connector.Error:
            pass
        conn.close()


@mysql_marker()
def test_cache_succeeds_for_mysql_databases_list_form(
    project, _mysql_blob_text_fixture
):
    """Repro for #51 + #52: a MySQL source declared with ``databases:[...]``
    plus a curation entry whose ``source_table`` is bare (not DB-qualified)
    must (a) succeed at cache time despite no default DB being set on the
    connection, and (b) land VARCHAR/TEXT columns as DuckDB ``VARCHAR``,
    keeping BLOB columns as ``BLOB``.

    **#51 (No database selected)** — Before the fix, ``run_extract``
    resolved the parent multi-DB source, whose ``self.database is None``
    and whose ``_connect_kwargs`` carries no ``database`` key. ``extract``
    issued ``SELECT ... FROM <table>`` with no DB prefix and MySQL
    returned ``1046 (3D000): No database selected``. After the fix:
    ``curation._bind_db`` rebuilds a child source bound to ``source_db``
    for any multi-DB parent, so connections land with a default DB.

    **#52 (BLOB/TEXT type-code overload)** — Before the fix,
    ``_mysql_field_type_to_arrow`` hard-coded FieldType 252 → ``pa.binary()``,
    so TEXT columns (same wire type as BLOB, discriminated only by
    ``FieldFlag.BINARY``) materialised as ``binary()`` in pyarrow and
    landed as ``BLOB`` in DuckDB. After the fix: the mapper consults
    ``desc[7]`` (flags) and returns ``pa.string()`` for non-binary BLOB-
    family types. The ``charset=utf8mb4`` / ``use_unicode=True``
    connect-kwargs hardening prevents bytes-vs-str regressions on older
    connector versions where the C-extension default differs.

    The fixture seeds a table with one TEXT column and one BLOB column on
    the same FieldType code (252), so a partial revert of either fix
    (the type-map change or the connect-kwarg change) re-triggers the
    bug. The straight VARCHAR fixture (``erp_sales``) is intentionally
    not used here — modern mysql-connector returns VARCHAR as ``str`` by
    default, masking the issue.
    """
    table_name = _mysql_blob_text_fixture

    # Multi-DB source declaration (no top-level ``database:`` key).
    # This shape triggers the #51 1046 error.
    project.write_config(
        sources=[
            {
                "type": "mysql",
                "name": "TestMySQL",
                "host": "localhost",
                "user": "root",
                "databases": ["feather_test"],
            }
        ],
        destination={"path": str(project.root / "feather_data.duckdb")},
    )

    # Bare ``source_table`` (no ``feather_test.`` prefix) — the form that
    # rama_dw curation files use today and that #51 reproduces against.
    write_curation(
        project.root,
        [make_curation_entry("feather_test", table_name, table_name)],
    )

    cfg = load_config(project.config_path)
    results = run_extract(cfg, cfg.tables, project.root)

    # #51: every result must succeed (not fail with 1046).
    assert len(results) == 1
    assert results[0].status == "success", (
        f"expected success, got {results[0].status}: {results[0].error_message}"
    )
    assert results[0].rows_loaded == 2  # fixture has 2 rows

    # #52: TEXT must be VARCHAR in DuckDB; BLOB must stay BLOB.
    con = duckdb.connect(str(cfg.destination.path), read_only=True)
    try:
        bronze_name = f"feather_test_{table_name}"
        col_types = {
            r[0]: r[1]
            for r in con.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = 'bronze' "
                f"AND table_name = '{bronze_name}'"
            ).fetchall()
        }
        assert col_types.get("body") == "VARCHAR", (
            f"#52 regression: TEXT column 'body' should be VARCHAR, "
            f"got {col_types.get('body')}. All columns: {col_types}"
        )
        assert col_types.get("raw") == "BLOB", (
            f"BLOB column 'raw' should remain BLOB, got {col_types.get('raw')}"
        )

        # Confirm values: TEXT round-trips as Python str; BLOB stays bytes-ish.
        rows = con.execute(
            f"SELECT body, raw FROM bronze.{bronze_name} ORDER BY id"
        ).fetchall()
        assert rows[0][0] == "hello-text"
        assert isinstance(rows[0][0], str)
        # DuckDB returns BLOB values as bytes objects in Python.
        assert rows[0][1] == b"Hello"
    finally:
        con.close()
