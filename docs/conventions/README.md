# Conventions

How we work on feather-flow — code layout patterns, spec templates, testing styles, and other "this is how we do it" documents. Conventions here apply to every contributor, human or agent, and to every feature.

If you're about to write code, a spec, or a plan for feather-flow, skim the relevant doc here first.

## Contents

- [`package-layout.md`](package-layout.md) — repo-level package layout: where files live, the three-axis model (sources / destinations / commands) + `core/`, placement rules, test colocation, `tests/structural/` tier, `src/feather_flow/testing/` sub-package, and the required `pyproject.toml` changes. Read before adding a new module, moving a file, or creating a new command or source type.
- [`code-layout.md`](code-layout.md) — hybrid in-file organization template combining DHH TOC banners, Clean Code stepdown rule, and agent-first greppable section labels. Applies to every non-trivial module. Read before making layout changes to existing files or before introducing a new file larger than ~100 lines.
- [`spec-template.md`](spec-template.md) — canonical structure for feature design specs under `docs/superpowers/specs/`. Embodies seven cross-project principles (form-follows-grain, roles-to-documents-mapping, scope-describes-outcomes, spec-sections-natural-shape, spec-sections-pull-unique-weight, terrain-not-procedure, senior-lead-voice). Mirrored from Siraj's cross-project vault; this in-repo copy is the source of truth.

## When to add a doc here

Add a file here when a convention:

- is stable (not changing every sprint),
- applies across the whole project (not just one feature),
- is prescriptive (tells the reader *how* to do something), and
- would benefit from being cited by name in reviews and PRs.

For project-level architectural decisions, use `docs/architecture/` instead. For one-off feature designs, use `docs/superpowers/specs/`.

When adding a doc, update the Contents list above with a one-line summary of what it covers and when to read it.
