This is a complete rewrite and reimagining of feather-etl, grounded in actual
usage experience (see `docs/reference/` for context, `docs/prd/` for active
requirements).

Two prior implementations exist for consultation:

- `main` branch — the current shipped codebase before this rewrite began.
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
read `docs/spec-conventions.md` first. It locks the requirement/scenario
numbering (`1`, `1a`, `1b`, `2a`, ...) with local scope per spec file.
Cross-file references use `<capability>.<N><letter>` (e.g. `init.1a`).

Dogfood feather inside this repo. Create example client projects under
`examples/<client>/` (e.g. `examples/rama_dw/`) using the verbs you build.
Do not pre-scaffold example contents — they emerge from running `feather init`
and subsequent verbs against real source data.
