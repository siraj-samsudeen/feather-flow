# Warehouse layers — canonical definitions

**Version:** 1.0
**Date:** 2026-04-21
**Status:** Stable — referenced by every transform design, every porting decision
**Personas reference:** [`docs/personas.md`](../personas.md) v1.2

| Version | Changes |
|---|---|
| 1.0 | Initial version. Four-layer model: Bronze → Silver → Gold → Departmental Data Marts. Gold tightened to the Kimball dimensional model (facts + conformed dimensions); denormalized per-consumer outputs moved to Marts. Extracted during issue #26 brainstorming after noting that `docs/prd.md` v1.8 pinned Bronze/Silver/Gold but left "data mart" undefined across the repo. |

---

## Purpose

feather-etl's warehouse model is the Medallion architecture (Bronze → Silver → Gold) extended with a fourth Kimball-style layer for Departmental Data Marts. Every transform written in a feather-etl deployment lives in exactly one of these four layers. This document pins what belongs in each — and, equally important, what does not.

Agents and humans working on feather-etl should read this doc **before** writing any transform SQL, before deciding where to port a source view's logic, and before answering an Analyst's question about where a number lives. When in doubt during implementation, ask: *"Which layer does this belong in, and what is the evidence?"*

The binding rule: **every layer serves at least one of the primary personas (`personas.md`) or artifact consumers.** Features that would add a transform to no layer, or that blur the boundary between layers, require explicit justification before landing.

---

## The four layers

### 1. Bronze — faithful replica

**Purpose.** A cleaned-up copy of the relevant slice of source rows, preserving source-system-native names and shapes. Bronze is what the Builder caches to avoid hammering the ERP during silver/gold iteration. For regulated clients it is also the audit substrate — append-only, with enough breadcrumbs to reconstruct any historical run.

**What goes in.**

- One schema per source, tables named by their source-native names (`bronze.ORDR`, `bronze.SalesHeader`, `bronze.SI_MAST`).
- All rows that survive the YAML `filter:` clause — the `filter:` is feather-etl's one concession to SMB resource constraints (soft-deleted POS records, cancelled invoices, test data).
- Type coercion and encoding fixes (fixing Windows-1252 → UTF-8, normalizing numeric types).
- Audit metadata columns: `_etl_loaded_at`, `_etl_run_id`. For regulated clients: retain all rows across runs (append-only). For SMB clients: replace on each run.
- Optional per table — some tables skip bronze entirely and land directly in Silver via YAML `column_map` (see PRD FR2).

**What does NOT go in.**

