"""Extract transform-layer and bronze-layer dependencies from SQL via
sqlglot AST walk.

Used by ``transforms.parse_transform_file`` to auto-infer DAG edges
(silver / gold) so operators don't have to repeat themselves with
``-- depends_on:`` comments that duplicate the ``FROM``/``JOIN`` clauses
below them, AND by ``transforms.check_bronze_dependencies`` to derive the
advisory bronze-existence input from the same SQL body (so there is one
source of truth for *all* dependency information).

See GitHub issue #54.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

TRANSFORM_SCHEMAS: frozenset[str] = frozenset({"silver", "gold"})
BRONZE_SCHEMA: str = "bronze"

# Statement node types where an ``exp.Table`` child is the *target*
# (output) of a write — not a dependency. The SELECT body that produces
# the rows is read separately; only its FROM/JOIN refs become edges.
#
# sqlglot's class names for ALTER vary across versions: older releases
# expose ``exp.AlterTable``, newer ones expose ``exp.Alter``. We probe
# for both and include whichever exists.
_WRITE_TARGET_PARENTS: tuple[type, ...] = tuple(
    cls
    for cls in (
        exp.Create,
        exp.Insert,
        exp.Drop,
        getattr(exp, "AlterTable", None),
        getattr(exp, "Alter", None),
        exp.Delete,
        exp.Update,
    )
    if cls is not None
)


class TransformDepParseError(ValueError):
    """Raised when sqlglot cannot parse a transform's SQL body."""


def _walk_tables_in_schemas(
    sql: str, schemas: frozenset[str], dialect: str
) -> list[str]:
    """Internal helper: walk the AST of ``sql`` and return every
    ``exp.Table`` whose ``db`` is in ``schemas``, deduped + sorted as
    ``"<schema>.<name>"``.
    """
    try:
        tree = sqlglot.parse_one(sql, dialect=dialect)
    except ParseError as e:
        raise TransformDepParseError(f"sqlglot parse failed: {e}") from e

    refs: set[str] = set()
    for table in tree.find_all(exp.Table):
        if table.db not in schemas:
            continue
        # Skip tables that are the *target* of a CREATE/INSERT/DROP/etc.
        # — they're outputs, not dependencies. Only the SELECT body's
        # FROM/JOIN refs are real edges.
        if isinstance(table.parent, _WRITE_TARGET_PARENTS):
            continue
        refs.add(f"{table.db}.{table.name}")
    return sorted(refs)


def extract_dependencies(sql: str, dialect: str = "duckdb") -> list[str]:
    """Return the sorted, deduplicated list of ``silver.*`` / ``gold.*``
    tables referenced anywhere in ``sql``.

    Walks the AST produced by ``sqlglot.parse_one`` and returns every
    ``exp.Table`` whose ``db`` part is in ``TRANSFORM_SCHEMAS``. CTE names,
    string literals, comments, and table-valued functions like
    ``read_csv(...)`` are not ``exp.Table`` nodes and are therefore ignored
    automatically.

    Raises ``TransformDepParseError`` if the SQL cannot be parsed.
    """
    return _walk_tables_in_schemas(sql, TRANSFORM_SCHEMAS, dialect)


def extract_bronze_dependencies(sql: str, dialect: str = "duckdb") -> list[str]:
    """Return the sorted, deduplicated list of ``bronze.*`` tables
    referenced anywhere in ``sql``. Used by the advisory bronze-existence
    check (see ``transforms.check_bronze_dependencies``).

    Same AST-walk guarantees as :func:`extract_dependencies` — CTEs,
    string literals, comments, and TVFs do not match.

    Raises ``TransformDepParseError`` if the SQL cannot be parsed.
    """
    return _walk_tables_in_schemas(sql, frozenset({BRONZE_SCHEMA}), dialect)
