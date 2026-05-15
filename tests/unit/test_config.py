"""Tests for feather.config module."""

import json
import os
from pathlib import Path

import pytest
import yaml

from tests.helpers import make_curation_entry, write_config, write_curation


def _minimal_config(tmp_path: Path, source_path: str | None = None) -> dict:
    """Return a valid minimal config dict (no tables — those come from curation)."""
    if source_path is None:
        db = tmp_path / "source.duckdb"
        db.touch()
        source_path = str(db)
    return {
        "sources": [{"type": "duckdb", "name": "src", "path": source_path}],
        "destination": {"path": str(tmp_path / "feather_data.duckdb")},
    }


def _minimal_curation(tmp_path: Path) -> None:
    """Write a minimal curation.json with one table."""
    write_curation(
        tmp_path,
        [make_curation_entry("src", "main.test", "test_table")],
    )


class TestConfigParsing:
    def test_valid_config_parses(self, tmp_path: Path):
        from feather_etl.config import load_config

        cfg = _minimal_config(tmp_path)
        config_file = write_config(tmp_path, cfg)
        _minimal_curation(tmp_path)
        result = load_config(config_file, validate=False)
        assert result.sources[0].type == "duckdb"
        assert len(result.tables) == 1
        assert result.tables[0].name == "src_test_table"

    def test_env_var_substitution(self, tmp_path: Path):
        from feather_etl.config import load_config

        os.environ["FEATHER_TEST_PATH"] = str(tmp_path / "source.duckdb")
        try:
            cfg = _minimal_config(tmp_path)
            cfg["sources"][0]["path"] = "${FEATHER_TEST_PATH}"
            config_file = write_config(tmp_path, cfg)
            _minimal_curation(tmp_path)
            result = load_config(config_file, validate=False)
            assert "${" not in str(result.sources[0].path)
            assert "source.duckdb" in str(result.sources[0].path)
        finally:
            del os.environ["FEATHER_TEST_PATH"]

    def test_path_resolution_relative_to_config(self, tmp_path: Path):
        from feather_etl.config import load_config

        subdir = tmp_path / "project"
        subdir.mkdir()
        cfg = _minimal_config(tmp_path)
        cfg["sources"][0]["path"] = "./source.duckdb"
        cfg["destination"]["path"] = "./data.duckdb"
        config_file = write_config(tmp_path, cfg, directory=subdir)
        write_curation(
            subdir,
            [make_curation_entry("src", "main.test", "test_table")],
        )
        result = load_config(config_file, validate=False)
        assert result.sources[0].path == subdir / "source.duckdb"
        assert result.destination.path == subdir / "data.duckdb"

    def test_no_primary_key_does_not_error(self, tmp_path: Path):
        """primary_key validation deferred to Slice 3."""
        from feather_etl.config import load_config

        cfg = _minimal_config(tmp_path)
        config_file = write_config(tmp_path, cfg)
        _minimal_curation(tmp_path)
        result = load_config(config_file, validate=False)
        # curation entries have primary_key=["id"] by default
        assert result.tables[0].primary_key == ["id"]


class TestConfigValidation:
    def test_bad_source_type(self, tmp_path: Path):
        from feather_etl.config import load_config

        cfg = _minimal_config(tmp_path)
        cfg["sources"][0]["type"] = "mongodb"
        config_file = write_config(tmp_path, cfg)
        _minimal_curation(tmp_path)
        with pytest.raises(ValueError, match="not implemented"):
            load_config(config_file)

    def test_missing_source_path_for_file_source(self, tmp_path: Path):
        """Load succeeds but source.check() reports the path is unreachable."""
        from feather_etl.config import load_config

        cfg = _minimal_config(tmp_path)
        cfg["sources"][0]["path"] = str(tmp_path / "nonexistent.duckdb")
        config_file = write_config(tmp_path, cfg)
        _minimal_curation(tmp_path)
        cfg_result = load_config(config_file)
        assert cfg_result.sources[0].check() is False

    def test_bad_strategy(self, tmp_path: Path):
        from feather_etl.config import load_config

        cfg = _minimal_config(tmp_path)
        config_file = write_config(tmp_path, cfg)
        write_curation(
            tmp_path,
            [make_curation_entry("src", "main.test", "test_table", strategy="upsert")],
        )
        with pytest.raises(ValueError, match="strategy"):
            load_config(config_file)

    def test_bad_target_schema_prefix(self, tmp_path: Path):
        """Curation always produces bronze.* targets, so this test validates
        directly via _validate with a manually-constructed table."""
        from feather_etl.config import load_config

        cfg = _minimal_config(tmp_path)
        config_file = write_config(tmp_path, cfg)
        _minimal_curation(tmp_path)
        result = load_config(config_file, validate=False)
        # Manually override to test validation
        result.tables[0].target_table = "staging.test_table"
        from feather_etl.config import _validate

        errors = _validate(result)
        assert any("schema" in e for e in errors)


