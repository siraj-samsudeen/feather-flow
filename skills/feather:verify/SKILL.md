---
name: feather:verify
description: Checkpoint 2 - Verify feature works after implementation. Runs automated checks, generates manual verification guide, collects structured feedback, saves to .feedback/ folder.
attribution: Inspired by superpowers:verification-before-completion by Jesse Vincent (MIT)
---

# Verify Feature

## Overview

Checkpoint 2: After implementation. Verify the feature actually works, not just that tests pass. Collect structured feedback for tracking.

**Core principle:** Feature works > Tests pass

**Announce at start:** "I'm using feather:verify for Checkpoint 2 verification."

## Checkpoint 2: After Implementation

```
┌─────────────────────────────────────────────────────────────────┐
│ CHECKPOINT 2: After Implementation                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1. Automated Verification (Claude runs)                        │
│     □ Test suite passes                                         │
│     □ Integration checks pass                                   │
│     □ Type/lint checks pass                                     │
│                                                                 │
│  2. Manual Verification Guide (Claude generates, User performs) │
│     □ Step-by-step instructions grouped by spec                 │
│     □ Each step traces to requirement group                     │
│                                                                 │
│  3. Feedback Collection (User provides, Claude saves)           │
│     □ Failed steps (by number)                                  │
│     □ Related issues                                            │
│     □ Unrelated issues noticed                                  │
│                                                                 │
│  4. Save Feedback                                               │
│     □ Create .feedback/open/ files                              │
│     □ Categorize by type                                        │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## Step 1: Automated Verification

Run these checks and report results:

```markdown
## Automated Verification

| Check | Command | Result |
|-------|---------|--------|
| Test Suite | `npm test` | ✓ 42 passed / ✗ 3 failed |
| Type Check | `npm run typecheck` | ✓ No errors |
| Lint | `npm run lint` | ✓ No warnings |
| Build | `npm run build` | ✓ Success |
| Coverage | `npm test -- --coverage` | ✓ 100% branches |

**Automated Result:** PASS / FAIL
```

If automated checks fail, fix them before proceeding to manual verification.

### Coverage Gate (Mandatory)

Coverage MUST be 100% for new/modified files:

| Metric | Threshold |
|--------|-----------|
| Line coverage | 100% |
| Branch coverage | 100% |
| Function coverage | 100% |

```bash
npm test -- --coverage
```

**If coverage < 100%:**
1. Identify uncovered lines from the report
2. Either add tests for those lines, OR
3. Remove the untested code (it shouldn't exist without a test driving it)
4. Re-run until 100%

**Why this matters:** The TodoList example had 75% branch coverage. The uncovered 25% included the `onBlur` handler that was never wired to the backend. 100% coverage would have caught this.

**Uncovered branch = untested code path = potential bug.**

## Step 2: Generate Manual Verification Guide

Generate step-by-step instructions **grouped by spec requirement groups** with traceability.

### Template

```markdown
## Manual Verification: [Feature Name]

**Spec:** `docs/specs/[feature].md`

Please verify each step. Report results using the feedback format below.

---

### [GROUP-1]: [Capability Name]

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | [Specific action] | [What should happen] |
| 2 | [Specific action] | [What should happen] |
| 3 | [Specific action] | [What should happen] |

---

### [GROUP-2]: [Capability Name]

| Step | Action | Expected Result |
|------|--------|-----------------|
| 4 | [Specific action] | [What should happen] |
| 5 | [Specific action] | [What should happen] |
| 6 | [Specific action] | [What should happen] |

---

### [GROUP-3]: [Capability Name]

| Step | Action | Expected Result |
|------|--------|-----------------|
| 7 | [Specific action] | [What should happen] |
| 8 | [Specific action] | [What should happen] |
```

### Example: Quick Task

```markdown
## Manual Verification: Quick Task

**Spec:** `docs/specs/quick-task.md`

---

### QT-1: Quick Task Creation

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Navigate to "My Tasks" | Task list with input field at top |
| 2 | Type "Test quick task" | Text appears in input |
| 3 | Press Enter | Task appears in list below |
| 4 | Check input field | Input is now empty |
| 5 | Press Enter with empty input | Nothing happens |

---

### QT-2: Quick Task Visibility

| Step | Action | Expected Result |
|------|--------|-----------------|
| 6 | Create quick task, open it | Assignee shows your name |
| 7 | Check visibility field | Shows "Private" |
| 8 | Change assignee to teammate | Visibility changes to "Shared" |
| 9 | Switch to teammate's view | Task visible in their list |

---

### QT-3: Project Task Creation

| Step | Action | Expected Result |
|------|--------|-----------------|
| 10 | Navigate to a project | Project task list shown |
| 11 | Create task via input | Task appears in project list |
| 12 | Check visibility | Shows "Shared" |
```

## Step 3: Collect Feedback

After user performs manual verification, collect structured feedback:

```markdown
## Feedback Collection

