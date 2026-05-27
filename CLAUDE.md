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

The PRD at `docs/prd/PRD-001-explore-phase.md` is the source of truth for what
to build. The user directs sequencing commit by commit. PRDs follow the naming
pattern `PRD-NNN-<phase>.md`; one file per phase, kept in `docs/prd/`.

What is mentioned in PRD 001 is only a slice of the current feather-etl
functionality. Other capabilities (curate, extract, transform, run, rerun,
history, etc.) will be added in subsequent PRDs after this one is complete.
Do NOT assume that functionality absent from PRD 001 has been dropped.

When writing tests, read `docs/testing.md` first.

Build verbs in PRD §10 workflow order. The `add-feather-init` change is the
template every later verb inherits — its design decisions carry forward unless
a later verb's `design.md` explicitly overrides.

For any OpenSpec change work, invoke the `openspec-siraj-flow` skill.

Dogfood feather inside this repo. Create example client projects under
`examples/<client>/` (e.g. `examples/rama_dw/`) using the verbs you build.
Do not pre-scaffold example contents — they emerge from running `feather init`
and subsequent verbs against real source data.