class TestValidationJson:
    def test_writes_on_success(self, tmp_path: Path):
        from feather_etl.config import load_config, write_validation_json

        cfg = _minimal_config(tmp_path)
        config_file = write_config(tmp_path, cfg)
        _minimal_curation(tmp_path)
        result = load_config(config_file)
        write_validation_json(config_file, result)
        vj = json.loads((tmp_path / "feather_validation.json").read_text())
        assert vj["valid"] is True
        assert vj["errors"] == []
        assert vj["tables_count"] == 1

    def test_writes_on_failure(self, tmp_path: Path):
        from feather_etl.config import write_validation_json

        write_validation_json(
            tmp_path / "feather.yaml", None, errors=["bad source type"]
        )
        vj = json.loads((tmp_path / "feather_validation.json").read_text())
        assert vj["valid"] is False
        assert "bad source type" in vj["errors"]


class TestEnvVarEdgeCases:
    def test_numeric_values_pass_through(self, tmp_path: Path):
        """config.py line 73: non-string values returned as-is."""
        from feather_etl.config import load_config

        cfg = _minimal_config(tmp_path)
        cfg["defaults"] = {"overlap_window_minutes": 5, "batch_size": 50000}
        config_file = write_config(tmp_path, cfg)
        _minimal_curation(tmp_path)
        result = load_config(config_file, validate=False)
        assert result.defaults.overlap_window_minutes == 5
        assert result.defaults.batch_size == 50000

    def test_dotenv_auto_loaded_from_config_dir(self, tmp_path: Path, monkeypatch):
        """Regression for siraj-samsudeen/feather-etl#1."""
        from feather_etl.config import load_config

        monkeypatch.delenv("FEATHER_TEST_DOTENV_VAR", raising=False)
        assert "FEATHER_TEST_DOTENV_VAR" not in os.environ

        (tmp_path / ".env").write_text("FEATHER_TEST_DOTENV_VAR=hello\n")

        cfg = _minimal_config(tmp_path)
        config_file = write_config(tmp_path, cfg)
        _minimal_curation(tmp_path)
        load_config(config_file, validate=False)
        # Just verify .env was loaded — the variable was resolved
        assert os.environ.get("FEATHER_TEST_DOTENV_VAR") == "hello"

    def test_dotenv_does_not_override_existing_env(self, tmp_path: Path, monkeypatch):
        """Existing shell/CI env vars must win over .env (override=False)."""
        from feather_etl.config import load_config

        monkeypatch.setenv("FEATHER_TEST_OVERRIDE_VAR", "gold")
        (tmp_path / ".env").write_text("FEATHER_TEST_OVERRIDE_VAR=bronze\n")

        cfg = _minimal_config(tmp_path)
        config_file = write_config(tmp_path, cfg)
        _minimal_curation(tmp_path)
        load_config(config_file, validate=False)
        assert os.environ["FEATHER_TEST_OVERRIDE_VAR"] == "gold"

    def test_unresolved_env_var_gives_clear_error(self, tmp_path: Path):
        """UX-4: Unset env vars should show which variable is missing."""
        from feather_etl.config import load_config

        cfg = _minimal_config(tmp_path)
        cfg["destination"]["path"] = "${MISSING_DEST_PATH}"
        config_file = write_config(tmp_path, cfg)
        _minimal_curation(tmp_path)
        with pytest.raises(ValueError, match="MISSING_DEST_PATH"):
            load_config(config_file, validate=False)


