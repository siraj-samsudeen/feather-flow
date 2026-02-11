---
name: feather:derive-tests
description: Expand feather-spec into Gherkin scenarios (including edge cases) for user review before implementation. Groups match spec IDs. Creates checkpoint for scenario approval.
---

# Derive Tests From Spec

## Overview

Expand lightweight feather-spec requirements into complete Gherkin scenarios. This is where edge cases are added. User reviews scenarios before implementation begins.

**Core principle:** Feather-spec is light (client sign-off). This skill expands it to full test coverage.

**Announce at start:** "I'm using derive-tests-from-spec to generate Gherkin scenarios for review."

## The Problem This Solves

Feather-spec intentionally excludes edge cases to keep it light. But implementation needs complete coverage. This skill bridges the gap:

```
feather-spec (light)  →  derive-tests-from-spec  →  Full Gherkin (complete)
    4 requirements           (THIS SKILL)            12 scenarios
    client signs off                                 you review
```

## Process

### Step 1: Read the Feather-Spec

Identify:
- Requirement groups (QT-1, QT-2, etc.)
- EARS statements in each group
- **Examples tables** (sample data from user)
- Data model constraints
- Out of scope items

**Examples are gold.** They become concrete Gherkin scenario data instead of generic placeholders.

### Step 2: Expand Each Group to Gherkin

For each requirement group:

1. **Happy path scenarios** - Direct translation of EARS
2. **Edge case scenarios** - What wasn't in the spec but needs testing
3. **Error scenarios** - What happens when things go wrong

### Step 3: Present for Review (Checkpoint)

User reviews all Gherkin scenarios before implementation. This is **Checkpoint: Gherkin Review**.

## EARS → Gherkin Expansion

### WHEN (Event-Driven) → Happy Path + Edge Cases

**Spec (with examples):**
```markdown
WHEN I type and press Enter, THE SYSTEM SHALL create a task

**Examples:**
| Input | Result |
|-------|--------|
| "Buy milk" | Task created, incomplete |
| "Call dentist" | Task created, incomplete |
```

**Gherkin (uses examples as data):**
```gherkin
# Happy path - uses example data from spec
Scenario: QT-1.1 - Create task quickly
  When I type "Buy milk" and press Enter
  Then "Buy milk" appears in my task list
  And "Buy milk" is marked incomplete

Scenario: QT-1.2 - Create another task
  When I type "Call dentist" and press Enter
  Then "Call dentist" appears in my task list

# Edge cases (added by this skill)
Scenario: QT-1.3 - Empty input is ignored
  When I press Enter with nothing typed
  Then no task is created

Scenario: QT-1.4 - Whitespace-only input is ignored
  When I type "   " and press Enter
  Then no task is created

Scenario: QT-1.5 - Input clears after creation
  When I type "Buy milk" and press Enter
  Then the input field is empty
```

**Key:** "Buy milk" and "Call dentist" came from the spec examples, not invented here.

### IF/THEN (Error Cases) → Error Scenarios

**Spec:**
```
IF task title exceeds 500 characters THEN THE SYSTEM SHALL show error
```

**Gherkin:**
```gherkin
Scenario: Title too long shows error
  When I type a 501-character title
  Then I see "Title must be 500 characters or less"
  And no task is created
```

### WHILE (State-Driven) → State Scenarios

**Spec:**
```
WHILE task is in progress THE SYSTEM SHALL show timer
```

**Gherkin:**
```gherkin
Scenario: Timer shows for in-progress tasks
  Given a task with status "in progress"
  Then I see a timer on that task

Scenario: Timer hidden for other statuses
  Given a task with status "todo"
  Then I do not see a timer on that task
```

## Output Format

Generate clean, grouped Gherkin. Save to `docs/specs/<feature>/gherkin.md`.

**In the file** (for traceability): Include IDs, test level mappings, seed data.

**In conversation** (for user): Show clean Gherkin grouped by spec section, brief summary, and ask for approval.

Example conversation output:

```markdown
# Gherkin Scenarios: TodoList

## TL-1: Create Task

### Happy Path (in spec)

```gherkin
Scenario: Create task as manager
  Given I am logged in as Alice (manager)
  When I type "Review quarterly reports" and press Enter
  Then "Review quarterly reports" appears in my task list
```

### Edge Cases (not in spec)

```gherkin
Scenario: Empty input is ignored
  When I press Enter with nothing typed
  Then no task is created
