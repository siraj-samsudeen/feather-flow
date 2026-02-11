---
name: feather:pause-slice
description: Create handoff file when stopping mid-TDD cycle before slice completes. Captures exact TDD phase position for clean session handoff. Use when context is filling up or need to stop work temporarily.
---

# Pause Slice

## Overview

Create a handoff file when stopping in the middle of a TDD cycle, before the slice is complete. Captures exact position so `/feather:resume-slice` can restore context.

**When to use:**
- Context window filling up
- Need to stop before slice is complete
- Switching to other work temporarily
- End of work session

**Announce at start:** "I'm using pause-slice to create a handoff for [SLICE-ID]."

## Process

### Step 1: Detect Current Slice

Read `.slices/slices.json` to find the in-progress slice (first with `passes: false` and met dependencies).

### Step 2: Determine TDD Phase

Analyze current state to determine phase:

| Phase | Indicators |
|-------|------------|
| `spec` | Spec created, tests not derived |
| `gherkin` | Tests derived, not implemented |
| `red` | Tests written, some failing |
| `green` | Implementing, working toward passing |
| `verify` | All tests pass, running verification |

Detection commands:
```bash
# Check for spec
ls docs/specs/<slice-id>/spec.md

# Check for gherkin
ls docs/specs/<slice-id>/gherkin.md

# Check test status
npm test 2>&1 | grep -E "FAIL|PASS"

# Check for uncommitted changes
git status --porcelain
```

### Step 3: Capture State to CONTINUE.md

Create `.slices/CONTINUE.md`:

```markdown
---
slice_id: AUTH-1
phase: red
last_updated: 2025-02-04T14:30:00Z
---

# Continue AUTH-1

## Current Position
- **Slice:** AUTH-1 (Username/password auth)
- **TDD Phase:** RED - tests written, 3/5 failing
- **Last Test:** `should reject invalid password`

## Work Completed
- [x] Spec created: docs/specs/auth-1/spec.md
- [x] Gherkin derived: 5 scenarios
- [x] Tests written: 5 tests
- [ ] Implementation: 2/5 passing

## Uncommitted Changes
- src/auth/login.test.ts (new)
- src/auth/login.ts (in progress)

## Decisions Made
- Using bcrypt for password hashing
- JWT for session tokens

## Next Action
Continue GREEN phase: implement `validatePassword()` to pass test 3
```

### Step 4: DO NOT Commit CONTINUE.md

CONTINUE.md is local-only (gitignored). It's transient state for session handoff.

### Step 5: Display Confirmation

```markdown
✓ Paused AUTH-1 at RED phase (3/5 tests failing)

Handoff: .slices/CONTINUE.md

To resume: /feather:resume-slice
```

## CONTINUE.md Schema

```markdown
---
slice_id: <SLICE-ID>
phase: spec | gherkin | red | green | verify
last_updated: <ISO timestamp>
---

# Continue <SLICE-ID>

## Current Position
- **Slice:** <ID> (<name>)
- **TDD Phase:** <phase> - <detail>
- **Last Test:** <test name or N/A>

## Work Completed
- [x/] <completed items>
- [ ] <incomplete items>

## Uncommitted Changes
- <file> (<status>)

## Decisions Made
- <decision 1>
- <decision 2>

## Next Action
<specific next step to take>
```

## Key Constraints

- **Does NOT update slices.json** - Slice is not complete
- **Does NOT commit** - CONTINUE.md is local-only
- **Captures exact position** - Enough detail to resume without context loss

## What Gets Captured

| Item | Why |
|------|-----|
| Slice ID | Know which slice |
| TDD Phase | Know where in cycle |
| Test status | Know which tests pass/fail |
| Uncommitted files | Know what's in progress |
| Decisions | Avoid re-making decisions |
| Next action | Know exactly what to do |

## Example Session

```
User: "I need to stop for now"

Claude: "I'm using pause-slice to create a handoff for AUTH-1."

Detecting state...
- Slice: AUTH-1
- Phase: GREEN (implementing)
- Tests: 3/5 passing
- Uncommitted: src/auth/login.ts

Creating .slices/CONTINUE.md...

✓ Paused AUTH-1 at GREEN phase (3/5 tests passing)

Uncommitted work:
- src/auth/login.ts (in progress)

Handoff: .slices/CONTINUE.md

To resume: /feather:resume-slice
```

## Relationship to Other Skills

```
/feather:work-slice (in progress)
     │
     ↓
/feather:pause-slice (THIS SKILL)
     │
     ├── Creates: .slices/CONTINUE.md
     │
     ↓
[Session break]
     │
     ↓
/feather:resume-slice (reads CONTINUE.md, routes back to /feather:work-slice)
```