class TestConfigValidationExtended:
    def test_unregistered_source_type_rejected(self, tmp_path: Path):
        """Source types not in SOURCE_REGISTRY should be rejected at validate."""
        from feather_etl.config import load_config

        cfg = {
            "sources": [{"type": "ftp", "connection_string": "ftp://example.com"}],
            "destination": {"path": str(tmp_path / "data.duckdb")},
        }
        config_file = write_config(tmp_path, cfg)
        _minimal_curation(tmp_path)
        with pytest.raises(ValueError, match="not implemented"):
            load_config(config_file)

    def test_missing_source_section_gives_friendly_error(self, tmp_path: Path):
        """Missing 'source' section should raise ValueError, not KeyError."""
        from feather_etl.config import load_config

        cfg = {
            "destination": {"path": str(tmp_path / "data.duckdb")},
        }
        config_file = write_config(tmp_path, cfg)
        with pytest.raises(ValueError, match="Missing required config section"):
            load_config(config_file)

    def test_destination_parent_must_exist(self, tmp_path: Path):
        """BUG-6: Non-existent destination parent should fail at validate."""
        from feather_etl.config import load_config

        db = tmp_path / "source.duckdb"
        db.touch()
        cfg = {
            "sources": [{"type": "duckdb", "name": "src", "path": str(db)}],
            "destination": {
                "path": str(tmp_path / "nonexistent" / "sub" / "data.duckdb")
            },
        }
        config_file = write_config(tmp_path, cfg)
        _minimal_curation(tmp_path)
        with pytest.raises(ValueError, match="Destination directory does not exist"):
            load_config(config_file)

    def test_incremental_requires_timestamp_column(self, tmp_path: Path):
        """UX-8: incremental strategy without timestamp_column should be rejected."""
        from feather_etl.config import load_config

        cfg = _minimal_config(tmp_path)
        config_file = write_config(tmp_path, cfg)
        write_curation(
            tmp_path,
            [
                make_curation_entry(
                    "src", "main.test", "test_table", strategy="incremental"
                )
            ],
        )
        with pytest.raises(ValueError, match="timestamp_column"):
            load_config(config_file)

    def test_incremental_with_timestamp_column_passes(self, tmp_path: Path):
        """incremental + timestamp_column should be accepted."""
        from feather_etl.config import load_config

        cfg = _minimal_config(tmp_path)
        config_file = write_config(tmp_path, cfg)
        write_curation(
            tmp_path,
            [
                make_curation_entry(
                    "src",
                    "main.test",
                    "test_table",
                    strategy="incremental",
                    timestamp_column="modified_date",
                ),
            ],
        )
        result = load_config(config_file)
        assert result.tables[0].strategy == "incremental"

    def test_negative_overlap_window_rejected(self, tmp_path: Path):
        """H-2: negative overlap_window_minutes must be rejected."""
        from feather_etl.config import load_config

        cfg = _minimal_config(tmp_path)
        cfg["defaults"] = {"overlap_window_minutes": -5}
        config_file = write_config(tmp_path, cfg)
        _minimal_curation(tmp_path)
        with pytest.raises(ValueError, match="overlap_window_minutes"):
            load_config(config_file)

    def test_zero_overlap_window_accepted(self, tmp_path: Path):
        """H-2: overlap_window_minutes = 0 is valid."""
        from feather_etl.config import load_config

        cfg = _minimal_config(tmp_path)
        cfg["defaults"] = {"overlap_window_minutes": 0}
        config_file = write_config(tmp_path, cfg)
        _minimal_curation(tmp_path)
        result = load_config(config_file)
        assert result.defaults.overlap_window_minutes == 0

    def test_source_table_with_semicolon_rejected(self, tmp_path: Path):
        """M-1: source_table containing semicolons must be rejected."""
        from feather_etl.config import load_config

        cfg = _minimal_config(tmp_path)
        config_file = write_config(tmp_path, cfg)
        write_curation(
            tmp_path,
            [make_curation_entry("src", "main.test; DROP TABLE foo", "test_table")],
        )
        with pytest.raises(ValueError, match="source_table.*invalid"):
            load_config(config_file)

    def test_source_table_with_comment_rejected(self, tmp_path: Path):
        """M-1: source_table containing SQL comments must be rejected."""
        from feather_etl.config import load_config

        cfg = _minimal_config(tmp_path)
        config_file = write_config(tmp_path, cfg)
        write_curation(
            tmp_path,
            [make_curation_entry("src", "main.test--comment", "test_table")],
        )
        with pytest.raises(ValueError, match="source_table.*invalid"):
            load_config(config_file)

    def test_valid_source_table_formats_accepted(self, tmp_path: Path):
        """M-1: legitimate source_table formats should pass validation."""
        from feather_etl.config import load_config

        cfg = _minimal_config(tmp_path)
        config_file = write_config(tmp_path, cfg)
        write_curation(
            tmp_path,
            [make_curation_entry("src", "erp.SalesInvoice", "sales_invoice")],
        )
        result = load_config(config_file)
        assert result.tables[0].source_table == "erp.SalesInvoice"

    def test_csv_source_table_filename_accepted(self, tmp_path: Path):
        """M-1: CSV source_table (filename) should pass validation."""
        from feather_etl.config import load_config

        csv_dir = tmp_path / "csv_data"
        csv_dir.mkdir()
        cfg = _minimal_config(tmp_path)
        cfg["sources"] = [{"type": "csv", "name": "csvs", "path": str(csv_dir)}]
        config_file = write_config(tmp_path, cfg)
        write_curation(
            tmp_path,
            [make_curation_entry("csvs", "orders.csv", "orders")],
        )
        result = load_config(config_file)
        assert result.tables[0].source_table == "orders.csv"

    def test_duckdb_source_table_without_schema_rejected(self, tmp_path: Path):
        """R-1: DuckDB source_table must be schema.table format."""
        from feather_etl.config import load_config

        cfg = _minimal_config(tmp_path)
        config_file = write_config(tmp_path, cfg)
        write_curation(
            tmp_path,
            [make_curation_entry("src", "just_a_table", "test_table")],
        )
        with pytest.raises(ValueError, match="source_table.*schema\\.table"):
            load_config(config_file)

    def test_duckdb_source_table_with_spaces_rejected(self, tmp_path: Path):
        """R-1: DuckDB source_table with spaces in identifiers must be rejected."""
        from feather_etl.config import load_config

        cfg = _minimal_config(tmp_path)
        config_file = write_config(tmp_path, cfg)
        write_curation(
            tmp_path,
            [make_curation_entry("src", "erp.Sales Invoice", "test_table")],
        )
        with pytest.raises(ValueError, match="source_table.*invalid"):
            load_config(config_file)

    def test_duckdb_source_table_with_parens_rejected(self, tmp_path: Path):
        """R-1: DuckDB source_table with parentheses must be rejected."""
        from feather_etl.config import load_config

        cfg = _minimal_config(tmp_path)
        config_file = write_config(tmp_path, cfg)
        write_curation(
            tmp_path,
            [make_curation_entry("src", "erp.test()", "test_table")],
        )
        with pytest.raises(ValueError, match="source_table.*invalid"):
            load_config(config_file)