```

## TL-2: View Tasks

### Happy Path (in spec)

```gherkin
Scenario: Empty state shows message
  Given I have no tasks
  Then I see "No todos yet"

Scenario: User sees only their own tasks
  Given Alice has task "Review reports"
  And Bob has task "Fix bug"
  When I am logged in as Alice
  Then I see "Review reports"
  And I do not see "Fix bug"
```

---

21 scenarios across 5 groups. Saved to `docs/specs/todolist/gherkin.md`.

**Approved to commit?**
```

## Edge Case Categories

When expanding, consider these categories:

### Input Edge Cases
- Empty input
- Whitespace-only input
- Very long input
- Special characters
- Unicode/emoji

### State Edge Cases
- Already in target state
- Conflicting states
- Rapid state changes

### Boundary Edge Cases
- First item
- Last item
- Only item
- Maximum items
- Zero items

### Permission Edge Cases
- Own vs. others' items
- Shared vs. private
- Role-based access

### Timing Edge Cases
- Concurrent modifications
- Stale data
- Offline/reconnect

## What NOT to Add

Don't add scenarios for:
- Implementation details (internal state management)
- Technical edge cases user wouldn't encounter
- Scenarios covered by framework (auth, CSRF, etc.)
- Duplicate coverage across groups

## Use Best Judgment (Don't Ask User)

Feather-spec intentionally omits edge cases that aren't important for user sign-off. When deriving tests, **use sensible defaults for standard UX patterns** instead of asking the user.

### Standard Defaults (Never Ask)

