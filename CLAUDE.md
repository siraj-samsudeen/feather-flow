# feather-flow — agent context

Config-driven Python ETL for heterogeneous ERP sources → local DuckDB.

**Full project context:** `.claude/rules/feather-etl-project.md`
**Requirements:** `docs/prd.md`
**Architecture (codebase):** `README.md`
**Architecture (warehouse model):** `docs/architecture/warehouse-layers.md` — four-layer Bronze/Silver/Gold/Departmental-Data-Marts model. Read before writing any transform SQL.
**Personas:** `docs/personas.md` — Builder, Analyst, artifact consumers. Cite by version in every feature spec.
**Work conventions:** `docs/CONTRIBUTING.md`

## Where to find things

All project documentation lives under `docs/`. Start with [`docs/README.md`](docs/README.md) — it is the top-level map. Each sub-folder (`architecture/`, `conventions/`, `issues/`) has its own `README.md` hub listing every doc in that folder with a one-line summary. Read the relevant hub before reading individual docs, and update the hub when you add a new doc.

## Before you write a single line of code

Run the test suite and confirm green:

```bash
uv run pytest -q               # currently: 720 tests
```

If anything is red before you touch anything, report immediately.

---

# Standing workflow preferences for superpowers skills

Superpowers skills honor CLAUDE.md preferences over their built-in defaults. The
rules below apply to every invocation of `/brainstorming`, `/writing-plans`,
`/subagent-driven-development`, and `/finishing-a-development-branch` when
working in this repo.

The patched versions of `brainstorming/SKILL.md` and `writing-plans/SKILL.md`
live vendored in `.claude/skills/`. See `.claude/skills/README.md` for
provenance and how to re-sync with upstream.

## After spec approval

Do NOT ask "Subagent-Driven vs Inline Execution." Always use Subagent-Driven
Development. Announce and proceed:

> "Plan complete. Proceeding with Subagent-Driven execution."

Inline execution is used only on explicit user request ("use executing-plans
instead").

## Branch setup

Before dispatching the first implementer subagent: automatically create a git
worktree on branch `feature/<slug>`, where `<slug>` = the plan filename minus
the `YYYY-MM-DD-` prefix and `.md` suffix. Do not ask; do not present options.

The worktree is created via the `superpowers:using-git-worktrees` skill (not
plain `git checkout -b`), so implementation work stays isolated from the main
workspace.

Plain in-place branching is used only on explicit user request ("stay in this
workspace" or "don't use a worktree").

## Spec review gate is preserved

The spec review gate in `brainstorming` (user approves the written spec before
implementation begins) is kept. All other questions in that flow are also kept
as defined by the skill. The autopilot starts ONLY after the spec is approved.
