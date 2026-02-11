---
name: feather:create-issue
description: Quickly capture bugs and UX tweaks discovered during use to the polish queue. Use when you notice something minor that doesn't warrant a full slice.
---

# Note Issue

## Overview

Quickly capture bugs and UX improvements to the polish queue without interrupting current work. These get addressed later with `/feather:polish`.

**When to use:**
- Found a minor bug while testing
- Noticed a UX improvement after seeing final output
- Too small for a full slice, but needs tracking
- "I'll fix this later" moments

**Announce at start:** "I'm noting this issue to the polish queue."

## Process

### Step 1: Capture the Issue

User describes the issue briefly:
- "The save button is hard to see"
- "Login fails with special characters"
- "Add loading spinner on form submit"

### Step 2: Categorize

| Type | Use For |
|------|---------|
| **BUG** | Something broken that needs fixing |
| **UX** | User experience improvement |
| **PERF** | Performance issue |
| **A11Y** | Accessibility improvement |

### Step 3: Append to polish.md

Add to `.slices/feather:polish.md`:

```markdown
## Polish Queue

- [ ] **BUG** Login fails with special chars in password (during AUTH-1)
- [ ] **UX** Save button contrast too low (during TODO-1)
- [ ] **UX** Add loading spinner on form submit
- [ ] **PERF** Todo list slow with 100+ items
- [ ] **A11Y** Missing aria-label on delete button
```

### Step 4: Confirm

```markdown
Added to polish queue (now 5 items):
- **UX** Save button contrast too low

Run /feather:polish when ready to address these.
```

## polish.md Format

```markdown
# Polish Queue

Items captured during development for later polish.

## Open

- [ ] **BUG** Login fails with special chars in password (AUTH-1)
- [ ] **UX** Save button contrast too low (TODO-1)
- [ ] **UX** Add loading spinner on form submit
- [ ] **PERF** Todo list slow with 100+ items

## Done

- [x] **BUG** Double-click creates duplicate todos (TODO-1) - fixed 2025-02-04
```

## Context Capture

When noting an issue, capture relevant context:

| Context | Example |
|---------|---------|
| Related slice | (during AUTH-1) |
| Steps to reproduce | "Enter password with @" |
| Expected behavior | "Should accept special chars" |
| Actual behavior | "Shows 'invalid password' error" |

Minimal is fine - just enough to remember what to fix.

## Key Constraints

- **Just captures, doesn't fix** - Quick and lightweight
- **TDD still required** - When fixing via `/feather:polish`, tests first
- **Not for large features** - Use `/feather:add-slice` for those

## When to Use /feather:create-issue vs /feather:add-slice

| Situation | Use |
|-----------|-----|
| Bug fix (< 1 hour) | /feather:create-issue → /feather:polish |
| UX tweak (< 30 min) | /feather:create-issue → /feather:polish |
| New feature | /feather:add-slice |
| Requires spec | /feather:add-slice |
| Affects multiple files | /feather:add-slice (probably) |

## Example Session

```
User: "The save button is hard to see on dark backgrounds"

Claude: "I'm noting this issue to the polish queue."

Added to polish queue (now 3 items):
- **UX** Save button contrast too low on dark backgrounds

Run /feather:polish when ready to address polish items.
```

## Batch Noting

Multiple issues at once:

```
User: "A few things I noticed:
- Save button hard to see
- No loading state on submit
- Delete confirmation too small"

Claude: "I'm noting these issues to the polish queue."

Added 3 items to polish queue (now 6 total):
- **UX** Save button contrast too low
- **UX** Add loading state on submit
- **UX** Enlarge delete confirmation dialog

Run /feather:polish when ready.
```

## Relationship to Other Skills

```
/feather:work-slice (during implementation)
     │
     ↓
/feather:create-issue (THIS SKILL - quick capture)
     │
     ├── Appends to: .slices/feather:polish.md
     │
     ↓
[Later]
     │
     ↓
/feather:polish (work through queue with TDD)
```
