---
name: feather:slice-project
description: Transform vague project requests into ordered vertical slices with dependencies. Outputs DOMAIN.md, slices.json, SLICES.md, and STATE.md. Use when starting a multi-feature project that spans multiple sessions.
---

# Slice Project

## Overview

Transform a vague project idea into ordered vertical slices ready for TDD execution. Each slice is a complete vertical cut - user-facing functionality from UI to database.

**Core principle:** Show first, refine later. Don't ask questions upfront - generate best-guess slices immediately, let user critique.

**Announce at start:** "I'm using slice-project to break this down into vertical slices."

## Prerequisites

Before slicing, verify TDD infrastructure:

```bash
# Check for tdd-guard hook
cat .claude/settings.json | grep -q "tdd-guard" && echo "TDD Guard: OK" || echo "TDD Guard: MISSING"

# Check for coverage thresholds in vitest config
grep -q "thresholds" vitest.config.* && echo "Coverage thresholds: OK" || echo "Coverage thresholds: MISSING"
```

**If missing:** Run `/feather:setup-tdd-guard` first. Slice workflow requires executable TDD enforcement.

## Process

### Step 1: Generate Slices Immediately

Take the vague request and generate best-guess slices without asking questions:

```markdown
Based on "[user's vague request]", here are suggested slices:

| # | Slice | Description | Depends On |
|---|-------|-------------|------------|
| 1 | AUTH-1 | Username/password login | — |
| 2 | PROJ-1 | Create/list projects | AUTH-1 |
| 3 | TODO-1 | Basic todo list in project | PROJ-1 |
| 4 | MEMBER-1 | Invite members to project | PROJ-1, AUTH-1 |

Pick one to start, or tell me what to change.
```

### Step 2: Offer Feather-Mockups for UI Slices

For slices with UI, offer quick visual previews:

```markdown
Slices with UI: TODO-1, PROJ-1, MEMBER-1

Want to see feather-mockups before deciding? (quick visual, not final design)
```

If yes, use `/feather:create-ui-mockup` for requested slices. Save to `docs/mockups/<slice-id>.html`.

### Step 3: Refine Based on Feedback

User can:
- **Pick one slice** to start (single-slice mode)
- **Approve all** and start with first
- **Add/remove/reorder** slices
- **Request mockups** for specific slices
- **Ask questions** (refinement happens after they've seen the proposal)

### Step 4: Create Project Structure

Once slices are approved, create:

```
project/
├── .slices/
│   ├── STATE.md           # Session state (human-readable)
│   ├── slices.json        # Machine-readable slice status
│   ├── SLICES.md          # Overview table
│   └── polish.md          # Bug/UX polish queue (empty initially)
├── docs/
│   ├── DOMAIN.md          # Data model tree
│   └── specs/             # Per-slice specs (created by /feather:work-slice)
│       └── <slice-id>/
└── src/
```

### Step 5: Add to .gitignore

Add transient files to `.gitignore`:

```
# Slice workflow (transient, local-only)
.slices/CONTINUE.md
```

## Output Files

### .slices/slices.json

```json
{
  "project": "Project Name",
  "created": "2025-02-04",
  "slices": [
    {
      "id": "AUTH-1",
      "name": "Username/password authentication",
      "order": 1,
      "depends_on": [],
      "passes": false,
      "spec_path": null
    },
    {
      "id": "PROJ-1",
      "name": "Create and list projects",
      "order": 2,
      "depends_on": ["AUTH-1"],
      "passes": false,
      "spec_path": null
    }
  ]
}
```

### .slices/SLICES.md

```markdown
# Project Slices

| # | ID | Name | Depends On | Status |
|---|-----|------|------------|--------|
| 1 | AUTH-1 | Username/password authentication | — | Pending |
| 2 | PROJ-1 | Create and list projects | AUTH-1 | Pending |
| 3 | TODO-1 | Basic todo list in project | PROJ-1 | Pending |

## Slice Details

### AUTH-1: Username/password authentication
- Login form with email/password
- Session management
- Logout functionality

### PROJ-1: Create and list projects
- Project creation form
- Project list view
- Project selection
```

### .slices/STATE.md

```markdown
# Slice State

**Project:** [Project Name]
**Started:** [date]
**Current Position:** Slice 0 (not started)

## Session Log

### [date]
- Initial slices defined
- User approved [N] slices
- Ready to start with [SLICE-ID]

## Decisions Made

(None yet)

## Blockers

(None)
```

### docs/DOMAIN.md

```markdown
# Domain Model

```
Project
├── id (unique)
├── name
├── owner → User
└── members → [User]

Todo
├── id (unique)
├── text
├── completed (yes/no)
├── project → Project
└── assignee → User

User
├── id (unique)
├── email
├── name
└── passwordHash
```

## Key Concepts

- **Project** contains Todos and has Members
- **User** owns Projects and is assigned Todos
- **Todo** belongs to exactly one Project
```

### .slices/feather:polish.md

```markdown
# Polish Queue

Items captured during development for later polish.

(Empty - use /feather:create-issue to add items)
```

## Slice ID Convention

Use short, memorable prefixes:

| Prefix | Domain |
|--------|--------|
| AUTH | Authentication |
| PROJ | Projects |
| TODO | Todos/Tasks |
| USER | User management |
| TEAM | Teams |
| NOTIFY | Notifications |
| REPORT | Reporting |

Format: `PREFIX-N` (e.g., AUTH-1, AUTH-2, TODO-1)

## What This Skill Does NOT Do

- **Does NOT start implementation** - Outputs structured plan for Ralph loop
- **Does NOT create specs** - That's `/feather:work-slice`
- **Does NOT ask questions upfront** - Show first, refine later

## Relationship to Other Skills

```
/feather:slice-project (THIS SKILL)
     │
     ├── Creates: .slices/*, docs/DOMAIN.md
     │
     ↓
/feather:work-slice (executes ONE slice with TDD)
     │
     ├── Creates: docs/specs/<slice-id>/spec.md
     ├── Uses: /feather:create-spec, /feather:derive-tests
     │
     ↓
/feather:pause-slice (if stopping mid-slice)
     │
     ├── Creates: .slices/CONTINUE.md
     │
     ↓
/feather:resume-slice (restore context, route to next action)
```

## Example Session

```
User: "I want to build a project management system like Basecamp"

Claude: "I'm using slice-project to break this down into vertical slices."

Based on "build a project management system like Basecamp", here are suggested slices:

| # | Slice | Description | Depends On |
|---|-------|-------------|------------|
| 1 | AUTH-1 | Username/password login | — |
| 2 | PROJ-1 | Create/list projects | AUTH-1 |
| 3 | TODO-1 | Basic todo list in project | PROJ-1 |
| 4 | MEMBER-1 | Invite members to project | PROJ-1, AUTH-1 |
| 5 | COMMENT-1 | Comments on todos | TODO-1 |
| 6 | NOTIFY-1 | Email notifications | AUTH-1 |

Slices with UI: PROJ-1, TODO-1, MEMBER-1, COMMENT-1

Pick one to start, or tell me what to change.
Want to see feather-mockups for any slice?

User: "Start with just AUTH-1 for now"

Claude: "Creating project structure for AUTH-1..."

Created:
- .slices/slices.json (1 slice)
- .slices/SLICES.md
- .slices/STATE.md
- docs/DOMAIN.md

Next: Run /feather:work-slice to start AUTH-1 with TDD.
```

## Git Workflow

**After creating slice structure:**
```bash
git add .slices/ docs/DOMAIN.md .gitignore
git commit -m "Initialize slice project structure"
```
