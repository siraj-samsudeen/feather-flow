"""Configuration parsing and validation for feather-etl."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from dotenv import load_dotenv

if TYPE_CHECKING:
    from feather_etl.sources import Source

_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def _sanitize(segment: str) -> str:
    """Replace any char outside [A-Za-z0-9._-] with underscore. Preserves dots and hyphens."""
    return _UNSAFE_CHARS.sub("_", segment)


def resolved_source_name(cfg: "Source") -> str:
    """Return the sanitized identity used in discover output filenames.

    If cfg.name is set, sanitize and return it. Otherwise derive:
      - DB sources (sqlserver, postgres): '<type>-<host>'
      - CSV (path is a directory): 'csv-<dirname>'
      - Other file sources (sqlite, duckdb, excel, json): '<type>-<basename-without-ext>'
    Falls back to '<type>-unknown' when the relevant field is missing.
    """
    if cfg.name:
        return _sanitize(cfg.name)

    # File source — has .path
    path = getattr(cfg, "path", None)
    if path is not None:
        if cfg.type == "csv":
            basename = path.name
        else:
            basename = path.stem
        return _sanitize(f"{cfg.type}-{basename}")

    # DB source — has .host
    host = getattr(cfg, "host", None) or "unknown"
    return _sanitize(f"{cfg.type}-{host}")


def schema_output_path(cfg: "Source") -> Path:
    """Return the target Path for `feather discover` JSON output.

    Format:
      - Explicit source name: ./schema_<type>_<name>.json
      - Auto-derived name:    ./schema_<name>.json
    """
    stem = resolved_source_name(cfg)
    if getattr(cfg, "_explicit_name", False):
        stem = f"{cfg.type}_{stem}"
    return Path(f"schema_{stem}.json")


VALID_STRATEGIES = {"full", "incremental", "append"}
VALID_SCHEMA_PREFIXES = {"bronze", "silver", "gold"}
_SQL_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_UNRESOLVED_ENV_RE = re.compile(r"\$\{([^}]+)\}")


@dataclass
class DestinationConfig:
    path: Path


@dataclass
class ExtractDefaultsConfig:
    heartbeat_every_rows: int = 100_000
    heartbeat_every_seconds: int = 30
    # Bucket-size thresholds for the pre-flight planner (issue #63).
    t_small_rows: int = 100_000
    t_large_rows: int = 5_000_000
    t_narrow_cols: int = 25
    t_wide_cols: int = 60


@dataclass
class DefaultsConfig:
    overlap_window_minutes: int = 2
    batch_size: int = 120_000
    row_limit: int | None = None
    extract: ExtractDefaultsConfig = field(default_factory=ExtractDefaultsConfig)


@dataclass
class TableConfig:
    name: str
    source_table: str
    strategy: str
    target_table: str = ""
    primary_key: list[str] | None = None
    timestamp_column: str | None = None
    checksum_columns: list[str] | None = None
    filter: str | None = None
    quality_checks: dict | None = None
    column_map: dict[str, str] | None = None
    schedule: str | None = None
    dedup: bool = False
    dedup_columns: list[str] | None = None
    source_name: str | None = None  # resolved source name from curation
    database: str | None = None  # resolved database from curation


@dataclass
class AlertsConfig:
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    alert_to: str
    alert_from: str = ""  # defaults to smtp_user if empty


@dataclass
class FeatherConfig:
    sources: list  # list[Source] — forward ref
    destination: DestinationConfig
    tables: list[TableConfig]
    defaults: DefaultsConfig = field(default_factory=DefaultsConfig)
    config_dir: Path = field(default_factory=lambda: Path("."))
    alerts: AlertsConfig | None = None


def _resolve_env_vars(text: str) -> str:
    return os.path.expandvars(text)


def _resolve_yaml_env_vars(data: dict | list | str) -> dict | list | str:
    """Recursively resolve ${VAR} in all string values."""
    if isinstance(data, dict):
        return {k: _resolve_yaml_env_vars(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_resolve_yaml_env_vars(item) for item in data]
    if isinstance(data, str):
        return _resolve_env_vars(data)
    return data


def _resolve_path(config_dir: Path, raw: str) -> Path:
    """Resolve a path relative to config file directory, not CWD."""
    p = Path(raw)
    if p.is_absolute():
        return p
    return (config_dir / p).resolve()


def _validate(config: FeatherConfig) -> list[str]:
    """Validate config, return list of error messages."""
    errors: list[str] = []

    if not config.destination.path.parent.exists():
        errors.append(
            f"Destination directory does not exist: {config.destination.path.parent}"
        )

    if config.defaults.overlap_window_minutes < 0:
        errors.append(
            f"overlap_window_minutes must be >= 0, "
            f"got {config.defaults.overlap_window_minutes}"
        )

    from feather_etl.curation import resolve_source

    for table in config.tables:
        if table.strategy not in VALID_STRATEGIES:
            errors.append(
                f"Table '{table.name}': invalid strategy '{table.strategy}'. "
                f"Valid: {sorted(VALID_STRATEGIES)}"
            )

        if table.target_table:  # explicit target — validate schema prefix
            if "." in table.target_table:
                schema_prefix, table_part = table.target_table.split(".", 1)
                if schema_prefix not in VALID_SCHEMA_PREFIXES:
                    errors.append(
                        f"Table '{table.name}': target_table schema '{schema_prefix}' "
                        f"must be one of {sorted(VALID_SCHEMA_PREFIXES)}"
                    )
                if not _SQL_IDENTIFIER_RE.match(table_part):
                    errors.append(
                        f"Table '{table.name}': target name '{table_part}' contains "
                        f"invalid characters. Use letters, digits, and underscores only."
                    )
            else:
                errors.append(
                    f"Table '{table.name}': target_table '{table.target_table}' "
                    f"must include a schema prefix (e.g., bronze.{table.target_table})"
                )
        # empty target_table is valid — defaults to bronze.<name> at runtime

        if table.strategy == "incremental" and not table.timestamp_column:
            errors.append(
                f"Table '{table.name}': strategy 'incremental' requires "
                f"a timestamp_column."
            )

        if table.dedup and table.dedup_columns:
            errors.append(
                f"Table '{table.name}': dedup and dedup_columns are mutually "
                f"exclusive — use one or the other."
            )

        # Source-type-aware source_table validation — resolve per-table source.
        try:
            source = resolve_source(
                table.database or table.source_name or "",
                config.sources,
            )
        except ValueError:
            source = config.sources[0]
        for err in source.validate_source_table(table.source_table):
            errors.append(f"Table '{table.name}': {err}")

    return errors


def _check_unresolved_env_vars(data: dict | list | str, path: str = "") -> list[str]:
    """Check for unresolved ${VAR} patterns after env var expansion."""
    errors: list[str] = []
    if isinstance(data, dict):
        for k, v in data.items():
            errors.extend(_check_unresolved_env_vars(v, f"{path}.{k}" if path else k))
    elif isinstance(data, list):
        for i, v in enumerate(data):
            errors.extend(_check_unresolved_env_vars(v, f"{path}[{i}]"))
    elif isinstance(data, str):
        match = _UNRESOLVED_ENV_RE.search(data)
        if match:
            errors.append(
                f"Unresolved environment variable ${{{match.group(1)}}} "
                f"in '{path}'. Set the variable or remove it from config."
            )
    return errors


def load_config(
    config_path: Path,
    validate: bool = True,
) -> FeatherConfig:
    """Load and validate feather.yaml, raising ValueError on invalid config."""
    config_dir = config_path.parent.resolve()

    # Auto-load .env from the config directory so users don't have to
    # manually export variables before running feather commands (closes #1).
    # override=False respects any variables the user already exported in
    # their shell, which is important for CI/CD where secrets come from
    # the environment, not a committed .env file.
    dotenv_path = config_dir / ".env"
    if dotenv_path.is_file():
        load_dotenv(dotenv_path, override=False)

    raw = yaml.safe_load(config_path.read_text())
    raw = _resolve_yaml_env_vars(raw)

    env_errors = _check_unresolved_env_vars(raw)
    if env_errors:
        raise ValueError("; ".join(env_errors))

    if "mode" in raw:
        import warnings

        warnings.warn(
            "'mode:' key in feather.yaml is ignored and will be removed. "
            "Remove it from your config.",
            UserWarning,
            stacklevel=2,
        )

    if "source" in raw and "sources" not in raw:
        raise ValueError(
            "feather.yaml now uses 'sources:' (list). Wrap your existing source in a list:\n"
            "  sources:\n"
            "    - name: ...\n"
            "      type: ...\n"
        )

    if "sources" not in raw or "destination" not in raw:
        missing = "sources" if "sources" not in raw else "destination"
        raise ValueError(f"Missing required config section: '{missing}'")

    sources_raw = raw["sources"]
    if not isinstance(sources_raw, list) or not sources_raw:
        raise ValueError("'sources' must be a non-empty list.")

    from feather_etl.sources.registry import get_source_class

    sources: list = []
    seen_names: set[str] = set()
    multi = len(sources_raw) > 1
    for idx, entry in enumerate(sources_raw):
        if not isinstance(entry, dict):
            raise ValueError(
                f"sources[{idx}] must be a mapping (dict), got {type(entry).__name__}."
            )
        if "type" not in entry:
            raise ValueError(f"sources[{idx}] missing required field 'type'.")
        if multi and not entry.get("name"):
            raise ValueError(
                f"sources[{idx}]: 'name' is required when multiple sources are configured."
            )
        src_cls = get_source_class(entry["type"])
        src = src_cls.from_yaml(entry, config_dir)
        # Resolve display name when single-entry + name omitted.
        if not src.name and not multi:
            src.name = resolved_source_name(src)
        if src.name in seen_names:
            raise ValueError(
                f"duplicate source name '{src.name}'; names must be unique."
            )
        seen_names.add(src.name)
        sources.append(src)

    dest = DestinationConfig(
        path=_resolve_path(config_dir, raw["destination"]["path"]),
    )

    defaults_raw = raw.get("defaults", {})
    extract_raw = defaults_raw.get("extract", {})
    defaults = DefaultsConfig(
        overlap_window_minutes=defaults_raw.get("overlap_window_minutes", 2),
        batch_size=defaults_raw.get("batch_size", 120_000),
        row_limit=defaults_raw.get("row_limit"),
        extract=ExtractDefaultsConfig(
            heartbeat_every_rows=extract_raw.get("heartbeat_every_rows", 100_000),
            heartbeat_every_seconds=extract_raw.get("heartbeat_every_seconds", 30),
            t_small_rows=extract_raw.get("t_small_rows", 100_000),
            t_large_rows=extract_raw.get("t_large_rows", 5_000_000),
            t_narrow_cols=extract_raw.get("t_narrow_cols", 25),
            t_wide_cols=extract_raw.get("t_wide_cols", 60),
        ),
    )

    from feather_etl.curation import load_curation_tables

    try:
        tables = load_curation_tables(config_dir)
    except (FileNotFoundError, ValueError):
        if validate:
            raise
        tables = []

    # Parse optional alerts section
    alerts: AlertsConfig | None = None
    alerts_raw = raw.get("alerts")
    if alerts_raw:
        _ALERTS_REQUIRED = (
            "smtp_host",
            "smtp_port",
            "smtp_user",
            "smtp_password",
            "alert_to",
        )
        missing = [f for f in _ALERTS_REQUIRED if f not in alerts_raw]
        if missing:
            raise ValueError(
                f"alerts section missing required field(s): {', '.join(missing)}"
            )
        alerts = AlertsConfig(
            smtp_host=alerts_raw["smtp_host"],
            smtp_port=int(alerts_raw["smtp_port"]),
            smtp_user=alerts_raw["smtp_user"],
            smtp_password=alerts_raw["smtp_password"],
            alert_to=alerts_raw["alert_to"],
            alert_from=alerts_raw.get("alert_from") or alerts_raw["smtp_user"],
        )

    config = FeatherConfig(
        sources=sources,
        destination=dest,
        tables=tables,
        defaults=defaults,
        config_dir=config_dir,
        alerts=alerts,
    )

    if validate:
        errors = _validate(config)
        if errors:
            raise ValueError("; ".join(errors))

    return config


def write_validation_json(
    config_path: Path,
    config: FeatherConfig | None,
    errors: list[str] | None = None,
) -> Path:
    """Write feather_validation.json alongside feather.yaml."""
    validation_path = config_path.parent / "feather_validation.json"
    if errors is None:
        errors = []

    result = {
        "valid": config is not None and len(errors) == 0,
        "errors": errors,
        "tables_count": len(config.tables) if config else 0,
        "resolved_paths": {
            "source": str(getattr(config.sources[0], "path", None))
            if config and getattr(config.sources[0], "path", None)
            else None,
            "destination": str(config.destination.path) if config else None,
            "config_dir": str(config.config_dir) if config else None,
        }
        if config
        else {},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    validation_path.write_text(json.dumps(result, indent=2))
    return validation_path
