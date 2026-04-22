## Context

The code-reorg branch is a from-scratch rebuild of feather-etl. `feather init`
is step 1 in the feature migration order — every subsequent command
(validate, discover, run) depends on a correctly scaffolded project.

Main's init lives in two files: `init_wizard.py` (scaffold_project function)
and `commands/init.py` (Typer wrapper). The V3 convention requires
`commands/init/cli.py` + `commands/init/core.py`. This is the first command
to land, so it also sets the pattern for all future commands.

The rewrite must also ship skill definitions so that agents in client
projects can add sources without a CLI wizard. This is new territory —
main has no skill installation mechanism.

## Goals / Non-Goals

**Goals:**
- Stamp a minimal, honest `feather.yaml` (empty sources/tables, real destination)
- Install skills into the client project for agent-driven source configuration
- Be idempotent: safe to re-run, skip existing data files, overwrite skills
- Merge `.gitignore` rather than replace it
- Stamp a `feather.example.yaml` with all source-type schemas
- Stamp a `CLAUDE.md` that points agents to installed skills
- Work in empty directories *and* existing projects

**Non-Goals:**
- Interactive wizard (agents edit yaml, not a prompt flow)
- `feather add-source` CLI command (future work)
- `feather update-skills` command (for now, re-running init overwrites skills)
- Detecting uncommitted changes or git-tracked status
- Tailoring templates to a specific source type at init time

## Decisions

### 1. Idempotent file handling — skip, merge, overwrite

Three behaviors for three file categories:

| Category | Files | Behavior |
|----------|-------|----------|
| Data | feather.yaml, .env.example, pyproject.toml, CLAUDE.md | Skip if exists |
| Merge | .gitignore | Append feather entries if missing |
| Overwrite | .claude/skills/**, feather.example.yaml | Always overwrite from package |

Rationale: data files are user-owned after stamping — init must not clobber
them. Skills are package-owned — like `node_modules`, they get replaced.
`.gitignore` is additive — feather entries should exist, but user entries
must be preserved.

Alternative considered: git-tracked check before overwriting. Rejected —
too complex for early stages, and the skill-overwrite convention matches
established patterns (node_modules, vendor/).

### 2. Source examples in feather.example.yaml, not feather.yaml

Main's template puts commented-out source configs directly in
`feather.yaml`. The rewrite separates reference data from working config:
`feather.yaml` is empty and honest; `feather.example.yaml` is the reference.

Rationale: agents will read the example file, find the matching source type,
and write the real entry. A clean `feather.yaml` with empty lists is
unambiguous — no "which of these commented blocks is active?"

### 3. Skills installed from package resources

Skills live in `src/feather_etl/resources/skills/` as package data. Init
copies them into the client project's `.claude/skills/`. This is the
Convex model — `npx convex init` installs skills locally.

Rationale: `.claude/skills/` is the Claude Code convention. Agents in
client projects discover skills locally. The package dependency
(`uv upgrade`) brings new skill versions to the package, and re-running
init copies them into the project. No fragile path pointers, works with
uvx installs. Other agents (Codex, opencode) can port from `.claude/`
manually — document this, don't over-engineer for multiple locations.

Alternative considered: install to all known agent skill directories
(.claude/, .opencode/, .codex/). Rejected — premature, adds complexity
for consumers that don't exist yet.

Alternative considered: CLAUDE.md points to the package's skill directory.
Rejected — depends on feather-etl being a local neighbor, breaks with
uvx/pip installs.

### 4. core.py owns domain logic, cli.py is thin

```
cli.py:
  - Parse args (project_name or ".")
  - Resolve "." → CWD directory name
  - Call core.init_project(path, name)
  - Format output (text or JSON)

core.py:
  - init_project(path, name) → InitResult
  - File stamping logic
  - .gitignore merge logic
  - Skill installation logic
  - Returns what was created/skipped
```

Rationale: the package-layout convention (Rule 14) allows interactive
core.py for init. The "skip if exists" logic is domain behavior, not
presentation. cli.py handles Typer wiring only.

### 5. Project name from directory basename

When the user runs `feather init .`, the project name is derived from the
CWD basename (e.g., `/projects/rama_dw` → name = `rama_dw`). When they
pass a path like `feather init my-proj`, the name is `my-proj`. The name
is used in `pyproject.toml`.

### 6. pyproject.toml with commented local pointer

Stamps the published dependency with a commented-out local dev override:

```toml
dependencies = ["feather-etl>=0.1.0"]
# [tool.uv.sources]
# feather-etl = { path = "../feather-etl", editable = true }
```

Rationale: the published form is the default. Local dev is a manual
override — uncomment and adjust the path.

## Risks / Trade-offs

- **[Skills get overwritten on re-run]** → Acceptable. Convention: don't
  edit installed skills. Same as node_modules. If a user needs a custom
  skill, they add a new one, not patch an installed one.

- **[feather.example.yaml goes stale after init]** → Acceptable for now.
  The file is reference data, not operational config. When `feather
  add-source` exists, it reads from the package directly and the example
  file becomes optional.

- **[CLAUDE.md goes stale after init]** → Same trade-off. CLAUDE.md is
  stamped once and becomes user-owned. Future: init could overwrite it
  (it's in the "data" skip category today, could move to "overwrite").

- **[No guard against overwriting user-modified .gitignore]** → The merge
  logic only appends feather entries that don't already exist. This is
  safe — it never removes user entries.
