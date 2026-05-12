"""Silver/gold transform engine — parse, discover, order, and execute SQL transforms."""

from __future__ import annotations

import graphlib
import logging
import string
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from feather_etl.transform_deps import extract_dependencies, TransformDepParseError

logger = logging.getLogger(__name__)

VALID_SCHEMAS = {"silver", "gold"}


@dataclass
class TransformMeta:
    """Metadata parsed from a .sql transform file."""

    name: str
    schema: str  # "silver" or "gold"
    sql: str
    depends_on: list[str] = field(default_factory=list)
    materialized: bool = False
    fact_table: str | None = None
    path: Path = field(default_factory=lambda: Path())

    @property
    def qualified_name(self) -> str:
        return f"{self.schema}.{self.name}"


@dataclass
class TransformResult:
    """Result of executing a single transform."""

    name: str
    schema: str
    type: str  # "view" or "table"
    status: str  # "success" or "error"
    error: str | None = None


def parse_transform_file(path: Path) -> TransformMeta:
    """Parse a .sql file into TransformMeta.

    DAG edges (`depends_on`) are derived **solely** from the SQL body via
    :func:`feather_etl.transform_deps.extract_dependencies` — every
    ``silver.*`` / ``gold.*`` table referenced in a ``FROM``/``JOIN``
    clause becomes an edge. CTEs, comments, string literals, and
    table-valued functions like ``read_csv(...)`` do not create edges.

    Header comment lines retain meaning only for information the SQL body
    cannot encode: ``-- materialized: true`` (gold materialisation) and
    ``-- fact_table: <name>`` (join-health-check declaration). The
    ``-- depends_on:`` header is no longer recognised — see GitHub
    issue #54; any such line in an existing file is silently ignored
    (treated as a regular SQL comment).

    Raises ``TransformDepParseError`` if the SQL body cannot be parsed.
    """
    text = path.read_text()
    lines = text.splitlines()

    materialized = False
    fact_table: str | None = None
    sql_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped == "-- materialized: true":
            materialized = True
        elif stripped.startswith("-- fact_table:"):
            fact_table = stripped.removeprefix("-- fact_table:").strip() or None
        else:
            sql_lines.append(line)

    # Strip leading/trailing blank lines from SQL body
    sql = "\n".join(sql_lines).strip()

    name = path.stem  # sales_invoice.sql -> sales_invoice
    schema = path.parent.name  # transforms/silver/ -> silver

    if schema not in VALID_SCHEMAS:
        raise ValueError(
            f"Transform '{path}' is in directory '{schema}', "
            f"expected one of {sorted(VALID_SCHEMAS)}"
        )

    # Derive deps from the SQL body. Issue #54 — sole source of truth.
    try:
        depends_on = extract_dependencies(sql)
    except TransformDepParseError as e:
        raise TransformDepParseError(
            f"Failed to parse transform SQL at {path}: {e}"
        ) from e

    return TransformMeta(
        name=name,
        schema=schema,
        sql=sql,
        depends_on=depends_on,
        materialized=materialized,
        fact_table=fact_table,
        path=path,
    )


def discover_transforms(config_dir: Path) -> list[TransformMeta]:
    """Scan transforms/silver/*.sql and transforms/gold/*.sql under config_dir.

    Returns empty list if the transforms directory does not exist.
    """
    transforms_dir = config_dir / "transforms"
    if not transforms_dir.is_dir():
        return []

    results: list[TransformMeta] = []
    for schema_name in ("silver", "gold"):
        schema_dir = transforms_dir / schema_name
        if not schema_dir.is_dir():
            continue
        for sql_file in sorted(schema_dir.glob("*.sql")):
            results.append(parse_transform_file(sql_file))

    return results


