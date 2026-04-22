---
name: using-git-worktrees
description: Use when starting feature work that needs isolation from current workspace or before executing implementation plans - creates isolated git worktrees with smart directory selection and safety verification
---

# Using Git Worktrees

## Overview

Git worktrees create isolated workspaces sharing the same repository, allowing work on multiple branches simultaneously without switching.

**Core principle:** Systematic directory selection + safety verification = reliable isolation.

**Announce at start:** "I'm using the using-git-worktrees skill to set up an isolated workspace."

## Step 0: Discover Existing Convention

**ALWAYS run this FIRST — before touching directories or names.**

A repo may already have established worktree conventions created by teammates, other agent tools (Claude Desktop, CI), or prior sessions. Blindly applying defaults creates inconsistency and fights the existing pattern.

```bash
git worktree list
```

Examine the output. Look for:

1. **Consistent parent directory** — e.g., every existing worktree lives under `.claude/worktrees/`, `.worktrees/`, or similar. If ≥2 worktrees share a parent, that parent IS the convention. Use it.
2. **Branch naming pattern** — e.g., `feature/<slug>`, `claude/<random>`, `wip/<topic>`. If a pattern is visible in ≥2 existing branches, follow it.
3. **Main repo location** — the first entry (no branch decoration or tagged `[main]`) is the main worktree, not a candidate pattern.

**If a convention is visible, use it and move directly to Step 2 (Safety Verification).** Do NOT fall through to directory priority or ask the user — the pattern is already established.

**If only one existing worktree exists** and its convention is ambiguous (single data point), prefer CLAUDE.md over the sample-of-one.

**If no existing worktrees** (only the main repo is listed), fall through to Step 1.

## Step 1: Directory Selection Process

Follow this priority order **only when Step 0 found no convention**:

### 1. Check Existing Directories

```bash
# Check in priority order
ls -d .claude/worktrees 2>/dev/null  # Claude-tool convention (if present)
ls -d .worktrees 2>/dev/null         # superpowers default (hidden)
ls -d worktrees 2>/dev/null          # Alternative
```

**If found:** Use that directory. Priority order is top-to-bottom; first match wins.

### 2. Check CLAUDE.md

```bash
grep -i "worktree.*director" CLAUDE.md 2>/dev/null
```

**If preference specified:** Use it without asking.

### 3. Ask User

If no directory exists and no CLAUDE.md preference:

```
No worktree directory found. Where should I create worktrees?

1. .worktrees/ (project-local, hidden)
2. ~/.config/superpowers/worktrees/<project-name>/ (global location)

Which would you prefer?
```

## Step 2: Branch Naming Convention

**Before creating the branch, determine its name.** Priority:

1. **Step 0 pattern** — if existing worktrees show a branch prefix like `feature/*` or `wip/*`, use it.
2. **CLAUDE.md rule** — check CLAUDE.md for branch naming guidance:
   ```bash
   grep -iE "branch.*nam|feature/<slug>|worktree.*on branch" CLAUDE.md 2>/dev/null
   ```
   Many projects specify a pattern (e.g., `feature/<slug>` where slug derives from the plan filename).
3. **Ask user** if neither Step 0 nor CLAUDE.md gives a pattern.

Do NOT default to `claude/<random>` or similar agent-specific names in a repo that shows project-convention patterns — this creates inconsistency with teammate-created branches.

## Safety Verification

### For Project-Local Directories (.claude/worktrees, .worktrees, worktrees)

**MUST verify directory is ignored before creating worktree:**

```bash
# Check if directory is ignored (respects local, global, and system gitignore)
git check-ignore -q .claude/worktrees 2>/dev/null || \
  git check-ignore -q .worktrees 2>/dev/null || \
  git check-ignore -q worktrees 2>/dev/null
```

**If NOT ignored:**

Per Jesse's rule "Fix broken things immediately":
1. Add appropriate line to .gitignore
2. Commit the change
3. Proceed with worktree creation

**Why critical:** Prevents accidentally committing worktree contents to repository.

### For Global Directory (~/.config/superpowers/worktrees)

No .gitignore verification needed - outside project entirely.

## Creation Steps

### 1. Detect Project Name

```bash
project=$(basename "$(git rev-parse --show-toplevel)")
```

### 2. Create Worktree

```bash
# Determine full path — use the directory convention identified in
# Step 0 / Step 1, and the branch name from Step 2.
case $LOCATION in
  .claude/worktrees|.worktrees|worktrees)
    path="$LOCATION/$WORKTREE_NAME"
    ;;
  ~/.config/superpowers/worktrees/*)
    path="~/.config/superpowers/worktrees/$project/$WORKTREE_NAME"
    ;;
esac

# Note: WORKTREE_NAME (directory name under the parent) and BRANCH_NAME
# (full branch like "feature/<slug>") may differ. A common pattern is:
#   path: .claude/worktrees/rewrite
#   branch: feature/rewrite
# Match whatever Step 0 / Step 2 surfaced.

git worktree add "$path" -b "$BRANCH_NAME"
cd "$path"
```