class TestAlertsConfig:
    def test_no_alerts_section_sets_none(self, tmp_path: Path):
        from feather_etl.config import load_config

        cfg = _minimal_config(tmp_path)
        config_file = write_config(tmp_path, cfg)
        _minimal_curation(tmp_path)
        result = load_config(config_file, validate=False)
        assert result.alerts is None

    def test_valid_alerts_section_parses(self, tmp_path: Path):
        from feather_etl.config import load_config

        cfg = _minimal_config(tmp_path)
        cfg["alerts"] = {
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "smtp_user": "user@example.com",
            "smtp_password": "secret",
            "alert_to": "ops@example.com",
        }
        config_file = write_config(tmp_path, cfg)
        _minimal_curation(tmp_path)
        result = load_config(config_file, validate=False)
        assert result.alerts is not None
        assert result.alerts.smtp_host == "smtp.example.com"
        assert result.alerts.smtp_port == 587
        assert result.alerts.smtp_user == "user@example.com"
        assert result.alerts.alert_to == "ops@example.com"

    def test_alert_from_defaults_to_smtp_user(self, tmp_path: Path):
        from feather_etl.config import load_config

        cfg = _minimal_config(tmp_path)
        cfg["alerts"] = {
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "smtp_user": "user@example.com",
            "smtp_password": "secret",
            "alert_to": "ops@example.com",
        }
        config_file = write_config(tmp_path, cfg)
        _minimal_curation(tmp_path)
        result = load_config(config_file, validate=False)
        assert result.alerts.alert_from == "user@example.com"

    def test_explicit_alert_from(self, tmp_path: Path):
        from feather_etl.config import load_config

        cfg = _minimal_config(tmp_path)
        cfg["alerts"] = {
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "smtp_user": "user@example.com",
            "smtp_password": "secret",
            "alert_to": "ops@example.com",
            "alert_from": "noreply@example.com",
        }
        config_file = write_config(tmp_path, cfg)
        _minimal_curation(tmp_path)
        result = load_config(config_file, validate=False)
        assert result.alerts.alert_from == "noreply@example.com"

    def test_missing_required_alert_field_raises(self, tmp_path: Path):
        from feather_etl.config import load_config

        cfg = _minimal_config(tmp_path)
        cfg["alerts"] = {
            "smtp_host": "smtp.example.com",
        }
        config_file = write_config(tmp_path, cfg)
        _minimal_curation(tmp_path)
        with pytest.raises(ValueError, match="alerts.*missing"):
            load_config(config_file, validate=False)

    def test_alerts_env_var_resolved(self, tmp_path: Path, monkeypatch):
        from feather_etl.config import load_config

        monkeypatch.setenv("TEST_SMTP_PASS", "env_secret")
        cfg = _minimal_config(tmp_path)
        cfg["alerts"] = {
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "smtp_user": "user@example.com",
            "smtp_password": "${TEST_SMTP_PASS}",
            "alert_to": "ops@example.com",
        }
        config_file = write_config(tmp_path, cfg)
        _minimal_curation(tmp_path)
        result = load_config(config_file, validate=False)
        assert result.alerts.smtp_password == "env_secret"


