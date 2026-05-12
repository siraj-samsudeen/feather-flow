# Issue #54 — Auto-discover transform DAG from SQL refs

**GitHub:** https://github.com/siraj-samsudeen/feather-etl/issues/54
**Status:** implemented (see `docs/superpowers/plans/2026-05-11-auto-discover-transform-dag.md`)

## Why this exists

Before this change, the transform DAG was built from `-- depends_on:` header
comments that paraphrased the `FROM`/`JOIN` clauses one line below. Two sources
of truth, manually kept in sync. Silent drift was possible whenever an operator
added a `JOIN` and forgot to update the header.

## What changed

`parse_transform_file` now derives `TransformMeta.depends_on` **solely** from
the SQL body via `feather_etl.transform_deps.extract_dependencies`. It parses
the SQL with `sqlglot`, walks every `exp.Table` node, and adds an edge for
every `silver.*` / `gold.*` reference. CTE names, string literals, comments,
and table-valued functions like `read_csv(...)` are not `exp.Table` nodes in
sqlglot's AST, so they are not treated as edges — no phantom dependencies.

The `-- depends_on:` header is no longer part of the transform contract. The
parser branch that recognised it has been removed. Any legacy `.sql` file
that still contains the header is unaffected: the line falls through to the
SQL body and sqlglot strips it as a comment at parse time. There are zero
`.sql` transform files on disk in this repository at the time of the change.

`-- materialized: true` and `-- fact_table: <name>` headers retain their
original meaning — they encode information the SQL body cannot express.

## Why AST parsing, not regex; why not `{{ ref(...) }}`

See the issue body. The short version: a regex matches comments and string
literals; dbt's `ref()` exists primarily for environment-aware schema
resolution (which feather-etl doesn't need because its schemas are fixed).

## Failure modes

If sqlglot cannot parse a transform's SQL, `parse_transform_file` raises
`feather_etl.transform_deps.TransformDepParseError` with the file path. This
is intentional: a transform whose SQL is unparseable is a transform whose DAG
edges cannot be trusted, so we surface it at discover-time rather than at
execute-time. The same error is raised for a header-only file with no SELECT
body.

A transform that references a non-existent `silver.X` (whether via SQL body
or — historically — via header) still raises the same `ValueError` from
`build_execution_order` as before. The error message and the validation path
in `build_execution_order` are unchanged.

## Scope

In scope: `silver.*` and `gold.*` references (the transform schemas defined in
`docs/architecture/warehouse-layers.md`).

Out of scope: column-level lineage; full Jinja templating; `source()` macros
for bronze references; cross-project refs.

## No backward compatibility

The `-- depends_on:` syntax is removed without a deprecation period. This is
safe because:

- Zero `.sql` transform files exist on disk in the feather-etl repository
  itself at the time of the change.
- Legacy headers in any *consumer* repo silently no-op (treated as SQL
  comments). Operators may discover the deletion via a missing-dependency
  error from `build_execution_order` only if their SQL body fails to
  reference a table that was *previously* declared via header only — a
  pathological case (it would have meant the SQL body and the header were
  already inconsistent under the old contract).

If a future operator needs an explicit data-only dependency (a transform
whose data must exist before this one, but which this one doesn't `SELECT`
from), the recommended workaround is to add a no-op `LEFT JOIN
<dep> ON FALSE` clause to the SQL body — this keeps the contract simple and
the DAG fully recoverable from SQL alone. If the workaround feels ugly in
practice, re-introducing `-- depends_on:` as an explicit-override syntax is
a possible future change.
