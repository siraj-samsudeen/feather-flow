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
from sqlglot.dialects.duckdb import DuckDB
from sqlglot.errors import ParseError
from sqlglot.parsers.duckdb import DuckDBParser
from sqlglot.tokens import TokenType

TRANSFORM_SCHEMAS: frozenset[str] = frozenset({"silver", "gold"})
BRONZE_SCHEMA: str = "bronze"


class TransformDepParseError(ValueError):
    """Raised when sqlglot cannot parse a transform's SQL body."""


class _LenientAfterDotDuckDBParser(DuckDBParser):
    """DuckDB parser that accepts reserved keywords as the *second* (or
    later) part of a dotted table reference — e.g. ``silver.inner`` or
    ``mydb.silver.order``.

    sqlglot 30.x's stock parser refuses ``INNER``, ``ORDER``, ``GROUP``,
    etc. as table names because they collide with reserved tokens.  In
    real warehouses these collisions happen (table-naming conventions
    don't always avoid keywords), so we relax the rule **only after a
    dot** — the first table part still has to be a normal identifier,
    which keeps ``SELECT FROM WHERE`` failing fast.
    """

    def _parse_table_parts(  # type: ignore[override]
        self,
        schema: bool = False,
        is_db_reference: bool = False,
        wildcard: bool = False,
        fast: bool = False,
    ) -> exp.Expression | None:
        if fast:
            return super()._parse_table_parts(
                schema=schema,
                is_db_reference=is_db_reference,
                wildcard=wildcard,
                fast=fast,
            )

        catalog: exp.Expression | str | None = None
        db: exp.Expression | str | None = None
        table: exp.Expression | str | None = self._parse_table_part(schema=schema)

        def _lenient_part() -> exp.Expression | None:
            return (
                (not schema and self._parse_function(optional_parens=False))
                or self._parse_id_var(any_token=True)
                or self._parse_string_as_identifier()
                or self._parse_placeholder()
            )

        while self._match(TokenType.DOT):
            if catalog:
                table = self.expression(
                    exp.Dot(this=table, expression=_lenient_part())
                )
            else:
                catalog = db
                db = table
                table = _lenient_part() or ""

        if (
            wildcard
            and self._is_connected()
            and (isinstance(table, exp.Identifier) or not table)
            and self._match(TokenType.STAR)
        ):
            if isinstance(table, exp.Identifier):
                table.args["this"] += "*"
            else:
                table = exp.Identifier(this="*")

        if is_db_reference:
            catalog = db
            db = table
            table = None

        if not table and not is_db_reference:
            self.raise_error(f"Expected table name but got {self._curr}")
        if not db and is_db_reference:
            self.raise_error(f"Expected database name but got {self._curr}")

        table = self.expression(exp.Table(this=table, db=db, catalog=catalog))

        comments = []
        for part in table.parts:
            if part_comments := part.pop_comments():
                comments.extend(part_comments)
        if comments:
            table.add_comments(comments)

        changes = self._parse_changes()
        if changes:
            table.set("changes", changes)

        at_before = self._parse_historical_data()
        if at_before:
            table.set("when", at_before)

        pivots = self._parse_pivots()
        if pivots:
            table.set("pivots", pivots)

        return table


_TOKENIZER = DuckDB.Tokenizer()


def _parse(sql: str, dialect: str) -> exp.Expression:
    """Parse ``sql`` with the lenient-after-dot DuckDB parser when
    ``dialect == "duckdb"``; otherwise fall back to vanilla
    ``sqlglot.parse_one``.
    """
    if dialect == "duckdb":
        try:
            tokens = _TOKENIZER.tokenize(sql)
            parser = _LenientAfterDotDuckDBParser()
            trees = parser.parse(tokens, sql)
        except ParseError as e:
            raise TransformDepParseError(f"sqlglot parse failed: {e}") from e
        if not trees or trees[0] is None:
            raise TransformDepParseError(
                f"sqlglot parse failed: empty parse tree for SQL: {sql!r}"
            )
        return trees[0]

    try:
        return sqlglot.parse_one(sql, dialect=dialect)
    except ParseError as e:
        raise TransformDepParseError(f"sqlglot parse failed: {e}") from e


def _walk_tables_in_schemas(
    sql: str, schemas: frozenset[str], dialect: str
) -> list[str]:
    """Internal helper: walk the AST of ``sql`` and return every
    ``exp.Table`` whose ``db`` is in ``schemas``, deduped + sorted as
    ``"<schema>.<name>"``.
    """
    tree = _parse(sql, dialect)

    refs: set[str] = set()
    for table in tree.find_all(exp.Table):
        if table.db in schemas:
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