| Edge Case | Default Behavior | Why |
|-----------|------------------|-----|
| Empty/whitespace input | Ignore silently | No one expects an error for empty |
| Escape key during edit | Cancel and revert | Universal UX pattern |
| Save empty text | Revert to original | Safe/forgiving > destructive |
| Input field after submit | Clear it | Standard form behavior |
| Data after action | Persists (it's a database app) | Obvious |

### Example: What We Learned

When deriving tests for a TodoList spec, these questions came up:

**Q1: Empty Input**
> When I press Enter with nothing typed, what should happen?

**Default answer:** Ignore silently. No error, no task created.

**Q2: Cancel Editing**
> When I'm editing "Buy milk", change to "Buy oat milk", press Escape - what happens?

**Default answer:** Revert to "Buy milk". Escape-to-cancel is universal.

**Q3: Edit to Empty**
> When I edit "Buy milk", delete all text, press Enter - what happens?

**Default answer:** Revert to "Buy milk". Forgiving > destructive.

**Lesson:** All three had obvious answers. Don't burden the user with questions that have clear defaults.

### When TO Ask

Only ask when there's genuine ambiguity:
- Business logic with no clear default ("should completed tasks auto-archive after 30 days?")
- Destructive vs non-destructive when both are equally valid
- Feature scope questions ("should edit also allow changing due date?")

## Keep Output Clean

**Don't show users:**
- Test IDs (internal reference only)
- Long summary tables with counts
- Mapping tables (test level, file locations)

**Do show users:**
- Grouped Gherkin scenarios (readable)
- Brief summary ("21 scenarios across 5 groups")
- Decisions made (for transparency)

IDs and mappings go in the output file for traceability, but don't clutter the conversation.

## Test Level Mapping

After Gherkin is approved, map to test levels:

| Scenario Type | Test Level |
|---------------|------------|
| Happy path | E2E (Playwright) |
| User-visible edge cases | E2E or Integration |
| Technical edge cases | Unit test |

```markdown
## Test Level Mapping

| Scenario | Level | File |
|----------|-------|------|
| Create task quickly | E2E | e2e/quick-task.spec.ts |
| Empty input ignored | Unit | unit/inline-task-form.test.ts |
| Private by default | E2E | e2e/quick-task.spec.ts |
```

## Test ID Convention

Each scenario gets a unique ID for end-to-end traceability.

### ID Format

`[GROUP]-[GROUP_NUM].[SCENARIO_NUM]`

Examples:
- `QT-1.1` = Quick Task group 1, scenario 1
- `TL-3.2` = Todo List group 3, scenario 2

### Generate Test ID Table

After expanding Gherkin, create a mapping table:

```markdown
## Test IDs

| ID | Spec Group | Scenario | Level | Test File |
|----|------------|----------|-------|-----------|
| TL-1.1 | TL-1: Create | creates task and persists | Integration | TodoList.integration.test.tsx |
| TL-1.2 | TL-1: Create | clears input after create | Integration | TodoList.integration.test.tsx |
| TL-1.3 | TL-1: Create | rejects empty text | Integration | TodoList.integration.test.tsx |
| TL-3.1 | TL-3: Edit | saves on Enter and persists | Integration | TodoList.integration.test.tsx |
| TL-3.2 | TL-3: Edit | saves on blur and persists | Integration | TodoList.integration.test.tsx |
| TL-3.3 | TL-3: Edit | Escape discards without persisting | Integration | TodoList.integration.test.tsx |
```

### Why IDs Matter

Traceability chain:

```
Spec requirement  →  Gherkin scenario  →  Test name  →  Verification step  →  Feedback
      TL-3              TL-3.2           "TL-3.2: ..."    "TL-3 step 2"      "TL-3.2 failed"
```

When feather:verify reports "TL-3 step 2 failed":
1. Find test `TL-3.2` in the test file
2. Find Gherkin scenario `TL-3.2` in the derived tests
3. Find spec requirement `TL-3` in the spec
4. Understand exactly what broke and why

Without IDs, you're guessing which test corresponds to which requirement.

## Checkpoint Gate

**Do NOT proceed to implementation until user approves Gherkin scenarios.**

This checkpoint ensures:
1. All user-expressed requirements have scenarios
2. Edge cases are appropriate (not over-tested)
3. User understands what will be tested
4. Traceability from spec → scenarios → tests

## Generating Seed Data

After Gherkin is approved, aggregate all examples into seed data:

```markdown
## Seed Data (from spec examples)

```typescript
// convex/seed.ts - Generated from spec examples
export const seedData = {
  todos: [
    // From QT-1 examples
    { text: "Buy milk", completed: false },
    { text: "Call dentist", completed: false },
    // From QT-2 examples (toggle scenarios)
    { text: "Finish report", completed: true },
  ],
  users: [
    // From QT-2 examples (visibility scenarios)
    { name: "Alice", email: "alice@example.com" },
  ],
  projects: [
    // From QT-3 examples
    { name: "Website Redesign", teamId: "team-1" },
  ],
};
```

This ensures development uses the same data users mentioned in requirements.
```

## Relationship to Other Skills

```
feather-spec (light, with examples)
     ↓
review-design (Checkpoint 1: artifacts)
     ↓
derive-tests-from-spec (THIS SKILL)
  - Uses examples as Gherkin data
  - Generates seed data file
     ↓
Checkpoint: Gherkin Review ← USER APPROVES HERE
     ↓
feather:create-plan (implementation plan)
     ↓
Implementation (TDD from Gherkin, using seed data)
```

## Git Workflow

**Before deriving tests:**
```bash
git status  # Verify clean state
```

**After Gherkin is approved:**
```bash
git add docs/specs/<feature>/gherkin.md claude-progress.md
git commit -m "Add <feature> Gherkin scenarios and progress tracking"
```

Each artifact gets its own atomic commit for clean history and easy rollback.

## Progress Tracking

Create/update `claude-progress.md` in the project root to track:

- **Work completed** - What was done this session
- **User inputs collected** - Questions asked and answers given
- **Decisions made** - Edge cases resolved with sensible defaults
- **Discoveries/learnings** - What was learned that improves future work
- **Wrong paths/corrections** - Mistakes made and how they were fixed
- **Items for user review** - Anything pending user attention
- **Next steps** - What comes next

This enables async handoff - user can step away and return to understand exactly what happened, what decisions were made, and what needs attention.

Example structure:
```markdown
# Claude Progress

## Session: YYYY-MM-DD

### Work Completed
- Derived Gherkin scenarios (21 total)

### User Inputs Collected
| Question | Answer |
|----------|--------|
| Empty input behavior | Ignore silently |

### Decisions Made (by Claude)
| Edge Case | Decision | Rationale |
|-----------|----------|-----------|
| Input clears after submit | Yes | Standard form behavior |

### Discoveries
- Don't ask about standard UX patterns

### Wrong Paths
| What Happened | Correction |
|---------------|------------|
| Showed IDs to user | User asked for cleaner format |

### Next Steps
1. Create implementation plan
2. Execute with TDD
```

## Traceability

All scenarios trace back to spec groups:

```
Spec Group    →    Gherkin Scenario         →    Test File
QT-1              Create task quickly            e2e/quick-task.spec.ts
QT-1              Empty input ignored            unit/inline-task-form.test.ts
QT-2              Private by default             e2e/quick-task.spec.ts
```

Feedback can reference: "QT-2 scenario 'Private by default' failed"