### A. Failed Steps (by number)

For each step that failed:

```
Step #: [number]
Group: [e.g., QT-2]
Expected: [what should have happened]
Actual: [what actually happened]
```

### B. Issues Related to This Feature

Issues discovered that ARE related to [Feature Name]:

```
Description: [what's wrong]
Severity: bug / tweak / ux-improvement
Steps to reproduce: [if applicable]
```

### C. Issues NOT Related to This Feature

Issues noticed while testing that are NOT related to [Feature Name]:

```
Description: [what's wrong]
Area: [e.g., navigation, sidebar, other feature]
Severity: bug / tweak / ux-improvement
```
```

## Step 4: Save Feedback to .feedback/

Create feedback files in `.feedback/open/`:

### File Naming

```
.feedback/
├── open/
│   ├── BUG-001-[slug].md        # Failed step or bug
│   ├── TWEAK-002-[slug].md      # Small improvement
│   ├── UX-003-[slug].md         # UX improvement
│   └── FEATURE-004-[slug].md    # New feature request
```

### Feedback File Format

```markdown
---
id: BUG-001
type: bug | tweak | ux-improvement | feature
source: checkpoint-2
priority: P0 | P1 | P2 | P3
feature: [feature-name]
group: [requirement group, e.g., QT-2]
step: [step number if from manual verification]
created: [date]
status: open
---

# [Short Title]

## Description

[What's wrong or what's requested]

## Source Details

- **Found during:** Checkpoint 2 manual verification
- **Feature:** [Feature Name]
- **Requirement Group:** [QT-2]
- **Verification Step:** [Step 7]

## Expected vs. Actual

- **Expected:** [what should happen]
- **Actual:** [what happened]

## Steps to Reproduce

1. [step]
2. [step]

## Notes

[Any additional context]
```

### Priority Guidelines

| Priority | Definition | Action |
|----------|------------|--------|
| P0 | Blocks release | Fix immediately |
| P1 | Major issue | Fix before phase complete |
| P2 | Minor issue | Fix this phase if time |
| P3 | Nice to have | Backlog |

### Auto-generate INDEX.md

After saving feedback, update `.feedback/INDEX.md`:

```markdown
# Feedback Dashboard

Last updated: [date]

## Summary

| Status | Count |
|--------|-------|
| Open | 3 |
| In Progress | 1 |
| Resolved | 5 |

## Open by Priority

### P0 (Fix Now)
- None

### P1 (Before Phase Complete)
- [BUG-001](open/BUG-001-visibility-not-updating.md) - QT-2: Visibility not updating

### P2 (This Phase If Time)
- [TWEAK-002](open/TWEAK-002-input-focus.md) - QT-1: Input loses focus

### P3 (Backlog)
- [UX-003](open/UX-003-keyboard-shortcut.md) - Add keyboard shortcut
```

## Output Format

Complete Checkpoint 2 report:

```markdown
## Checkpoint 2 Report: [Feature Name]

### Automated Verification

| Check | Result |
|-------|--------|
| Test Suite | ✓ 42 passed |
| Type Check | ✓ No errors |
| Lint | ✓ No warnings |
| Build | ✓ Success |

**Automated:** PASS

---

### Manual Verification

| Group | Steps | Passed | Failed |
|-------|-------|--------|--------|
| QT-1 | 1-5 | 5 | 0 |
| QT-2 | 6-9 | 3 | 1 |
| QT-3 | 10-12 | 3 | 0 |

**Failed Steps:**
- Step 8 (QT-2): Visibility didn't update when assignee changed

---

### Feedback Saved

| ID | Type | Priority | Group | Description |
|----|------|----------|-------|-------------|
| BUG-001 | bug | P1 | QT-2 | Visibility not updating |
| UX-002 | ux | P3 | — | Keyboard shortcut suggestion |

---

### Verdict

**Checkpoint 2:** PASS WITH ISSUES / FAIL

**Next Steps:**
- [ ] Fix P1 issues before proceeding
- [ ] P2/P3 issues logged for later
```

## Relationship to Other Skills

```
Implementation complete
        ↓
  feather:verify (THIS SKILL - Checkpoint 2)
        ↓
    ┌───┴───┐
    │       │
  PASS    FAIL
    │       │
    ↓       ↓
feather:  Fix issues,
finish    re-verify
```

## Red Flags - STOP

- "Tests pass, feature is done" - NO, run Checkpoint 2
- Skipping manual verification
- Not saving feedback to .feedback/
- Proceeding with P0/P1 issues unresolved

## The Iron Law

```
NO COMPLETION CLAIMS WITHOUT CHECKPOINT 2

Automated checks + Manual verification + Feedback saved = Verified
```