class TestSourceName:
    def test_source_name_is_optional(self, tmp_path: Path):
        """When 'name:' is omitted, auto-derivation fills it with '<type>-<basename>'."""
        from feather_etl.config import load_config

        cfg_dict = _minimal_config(tmp_path)
        del cfg_dict["sources"][0]["name"]
        config_file = write_config(tmp_path, cfg_dict)
        _minimal_curation(tmp_path)
        result = load_config(config_file, validate=False)
        assert result.sources[0].name == "duckdb-source"

    def test_source_name_is_accepted(self, tmp_path: Path):
        from feather_etl.config import load_config

        cfg_dict = _minimal_config(tmp_path)
        cfg_dict["sources"][0]["name"] = "prod-erp"
        config_file = write_config(tmp_path, cfg_dict)
        _minimal_curation(tmp_path)
        result = load_config(config_file, validate=False)
        assert result.sources[0].name == "prod-erp"


class TestSourcesList:
    def test_sources_list_with_single_entry(self, tmp_path: Path):
        from feather_etl.config import load_config

        cfg_dict = {
            "sources": [{"type": "duckdb", "path": str(tmp_path / "s.duckdb")}],
            "destination": {"path": str(tmp_path / "out.duckdb")},
        }
        (tmp_path / "s.duckdb").write_bytes(b"")
        config_file = tmp_path / "feather.yaml"
        config_file.write_text(yaml.dump(cfg_dict))
        cfg = load_config(config_file, validate=False)
        assert len(cfg.sources) == 1
        assert cfg.sources[0].type == "duckdb"

    def test_sources_list_multi_entry_requires_names(self, tmp_path: Path):
        from feather_etl.config import load_config

        cfg_dict = {
            "sources": [
                {"type": "duckdb", "path": str(tmp_path / "a.duckdb")},
                {"type": "duckdb", "path": str(tmp_path / "b.duckdb")},
            ],
            "destination": {"path": str(tmp_path / "out.duckdb")},
        }
        for f in ("a.duckdb", "b.duckdb"):
            (tmp_path / f).write_bytes(b"")
        config_file = tmp_path / "feather.yaml"
        config_file.write_text(yaml.dump(cfg_dict))
        with pytest.raises(ValueError, match="name.*required"):
            load_config(config_file, validate=False)

    def test_sources_list_multi_entry_with_names_ok(self, tmp_path: Path):
        from feather_etl.config import load_config

        cfg_dict = {
            "sources": [
                {"name": "a", "type": "duckdb", "path": str(tmp_path / "a.duckdb")},
                {"name": "b", "type": "duckdb", "path": str(tmp_path / "b.duckdb")},
            ],
            "destination": {"path": str(tmp_path / "out.duckdb")},
        }
        for f in ("a.duckdb", "b.duckdb"):
            (tmp_path / f).write_bytes(b"")
        config_file = tmp_path / "feather.yaml"
        config_file.write_text(yaml.dump(cfg_dict))
        cfg = load_config(config_file, validate=False)
        assert [s.name for s in cfg.sources] == ["a", "b"]

    def test_sources_list_duplicate_name_raises(self, tmp_path: Path):
        from feather_etl.config import load_config

        cfg_dict = {
            "sources": [
                {"name": "x", "type": "duckdb", "path": str(tmp_path / "a.duckdb")},
                {"name": "x", "type": "duckdb", "path": str(tmp_path / "b.duckdb")},
            ],
            "destination": {"path": str(tmp_path / "out.duckdb")},
        }
        for f in ("a.duckdb", "b.duckdb"):
            (tmp_path / f).write_bytes(b"")
        config_file = tmp_path / "feather.yaml"
        config_file.write_text(yaml.dump(cfg_dict))
        with pytest.raises(ValueError, match="duplicate.*'x'"):
            load_config(config_file, validate=False)

    def test_sources_empty_list_raises(self, tmp_path: Path):
        from feather_etl.config import load_config

        cfg_dict = {
            "sources": [],
            "destination": {"path": str(tmp_path / "out.duckdb")},
        }
        config_file = tmp_path / "feather.yaml"
        config_file.write_text(yaml.dump(cfg_dict))
        with pytest.raises(ValueError, match="non-empty"):
            load_config(config_file, validate=False)

    def test_sources_entry_must_be_mapping(self, tmp_path: Path):
        from feather_etl.config import load_config

        cfg_dict = {
            "sources": [None],
            "destination": {"path": str(tmp_path / "out.duckdb")},
        }
        config_file = tmp_path / "feather.yaml"
        config_file.write_text(yaml.dump(cfg_dict))
        with pytest.raises(ValueError, match=r"sources\[0\].*must be a mapping"):
            load_config(config_file, validate=False)

    def test_sources_entry_missing_type_raises(self, tmp_path: Path):
        """Each sources[N] entry must declare 'type' — we fail fast with the
        index in the error."""
        from feather_etl.config import load_config

        cfg_dict = {
            "sources": [{"name": "orphan", "path": str(tmp_path / "x.duckdb")}],
            "destination": {"path": str(tmp_path / "out.duckdb")},
        }
        config_file = tmp_path / "feather.yaml"
        config_file.write_text(yaml.dump(cfg_dict))
        with pytest.raises(
            ValueError, match=r"sources\[0\] missing required field 'type'"
        ):
            load_config(config_file, validate=False)