- Business logic of any kind.
- Column renames beyond encoding fixes.
- Joins. Aggregations. Derived columns.
- Source-system-agnostic shaping (that is Silver's job).

**Owned by.** The extractor (feather-etl itself). No human writes bronze SQL; feather produces it from the source connector.

**Default materialization.** Tables. Append-only for regulated; replace for SMB.

**Persona trace.** Serves the Builder's JTBD *"iterate locally against cached data without hammering the production ERP"* and the regulated-client path of *"detect regressions when source schemas or view DDLs change between runs."*

### 2. Silver — canonical cleaned rows

**Purpose.** The source-agnostic shape of the business. A SAP B1 table `ORDR` and a custom Indian ERP table `SalesHeader` both become `silver.sales_order` with the same 10–15 columns in the same shape. Silver is what the Analyst querying ad-hoc and the LLM agent working against the warehouse both read. It is the layer that gives feather-etl cross-client transferability — the planned connector library (PRD v2) will ship reusable Silver mappings per source system.

**What goes in.**

- One row per business entity in the source (one `silver.sales_order` row per ERP sales order).
- Cross-ERP canonical names (`silver.sales_order`, `silver.customer`, `silver.product`) — no source-system prefix, no abbreviations, no ALL_CAPS.
- Column renames from source names to canonical names (`SITYPE → invoice_type`, `SI_NO → invoice_no`). Either via YAML `column_map` (PRD-preferred for simple cases — see `arch_silver_direct_hybrid` memory) or via a SQL file (for joins, unions, case expressions).
- Simple lookup joins for code decode (`status_id` → `status_name` from a small reference table).
- Light cleansing: null normalization, whitespace trimming, de-duplication of exact duplicates.
- Source timestamp columns always kept (per `feedback_last_modified` memory — they answer freshness questions).

**What does NOT go in.**

- Dimensional modeling (facts or dimensions, surrogate keys, SCD handling). That is Gold.
- Aggregations, KPIs, per-period rollups. That is Gold or a Mart.
- Client-specific or department-specific shaping. That is a Mart.
- Compatibility aliases for source view names. That is a Mart.

**Owned by.** The Builder. YAML `column_map` by default; SQL file only when needed.

**Default materialization.** Views always. PRD FR5 pins this — views are lazy, always reflect current data, and add no pipeline step. Silver materialization is a future escape hatch only if performance forces it.

**Persona trace.** Serves the Builder's JTBD *"reuse business logic that already exists in source views rather than rewriting it from scratch"* and the Analyst's JTBD *"understandable mapping from ERP concepts to the warehouse's dimensional model"* (Silver is the first rung of that mapping).

### 3. Gold — the dimensional model (Kimball presentation area)

**Purpose.** The shared, enterprise-wide truth of the business as a dimensional model. One fact grain per fact table. Conformed dimensions — the same `dim_customer` row means the same customer in `fact_sales` and in `fact_payment_received`. Every Mart, every downstream consumer, every Analyst query that asks about the business *reads from Gold*. Gold is the answer to "what really happened."

**What goes in.**

- **Fact tables** — one per business process at a single grain: `gold.fact_sales` (one row per sales-order line), `gold.fact_inventory_movement` (one row per stock movement), `gold.fact_payment_received` (one row per payment). The grain is documented as an SQL comment at the top of each fact transform: `-- grain: one row per sales order line`.
- **Conformed dimension tables** — `gold.dim_customer`, `gold.dim_product`, `gold.dim_date`, `gold.dim_location`. Surrogate keys named `<dim>_sk`. Natural keys preserved alongside.
- **Slowly-changing-dimension treatment** where warranted — Type 1 (overwrite) by default; Type 2 (history-preserving) when the business explicitly needs point-in-time accuracy.
- Join logic from Silver — a fact table might draw columns from three Silver tables and conform them against two Gold dimensions.

**What does NOT go in.**

- Denormalized report-shaped wide tables. That is a Mart.
- Department-specific calculations (finance's revenue recognition vs sales's gross bookings). Those belong in each department's Mart, feeding from the same Gold facts.
- Compatibility aliases for source view names (`V_SALES_MONTHLY`). Those belong in a Mart.
- Ad-hoc one-off queries. Those belong in an analysis notebook or a throwaway Mart table.
- Raw Silver columns passed through verbatim with no dimensional purpose.

**Owned by.** The Builder, with conformance input from the Analyst — the Analyst is the final judge of whether a dimension matches how the business actually talks about the entity.

**Default materialization.** Views by default (PRD FR5, R-0636). Fact tables with large joins opt into materialization via the `-- materialized: true` annotation — rebuilt after each extraction run.

**Dependency edges are inferred from SQL.** feather-etl parses each
transform's `FROM`/`JOIN` clauses with `sqlglot` and builds the DAG from the
`silver.*` / `gold.*` references it finds. The SQL body is the only source
of truth for DAG edges — there is no `-- depends_on:` header. CTE names,
string literals, comments, and table-valued functions like `read_csv(...)`
are not treated as edges. See GitHub issue #54.

**Persona trace.** Serves the Analyst's binding invariant *"information parity with source"* (Gold is where "the same question answered from the ERP today is answerable from the warehouse tomorrow" lives), and the Builder's JTBD *"guarantee that ported logic produces numbers identical to the source."*

### 4. Departmental Data Marts — consumer-shaped outputs

**Purpose.** Per-department, per-dashboard, per-report denormalized slices of Gold. Everything here is disposable — blow away any mart table at any time and rebuild it from Gold without information loss. Marts are where the Analyst's specific questions become specific tables the Decision-maker's dashboards read from.

**What goes in.**

- **Wide denormalized report tables** shaped for one consumer: `finance_mart.monthly_close`, `sales_mart.pipeline_summary`, `ops_mart.inventory_snapshot_current`.
- **Pre-aggregated rollups** the dashboard would otherwise recompute on every render: `finance_mart.revenue_by_month_by_segment`.
- **Compatibility views for legacy ERP view names**: `compat_mart.V_SALES_MONTHLY` mimics a source ERP's view under its familiar name, implemented as a view over `gold.fact_sales` + `gold.dim_*`. This is how the Analyst subtype (a) — per `personas.md` — has their existing SQL continue to resolve after migration, when the Builder judges a specific source view worth preserving under its name.
- **Tables joined across multiple Gold facts and dims for a specific workflow**: `finance_mart.ar_aging` joins `fact_invoice`, `fact_payment_received`, and `dim_customer` into a wide AR-aging table.

**What does NOT go in.**

- The single source of any number. If a metric's definition lives only in a Mart, it's indirectly defined and silently drifts between Marts. Metrics live in Gold (as a fact column or a derived computation traceable to fact columns); Marts only reshape them.
- Facts or dimensions that don't already exist in Gold. If a Mart needs something new, add it to Gold first, then reference it.
- Raw Silver columns that bypass Gold. A Mart reads from Gold only.
- Raw Bronze columns. Ever.

**Owned by.** The Builder writes the SQL; the Analyst specifies the requirements and validates the output matches expectations (and reconciles with ERP reports or legacy Power BI numbers).

**Default materialization.** Same mechanism as Gold — views by default, `-- materialized: true` annotation opts in to a table. Dashboards with latency requirements (renders inside 2 seconds) typically need materialization; exploratory marts stay as views.

**Persona trace.** Serves both Analyst subtypes: subtype (a)'s "my old view name still resolves" via `compat_mart`, and subtype (b)'s "the number in the warehouse matches Power BI" via `finance_mart` / `sales_mart` / `ops_mart` tables shaped to the existing dashboards. Also serves the Builder's JTBD *"leave behind a deployment the Analyst will actually adopt and trust"* — a Mart-shaped output is what the Analyst adopts.

---

## How to decide which layer a transform belongs in

A flowchart rendered as a decision list.

1. **Is the transform just feather extracting rows from a source?** → Bronze. No SQL file authored by a human.
2. **Is the transform a rename or light cleanup producing a source-agnostic canonical shape?** → Silver. Prefer YAML `column_map`; fall back to a SQL file only for joins or unions.
3. **Is the transform producing a fact or dimension in the dimensional model — something every downstream consumer should share?** → Gold. Document the grain. Use `fact_` or `dim_` prefix.
4. **Is the transform producing a wide, consumer-specific output — a dashboard-shaped table or a compatibility alias for a legacy view name?** → Mart. Put it in the department's schema.
5. **Does the transform not fit any of the four?** → Stop. The transform's purpose is unclear, or the transform is doing two jobs at once. Split it or clarify intent before writing SQL.

The common failure mode: transforms that do "a bit of dimensional modeling and a bit of reshaping for a dashboard" in one SQL file. These belong in two transforms — one in Gold, one in a Mart — with the Mart reading from the Gold. Do the work twice; keep the boundary clean.

---

## Naming conventions

- **Schemas.** `bronze.<source_table>`, `silver.<canonical_name>`, `gold.<fact_or_dim>`. Mart schema naming is currently **open — to be decided as concrete marts land.** Two candidate patterns:
  - `<dept>_mart.<table>` — e.g., `finance_mart.monthly_close`. Per-department schemas; permissions and cleanup scoped by department.
  - `gold_<dept>.<table>` — e.g., `gold_finance.monthly_close`. Keeps mart content visually adjacent to Gold when browsing; signals "this depends on Gold" in the name.
  The first concrete mart built in a feather-etl deployment sets the precedent for the rest. Document the chosen convention in the deployment's client repo `README.md` so the pattern is consistent across marts.
- **Gold fact tables.** Prefixed `fact_`. Grain documented as an SQL comment at the top of each transform: `-- grain: one row per <business-event>`.
- **Gold dimension tables.** Prefixed `dim_`. Surrogate keys named `<dim>_sk`. Natural keys preserved alongside with their source-system name.
- **Mart tables.** No prefix — the mart schema name already signals the role. Tables named by what the consumer calls them (`monthly_close`, not `mart_monthly_close`).
- **Compatibility aliases.** Live in `compat_mart.*` (or `gold_compat.*` — follows the same schema-naming question as above). Use the exact original source view name — that's the point.

---

## Relationship to `docs/prd.md` v1.8

The PRD currently describes transforms as a "two-layer model" (Silver + Gold) with Bronze as an optional extraction layer. This doc supersedes that framing with a **three-layer transform model** (Silver + Gold + Marts) plus Bronze as the extraction layer — four layers total.

Concretely:

- PRD §FR5 line 589 — "two-layer model" → "three-layer transform model" in a forthcoming PRD v1.9 bump. Silver and Gold are carried forward unchanged; Marts are added.
- PRD §FR6 (setup creates `bronze`, `silver`, `gold`, `_quarantine` schemas) → extended to optionally create per-department Mart schemas lazily as SQL transforms reference them, OR eagerly if a deployment's `feather.yaml` declares them.
- PRD §FR5 Gold definition — "client-specific KPIs, aggregations, denormalized tables for dashboards" — tightens to the Kimball dimensional-model definition in this doc. The denormalized-dashboard outputs move to Marts.

The PRD v1.9 edit is a small, targeted follow-up. Until it lands, this doc is the authoritative reference for the warehouse model; the PRD remains authoritative for product scope and requirements.

---

## Open questions, preserved for future resolution

- **Mart schema naming convention** — `<dept>_mart` vs `gold_<dept>`. Decided when the first concrete mart is built in a deployment.
- **Quarantine handling for dimensional conformance failures** — when Silver rows don't match a Gold dimension (unknown `customer_id`), do they flow through to Gold with a default dimension member, land in `_quarantine`, or fail the run? Currently deferred.
- **Late-arriving facts and Type 2 SCD mechanics** — the layer model implies support; the concrete engine work is a future feature.
- **Cross-deployment mart reuse** — if two clients both need a `finance_mart.monthly_close` shape, can the SQL be shared? This is part of the planned connector/transform library (PRD v2) but has implications for Mart naming and versioning that are not decided here.

---

## How this document evolves

- Adding a new layer requires a version bump, a clear persona-and-consumer justification, and a re-examination of the decision tree above.
- Retracting a layer (merging Gold and Marts, for example) requires the same.
- Tightening or relaxing what "goes in" or "does not go in" a layer is a minor revision — bump the version and note the change in the version history.
- Every transform design under `docs/superpowers/specs/` and every plan under `docs/plans/` should cite this document by version when making layer-routing claims.
