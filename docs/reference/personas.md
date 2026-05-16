# feather-etl: User Personas

**Version:** 1.2
**Date:** 2026-04-21
**Status:** Stable — referenced by every feature design doc

| Version | Changes |
|---------|---------|
| 1.0 | Initial version. Builder and Analyst as primary personas; Decision-maker as latent stakeholder; four deferred personas explicitly out of scope. Extracted during brainstorming for issue #26 (view discovery). |
| 1.1 | Analyst JTBD sharpened: added "information parity with source" as an explicit care; removed the misleading "does not care about preserving source view names" bullet. The Analyst's real invariant is not about names — it is about never losing access to information they currently have. Name preservation is one possible vehicle, not the requirement. Retraction note in "Design discipline" updated to mark name preservation as an explicitly supported per-case option (not a platform default, but not a banned outcome either). |
| 1.2 | New section "Artifact consumers — human and machine" added to explicitly elevate the AI triage agent (consuming feather's metadata artifacts such as the discovery schema JSON) to first-class status alongside the human viewer. The AI triage agent is **not** a persona in the JTBD sense — it has no identity or career risk — but it imposes concrete design constraints (eager materialization, self-contained artifacts, machine-parseable shape) that matter as much as any human JTBD. The existing deferred-persona entry "AI agent as warehouse consumer" is a *different* role (agent querying gold tables for business answers) and remains deferred as before. |

---

## Purpose

This document fixes the vocabulary for who feather-etl is for. Every feature design — current and future — must trace each decision back to serving at least one persona named here. Features that serve no named persona are YAGNI until a new persona is formally promoted in a new version of this document.

Agents working on feather-etl should read this doc **before** starting any brainstorming, design, or spec work. When in doubt during implementation, ask: "Which persona does this serve, and how?"

---

## Primary personas

feather-etl serves two primary personas: the **Builder** and the **Analyst**. In small deployments they may be the same human wearing two hats; in larger ones they are different people. The design must work in both cases.

A third party — the **Decision-maker** — is the ultimate consumer of the numbers the warehouse produces, but never touches SQL, config, or the pipeline. They are a *latent stakeholder*, not a persona. Their existence is why validation is load-bearing for both primary personas below.

---

### The Builder

**Identity.** The person who configures `feather.yaml`, writes silver and gold transforms, and operates the pipeline. Usually a consultant delivering for a client, or the sole data/IT person at a small company. Sometimes the same human as the Analyst.

**Primary job-to-be-done.** Guarantee that ported logic (source views, SQL extracted from ERP reports, custom transforms) produces numbers identical to the source. *Prove correctness, not just shipment.*

**Supporting jobs.**

- Stand up an ERP→warehouse pipeline in days, not months
- Iterate locally against cached data without hammering the production ERP
- Reuse business logic that already exists in source views rather than rewriting it from scratch
- Detect regressions when source schemas or view DDLs change between runs
- Leave behind a deployment the Analyst will actually adopt and trust

**Cares about.**

- Reconciliation tooling — source vs warehouse diff, row-level and aggregate-level
- Porting-correctness evidence — demonstrable proof that ported logic matches source
- Config simplicity — YAML over code, sensible defaults, few knobs
- Fast local feedback loops — dev cache, deterministic fixtures, quick reruns
- Dialect and porting friction — the cost of translating T-SQL / PL/pgSQL to DuckDB
- Change-detection cost — cheap checksums, honest "I don't know" signals
- Cheap infra — local DuckDB first, MotherDuck only where needed

**Does not care about.**

- Pixel-perfect dashboards
- REST API contracts for downstream apps
- Enterprise governance ceremony
- Elaborate role-based access control

---

### The Analyst

**Identity.** The client-side power user who queries the warehouse to produce numbers the Decision-maker acts on. Operates as the Decision-maker's spokesperson and fact-checker. Two common subtypes — both in scope:

- **(a) View-literate Analyst** — already queries the ERP at the view level. Knows names like `V_SALES_MONTHLY` by heart. May hold a library of SQL built against those view names.
- **(b) Report-consumer Analyst** — doesn't touch views or tables directly. Works from Power BI outputs, finance spreadsheets, and ERP-generated reports. Reconciles at the *report-number* level, not the SQL level.

**Primary job-to-be-done.** Get the Decision-maker the information they need to make decisions, *and validate that the numbers are correct so the Decision-maker is not put at risk by bad data*. Validation is not optional — it is how the Analyst protects their own professional credibility.

**Supporting jobs.**

- Recognize which warehouse tables correspond to the ERP concepts they already understand (subtype a may need a migration mapping from their old view-level SQL to the new star-schema model — this is a Builder responsibility, not a naming-preservation responsibility)
- Cross-check warehouse totals against ERP reports or Power BI numbers (subtype b)
- Trust that reconciliation holds over time as data grows and source systems evolve
- Answer the Decision-maker's question "where did this number come from?" without a two-week forensic exercise

**Cares about.**

- Number accuracy and reconciliation with source
- **Information parity with source** — whatever question they can answer by querying the ERP today, they must still be able to answer against the warehouse tomorrow. The access path may change (star schema, compatibility view, data mart); the available information must not regress.
- Understandable mapping from ERP concepts to the warehouse's dimensional model
- Minimal retraining — a clean, well-named star schema they can learn once
- Clear provenance — lineage from gold back to bronze back to source
- Stability — gold table contracts that don't silently change shape

**Does not care about.**

- Bronze / silver / gold layering as a concept
- Pipeline internals — change detection, watermarks, extraction strategies
- Warehouse infrastructure — DuckDB vs MotherDuck, file layouts
- The mechanism by which information parity is achieved — whether through a preserved view name, a star-schema translation, a curated data mart, or a compatibility alias on top of gold, is a Builder decision the Analyst does not weigh in on, provided parity holds

---

## Artifact consumers — human and machine

Personas describe *humans* whose JTBD we serve. They are not the only audience the product must satisfy. feather-etl produces artifacts — the discovery schema JSON, state files, eventually a unified SQLite metadata database — that are consumed by both human users and automation. The human-vs-machine distinction matters because the design constraints each imposes are different, and missing one leads to artifacts that silently fail a real consumer.

This section is a catalog of artifact consumers. Entries here are **not** personas (they have no identity, no career risk), but they constrain the product design the way a persona's JTBD does. Feature designs that touch any artifact listed here must trace back to at least one consumer's requirements.

### Human viewer (`feather_etl.resources.schema_viewer.html`)

- **What it consumes.** The per-source discovery schema JSON, read from disk over HTTP served locally by `viewer_server.py`.
- **Who operates it.** The Builder (primary), occasionally the Analyst (to peek at what got discovered).
- **Design constraints it imposes.** The artifact must be renderable; fields must be addressable by simple paths; missing fields must degrade gracefully rather than crash.

### AI triage agent (consumer of the discovery artifact)

- **What it consumes.** The discovery schema JSON, typically the whole file for a source in one pass, offline. Output is a per-view recommendation routing each view into one of the three buckets from Issue #26 R1 (ignore / rebuild in silver or gold / copy verbatim).
- **Who operates it.** The Builder, who reviews and accepts, edits, or overrides the recommendations.
- **Design constraints it imposes.**
  - **Eager materialization.** All DDLs must be present in the artifact at discover time. Lazy fetch (per-DDL round trips) fails the agent: (1) it cannot spot "these ten views share structure and should be rebuilt as a single fact table" without seeing them together; (2) round-trip tool calls burn tokens linearly with view count; (3) the artifact must be interpretable without a live source connection so a second agent or a teammate can review offline.
  - **Machine-parseable shape.** Structured JSON, not pretty-prose. Fields are named predictably; values have stable types; nested structures translate mechanically to relational rows.
  - **Self-contained.** One file per source, no live-connection dependency to interpret.
- **Relationship to the deferred "AI agent as warehouse consumer" entry below.** That deferred role is an agent querying gold/silver tables to answer business questions — a completely different consumer with completely different requirements (semantic schema annotations, reliable joins, query-time performance). The triage agent above is a first-class consumer *today* because the discovery artifact is real and shipping; the warehouse-consumer role stays deferred because the semantic layer it would need has not been designed.

### Future SQLite metadata database (planned consolidation)

- **What it will consume.** Today's per-purpose JSON artifacts (including the discovery schema JSON) will be consolidated into a unified SQLite metadata database in a parallel workstream. Feather's internal code paths will read from SQLite instead of JSON files after the migration lands.
- **Who operates it.** Feather itself, indirectly all human personas and the triage agent.
- **Design constraints it imposes on artifacts today.** JSON shapes should map 1-to-1 onto prospective SQLite rows — top-level by entity kind (e.g., `streams`), one level of nesting for child collections (e.g., `columns`), no free-form blobs, no duplicated fields. The goal is that the eventual SQLite migration is a mechanical translation, not a re-design.

---

## Latent stakeholder: the Decision-maker

**Identity.** The business owner, CEO, CFO, or CXO who reads the dashboards and reports the Analyst produces, and acts on them. In SMB contexts, frequently also the person paying for the feather-etl engagement.

**Relationship to the product.** Never touches the warehouse directly. Consumes numbers through the Analyst. Their existence is the reason the Analyst's validation JTBD is load-bearing — wrong numbers put the Decision-maker at business risk, which the Analyst is professionally accountable to prevent.

**Why this is a latent stakeholder and not a persona.** Treating them as a persona would force feature work that directly serves their workflow (dashboards, executive summaries, mobile views). Those are downstream of the warehouse, not part of it. The correct design move is to serve the Decision-maker *through* the Analyst — by making the Analyst's validation job as trustworthy and evidence-rich as possible.

---

## Deferred personas — explicitly out of scope today

Every persona below could become real in a future deployment. None of them shape current feature work. They are listed here so future discussions don't re-invent them from scratch.

| Persona | Why deferred | Promotion trigger |
|---|---|---|
| **App / API developer** | Rare in SMB ERP deployments. Would require stable external contracts and probably an API layer feather does not have. | First concrete deployment that needs programmatic access beyond SQL. |
| **Governance / auditor** | Lineage matters eventually. Premature to design around while the core pipeline is still settling. | First deployment with regulatory reporting obligations (SOX, GDPR, etc.). |
| **AI agent as warehouse consumer** | Emerging and interesting. Premature to design around. Requires semantic schema annotations feather does not yet produce. | First deployment where an internal copilot or agent is a primary consumer of the warehouse. |
| **External BI vendor** | Consumes the same gold tables the Analyst produces. No additional requirement today. | First deployment where a BI vendor requires a schema contract feather does not already expose. |

Promoting any of these to a primary persona is a deliberate decision that requires updating this document and re-examining feature priorities. Features that would only serve a deferred persona do not enter scope until promotion happens.

---

## Design discipline

When making any feature decision — at brainstorming, spec, plan, or implementation — trace it back to one or more of:

- *"Serves the Builder by [specific JTBD bullet above]"*
- *"Serves the Analyst by [specific JTBD bullet above]"*
- *"Enables the Analyst to protect the Decision-maker from bad numbers by [specific mechanism]"*
- *"Satisfies the [human viewer / AI triage agent / future SQLite metadata DB] constraint that [specific constraint from the Artifact consumers section]"*

If a decision cannot be traced this way, it is probably YAGNI or it is serving a deferred persona or an artifact consumer not yet on the catalog. All such cases require explicit justification before the decision lands.

**Notable design retractions preserved here so they are not re-proposed.**

- "Preserve source view names in the warehouse" is not the default. The warehouse uses dimensional (star-schema) naming for ported logic. For thoughtful source views that the Builder explicitly judges worth keeping under their original name, the name may be retained via a compatibility view on top of the star schema — this is a supported per-view option, not a platform-wide default and not a mandatory outcome. The binding invariant is the Analyst's information parity, not any particular name preservation policy.
- "Auto-materialize source views into the cache or warehouse" is not a goal. Source views are a mixed bag of intentional logic and temporary hacks; feather cannot tell them apart. Porting is a deliberate per-view Builder decision informed by triage, not a platform default.
- "Treat the Decision-maker as a persona" is not a goal. Serving them through the Analyst's validation JTBD is the design move.

---

## How this document evolves

- New primary personas require a version bump and a clear promotion justification.
- Retractions (removing a persona, narrowing a JTBD) require the same.
- JTBD wording changes that sharpen meaning without changing scope are minor revisions — bump the version and note the change.
- Every feature spec under `docs/superpowers/specs/` and every plan under `docs/plans/` should cite this document by version when making persona-based claims.
