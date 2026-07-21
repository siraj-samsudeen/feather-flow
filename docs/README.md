# `docs/` — map

Documentation for feather-flow lives here. This file is the map — read it first, then navigate to the folder you need. Each sub-folder has its own `README.md` hub with a one-line summary of every doc in that folder.

## Top-level docs (stable, cross-cutting)

- [`prd.md`](prd.md) — product requirements document. Version-tagged; authoritative for product scope, feature list, use cases, EARS requirements, and acceptance criteria. Read before proposing any product-level change.
- [`personas.md`](personas.md) — Builder, Analyst, Decision-maker (latent stakeholder), and artifact consumers (human viewer, AI triage agent, future SQLite metadata DB). Every feature design must cite this.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — work conventions: plan-then-code, spec/plan/review artifact chain, commit style.
- [`research.md`](research.md) — ongoing research notes about ERP sources, data patterns, and scaling considerations.
- [`testing-feather-flow.md`](testing-feather-flow.md) — testing strategy and patterns.

## Sub-folders (each has its own `README.md` hub)

- [`architecture/`](architecture/README.md) — project-level architectural decisions with long half-life (warehouse-layers model, etc.). Cited by transform designs, porting decisions, schema choices.
- [`conventions/`](conventions/README.md) — how we work: code-layout template, spec template, testing patterns. Applies to every contributor.
- [`issues/`](issues/README.md) — per-issue working notes captured during brainstorming, before a design spec lands.
- [`plans/`](plans/) — implementation plans (older format; pre-superpowers-skill).
- [`reviews/`](reviews/) — post-implementation reviews.
- [`superpowers/`](superpowers/) — design specs and plans produced via the brainstorming + writing-plans skills. Current home for new feature work.

## When to add a doc

Before adding a file anywhere under `docs/`, check whether the information belongs:

- in `prd.md` (product scope / requirements — update version table),
- in `personas.md` (who we serve — update version table),
- in an existing section of an existing doc (expand an existing file rather than fragmenting),
- in one of the sub-folders above (follow that folder's "when to add" guidance in its `README.md`),
- or as a new top-level doc (rare — reserved for project-wide concerns that don't fit any sub-folder).

**Rule of thumb — the 300-word test.** If the topic would require fewer than ~300 words, it belongs as a section in an existing doc, not a new file. Doc proliferation is the enemy of findability; well-organized mid-sized docs beat a directory of 200 micro-files.

When adding a doc to a sub-folder, update that folder's `README.md` hub with a one-line entry. When adding a top-level doc, update this file.
