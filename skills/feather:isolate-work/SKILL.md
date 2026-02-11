---
name: feather:isolate-work
description: Use when starting feature work that needs isolation. Offers choice between simple branch or git worktree, with smart defaults for worktree location.
attribution: Inspired by superpowers:using-git-worktrees by Jesse Vincent (MIT)
---

# Isolate Work

## Overview

Create an isolated workspace for feature development. Choose between a simple branch (stay in same directory) or a git worktree (separate directory).

**Core principle:** Ask user preference, then set up reliably.

**Announce at start:** "I'm using the isolate-work skill to set up an isolated workspace."

## Step 1: Ask Isolation Method

Present the choice:

```
How would you like to isolate this work?

1. Branch only (stay in current directory, switch branches)
2. Worktree (separate directory, work on both simultaneously)

Branch is simpler. Worktree lets you keep main branch accessible.
Which would you prefer?
```

**Default recommendation:** Branch for simple features, Worktree for longer work or when you need to reference main.

## Branch Flow (Option 1)

### Create Branch
```bash
git checkout -b feature/<feature-name>
```

### Verify Clean Baseline
```bash
# Run project tests
npm test / cargo test / pytest / go test ./...
```

### Report Ready
```
Branch ready: feature/<feature-name>
Tests passing (<N> tests)
Ready to implement <feature-name>
```

## Worktree Flow (Option 2)

### 2a. Determine Worktree Location

**Priority order:**

1. **Check existing directories:**
```bash
ls -d .worktrees 2>/dev/null     # Project-local (hidden)
ls -d worktrees 2>/dev/null      # Project-local
```
If found → use that directory

2. **Check CLAUDE.md for preference:**
```bash
grep -i "worktree.*director" CLAUDE.md 2>/dev/null
```
If specified → use it

3. **Ask user with default:**
```
Where should I create worktrees?

1. .worktrees/ (project-local, hidden) - Recommended
2. ~/worktrees/<project-name>/ (global location)
3. Other (specify path)

Default location for future worktrees: ~/worktrees/
```

**Global default:** `~/worktrees/<project-name>/`

This keeps worktrees organized across projects and avoids cluttering project directories.

### 2b. Verify Gitignore (Project-Local Only)

**If using project-local directory (.worktrees or worktrees):**

```bash
git check-ignore -q .worktrees 2>/dev/null || git check-ignore -q worktrees 2>/dev/null
```

**If NOT ignored:**
1. Add to .gitignore
2. Commit the change
3. Proceed

### 2c. Create Worktree

```bash
# Determine full path
project=$(basename "$(git rev-parse --show-toplevel)")

# For project-local
path=".worktrees/<branch-name>"

# For global
path="~/worktrees/$project/<branch-name>"

# Create worktree with new branch
git worktree add "$path" -b feature/<feature-name>
cd "$path"
```

### 2d. Run Project Setup

Auto-detect and run:

```bash
# Node.js
if [ -f package.json ]; then npm install; fi

# Rust
if [ -f Cargo.toml ]; then cargo build; fi

# Python
if [ -f requirements.txt ]; then pip install -r requirements.txt; fi
if [ -f pyproject.toml ]; then poetry install; fi

# Go
if [ -f go.mod ]; then go mod download; fi
```

### 2e. Verify Clean Baseline

```bash
npm test / cargo test / pytest / go test ./...
```

**If tests fail:** Report failures, ask whether to proceed or investigate.

### 2f. Report Ready

```
Worktree ready at <full-path>
Tests passing (<N> tests)
Ready to implement <feature-name>

To return to main: cd <original-path>
```

## Quick Reference

| Choice | When to Use | Pros | Cons |
|--------|-------------|------|------|
| **Branch** | Simple features, quick fixes | Simple, no extra dirs | Can't access main simultaneously |
| **Worktree** | Long features, need reference | Both branches accessible | Extra directory to manage |

| Worktree Location | When to Use |
|-------------------|-------------|
| `.worktrees/` (project-local) | Single project, want hidden |
| `~/worktrees/<project>/` (global) | Multiple projects, organized |

## Saving Preferences

After first use, suggest adding to CLAUDE.md:

```markdown
## Worktree Preferences

Location: ~/worktrees/
Method: worktree (or branch)
```

This avoids re-asking on future features.

## Red Flags

**Never:**
- Create project-local worktree without verifying gitignore
- Skip baseline test verification
- Proceed with failing tests without asking
- Assume location without checking existing directories

**Always:**
- Ask branch vs worktree preference
- Follow directory priority: existing > CLAUDE.md > ask
- Verify gitignore for project-local
- Run project setup
- Verify tests pass

## Integration

**Called by:**
- `feather:workflow` - When design is approved and implementation follows
- Any workflow needing isolated workspace

**Pairs with:**
- `feather:finish` - Cleanup after work complete
- `feather:execute` - Work happens in isolated workspace