class TestValidateUnknownDatabase:
    def test_unknown_source_db_falls_back_to_first_source(self, tmp_path: Path):
        """When a curation entry's source_db doesn't match any configured
        source, validation falls back to sources[0] for source_table checks
        (the mismatch itself is reported elsewhere). The fallback must still
        surface any source_table validation errors."""
        from feather_etl.config import _validate, load_config

        # Single DuckDB source + a curation entry whose source_db names a
        # non-existent source. The source_table is an invalid identifier
        # (contains parens) so the fallback validator flags it.
        src_db = tmp_path / "src.duckdb"
        src_db.write_bytes(b"")
        cfg_dict = {
            "sources": [{"type": "duckdb", "name": "src", "path": str(src_db)}],
            "destination": {"path": str(tmp_path / "out.duckdb")},
        }
        config_file = write_config(tmp_path, cfg_dict)
        write_curation(
            tmp_path,
            [
                make_curation_entry(
                    "doesnt_exist",  # unknown source_db → triggers the fallback
                    "bad(name)",  # invalid identifier → surfaces via fallback
                    "t",
                ),
            ],
        )

        cfg = load_config(config_file, validate=False)
        errors = _validate(cfg)
        # The validator yields an error message from source[0].validate_source_table
        assert any("bad(name)" in err for err in errors)