def build_execution_order(transforms: list[TransformMeta]) -> list[TransformMeta]:
    """Topological sort of transforms based on depends_on declarations.

    Dependencies on non-transform schemas (e.g. ``bronze.*``) are treated as
    external and silently ignored — they exist in the extraction layer, not the
    transform layer.  Dependencies on ``silver.*`` or ``gold.*`` that cannot be
    resolved to a discovered transform raise ValueError.  Lets
    graphlib.CycleError propagate on cycles.
    """
    by_name: dict[str, TransformMeta] = {t.qualified_name: t for t in transforms}

    # Validate transform-layer dependencies exist; skip external ones (bronze, etc.)
    for t in transforms:
        for dep in t.depends_on:
            dep_schema = dep.split(".")[0] if "." in dep else ""
            if dep_schema in VALID_SCHEMAS and dep not in by_name:
                raise ValueError(
                    f"Transform '{t.qualified_name}' depends on '{dep}', "
                    f"which was not found among discovered transforms. "
                    f"Known: {sorted(by_name)}"
                )

    # Build dependency graph: node -> set of predecessors (transform-layer only)
    graph: dict[str, set[str]] = {}
    for t in transforms:
        graph[t.qualified_name] = {
            dep for dep in t.depends_on if dep.split(".")[0] in VALID_SCHEMAS
        }

    sorter = graphlib.TopologicalSorter(graph)
    ordered_names = list(sorter.static_order())
    return [by_name[name] for name in ordered_names]


def execute_transforms(
    con: duckdb.DuckDBPyConnection,
    transforms: list[TransformMeta],
    variables: dict[str, str] | None = None,
    force_views: bool = False,
) -> list[TransformResult]:
    """Execute an already-sorted list of transforms against a DuckDB connection.

    Silver transforms become VIEWs. Gold transforms become VIEWs by default,
    or TABLEs when ``materialized=True`` (unless ``force_views=True``,
    which creates everything as VIEWs — used in dev/test mode).
    """
    results: list[TransformResult] = []
    subs = variables or {}

    for t in transforms:
        sql = string.Template(t.sql).safe_substitute(subs)

        if t.materialized and t.schema == "gold" and not force_views:
            # DROP existing VIEW — CREATE OR REPLACE TABLE can't replace a VIEW
            try:
                con.execute(f"DROP VIEW IF EXISTS {t.schema}.{t.name}")
            except duckdb.CatalogException:
                pass  # object exists but isn't a VIEW — TABLE replace handles it
            ddl = f"CREATE OR REPLACE TABLE {t.schema}.{t.name} AS {sql}"
            obj_type = "table"
        else:
            # DROP existing TABLE — CREATE OR REPLACE VIEW can't replace a TABLE
            try:
                con.execute(f"DROP TABLE IF EXISTS {t.schema}.{t.name}")
            except duckdb.CatalogException:
                pass  # object exists but isn't a TABLE — VIEW replace handles it
            ddl = f"CREATE OR REPLACE VIEW {t.schema}.{t.name} AS {sql}"
            obj_type = "view"

        try:
            con.execute(ddl)
            results.append(
                TransformResult(
                    name=t.name,
                    schema=t.schema,
                    type=obj_type,
                    status="success",
                )
            )
            logger.info("Created %s %s.%s", obj_type, t.schema, t.name)
        except Exception as e:
            error_msg = str(e)
            results.append(
                TransformResult(
                    name=t.name,
                    schema=t.schema,
                    type=obj_type,
                    status="error",
                    error=error_msg,
                )
            )
            logger.error(
                "Failed to create %s %s.%s: %s", obj_type, t.schema, t.name, error_msg
            )

    return results


def check_join_health(
    con: duckdb.DuckDBPyConnection,
    transform: TransformMeta,
) -> str | None:
    """Compare gold table row count against its declared fact table.

    Returns a warning message if counts differ, None if healthy.
    Requires ``-- fact_table: silver.X`` comment in the gold SQL file.
    """
    if not transform.fact_table:
        return None

    try:
        gold_count = con.execute(
            f"SELECT COUNT(*) FROM {transform.qualified_name}"
        ).fetchone()[0]
        fact_count = con.execute(
            f"SELECT COUNT(*) FROM {transform.fact_table}"
        ).fetchone()[0]
    except Exception as e:
        return f"Join health check failed for {transform.qualified_name}: {e}"

    if gold_count == fact_count:
        return None

    diff = gold_count - fact_count
    if diff > 0:
        return (
            f"JOIN INFLATION: {transform.qualified_name} has {gold_count:,} rows "
            f"but fact table {transform.fact_table} has {fact_count:,} rows "
            f"(+{diff:,} extra). Check for duplicate keys in dimension tables."
        )
    return (
        f"JOIN LOSS: {transform.qualified_name} has {gold_count:,} rows "
        f"but fact table {transform.fact_table} has {fact_count:,} rows "
        f"({diff:,} missing). Check for INNER JOINs or missing dimension matches."
    )


