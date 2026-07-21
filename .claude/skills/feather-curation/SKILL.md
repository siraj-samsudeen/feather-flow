---
name: feather-curation
description: Use when populating or updating a `curation.json` file from feather-flow schema JSON outputs. Merges newly discovered tables into existing curation without overwriting prior decisions. Triggers on requests like "populate curation", "update curation from discover", "add new tables to curation", or after running `feather discover`.
---

# feather-curation

Turn the output of `feather discover` into a durable curation file that records, for every discovered table, whether we ingest it, how, and why.

## When to use

- After `feather discover` produces `schema_*.json` files and you want to record include/exclude/review decisions for each table.
- When new tables have appeared in the source since the last discovery and curation.json needs to be reconciled.
- Whenever the user says "populate curation", "update curation", "add new tables to curation", or similar.

Do NOT use this skill to generate feather.yaml's `tables:` block — that is a separate step (a generator script reads curation.json and produces the block). This skill only manages curation.json itself.

## Core principle: MERGE, never overwrite

`curation.json` records human and agent decisions about data. Those decisions are expensive to re-derive and easy to lose. When curation.json already exists, this skill treats it as authoritative for existing entries and only adds new ones.

Concretely:

1. **Preserve existing entries**. For every table already in curation.json, keep `decision`, `tier`, `strategy`, `primary_key`, `timestamp_column`, `target_table`, `reason`, and `columns` unchanged.
2. **Add new entries only**. For tables present in schema JSONs but absent from curation.json, append a new entry. Default new entries to `decision: "review"` — do NOT auto-classify as include/exclude without user confirmation.
3. **Mark stale entries**. For tables present in curation.json but absent from the current schema JSONs, add `"status": "stale"` to the entry and set `updated_at` on that entry. Do NOT delete — the user may have renamed the table, temporarily lost access, or removed it from the `databases:` list.
4. **Only update `updated_at` where data actually changed**. Unchanged entries keep their old timestamp. This preserves a meaningful audit trail.
5. **If curation.json does not exist**, create it with every discovered table set to `decision: "review"`. The user/agent will classify in a follow-up pass.

## File shape

```json
{
  "version": 1,
  "updated_at": "ISO-8601 timestamp",
  "notes": "optional freeform note",
  "tables": [
    {
      "source_db": "string",
      "source_table": "string (e.g., 'dbo.POS_Sales')",
      "fq_name": "string (e.g., 'ZAKYA.dbo.POS_Sales') — unique lookup key",
      "decision": "include" | "exclude" | "review",
      "tier": 1 | 2 | 3 | 4 | null,
      "strategy": "incremental" | "full" | "append" | null,
      "primary_key": ["col1", "col2"] | null,
      "timestamp_column": "string" | null,
      "target_table": "string (e.g., 'bronze.zakya_pos_sales')" | null,
      "reason": "string — why this decision",
      "columns": null,
      "updated_at": "ISO-8601 timestamp",
      "status": "stale"   // only present if table no longer exists in source
    }
  ]
}
```

### Field rules

- **`fq_name`** is the primary lookup key. Format: `<source_db>.<source_table>` (including the `dbo.` schema prefix if present in the source). Must be unique.
- **`decision`** is three-valued. `review` means "undecided" — explicitly different from `exclude`.
- **`tier`** is `null` when decision is `exclude` or `review` with no tentative assignment. Tier captures priority: 1 = ingest first, 4 = exclude.
- **`strategy`**, **`primary_key`**, **`timestamp_column`**, **`target_table`** are all `null` when decision is `exclude` or `review`. They are only populated for `include`.
- **`reason`** is required for every entry. Even for includes — future readers need to know why a particular PK / watermark was chosen.
- **`columns`** is reserved `null` for now. Future column-level curation will nest per-column overrides here.

## Classification rubric — for NEW entries (after user confirmation)

After defaulting new tables to `review`, you may propose classifications. Surface your proposals to the user before writing `include`/`exclude` decisions.