class TestSingularSourceMigrationError:
    def test_singular_source_raises_with_guidance(self, tmp_path: Path):
        from feather_etl.config import load_config

        cfg_dict = {
            "source": {"type": "duckdb", "path": str(tmp_path / "s.duckdb")},
            "destination": {"path": str(tmp_path / "out.duckdb")},
        }
        (tmp_path / "s.duckdb").write_bytes(b"")
        config_file = tmp_path / "feather.yaml"
        config_file.write_text(yaml.dump(cfg_dict))
        with pytest.raises(ValueError) as exc:
            load_config(config_file, validate=False)
        msg = str(exc.value)
        assert "sources:" in msg
        assert "Wrap your existing source in a list" in msg


class TestExtractDefaultsThresholds:
    """Issue #63: bucket-threshold fields on ExtractDefaultsConfig."""

    def test_extract_defaults_threshold_defaults(self):
        """Dataclass defaults match the documented bucket boundaries."""
        from feather_etl.config import ExtractDefaultsConfig

        cfg = ExtractDefaultsConfig()
        assert cfg.t_small_rows == 100_000
        assert cfg.t_large_rows == 5_000_000
        assert cfg.t_narrow_cols == 25
        assert cfg.t_wide_cols == 60

    def test_extract_defaults_threshold_yaml_override(self, tmp_path: Path):
        """All four threshold fields can be overridden via YAML."""
        from feather_etl.config import load_config

        cfg = _minimal_config(tmp_path)
        cfg["defaults"] = {
            "extract": {
                "t_small_rows": 50_000,
                "t_large_rows": 10_000_000,
                "t_narrow_cols": 20,
                "t_wide_cols": 80,
            }
        }
        config_file = write_config(tmp_path, cfg)
        _minimal_curation(tmp_path)
        result = load_config(config_file, validate=False)
        ext = result.defaults.extract
        assert ext.t_small_rows == 50_000
        assert ext.t_large_rows == 10_000_000
        assert ext.t_narrow_cols == 20
        assert ext.t_wide_cols == 80

    def test_extract_defaults_threshold_partial_yaml_keeps_other_defaults(
        self, tmp_path: Path
    ):
        """Overriding one threshold leaves the others at their defaults."""
        from feather_etl.config import load_config

        cfg = _minimal_config(tmp_path)
        cfg["defaults"] = {"extract": {"t_small_rows": 10_000}}
        config_file = write_config(tmp_path, cfg)
        _minimal_curation(tmp_path)
        result = load_config(config_file, validate=False)
        ext = result.defaults.extract
        assert ext.t_small_rows == 10_000
        assert ext.t_large_rows == 5_000_000
        assert ext.t_narrow_cols == 25
        assert ext.t_wide_cols == 60
        # existing heartbeat fields also unaffected
        assert ext.heartbeat_every_rows == 100_000
        assert ext.heartbeat_every_seconds == 30
