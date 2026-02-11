---
name: feather:add-slice
description: Add more slices to an existing slice project. Use when you started with one slice and want to add more, or when new features emerge during development.
---

# Add Slice

## Overview

Add new slices to an existing project after initial setup with `/feather:slice-project`.

**When to use:**
- Started with one slice, want to add more
- New feature idea during development
- Breaking a large slice into smaller ones
- Reorganizing slice order

**Announce at start:** "I'm using add-slice to add new slices to the project."

## Prerequisites

- `.slices/slices.json` must exist (created by `/feather:slice-project`)
- New slice should have clear scope

## Process

### Step 1: Read Existing Slices

```bash
cat .slices/slices.json
```

Understand:
- Current slices and their IDs
- Dependencies between slices
- Which slices are complete (`passes: true`)

### Step 2: Determine New Slice Details

For the new slice, determine:
- **ID:** Use existing prefix pattern or new prefix (e.g., NOTIFY-1)
- **Name:** One-line description
- **Dependencies:** Which existing slices must complete first
- **Order:** Where it fits in execution sequence

### Step 3: Add to slices.json

Append new slice(s) with next available order number:

```json
{
  "id": "NOTIFY-1",
  "name": "Email notifications for task assignments",
  "order": 5,
  "depends_on": ["AUTH-1", "TODO-1"],
  "passes": false,
  "spec_path": null
}
```

### Step 4: Update SLICES.md

Add to the overview table and details section.

### Step 5: Update STATE.md

Log the addition:

```markdown
### [date]
- Added NOTIFY-1: Email notifications
- Depends on AUTH-1, TODO-1
- Order: 5
```

## Operations

### Add Single Slice

```
User: "Add a slice for email notifications"

→ Reads slices.json (has AUTH-1, TODO-1)
→ Creates NOTIFY-1 with depends_on: ["AUTH-1"]
→ Appends to slices.json
→ Updates SLICES.md
→ Updates STATE.md
```

### Add Multiple Slices

```
User: "Add slices for notifications and reporting"

→ Creates NOTIFY-1 and REPORT-1
→ Determines dependencies for each
→ Assigns order numbers
→ Updates all files
```

### Reorder Slices

```
User: "Move TODO-1 before PROJ-1"

→ Checks dependencies (fails if TODO-1 depends on PROJ-1)
→ Updates order numbers
→ Updates SLICES.md table
```

**Warning:** Cannot reorder if it breaks dependencies.

### Split a Slice

```
User: "Split TODO-1 into basic and advanced"

→ Creates TODO-1a (basic CRUD) and TODO-1b (advanced features)
→ TODO-1b depends_on TODO-1a
→ Removes original TODO-1
→ Updates all references
```

### Remove a Slice

```
User: "Remove MEMBER-1"

→ Checks nothing depends on it
→ Removes from slices.json
→ Updates SLICES.md
→ Logs removal in STATE.md
```

**Warning:** Cannot remove if other slices depend on it.

## Dependency Rules

1. **No circular dependencies** - A cannot depend on B if B depends on A
2. **Dependencies must exist** - Can only depend on slices in slices.json
3. **Order respects dependencies** - Slice order must allow dependencies to complete first

```
VALID:
  AUTH-1 (order 1) → PROJ-1 (order 2) → TODO-1 (order 3)
  TODO-1 depends_on: ["PROJ-1"]
  PROJ-1 depends_on: ["AUTH-1"]

INVALID:
  TODO-1 (order 1) → PROJ-1 (order 2)
  PROJ-1 depends_on: ["TODO-1"]  # Can't - TODO-1 hasn't run yet
```

## Example Session

```
User: "Add a slice for email notifications"

Claude: "I'm using add-slice to add new slices to the project."

Current slices:
| # | ID | Status |
|---|-----|--------|
| 1 | AUTH-1 | Complete |
| 2 | TODO-1 | In Progress |

Adding:
| # | ID | Name | Depends On |
|---|-----|------|------------|
| 3 | NOTIFY-1 | Email notifications | AUTH-1 |

NOTIFY-1 will run after TODO-1 completes.

Created. Run /feather:work-slice when ready.
```

## Git Workflow

**After adding slices:**
```bash
git add .slices/slices.json .slices/SLICES.md .slices/STATE.md
git commit -m "Add NOTIFY-1 slice"
```
