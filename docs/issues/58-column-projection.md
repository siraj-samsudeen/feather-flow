# Issue #58 — bronze projection via `transforms/bronze/*.sql`

Pre-design notes pinning the requirements that won't change. Plan: [`docs/plans/2026-05-12-issue-58-column-projection.md`](../plans/2026-05-12-issue-58-column-projection.md).

## Problem

`feather` extracts wide source tables with `SELECT *` even when only a fraction of the columns are needed. The motivating case (rama-dw, 2026-05-12) was a 67M-row × 97-column SQL Server `ZAKYA.dbo.Sales` extract over ODBC where only 32 columns are actually used. The pyodbc bootstrap took ~3.5 hours, and two-thirds of the network bytes and on-disk bronze rows were waste.

## Design choice — projection lives in SQL, beside silver

The original issue draft proposed `column_overrides` inside `discovery/curation.json`. That was rejected in brainstorming for three reasons:

1. **JSON is the wrong surface for column lists.** Issue #54 already established that SQL is the single source of truth for dependency information. Re-introducing column metadata into JSON re-opens the dual-source-of-truth problem.
2. **Bronze must not depend on silver.** Auto-inferring projections from silver SQL (an alternative considered) inverts the layer ordering and turns extract into a downstream-of-transforms step. That would mean editing a silver SELECT silently reshapes the next bronze extract — spooky action at a distance.
3. **Bronze-for-browsing.** Operators routinely query bronze directly (Datasette, ad-hoc DuckDB). The default has to remain "all source columns are preserved at bronze." Inference would silently strip columns no silver consumed yet.

The chosen mechanism: an **optional, per-bronze-table SQL file** under `transforms/bronze/<bronze_table>.sql`. If the file exists, feather parses its SELECT list with sqlglot and uses those columns at extract. If absent, today's `SELECT *` behaviour is preserved (zero regression). Bronze projection is the operator's deliberate, per-table opt-in for rogue wide tables — not a global default and not an inference.

## File shape

```sql
-- transforms/bronze/zakya_dbo_sales.sql
SELECT
    [Quantity],
    [Invoice Date],
    [Branch ID]
FROM bronze.zakya_dbo_sales;
```

- One SQL file per bronze table. File stem matches the sanitized bronze table name produced by `curation._sanitize_bronze_name`.
- A single `SELECT` statement. The `FROM bronze.<table>` clause is there only so the file is lintable and runnable in a SQL IDE; feather extracts column names from the SELECT list and renders them in the source dialect at extract time.
- Identifiers may carry any quoting style — bare, `[...]`, `"..."`, `` `...` `` — sqlglot's parser normalizes. Feather re-quotes per source dialect at extract.

## Strict validation (loud failures, parsed at validate-time)

The bronze projection is a column subset, nothing more. The parser rejects anything that smells like business logic:

- Expressions, function calls, arithmetic (`UPPER(col)`, `col1 + col2`, `CAST(...)`).
- Column aliases (`col AS x`).
- `DISTINCT`, `WHERE`, `JOIN`, `GROUP BY`, `ORDER BY`, `LIMIT`, set ops, subqueries, CTEs.
- Multiple statements in one file.
- `SELECT *` mixed with column names.

A bare `SELECT *` is **allowed** and treated as a no-op (same as no file). It exists so an operator can record an explicit "I considered narrowing this table and chose not to" decision.

## Where casts live

Casts stay in silver SQL (`CAST(col AS DECIMAL(18,3))`). Bronze remains a faithful, type-preserving copy of (a subset of) source columns. The `cast_to` field from the original issue draft is deleted from scope.

## Acceptance

- A `transforms/bronze/zakya_dbo_sales.sql` listing 32 of 97 columns causes `feather run` to emit `SELECT [col1], [col2], ..., [col32] FROM <table>` against SQL Server, and the resulting bronze table contains exactly those columns.
- The same projection file works against Postgres (double-quoted identifiers) and MySQL (backtick-quoted) via sqlglot's dialect rendering.
- A bronze table without a projection file extracts every source column (today's behaviour, no regression).
- A projection file violating any of the strict rules above fails `feather validate` with a parser-aware error pointing at the offending construct.
- `feather validate` reports per-table projection state: either `bronze projection: 32/97 columns` or `bronze projection: default (all columns)`.

## Out of scope

- `cast_to` enforcement (lives in silver SQL).
- Auto-generating bronze projection files from silver SQL (the inversion we rejected).
- Mutating curation.json shape — the `column_overrides` key proposed in the original issue body is not added.
- A YAML alternative to the SQL file.
- Per-environment projections (one file per bronze table is enough; mode-specific projections can come later if needed).

## Related

- Issue #57 (extract heartbeat) and a forthcoming connectorx adoption together turn the long-tail wide-table bootstrap from "3.5 hr unobservable" into "30 min observable". Each lands independently.
- Issue #54 (auto-discovered transform DAG via sqlglot) established sqlglot as the AST parser of choice and the convention of one helper module per SQL-parse concern. This issue follows that pattern (`src/feather_etl/bronze_projections.py`).
