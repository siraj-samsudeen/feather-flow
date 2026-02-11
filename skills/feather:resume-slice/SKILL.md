---
name: feather:resume-slice
description: Restore context after session break. Detects graceful handoff (CONTINUE.md) or recovers from abrupt cutoff. Shows status and routes to next action.
---

# Resume Slice

## Overview

Restore context after a session break. Handles both graceful handoffs (CONTINUE.md exists) and recovery from abrupt cutoffs.

**When to use:**
- Starting a new session on an existing slice project
- After running `/feather:pause-slice`
- After context reset (`/clear`)
- When unsure where you left off

**Announce at start:** "I'm using resume-slice to restore context."

## Process

### Step 1: Check for Graceful Handoff

```bash
ls .slices/CONTINUE.md 2>/dev/null
```

**If CONTINUE.md exists:** Graceful handoff path (Step 2a)
**If not:** Recovery mode path (Step 2b)

### Step 2a: Graceful Handoff (CONTINUE.md exists)

1. Read CONTINUE.md for exact position
2. Display status with TDD phase
3. Offer options:
   - **Continue** from where you left off
   - **Discard** and start slice fresh

```markdown
## Resuming AUTH-1

**Last Session:** 2025-02-04
**TDD Phase:** GREEN (3/5 tests passing)

### Work Completed
- [x] Spec created
- [x] Gherkin derived
- [x] Tests written
- [ ] Implementation (60%)

### Uncommitted Changes
- src/auth/login.ts (in progress)

### Next Action
Implement `validatePassword()` to pass test 3

---

**Options:**
1. Continue from here
2. Discard progress and start AUTH-1 fresh
```

### Step 2b: Recovery Mode (No CONTINUE.md)

Detect state through investigation:

```bash
# Check for uncommitted changes
git status --porcelain

# Check for failing tests
npm test 2>&1 | grep -E "FAIL|PASS"

# Check for spec/gherkin files
ls docs/specs/*/spec.md docs/specs/*/gherkin.md 2>/dev/null

# Check slices.json for incomplete slices
cat .slices/slices.json | grep '"passes": false'
```

**Recovery heuristics:**

| Finding | Likely Phase |
|---------|-------------|
| Uncommitted test files | RED or GREEN |
| Failing tests | GREEN (implementing) |
| Spec exists, slice not passing | In TDD loop |
| All tests pass, slice marked false | VERIFY phase |
| No uncommitted changes | Between slices |

Display with warning:

```markdown
## Recovery Mode

**No handoff file found.** Detecting state...

### Findings
- Uncommitted: src/auth/login.ts, src/auth/login.test.ts
- Test status: 3 failing
- Current slice: AUTH-1 (passes: false)

### Likely Position
**TDD Phase:** GREEN (implementing)
- Spec and Gherkin exist
- Tests written but failing
- Implementation in progress

---

**Options:**
1. Continue with recovery (may need manual verification)
2. Discard uncommitted changes and start AUTH-1 fresh
```

### Step 3: Read Project Status

Read `.slices/slices.json` and `.slices/STATE.md`:

```markdown
## Slice Project: [Project Name]

**Progress:** 2/6 slices complete

| Slice | Status | Coverage |
|-------|--------|----------|
| AUTH-1 | ✓ Complete | 100% |
| AUTH-2 | ✓ Complete | 100% |
| TODO-1 | → In Progress | — |
| PROJ-1 | Pending | — |
| MEMBER-1 | Pending | — |
| NOTIFY-1 | Pending | — |

**Last session:** 2025-02-04
**Decisions:** Using JWT for auth, bcrypt for passwords
```

### Step 4: Route to Next Action

Based on detected state:

| State | Action |
|-------|--------|
| CONTINUE.md exists | Show position, offer continue/discard |
| Recovery detected | Show findings, offer continue/reset |
| Slice in progress | Route to `/feather:work-slice` |
| All slices complete | "Project complete! Run `/feather:finish`" |
| Blocker noted | Show blocker, ask for resolution |

## Slice Flexibility

User can always request changes during resume:

### Redo a Slice
```
User: "Redo AUTH-1"

→ Mark AUTH-1 as passes: false
→ Optionally delete existing spec/tests
→ Re-enter TDD loop
```

### Reorder Slices
```
User: "Move TODO-1 before PROJ-1"

→ Check dependencies (warn if breaks them)
→ Update order numbers in slices.json
→ Update SLICES.md
```

### Remove a Slice
```
User: "Remove MEMBER-1"

→ Check nothing depends on it
→ Remove from slices.json
→ Update SLICES.md
```

### Split a Slice
```
User: "Split TODO-1 into basic and advanced"

→ Create TODO-1a and TODO-1b
→ TODO-1b depends on TODO-1a
→ Remove original TODO-1
```

**Requirements are iterative.** The slice list is always editable.

## Discard Option

If user chooses to discard:

1. Delete CONTINUE.md
2. Optionally reset uncommitted changes:
   ```bash
   git checkout -- .  # Discard uncommitted changes
   ```
3. Start slice fresh with `/feather:work-slice`

## Example Sessions

### Graceful Resume

```
Claude: "I'm using resume-slice to restore context."

Found .slices/CONTINUE.md

## Resuming AUTH-1

**Last Session:** 2025-02-04
**TDD Phase:** GREEN (3/5 tests passing)

### Next Action
Implement `validatePassword()` to pass test 3

Continue from here? (or "discard" to start fresh)

User: "continue"

Claude: Continuing AUTH-1 at GREEN phase...
[Routes to /feather:work-slice to continue implementation]
```

### Recovery Resume

```
Claude: "I'm using resume-slice to restore context."

No handoff file found. Entering recovery mode...

## Recovery Mode

### Findings
- Uncommitted: src/auth/login.ts
- Tests: 2 failing
- Slice: AUTH-1 (passes: false)

### Likely Position
GREEN phase - implementing to pass failing tests

Continue with these findings? (or "reset" to start fresh)

User: "continue"

Claude: Continuing AUTH-1 recovery...
Running tests to confirm state...
[Routes to /feather:work-slice]
```

### Project Complete

```
Claude: "I'm using resume-slice to restore context."

## Slice Project: Todo App

**Progress:** 6/6 slices complete

| Slice | Status |
|-------|--------|
| AUTH-1 | ✓ Complete |
| PROJ-1 | ✓ Complete |
| TODO-1 | ✓ Complete |
| MEMBER-1 | ✓ Complete |
| COMMENT-1 | ✓ Complete |
| NOTIFY-1 | ✓ Complete |

All slices complete! Run /feather:finish to finalize.
```

## Relationship to Other Skills

```
[Session start]
     │
     ↓
/feather:resume-slice (THIS SKILL)
     │
     ├── Reads: .slices/CONTINUE.md (if exists)
     ├── Reads: .slices/slices.json
     ├── Reads: .slices/STATE.md
     │
     ├── Routes to: /feather:work-slice (continue slice)
     ├── Routes to: /feather:finish (all complete)
     └── Routes to: /feather:add-slice (user wants more)
```
