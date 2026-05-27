# feather-etl

A config-driven Python ETL platform for deploying data pipelines across multiple clients with heterogeneous ERP sources — SQL Server, SAP B1, SAP S4 HANA, and custom Indian ERPs.

For clients with no data platform, feather-etl *is* the platform. For clients with a warehouse already, it is the lightweight extraction + local-transform layer that feeds only clean, curated gold tables to the cloud.

> **Status — complete rewrite in progress.** This branch is a ground-up rebuild, grounded in actual usage of the prior implementation. The first slice covers the **explore phase**; later phases add curate, extract, transform, run. See the [Roadmap](#roadmap).

## Why feather-etl?

Enterprise ETL stacks (dlt + dbt/SQLMesh + Dagster) impose a complexity tax — hundreds of transitive dependencies, proprietary abstractions, steep learning curves — that makes them unsuitable for Indian SMBs who need a working pipeline, not a data engineering career.

feather-etl replaces the full stack with a single Python package, deployable by a small team, configurable in YAML, and understandable by anyone who can read SQL.

| Heavy stack                        | feather-etl                                  |
| ---------------------------------- | -------------------------------------------- |
| dlt (extraction)                   | `sources/` — Protocol-based connectors       |
| dbt / SQLMesh (transforms)         | Plain `.sql` files executed in local DuckDB  |
| Dagster / Airflow (orchestration)  | APScheduler + CLI                            |

## Design Principles

- **Multi-client by design** — each client is an independent `feather.yaml`; same package, different config.
- **Config-driven** — YAML defines sources, tables, schedules, transforms; adding a table means editing YAML, not writing Python.
- **Local-first** — extract and transform in local DuckDB (free compute); push only gold to MotherDuck (minimal cloud cost).
- **Workbench, not framework** — short verbs an operator runs interactively; nothing magical between you and the data.
- **Package-first** — reusable library; client projects import and configure it.

## Layer Model

```
Source ERP (SQL Server / SAP B1 / SAP S4 HANA / custom)
    │
    ▼  column_map applied (zero-copy rename + select)
Local DuckDB
    │
    ├── bronze (optional)  — raw ERP data, all columns; dev cache or compliance audit.
    ├── silver (primary)    — canonical names, light cleaning. Always views.
    └── gold   (dashboards) — KPIs, denormalised tables. Views by default; materialised in prod.
                        │
                        ▼  (optional sync)
                  MotherDuck → Rill / BI
```

Canonical 4-layer definitions live in [docs/reference/warehouse-layers.md](docs/reference/warehouse-layers.md).

## Roadmap

The CLI is built verb-by-verb in user-workflow order. Each verb consumes the prior's output and the tool is meaningfully usable at every milestone.

### Phase 1 — Explore *(active; see [PRD-001](docs/prd/PRD-001-explore-phase.md))*

| #  | Verb                     | Purpose                                                          | Status                                  |
| -- | ------------------------ | ---------------------------------------------------------------- | --------------------------------------- |
| 1  | `feather init`           | scaffold a fresh client project                                  | spec + design locked, implementation in progress |
| 2  | `feather source add`     | register source, test connection, list databases                 | planned                                 |
| 3  | `feather source test`    | re-check connection                                              | planned                                 |
| 4  | `feather use`            | set / show / clear the current source context                    | planned                                 |
| 5  | `feather sample`         | first read against a registered source; row-shape inspection     | planned                                 |
| 6  | `feather sql`            | run arbitrary read-only SQL — powers every probe                 | planned                                 |
| 7  | `feather compare`        | diff two CSVs by key with tolerance                              | planned                                 |
| 8  | `feather tally`          | wrap `sql` + `compare` into versioned hypothesis iteration       | planned                                 |
| 9  | `feather explore log`    | observability over the daily exploration corpus                  | planned                                 |
| 10 | `feather explore replay` | re-run a saved exploration                                       | planned                                 |

### Phase 2+ — Operate *(future PRDs)*

- **Curate** — declare which tables to extract from each source.
- **Extract** — pull curated tables into local bronze.
- **Transform** — silver/gold SQL transforms.
- **Run** — full pipeline (extract + transform).
- **Rerun / History** — observability over scheduled runs.

Other capabilities from the prior implementation (DQ checks, schema drift detection, SMTP alerts, MotherDuck sync) return in later phases as their verbs are spec'd.

## Where to look

- [docs/prd/](docs/prd/) — active product requirements (source of truth for what to build; one file per phase).
- [docs/reference/](docs/reference/) — background context: warehouse layers, personas, Jobs-to-be-Done reframing.
- [docs/testing.md](docs/testing.md) — test cadence agreement and coverage policy.
- [openspec/changes/](openspec/changes/) — in-flight change proposals, designs, and tasks.
- [CLAUDE.md](CLAUDE.md) — agent instructions for working in this repo.

Example client projects appear under `examples/<client>/` as verbs ship — see [CLAUDE.md](CLAUDE.md) for the dogfooding convention.

## License

MIT
