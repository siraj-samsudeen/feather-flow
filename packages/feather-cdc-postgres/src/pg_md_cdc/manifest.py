"""Registration manifest: parse, hold, validate statically (#3079 D1/D12).

Constraints this file enforces and callers rely on:
  - a manifest NEVER carries a secret value — `dsn_env`/`token_env` are env-var NAMES, and
    nothing here (or in `plan`) may print an env value; use `redact()` on anything unsure.
  - `api_version` is exact; an unknown version is rejected rather than best-effort parsed.
  - field names are the contract with the runtime half (plan Appendix B) — renaming one breaks
    `runner.py`/`stream.py`, which are owned by another module.
"""
from __future__ import annotations

import fnmatch
import os
import pathlib
import re
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Union

import yaml

API_VERSION = "pg-md-cdc/v1"

_IDENT = re.compile(r"^[a-z][a-z0-9_]*$")
_PG_IDENT = re.compile(r"^[a-z_][a-z0-9_$]*$")
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
_QUALIFIED = re.compile(r"^[^.]+\.[^.]+$")

# Which deployment environment a registration belongs to (#4340). It must equal the Railway
# environment the worker runs in, so a dev instance copied from production's settings refuses
# to start production's registration (and the reverse) instead of mixing the two.
PRODUCTION = "production"
ENVIRONMENTS = (PRODUCTION, "dev", "staging")
RAILWAY_ENVIRONMENT_VAR = "RAILWAY_ENVIRONMENT_NAME"

TABLE_CLASSES = (
    "application",
    "source_system",
    "source_operational",
    "loader_internal",
    "warehouse_control",
)


@dataclass(frozen=True)
class Problem:
    """One validation finding. `code` is stable — tests and the CLI exit status key on it."""

    code: str
    severity: str  # "error" | "warning"
    message: str

    def __str__(self) -> str:  # pragma: no cover - formatting only
        return f"[{self.severity}] {self.code}: {self.message}"


@dataclass(frozen=True)
class Selection:
    schemas: List[str] = field(default_factory=lambda: ["*"])
    tables: List[str] = field(default_factory=list)  # "schema.table" fnmatch globs