### 3. Run Project Setup

Auto-detect and run appropriate setup:

```bash
# Node.js
if [ -f package.json ]; then npm install; fi

# Rust
if [ -f Cargo.toml ]; then cargo build; fi

# Python
if [ -f requirements.txt ]; then pip install -r requirements.txt; fi
if [ -f pyproject.toml ]; then
  if command -v uv >/dev/null 2>&1 && [ -f uv.lock ]; then
    uv sync
  else
    poetry install
  fi
fi

# Go
if [ -f go.mod ]; then go mod download; fi
```

### 4. Verify Clean Baseline

Run tests to ensure worktree starts clean:

```bash
# Examples - use project-appropriate command
npm test
cargo test
uv run pytest -q   # or: pytest
go test ./...
```

**If tests fail:** Report failures, ask whether to proceed or investigate.

**If tests pass:** Report ready.

### 5. Report Location

```
Worktree ready at <full-path>
Branch: <branch-name>
Tests passing (<N> tests, 0 failures)
Ready to implement <feature-name>
```

## Quick Reference

| Situation | Action |
|-----------|--------|
| Existing worktrees show a convention | Follow it (Step 0) |
| `.claude/worktrees/` exists | Use it (verify ignored) |
| `.worktrees/` exists | Use it (verify ignored) |
| `worktrees/` exists | Use it (verify ignored) |
| Multiple exist | First match in priority order |
| None exist | Check CLAUDE.md → Ask user |
| Directory not ignored | Add to .gitignore + commit |
| Tests fail during baseline | Report failures + ask |
| No package.json/Cargo.toml/pyproject.toml | Skip dependency install |

## Common Mistakes

### Skipping convention discovery

- **Problem:** Creates worktrees that conflict with teammate or other-tool conventions; branches show up with inconsistent naming in `git branch`
- **Fix:** Always run `git worktree list` BEFORE directory selection; follow established patterns

### Skipping ignore verification

- **Problem:** Worktree contents get tracked, pollute git status
- **Fix:** Always use `git check-ignore` before creating project-local worktree

### Assuming directory location

- **Problem:** Creates inconsistency, violates project conventions
- **Fix:** Follow priority: discovered > existing > CLAUDE.md > ask

### Hardcoding branch-name patterns

- **Problem:** Tool-specific defaults (e.g., `claude/<random>`) clash with teammate-set patterns like `feature/<slug>`
- **Fix:** Infer from existing branches + CLAUDE.md; only fall back to defaults when nothing is set

### Proceeding with failing tests

- **Problem:** Can't distinguish new bugs from pre-existing issues
- **Fix:** Report failures, get explicit permission to proceed

### Hardcoding setup commands

- **Problem:** Breaks on projects using different tools
- **Fix:** Auto-detect from project files (package.json, etc.)

## Example Workflow

```
You: I'm using the using-git-worktrees skill to set up an isolated workspace.

[Step 0: git worktree list shows 3 existing worktrees under
 .claude/worktrees/ with branches feature/*]
[Convention identified: .claude/worktrees/<slug> + feature/<slug>]
[Verify .claude/worktrees/ is in .gitignore - confirmed]
[Plan slug: "rewrite" → branch: feature/rewrite, path: .claude/worktrees/rewrite]
[Create worktree: git worktree add .claude/worktrees/rewrite -b feature/rewrite]
[uv sync]
[uv run pytest -q — 720 passing]

Worktree ready at /Users/jesse/myproject/.claude/worktrees/rewrite
Branch: feature/rewrite
Tests passing (720 tests, 0 failures)
Ready to implement rewrite feature
```

## Red Flags

**Never:**
- Skip `git worktree list` before selecting a directory
- Create worktree without verifying it's ignored (project-local)
- Skip baseline test verification
- Proceed with failing tests without asking
- Assume directory location when ambiguous
- Skip CLAUDE.md check
- Use agent-default branch naming (`claude/<random>`) when a project convention is visible

**Always:**
- Discover existing convention first (Step 0)
- Follow directory priority: discovered > existing > CLAUDE.md > ask
- Verify directory is ignored for project-local
- Match branch naming to convention (Step 0 → CLAUDE.md → ask)
- Auto-detect and run project setup
- Verify clean test baseline

## Integration

**Called by:**
- **brainstorming** (Phase 4) - REQUIRED when design is approved and implementation follows
- **subagent-driven-development** - REQUIRED before executing any tasks
- **executing-plans** - REQUIRED before executing any tasks
- Any skill needing isolated workspace

**Pairs with:**
- **finishing-a-development-branch** - REQUIRED for cleanup after work complete