### Tier 1 — include, incremental
- Has a typed timestamp column (`SyncDate`, `syncDate`, `last_modified_time`, `ModifiedAt`, `SysDate`). The column must be an actual date/datetime type, not nvarchar.
- Has an identifier or composite key that can serve as primary key.
- Columns are properly typed (decimals/dates/ints, not all-nvarchar).
- Fact-table shape: transactions, postings, line items, events.

### Tier 2 — include, full
- Small master / reference / dimension table (conceptually < ~50k rows).
- Has a clear identifier but no reliable watermark.
- Full reload each run is cheap.

### Tier 3 — review
- Table exists but purpose is ambiguous.
- Name hints at derivation: `*_Combined`, `*_Consolidated`, `*_Summary`, `*_Clean`, `*_Audit`, `T_*` prefix.
- Wide nvarchar dumps where analytics value is unclear.
- Appears to duplicate another table (e.g., `Vendor` and `Vendor_B`).
- Domain-specific tables where stakeholder confirmation is needed.

### Tier 4 — exclude
- `sysdiagrams` — SQL Server SSMS internal, not data.
- Auth / user / session tables containing passwords, tokens, or PII.
- All-nvarchar tables whose monetary columns lose precision without upstream retyping.
- Confirmed pipeline outputs (stakeholder says "that's the output of X job").
- Temp tables (`T_*`, `*_bak`, `*_temp`, `*_scratch`).

## Target table naming convention

`target_table` lands the raw data in the bronze layer of the destination DB. Convention:

```
bronze.<source_db_lower>_<source_table_lower_no_schema>
```

Rules:
- Lowercase both `source_db` and `source_table` parts.
- Strip the `dbo.` schema prefix from `source_table`.
- Replace any non-alphanumeric characters with underscores.
- **Preserve source typos in the table name** — do not auto-correct. Downstream joins reference the source, so consistency > prettiness.

Examples:
- `ZAKYA.dbo.POS_Sales` → `bronze.zakya_pos_sales`
- `SAP.dbo.Vendor_B` → `bronze.sap_vendor_b`
- `SAP.dbo.SalesB` → `bronze.sap_sales_b`
- `Gofrugal.dbo.GofugalStoreMas` → `bronze.gofrugal_gofugal_store_mas` (typo preserved)

Do not lowercase or rename **columns** in any metadata. Column names in `primary_key` and `timestamp_column` must match the source exactly, including spaces, typos, and trailing whitespace.

## Output checklist

When this skill completes a curation pass, report to the user:

1. Total tables in curation.json before / after.
2. How many new entries were added (with names).
3. How many existing entries were preserved unchanged.
4. How many entries are now marked `stale`.
5. Whether any new entries still need classification (listed as `decision: "review"` with no tentative tier).

If there are tables the user still needs to decide on, list them explicitly so the user can act.

## Anti-patterns — do not do these

- **Do not delete stale entries**. They may come back next discovery.
- **Do not overwrite existing decisions**. Even if you think a prior decision was wrong, raise it with the user first.
- **Do not auto-classify new tables as include/exclude** without user confirmation. Default is `review`.
- **Do not modify curation.json if nothing has changed**. A no-op write still invalidates the root `updated_at`.
- **Do not rename columns** (lowercasing, stripping spaces) when writing primary_key/timestamp_column. The source is the truth.
- **Do not flatten `fq_name`** (e.g., dropping the schema prefix) to "clean it up". Uniqueness beats aesthetics.

## Interaction pattern — typical flow

1. User says: "update curation from the latest discover output" (or similar).
2. Read all `schema_*.json` files in the discovery directory (typically `./` or `./discovery/`).
3. Read existing `curation.json` if present.
4. Compute the merge: set of tables to add (new in schema, not in curation), set of tables to mark stale (in curation, not in schema), set unchanged.
5. Write the merged curation.json.
6. Report counts and list unclassified new entries.
7. If the user confirms proposed classifications for new entries, apply them and rewrite curation.json.

Keep the write atomic. Do not partially update the file — if anything fails mid-merge, leave the old file intact and report the error.