def rebuild_materialized_gold(
    con: duckdb.DuckDBPyConnection,
    transforms: list[TransformMeta],
    variables: dict[str, str] | None = None,
) -> list[TransformResult]:
    """Re-execute only materialized gold transforms as TABLEs.

    Called after extraction to refresh gold tables with latest data.
    Runs join health checks for transforms with ``-- fact_table:`` declared.
    """
    mat_gold = [t for t in transforms if t.schema == "gold" and t.materialized]
    if not mat_gold:
        return []
    results = execute_transforms(con, mat_gold, variables)

    for t in mat_gold:
        warning = check_join_health(con, t)
        if warning:
            logger.warning(warning)

    return results


def check_bronze_dependencies(
    con: duckdb.DuckDBPyConnection,
    transforms: list[TransformMeta],
) -> list[str]:
    """Return a warning string for each unmet ``bronze.<table>`` dependency.

    Inspects every silver transform's ``-- depends_on:`` declarations and
    checks each ``bronze.<table>`` reference against the destination's
    ``information_schema.tables`` (filtered to the ``bronze`` schema).

    Returns one warning string per unmet dependency, in the order discovered.
    The caller decides whether to render each entry as its own line or
    collapse the list into a summary — that presentation concern is not the
    helper's job. Returns an empty list when every declared bronze dep is
    present (or when no silver transform declares any bronze dep).

    Only ``depends_on`` entries whose schema is exactly ``bronze`` are
    checked. Cross-transform deps (``silver.X``, ``gold.X``) are out of scope
    for this helper — the topological sorter in ``build_execution_order``
    already validates those.
    """
    # Build the set of available bronze tables in a single query.
    rows = con.execute(
        "SELECT table_schema || '.' || table_name "
        "FROM information_schema.tables "
        "WHERE table_schema = 'bronze'"
    ).fetchall()
    available: set[str] = {row[0] for row in rows}

    warnings: list[str] = []
    for t in transforms:
        if t.schema != "silver":
            continue
        for dep in t.depends_on:
            if not dep.startswith("bronze."):
                continue
            if dep not in available:
                warnings.append(
                    f"WARNING: bronze dependency missing: {dep} "
                    f"(run `feather extract` first)"
                )
    return warnings


def run_transforms(config) -> list[TransformResult]:
    """Run discovered silver/gold transforms against the destination DuckDB.

    Discovers transforms from ``config.config_dir``, orders them, opens the
    destination, executes per the resolved mode (prod honors
    ``-- materialized: true``; dev/test forces all to views), and returns one
    ``TransformResult`` per executed transform.

    This helper is the single execution path shared by ``feather run`` (which
    calls it after extraction) and ``feather transform`` (which calls it
    standalone). The body is the post-extract block previously inlined in
    ``pipeline.run_all`` — extracted verbatim to preserve behaviour.

    Returns an empty list when no transforms are discovered.
    """
    from feather_etl.destinations.duckdb import DuckDBDestination

    results: list[TransformResult] = []
    transforms = discover_transforms(config.config_dir)
    if not transforms:
        return results

    ordered = build_execution_order(transforms)
    dest = DuckDBDestination(path=config.destination.path)
    con = dest._connect()
    try:
        if config.mode == "prod":
            # Prod: create all as views first (gold needs silver),
            # then rebuild gold as materialized tables
            exec_results = execute_transforms(con, ordered)
            results.extend(exec_results)
            rebuild_results = rebuild_materialized_gold(con, ordered)
            count = sum(1 for r in rebuild_results if r.status == "success")
            if count:
                logger.info("Rebuilt %d materialized gold table(s)", count)
            results.extend(rebuild_results)
        else:
            # Dev/test: create everything as views (force_views)
            exec_results = execute_transforms(con, ordered, force_views=True)
            results.extend(exec_results)
    finally:
        con.close()

    return results
