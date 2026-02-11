---
name: feather:create-plan
description: Create implementation plans from specs. Use when you have approved specs and need to plan implementation. Outputs implementation-plan.md.
attribution: Based on superpowers:writing-plans by Jesse Vincent (MIT)
---

# Create Implementation Plan

## Overview

Create implementation plans that reference approved specs. Plans describe WHAT to build in behavioral terms. The executing agent discovers HOW. **Outputs: `docs/specs/<feature>/implementation-plan.md`**

**Core principle:** Plans specify behavior and verification, not code.

**Announce at start:** "I'm using create-implementation-plan to create the implementation plan."

**Prerequisites:**
- Approved spec in `docs/specs/<feature>/spec.md`
- Design review passed (if design.md exists)

**Save plans to:** `docs/specs/<feature>/implementation-plan.md`

## What Plans Should NOT Contain

| Don't Include | Why |
|---------------|-----|
| Full code implementations | Agent should write fresh, TDD-style |
| Specific function signatures | Discovered during implementation |
| Import statements | Discovered during implementation |
| Line-by-line instructions | Over-constrains the solution |

## What Plans SHOULD Contain

| Include | Why |
|---------|-----|
| Spec reference | Single source of truth |
| Acceptance criteria (from spec) | Defines "done" |
| Task breakdown | Manageable chunks |
| Test strategy | What proves each task works |
| File locations | Where to look/create |
| Dependencies between tasks | Execution order |

## Plan Document Structure

```markdown
# [Feature Name] Implementation Plan

> **For Claude:** Use superpowers:executing-plans to implement this plan.

**Spec:** `docs/specs/<feature>/spec.md`

**Goal:** [One sentence from spec]

**Approach:** [2-3 sentences on implementation strategy]

---

## Tasks

### Task 1: [Behavioral Description]

**Implements:** [Which acceptance criteria from spec]

**Files:**
- Test: `tests/path/to/test.ts`
- Implementation: `src/path/to/file.ts`

**Acceptance:**
- [ ] Test exists that verifies [WHEN X THE SYSTEM SHALL Y]
- [ ] Test fails before implementation (TDD red)
- [ ] Implementation makes test pass (TDD green)
- [ ] Manual verification: [specific check if applicable]

**Depends on:** None | Task N

---

### Task 2: [Behavioral Description]

...
```

## Task Granularity

Each task should:
- Implement ONE acceptance criterion (or closely related set)
- Be completable in one session
- Have clear pass/fail verification
- Be independently testable

**Too big:** "Implement task CRUD"
**Right size:** "Implement create task with required fields"

## Deriving Tasks from Specs

Map spec sections to tasks:

| Spec Section | Becomes |
|--------------|---------|
| Each WHEN clause | One task (or grouped if trivial) |
| Each IF/THEN clause | Error handling task |
| Business Validation rules | Validation task |
| Permissions section | Auth task |
| Data Model | Schema/migration task (first) |

**Example mapping:**

```
Spec says:
  WHEN user creates task THE SYSTEM SHALL save task with status "todo"
  WHEN user creates task without title THE SYSTEM SHALL reject with error

Tasks:
  Task 1: Create task with valid data → saves with status "todo"
  Task 2: Create task validation → rejects missing title
```

## Test Strategy Section

Every plan must include:

```markdown
## Test Strategy

**Unit tests:** [What isolated logic to test]

**Integration tests:** [What cross-component flows to verify]
- [ ] [Specific flow from spec, e.g., "Create task appears in task list"]

**Manual verification:** [What to check visually/interactively]
- [ ] [Specific check, e.g., "New task shows in UI after creation"]
```

This prevents "tests pass but feature broken" by requiring multiple verification layers.

## Test File Structure

Test files MUST mirror spec groups for traceability. This enables:
- Feedback tracking: "TL-3 step 2 failed" → maps to test TL-3.2
- Coverage analysis: Which spec requirements are tested?
- Debugging: Failed test → spec requirement → implementation

### Template

```typescript
/**
 * Integration tests for [Feature]
 * Spec: docs/specs/[feature]/spec.md
 *
 * Test IDs match spec groups:
 * - TL-1.x = Create Task tests
 * - TL-2.x = Toggle tests
 * - TL-3.x = Edit tests
 * - TL-4.x = Delete tests
 */

describe("TL-1: Create Task", () => {
  describe("Core", () => {
    it("TL-1.1: creates task and persists to database", async () => {
      // Action + UI assertion + Database assertion (required)
    });
  });

  describe("Edge", () => {
    it("TL-1.2: clears input after create", async () => { /* ... */ });
    it("TL-1.3: rejects empty text", async () => { /* ... */ });
  });
});

describe("TL-3: Edit Task", () => {
  describe("Core", () => {
    it("TL-3.1: saves on Enter and persists to database", async () => { /* ... */ });
    it("TL-3.2: saves on blur and persists to database", async () => { /* ... */ });
  });

  describe("Edge", () => {
    it("TL-3.3: Escape discards without persisting", async () => { /* ... */ });
  });
});
```

### Why This Matters

Without structure, tests become disconnected from requirements:

```typescript
// BAD: No traceability
describe("Edge: Edit", () => {
  it("saves on blur", ...);  // Which spec requirement? Unknown.
});

// GOOD: Clear traceability
describe("TL-3: Edit Task", () => {
  it("TL-3.2: saves on blur and persists to database", ...);
  // TL-3 = spec group, .2 = second scenario in that group
});
```

When feather:verify reports "TL-3 step 2 failed", you know exactly which test to check.

## Verification Checklist (for the plan itself)

Before marking plan complete:

- [ ] Every acceptance criterion in spec has a task
- [ ] Every task references which spec criteria it implements
- [ ] Every task has testable acceptance conditions
- [ ] Integration tests are specified (not just unit tests)
- [ ] Manual verification steps included where applicable
- [ ] No code is embedded in the plan
- [ ] Dependencies between tasks are explicit

## Anti-Patterns

| Don't | Do |
|-------|-----|
| Embed 2600 lines of code | Reference spec, describe behavior |
| "Implement the backend" | "Create task saves with status 'todo'" |
| Assume tests = verification | Require integration + manual checks |
| Skip spec reference | Always link to source of truth |
| Copy spec into plan | Reference spec, don't duplicate |

## Execution Handoff

After saving the plan:

**"Plan complete and saved to `docs/plans/<filename>.md`.**

**Before execution:**
1. Run `derive-tests-from-spec` to generate test cases from acceptance criteria
2. Choose execution approach:
   - **Subagent-Driven (this session)** - Fresh agent per task, review between
   - **Parallel Session (separate)** - Batch execution with checkpoints

**Which approach?"**

## Git Workflow

**Before writing plan:**
```bash
git status  # Verify clean state
```

**After plan is complete:**
```bash
git add docs/specs/<feature>/implementation-plan.md
git commit -m "Add <feature> implementation plan"
```

Each artifact gets its own atomic commit for clean history and easy rollback.

## Relationship to Other Skills

```
feather:create-spec  →  feather:create-plan  →  feather:derive-tests  →  feather:execute
(spec.md)               (implementation-plan.md)  (how to verify)         (build it)
```

The plan is a bridge between spec and execution. It organizes work without dictating implementation.
