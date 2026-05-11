"""Tests for mode: dev/prod/test config parsing (load_config)."""

from pathlib import Path

import pytest
import yaml

from tests.conftest import FIXTURES_DIR
from tests.helpers import make_curation_entry, write_curation


def _write_config(tmp_path: Path, mode: str | None = None, **overrides) -> Path:
    """Write a minimal feather.yaml with optional mode and return config path."""
    import shutil

    src_db = tmp_path / "sample_erp.duckdb"
    shutil.copy2(FIXTURES_DIR / "sample_erp.duckdb", src_db)

    config = {
        "sources": [{"type": "duckdb", "name": "erp", "path": str(src_db)}],
        "destination": {"path": str(tmp_path / "output.duckdb")},
    }
    if mode is not None:
        config["mode"] = mode
    config.update(overrides)

    config_path = tmp_path / "feather.yaml"
    config_path.write_text(yaml.dump(config, default_flow_style=False))
    write_curation(
        tmp_path,
        [make_curation_entry("erp", "erp.customers", "customers")],
    )
    return config_path


def test_config_mode_defaults_to_dev(tmp_path):
    """mode defaults to 'dev' when not specified."""
    from feather_etl.config import load_config

    config_path = _write_config(tmp_path)
    cfg = load_config(config_path)
    assert cfg.mode == "dev"


def test_config_mode_parsed_from_yaml(tmp_path):
    """mode: prod in YAML is parsed correctly."""
    from feather_etl.config import load_config

    config_path = _write_config(tmp_path, mode="prod")
    cfg = load_config(config_path)
    assert cfg.mode == "prod"


def test_config_invalid_mode_rejected(tmp_path):
    """Invalid mode value raises validation error."""
    from feather_etl.config import load_config

    config_path = _write_config(tmp_path, mode="staging")
    with pytest.raises(ValueError, match="mode"):
        load_config(config_path)


def test_config_mode_env_var_overrides_yaml(tmp_path, monkeypatch):
    """FEATHER_MODE env var overrides YAML mode."""
    from feather_etl.config import load_config

    config_path = _write_config(tmp_path, mode="dev")
    monkeypatch.setenv("FEATHER_MODE", "prod")
    cfg = load_config(config_path)
    assert cfg.mode == "prod"


def test_config_mode_cli_override(tmp_path):
    """mode_override parameter overrides both YAML and env var."""
    from feather_etl.config import load_config

    config_path = _write_config(tmp_path, mode="dev")
    cfg = load_config(config_path, mode_override="prod")
    assert cfg.mode == "prod"


def test_config_mode_cli_beats_env(tmp_path, monkeypatch):
    """mode_override (CLI) wins over FEATHER_MODE env var."""
    from feather_etl.config import load_config

    config_path = _write_config(tmp_path, mode="dev")
    monkeypatch.setenv("FEATHER_MODE", "test")
    cfg = load_config(config_path, mode_override="prod")
    assert cfg.mode == "prod"


def test_config_mode_env_beats_default(tmp_path, monkeypatch):
    """FEATHER_MODE env var wins over the default 'dev' when YAML omits mode."""
    from feather_etl.config import load_config

    config_path = _write_config(tmp_path)  # no mode in YAML
    monkeypatch.setenv("FEATHER_MODE", "prod")
    cfg = load_config(config_path)
    assert cfg.mode == "prod"


def test_config_mode_yaml_beats_default(tmp_path, monkeypatch):
    """YAML mode wins over the default 'dev' when no env var or CLI override is set."""
    from feather_etl.config import load_config

    config_path = _write_config(tmp_path, mode="test")
    monkeypatch.delenv("FEATHER_MODE", raising=False)
    cfg = load_config(config_path)
    assert cfg.mode == "test"


def test_config_mode_invalid_cli_override_rejected(tmp_path):
    """mode_override with an invalid value is rejected with a clear error."""
    from feather_etl.config import load_config

    config_path = _write_config(tmp_path, mode="dev")
    with pytest.raises(ValueError, match="mode"):
        load_config(config_path, mode_override="staging")


def test_config_mode_invalid_env_rejected(tmp_path, monkeypatch):
    """FEATHER_MODE with an invalid value is rejected with a clear error."""
    from feather_etl.config import load_config

    config_path = _write_config(tmp_path, mode="dev")
    monkeypatch.setenv("FEATHER_MODE", "bogus")
    with pytest.raises(ValueError, match="mode"):
        load_config(config_path)


def test_config_row_limit_parsed(tmp_path):
    """defaults.row_limit is parsed from YAML."""
    from feather_etl.config import load_config

    config_path = _write_config(tmp_path, defaults={"row_limit": 100})
    cfg = load_config(config_path)
    assert cfg.defaults.row_limit == 100


def test_config_row_limit_defaults_to_none(tmp_path):
    """defaults.row_limit defaults to None."""
    from feather_etl.config import load_config

    config_path = _write_config(tmp_path)
    cfg = load_config(config_path)
    assert cfg.defaults.row_limit is None


def test_config_empty_target_table_valid_with_mode(tmp_path):
    """Tables without target_table are valid -- mode derives the target.
    With curation, target_table is always set to bronze.<name>, so
    we test mode-derived target through pipeline behavior instead."""
    import shutil

    from feather_etl.config import load_config

    src_db = tmp_path / "sample_erp.duckdb"
    shutil.copy2(FIXTURES_DIR / "sample_erp.duckdb", src_db)

    config = {
        "sources": [{"type": "duckdb", "name": "erp", "path": str(src_db)}],
        "destination": {"path": str(tmp_path / "output.duckdb")},
        "mode": "dev",
    }
    config_path = tmp_path / "feather.yaml"
    config_path.write_text(yaml.dump(config, default_flow_style=False))
    write_curation(
        tmp_path,
        [make_curation_entry("erp", "erp.customers", "customers")],
    )

    cfg = load_config(config_path)
    # Curation leaves target_table empty — mode derives it at runtime
    assert cfg.tables[0].target_table == ""
