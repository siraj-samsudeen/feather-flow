This is a complete rewrite and reimagining of feather-etl, grounded in actual
usage experience (see `docs/reference/` for context, `docs/prd/` for active
requirements).

Two prior implementations exist for consultation:

- `pre-rewrite-main` branch — the previously shipped codebase before this
  rewrite was promoted to `main`.
- `code-reorg` branch — a partial rewrite attempt with installed OpenSpec
  scaffolding.

Both are references only. Do NOT automatically inherit decisions, conventions,
file layouts, or abstractions from either. Inherit only when the prior choice
is genuinely non-obvious and would not be reconstructed independently from
the PRD.

The PRD at `docs/prd/PRD-001-explore-phase.md` is the source of truth for what to
build. The user directs sequencing commit by commit. PRDs follow the naming
pattern `PRD-NNN-<phase>.md`; one file per phase, kept in `docs/prd/`.

What is mentioned in PRD 001 is only a slice of the current feather-etl
functionality. Other capabilities (curate, extract, transform, run, rerun,
history, etc.) will be added in subsequent PRDs after this one is complete.
Do NOT assume that functionality absent from PRD 001 has been dropped.

Tests colocate as `<module>_test.py` siblings next to the code they test.
Multi-module feature tests go in `scenarios/` within the feature's package.
Repo-wide invariants live in `tests/invariants/`. Importable test helpers
in `src/feather_etl/testing/`.

When writing tests, read `docs/testing.md` first. It locks the test-cadence
agreement (vertical scenario commits, test-first batch per slice, Option 4
layering) and the constraints (100% line+branch coverage, no mocked
filesystem, no snapshot tests). Verb design docs may add per-function
carve-outs; they do not override the global agreement.

When writing or editing a spec.md in `openspec/changes/<change>/specs/`,
the requirement/scenario numbering (`1`, `1a`, `1b`, ...), scenario
title shape (subject + verb + outcome, declarative, name the artifact),
cross-file references (`<capability>.<N><letter>`, e.g. `init.1a`), and
renumbering protocol are all defined in the `openspec-siraj-flow` skill.
Same skill carries the canonical `tasks.md` six-section commit-block
shape and the per-commit plan file shape (the latter actually lives in
`openspec-siraj-walk-plan`).

Build verbs in PRD §10 workflow order. The `add-feather-init` change is
the template every later verb inherits — its design decisions (core/cli
split, spec-driven errors only, output-stream convention, etc.) carry
forward unless a later verb's `design.md` explicitly overrides.

For the full OpenSpec change pipeline, invoke the `openspec-siraj-flow`
skill — it carries the canonical diagram (proposal → spec → design →
tasks → per-commit plans → execution → verify → archive) using the
`/openspec-siraj:*` slash commands (this project renames the default
`opsx/` command folder to `openspec-siraj/` for uniformity with the
custom skills; after `openspec update` regenerates `opsx/`, rename it
back). Project-local discipline: avoid `/openspec-siraj:apply` (use
`openspec-siraj-execute-task` instead), and avoid `:propose`, `:ff`,
`:continue` — they auto-generate design.md and tasks.md in shapes that
don't follow Format A or the six-section shape, so you'd just rewrite
them.

Dogfood feather inside this repo. Create example client projects under
`examples/<client>/` (e.g. `examples/rama_dw/`) using the verbs you build.
Do not pre-scaffold example contents — they emerge from running `feather init`
and subsequent verbs against real source data.
