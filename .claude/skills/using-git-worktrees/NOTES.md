# Vendoring notes — using-git-worktrees skill

**Upstream:** `claude-plugins-official/superpowers` v5.0.7,
`skills/using-git-worktrees/SKILL.md`
**Vendored:** 2026-04-22

## Patches applied to upstream

### Patch 1 — Step 0: Discover existing convention

Inserted a new top-level section (`## Step 0: Discover Existing Convention`)
BEFORE `## Directory Selection Process`. It mandates running
`git worktree list` first and inferring the parent directory + branch
naming pattern from existing worktrees.

**Why:** Upstream falls straight through to its directory priority
(`.worktrees/` > `worktrees/` > CLAUDE.md > ask), which ignores
conventions already set by teammates or other Claude interfaces. This
repo has three worktrees under `.claude/worktrees/` with branches like
`feature/<slug>` — the unpatched skill would create a fourth under
`.worktrees/` and break the pattern.

The new Step 0 makes inference the first-priority source; explicit
directory checks and CLAUDE.md fall through only when no worktrees
exist yet.

### Patch 2 — `.claude/worktrees/` added to directory priority

In `## Step 1: Directory Selection Process` (formerly just "Directory
Selection Process"), added `.claude/worktrees/` as the top priority
existing-directory check, above `.worktrees/` and `worktrees/`.

**Why:** Claude Desktop and several plugin-tool conventions create
worktrees under `.claude/worktrees/`. The upstream skill only knows
about `.worktrees/` and `worktrees/`, so it would never match the
Claude-tool directory even when present.

### Patch 3 — Step 2: Branch Naming Convention

Added a new `## Step 2: Branch Naming Convention` section between
"Step 1: Directory Selection Process" and "Safety Verification".
It establishes a priority order for branch naming:

1. Step 0 pattern (if existing branches show `feature/*` or similar)
2. CLAUDE.md rule (many projects mandate `feature/<slug>`)
3. Ask user

**Why:** Upstream never addresses branch naming at all — the skill
just uses whatever `$BRANCH_NAME` is passed in. This silently lets
tool-specific defaults (`claude/<random>`) leak into repos that have
explicit conventions (`feature/<slug>`). In this repo's case,
`CLAUDE.md` says "automatically create a git worktree on branch
`feature/<slug>`" — but the upstream skill never reads it for branch
guidance.

### Patch 4 — Python setup uses `uv` when present

In `Step 2 → Run Project Setup`, changed the Python branch from:

```bash
if [ -f pyproject.toml ]; then poetry install; fi
```

to:

```bash
if [ -f pyproject.toml ]; then
  if command -v uv >/dev/null 2>&1 && [ -f uv.lock ]; then
    uv sync
  else
    poetry install
  fi
fi
```

**Why:** This repo uses `uv`, not `poetry`. The upstream heuristic
would run the wrong command. The patch falls through to poetry when
uv isn't installed, so it's backward-compatible for poetry-based repos.

Similarly, the baseline-test example now shows `uv run pytest -q`
alongside plain `pytest`.

### Patch 5 — Minor references and examples

- Updated `## Quick Reference` table to include `.claude/worktrees/`.
- Updated `## Common Mistakes` with two new entries: "Skipping
  convention discovery" and "Hardcoding branch-name patterns".
- Updated `## Example Workflow` to show a realistic Step-0 discovery
  and `feature/<slug>` branch naming.
- Updated `## Red Flags` lists to include "Skip `git worktree list`"
  and the agent-default naming warning.

## Re-syncing with upstream

When `superpowers` bumps past v5.0.7 and you want to pull improvements:

1. Read the new upstream `SKILL.md` from
   `~/.claude/plugins/cache/claude-plugins-official/superpowers/<version>/skills/using-git-worktrees/SKILL.md`.
2. Diff against the v5.0.7 baseline to see upstream's changes.
3. Re-apply Patches 1–5 from this file onto the new upstream text.
4. Update the "Upstream" line above to the new version.
5. Commit: `chore(agents): re-sync vendored using-git-worktrees with superpowers <version>`.