@dataclass(frozen=True)
class Discovery:
    adapter: str
    options: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Source:
    dsn_env: str
    include: Selection = field(default_factory=Selection)
    exclude: Selection = field(default_factory=lambda: Selection(schemas=[], tables=[]))
    deny: Selection = field(default_factory=lambda: Selection(schemas=[], tables=[]))
    discovery: Optional[Discovery] = None
    # `deny.columns` (#4346): exact "schema.table" -> column names that never leave the CDC
    # process. A projection, not a publication column list: under REPLICA IDENTITY FULL a list
    # must cover every column, so leaving one out makes UPDATE/DELETE fail at the SOURCE.
    deny_columns: Dict[str, List[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class TableSchema:
    """One per-table routing rule: relations matching `match` land in target `schema`."""

    match: str  # "schema.table" fnmatch glob
    schema: str


@dataclass(frozen=True)
class Destination:
    token_env: str
    database: str
    schema_mapping: Union[str, Dict[str, str]] = "preserve_source"
    # Per-table target schemas, first match wins; a relation no rule matches falls back to
    # `schema_mapping`. Lets one source schema fan out into several bronze schemas (D-13).
    table_schemas: List[TableSchema] = field(default_factory=list)
    pipeline_name: Optional[str] = None
    retirement_policy: str = "freeze"  # freeze | archive
    # The MotherDuck account `token_env`'s token must sign in as (#4427). Required: run and
    # resnapshot check `current_user()` against it before creating or writing anything.
    owner: str = ""

    def target_schema(self, source_schema: str, table: Optional[str] = None) -> str:
        if table is not None:
            qualified = f"{source_schema}.{table}"
            for rule in self.table_schemas:
                if fnmatch.fnmatchcase(qualified, rule.match):
                    return rule.schema
        if self.schema_mapping == "preserve_source":
            return source_schema
        if isinstance(self.schema_mapping, dict):
            return self.schema_mapping.get(source_schema, source_schema)
        return source_schema


@dataclass(frozen=True)
class Replication:
    slot: str
    publication: str
    cadence_minutes: int = 5
    tables_without_key: str = "reject"  # reject | append_only
    replica_identity: str = "default"  # full | default
    slot_loss_policy: str = "resnapshot"  # resnapshot | pause
    target_batch_size: int = 1000
    key_overrides: Dict[str, str] = field(default_factory=dict)  # "schema.table" -> append_only


@dataclass(frozen=True)
class Control:
    extractor: str
    availability_policy: str = "pause_if_control_unreachable"
    event_durability: str = "direct_postgres"  # direct_postgres | persistent_local_wal
    wal_dir: Optional[str] = None
    # "schema.table" globs whose row counts this drainer must NOT reconcile: relations it
    # writes to DURING the run can never tie source-now against destination-now.
    reconcile_exclude: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class Rule:
    match: str  # "schema.table" fnmatch glob
    table_class: str


@dataclass(frozen=True)
class Classification:
    rules: List[Rule] = field(default_factory=list)  # first match wins


@dataclass(frozen=True)
class Manifest:
    api_version: str
    source_id: str
    source: Source
    destination: Destination
    replication: Replication
    control: Control
    classification: Classification = field(default_factory=Classification)
    environment: str = "production"
    path: Optional[str] = None


# --------------------------------------------------------------------------- parsing


class ManifestError(ValueError):
    """Malformed enough that no dataclass can be built — unknown key, wrong YAML shape."""


def _sel(raw: Any, *, default_schemas: List[str]) -> Selection:
    if raw is None:
        return Selection(schemas=list(default_schemas), tables=[])
    if not isinstance(raw, dict):
        raise ManifestError(f"expected a mapping for a selection block, got {type(raw).__name__}")
    _reject_unknown(raw, {"schemas", "tables"}, "selection")
    return Selection(
        schemas=list(raw.get("schemas") or default_schemas),
        tables=list(raw.get("tables") or []),
    )


def _deny_columns(raw: Any) -> Dict[str, List[str]]:
    """`source.deny.columns`: a mapping of "schema.table" to a list of column names. Shape
    only — names and keys are policed by validate_static so every finding is listed at once."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ManifestError("source.deny.columns must be a mapping of 'schema.table' to a list of columns")
    out: Dict[str, List[str]] = {}
    for rel, cols in raw.items():
        if isinstance(cols, str) or not isinstance(cols, list):
            raise ManifestError(f"source.deny.columns[{rel}] must be a list of column names")
        out[str(rel)] = [str(c) for c in cols]
    return out


def _reject_unknown(raw: Dict[str, Any], allowed: set, where: str) -> None:
    extra = sorted(set(raw) - allowed)
    if extra:
        raise ManifestError(f"unknown key(s) in {where}: {', '.join(extra)}")


def parse(raw: Dict[str, Any], *, path: Optional[str] = None) -> Manifest:
    """Build a Manifest from already-loaded YAML. Shape errors raise; policy errors are
    reported by validate_static so the CLI can list them all at once."""
    if not isinstance(raw, dict):
        raise ManifestError("manifest must be a YAML mapping")
    _reject_unknown(
        raw,
        {"api_version", "source_id", "environment", "source", "destination", "replication", "control",
         "classification"},
        "manifest",
    )
    src = raw.get("source") or {}
    _reject_unknown(src, {"dsn_env", "include", "exclude", "deny", "discovery"}, "source")
    deny_raw = src.get("deny")
    deny_columns_raw = None
    if isinstance(deny_raw, dict) and "columns" in deny_raw:
        deny_columns_raw = deny_raw["columns"]
        deny_raw = {k: v for k, v in deny_raw.items() if k != "columns"}
    disc_raw = src.get("discovery")
    discovery = None
    if disc_raw:
        _reject_unknown(disc_raw, {"adapter", "options"}, "source.discovery")
        discovery = Discovery(adapter=str(disc_raw.get("adapter", "")), options=dict(disc_raw.get("options") or {}))

    dest = raw.get("destination") or {}
    _reject_unknown(
        dest,
        {"token_env", "owner", "database", "schema_mapping", "table_schemas", "pipeline_name",
         "retirement_policy"},
        "destination",
    )
    table_schemas = []
    for entry in dest.get("table_schemas") or []:
        if not isinstance(entry, dict):
            raise ManifestError("destination.table_schemas entries must be mappings {match, schema}")
        _reject_unknown(entry, {"match", "schema"}, "destination.table_schemas[]")
        table_schemas.append(TableSchema(match=str(entry.get("match", "")), schema=str(entry.get("schema", ""))))
    rep = raw.get("replication") or {}
    _reject_unknown(
        rep,
        {
            "slot",
            "publication",
            "cadence_minutes",
            "tables_without_key",
            "replica_identity",
            "slot_loss_policy",
            "target_batch_size",
            "key_overrides",
        },
        "replication",
    )
    ctl = raw.get("control") or {}
    _reject_unknown(
        ctl,
        {"extractor", "availability_policy", "event_durability", "wal_dir", "reconcile_exclude"},
        "control",
    )
    cls_raw = raw.get("classification") or {}
    _reject_unknown(cls_raw, {"rules"}, "classification")
    rules = [Rule(match=str(r["match"]), table_class=str(r["table_class"])) for r in (cls_raw.get("rules") or [])]

    return Manifest(
        api_version=str(raw.get("api_version", "")),
        source_id=str(raw.get("source_id", "")),
        source=Source(
            dsn_env=str(src.get("dsn_env", "")),
            include=_sel(src.get("include"), default_schemas=["*"]),
            exclude=_sel(src.get("exclude"), default_schemas=[]),
            deny=_sel(deny_raw, default_schemas=[]),
            discovery=discovery,
            deny_columns=_deny_columns(deny_columns_raw),
        ),
        destination=Destination(
            token_env=str(dest.get("token_env", "")),
            database=str(dest.get("database", "")),
            schema_mapping=dest.get("schema_mapping", "preserve_source"),
            table_schemas=table_schemas,
            pipeline_name=dest.get("pipeline_name"),
            retirement_policy=str(dest.get("retirement_policy", "freeze")),
            owner=str(dest.get("owner") or ""),
        ),
        replication=Replication(
            slot=str(rep.get("slot", "")),
            publication=str(rep.get("publication", "")),
            cadence_minutes=int(rep.get("cadence_minutes", 5)),
            tables_without_key=str(rep.get("tables_without_key", "reject")),
            replica_identity=str(rep.get("replica_identity", "default")),
            slot_loss_policy=str(rep.get("slot_loss_policy", "resnapshot")),
            target_batch_size=int(rep.get("target_batch_size", 1000)),
            key_overrides={str(k): str(v) for k, v in (rep.get("key_overrides") or {}).items()},
        ),
        control=Control(
            extractor=str(ctl.get("extractor", "")),
            availability_policy=str(ctl.get("availability_policy", "pause_if_control_unreachable")),
            event_durability=str(ctl.get("event_durability", "direct_postgres")),
            wal_dir=ctl.get("wal_dir"),
            reconcile_exclude=[str(x) for x in (ctl.get("reconcile_exclude") or [])],
        ),
        classification=Classification(rules=rules),
        environment=str(raw.get("environment", "production")),
        path=path,
    )


def load(path: str) -> Manifest:
    """Read one registration file. Never resolves the referenced env vars."""
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return parse(raw, path=str(path))


def pipeline_name(m: Manifest, target_schema: str) -> str:
    """dlt pipeline name for one TARGET schema (= one dlt dataset). An explicit `pipeline_name`
    pins the whole registration to one pipeline — only unambiguous with a single target schema."""
    if m.destination.pipeline_name:
        return m.destination.pipeline_name
    return f"{m.source_id}_cdc__{target_schema}"


# --------------------------------------------------------------------------- redaction

_SECRETISH = re.compile(r"(?i)(password|token|secret|dsn|postgres(ql)?://\S+)")


def redact(text: str, *, env: Optional[Dict[str, str]] = None) -> str:
    """Blank out anything that looks like a credential before it reaches stdout or an event.

    Two passes, because both failure modes have bitten: (1) a DSN pasted into a manifest by
    mistake, (2) an env VALUE interpolated into a message. Values of the env vars a manifest
    names are matched literally, so a token that looks like an ordinary word is still removed.
    """
    env = os.environ if env is None else env
    out = text
    for value in sorted((v for v in env.values() if v and len(v) >= 8), key=len, reverse=True):
        if value in out:
            out = out.replace(value, "***redacted***")
    out = re.sub(r"(?i)\b(postgres(?:ql)?|mysql)://[^\s'\"]+", r"\1://***redacted***", out)
    return out


def redact_problems(problems: List[Problem]) -> List[Problem]:
    return [replace(p, message=redact(p.message)) for p in problems]


# --------------------------------------------------------------------------- validation


def _looks_like_a_value(s: str) -> bool:
    """An env-var REFERENCE that actually contains a value. Cheap and deliberately broad —
    a false positive costs one manifest edit; a false negative leaks a DSN into a log."""
    return "://" in s or "=" in s or " " in s or s != s.strip()


def validate_static(m: Manifest) -> List[Problem]:
    """Everything checkable without touching either database. Ordered, so output is stable."""
    p: List[Problem] = []
    err = lambda code, msg: p.append(Problem(code, "error", msg))  # noqa: E731
    warn = lambda code, msg: p.append(Problem(code, "warning", msg))  # noqa: E731

    if m.api_version != API_VERSION:
        err("api_version", f"api_version must be {API_VERSION!r}, got {m.api_version!r}")
    if not _IDENT.match(m.source_id or ""):
        err("source_id", f"source_id must be a lowercase identifier, got {m.source_id!r}")
    _check_enum(p, "environment", m.environment, ENVIRONMENTS)
    # A non-production mirror may share its MotherDuck owner with production (the dev Featherbase
    # CDC does, Siraj 23-Oct-2026), so the token alone does not keep it out of production bronze.
    # Its database must carry its environment's name; the runtime also refuses any database a
    # production registration owns (runner._refuse_wrong_environment).
    if m.environment != PRODUCTION and not m.destination.database.endswith(f"_{m.environment}"):
        err("environment_database",
            f"a {m.environment!r} registration must write a database ending in "
            f"'_{m.environment}', got {m.destination.database!r}")

    for label, name in (("source.dsn_env", m.source.dsn_env), ("destination.token_env", m.destination.token_env)):
        if not name:
            err("env_ref_missing", f"{label} is required")
        elif _looks_like_a_value(name):
            err("env_ref_is_a_value", f"{label} must be an environment-variable NAME, not a value")
        elif not _ENV_NAME.match(name):
            err("env_ref_name", f"{label}={name!r} is not a valid environment-variable name")

    for label, name in (("replication.slot", m.replication.slot), ("replication.publication", m.replication.publication)):
        if not name:
            err("pg_identifier", f"{label} is required")
        elif not _PG_IDENT.match(name) or len(name) > 63:
            err("pg_identifier", f"{label}={name!r} is not a valid unquoted PostgreSQL identifier")

    if not m.destination.database:
        err("destination_database", "destination.database is required")
    else:
        why = database_name_problem(m.destination.database)
        if why:
            err("destination_database", why)
    why = owner_problem(m.destination.owner)
    if why:
        err("destination_owner", why)
    _check_enum(p, "destination.retirement_policy", m.destination.retirement_policy, ("freeze", "archive"))

    sm = m.destination.schema_mapping
    if sm != "preserve_source":
        if not isinstance(sm, dict):
            err("schema_mapping", "destination.schema_mapping must be 'preserve_source' or a mapping")
        else:
            seen: Dict[str, str] = {}
            for src_schema, tgt in sorted(sm.items()):
                if tgt in seen:
                    err(
                        "schema_mapping_collision",
                        f"schemas {seen[tgt]!r} and {src_schema!r} both map to target schema {tgt!r}",
                    )
                seen[tgt] = src_schema
                _check_target_schema(p, f"destination.schema_mapping[{src_schema}]", tgt)
    for i, rule in enumerate(m.destination.table_schemas):
        if not _QUALIFIED.match(rule.match):
            err("table_schema_match", f"destination.table_schemas[{i}].match {rule.match!r} must be a 'schema.table' glob")
        _check_target_schema(p, f"destination.table_schemas[{i}].schema", rule.schema)
    if m.destination.table_schemas and m.destination.pipeline_name:
        n_targets = len({r.schema for r in m.destination.table_schemas})
        if n_targets > 1:
            err(
                "pipeline_name_ambiguous",
                "destination.pipeline_name pins ONE dlt pipeline, but table_schemas name "
                f"{n_targets} target schemas — one pipeline cannot load several datasets",
            )

    _check_enum(p, "replication.tables_without_key", m.replication.tables_without_key, ("reject", "append_only"))
    _check_enum(p, "replication.replica_identity", m.replication.replica_identity, ("full", "default"))
    _check_enum(p, "replication.slot_loss_policy", m.replication.slot_loss_policy, ("resnapshot", "pause"))
    if m.replication.cadence_minutes <= 0:
        err("cadence", "replication.cadence_minutes must be positive")
    if m.replication.target_batch_size <= 0:
        err("batch_size", "replication.target_batch_size must be positive")
    for rel, policy in sorted(m.replication.key_overrides.items()):
        if not _QUALIFIED.match(rel):
            err("key_override_name", f"key_overrides key {rel!r} must be 'schema.table'")
        if policy == "full_row":
            err(
                "full_row_unsupported",
                f"replication.key_overrides[{rel}]=full_row cannot mirror an UPDATE: pgoutput emits "
                "only the new tuple, so an all-column merge key never matches the old row and both "
                "versions stay — declare append_only for a history, or give the table a key",
            )
        else:
            _check_enum(p, f"replication.key_overrides[{rel}]", policy, ("append_only",))

    for label, sel in (("include", m.source.include), ("exclude", m.source.exclude), ("deny", m.source.deny)):
        for t in sel.tables:
            if not _QUALIFIED.match(t):
                err("pattern_qualified", f"source.{label}.tables entry {t!r} must be 'schema.table'")

    for rel, cols in sorted(m.source.deny_columns.items()):
        if not _QUALIFIED.match(rel) or any(ch in rel for ch in "*?[]"):
            err("deny_column_key", f"source.deny.columns key {rel!r} must be an exact 'schema.table' (no globs)")
        if not cols:
            err("deny_column_name", f"source.deny.columns[{rel}] is empty — name the column(s) to withhold")
        for col in cols:
            if not _PG_IDENT.match(col) or len(col) > 63:
                err("deny_column_name",
                    f"source.deny.columns[{rel}] entry {col!r} is not a valid unquoted column name")

    if m.source.discovery is not None and not m.source.discovery.adapter:
        err("adapter", "source.discovery.adapter is required when a discovery block is present")

    if not m.control.extractor:
        err("extractor", "control.extractor is required")
    if m.control.availability_policy != "pause_if_control_unreachable":
        err(
            "availability_policy",
            f"availability_policy {m.control.availability_policy!r} is not implemented in this runtime",
        )
    _check_enum(
        p, "control.event_durability", m.control.event_durability, ("direct_postgres", "persistent_local_wal")
    )
    for pat in m.control.reconcile_exclude:
        if not _QUALIFIED.match(pat):
            err("reconcile_exclude_pattern",
                f"control.reconcile_exclude entry {pat!r} must be a 'schema.table' glob")
    if m.control.event_durability == "persistent_local_wal":
        if not m.control.wal_dir:
            err("wal_dir_missing", "event_durability=persistent_local_wal requires control.wal_dir")
        elif not _on_a_mount(m.control.wal_dir):
            err(
                "wal_dir_not_persistent",
                f"control.wal_dir={m.control.wal_dir!r} is not on a mount point — a local WAL on "
                "ephemeral container storage is not cross-run crash recovery (D9)",
            )

    for rule in m.classification.rules:
        if not _QUALIFIED.match(rule.match):
            err("rule_pattern", f"classification rule match {rule.match!r} must be 'schema.table'")
        if rule.table_class not in TABLE_CLASSES:
            err("rule_class", f"unknown table_class {rule.table_class!r} (known: {', '.join(TABLE_CLASSES)})")

    if m.replication.replica_identity == "full":
        warn(
            "replica_identity_full_cost",
            "replica_identity: full writes the whole old row into WAL on every UPDATE/DELETE and "
            "takes a brief ACCESS EXCLUSIVE lock per ALTER — `plan` lists the ALTERs before any run",
        )
    return redact_problems(p)


RESERVED_TARGET_SCHEMAS = ("_cdc_runtime",)


def _check_target_schema(p: List[Problem], label: str, name: str) -> None:
    """A target schema is a dlt dataset name AND a MotherDuck schema: an unquoted identifier,
    and never the runtime's own metadata schema."""
    if not _PG_IDENT.match(name or "") or len(name) > 63:
        p.append(Problem("target_schema_identifier", "error",
                         f"{label}={name!r} is not a valid unquoted schema identifier"))
    elif name in RESERVED_TARGET_SCHEMAS or name.endswith("_staging"):
        p.append(Problem("target_schema_reserved", "error",
                         f"{label}={name!r} is reserved (runtime metadata / dlt merge staging)"))


def _check_enum(p: List[Problem], label: str, value: str, allowed: tuple) -> None:
    if value not in allowed:
        p.append(Problem("enum", "error", f"{label}={value!r} must be one of {', '.join(allowed)}"))


def _on_a_mount(path: str) -> bool:
    """True when `path` (or an existing ancestor other than '/') is a mount point. A declared
    volume that resolves to the container root filesystem is exactly the case D9 rejects."""
    cur = os.path.abspath(path)
    while True:
        if os.path.ismount(cur):
            return cur != os.sep
        parent = os.path.dirname(cur)
        if parent == cur:
            return False
        cur = parent


def environment_mismatch(m: Manifest, environ: Optional[Dict[str, str]] = None) -> Optional[str]:
    """Why this registration must not run here, or None (#4340).

    Railway names every deployment's environment in RAILWAY_ENVIRONMENT_NAME. When it is set and
    is not the registration's `environment`, the worker is in the wrong place — typically a dev
    instance whose settings were copied from production. Unset (a laptop, a test) means no check.
    """
    env = os.environ if environ is None else environ
    here = (env.get(RAILWAY_ENVIRONMENT_VAR) or "").strip()
    if here and here != m.environment:
        return (f"registration {m.source_id!r} is for environment {m.environment!r} but this "
                f"worker runs in Railway environment {here!r} — refusing to touch any database")
    return None


def database_name_problem(database: str) -> Optional[str]:
    """Why `database` is not a bare identifier, or None (#4340).

    The destination connects to `md:<database>`, which also accepts connection parameters and
    other syntax, so `bronze_featherbase?session_hint=x_dev` would pass a suffix check and still
    open bronze_featherbase. Only a full-string lowercase identifier is a database name here.
    """
    if not re.fullmatch(r"[a-z][a-z0-9_]*", database or "") or len(database) > 63:
        return (f"destination.database must be a bare identifier ([a-z][a-z0-9_]*), "
                f"got {database!r}")
    return None


def owner_problem(owner: str) -> Optional[str]:
    """Why `owner` is not a usable MotherDuck account name, or None (#4427)."""
    if not owner:
        return ("destination.owner is required: the MotherDuck account the token must sign in "
                "as (dbt_runner/dbt/seeds/access_control/layer_access_registry.csv)")
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@+-]*", owner) or len(owner) > 255:
        return f"destination.owner must be a MotherDuck account name, got {owner!r}"
    return None


def production_databases(registrations_dir: str) -> Dict[str, str]:
    """{destination database: source_id} for every production registration in the directory."""
    out: Dict[str, str] = {}
    for path in sorted(pathlib.Path(registrations_dir).glob("*.yml")):
        other = load(str(path))
        if other.environment == PRODUCTION:
            out[other.destination.database] = other.source_id
    return out


def errors(problems: List[Problem]) -> List[Problem]:
    return [x for x in problems if x.severity == "error"]
