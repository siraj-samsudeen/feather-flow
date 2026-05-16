# feather-etl

Config-driven Python ETL — workbench for heterogeneous ERP sources.

This branch is a **complete rewrite**, grounded in actual usage experience.
The first slice of the rewrite covers the **explore phase** — connecting to
a source, sampling, running probes, comparing against a reference, and
saving tally checks. Subsequent slices will add curate, extract, transform,
run, and beyond.

## Where to look

- `docs/prd/` — active product requirements (the source of truth for what
  to build, by phase).
- `docs/reference/` — background context (warehouse layers, personas,
  Jobs-to-be-Done reframing).
- `CLAUDE.md` — agent instructions for working in this repo.
- `openspec/` — OpenSpec configuration; changes live under `openspec/changes/`.

Example client projects appear under `examples/<client>/` once `feather init`
exists; see `CLAUDE.md` for the dogfooding convention.
