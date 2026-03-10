---
name: feather:work-slice
description: Execute ONE slice with strict TDD enforcement, then STOP. Creates spec, derives tests, runs TDD loop (RED→GREEN), verifies coverage. Use after /feather:slice-project to implement slices one at a time.
attribution: Inspired by superpowers:subagent-driven-development by Jesse Vincent (MIT)
---

# Work Slice

## Overview

Execute exactly ONE slice with strict TDD, update status, then STOP. This enforces the "one task per loop" pattern that prevents context rot.

**Core principle:** One slice per invocation. Complete stop after. Human-in-the-loop between slices.

**Announce at start:** "I'm using work-slice to implement [SLICE-ID] with TDD."

## Prerequisites

- `.slices/slices.json` exists (from `/feather:slice-project`)
- TDD Guard hook active (from `/feather:setup-tdd-guard`)
- Pre-commit hook for 100% coverage configured

Verify:
```bash
# TDD Guard active?
grep -q "tdd-guard" .claude/settings.json && echo "OK" || echo "MISSING"

# Coverage thresholds?
grep -q "thresholds" vitest.config.* && echo "OK" || echo "MISSING"
```

## Process

### Step 1: Pick Next Slice

Read `.slices/slices.json` and pick first slice where:
- `passes: false`
- All `depends_on` slices have `passes: true`

```bash
cat .slices/slices.json
```

If slice has unmet dependencies, report and stop.

### Step 2: Create Feather-Spec

Use `/feather:create-spec` for this slice only:

```
→ Output: docs/specs/<slice-id>/spec.md
```

The spec should be lightweight - just enough for implementation.

### Step 3: Derive Tests (Gherkin)

Use `/feather:derive-tests`:

```
→ Output: docs/specs/<slice-id>/gherkin.md
→ User reviews scenarios before proceeding
```

### Step 4: TDD Loop

With tdd-guard hook enforced:

For each scenario:

#### RED
1. Write test for the scenario
2. **VERIFY IT FAILS** — run tests, show output

#### GREEN
1. Write minimal code to pass
2. **VERIFY IT PASSES** — run tests, show output
3. **Commit both test + implementation together:**
   `git commit -m "[SLICE-ID]: [scenario description]"`

The pre-commit hook runs tests, so commits are only allowed when the tree is green. RED and GREEN always land in the same commit.

#### Repeat
Continue RED→GREEN→commit for each scenario until all pass.

### Step 5: Verify Coverage

Use `/feather:verify` with 100% coverage gate:

```bash
npm test -- --coverage
```

**Coverage must be 100%.** If not:
1. Identify uncovered lines
2. Add tests or remove untested code
3. Re-run until 100%

### Step 6: Present Test Checklist

Before marking complete, present checklist for human verification:

```markdown
## Test Checklist for [SLICE-ID]

- [ ] Tests are full-stack (UI action → database verification)
- [ ] Database state verified in assertions (not just UI)
- [ ] No `vi.mock("convex/react")` except for loading/error states
- [ ] No separate backend test files (convex/*.test.ts)
- [ ] No separate frontend-only tests (mocked hooks)
- [ ] Unit tests only for edge cases hard to trigger through UI

**Test files created:**
- `src/components/TodoList.integration.test.tsx` (12 tests)

**Patterns used:**
- ConvexTestProvider for real backend execution
- t.run() for database verification
```

Human reviews checklist before approving.

### Step 7: Update Status

After verification passes and checklist approved:

1. Update `slices.json`:
   ```json
   {
     "id": "AUTH-1",
     "passes": true,
     "spec_path": "docs/specs/auth-1/spec.md"
   }
   ```

2. Update `SLICES.md` status column

3. Update `STATE.md`:
   ```markdown
   ### [date]
   - Completed AUTH-1
   - Decisions: [summary]
   - Next: TODO-1
   ```

4. **Delete CONTINUE.md** if exists (cleanup)

### Step 8: STOP

```markdown
✓ AUTH-1 complete. Next: TODO-1. Run /feather:work-slice to continue.
```

**DO NOT** proceed to next slice. Human must invoke next `/feather:work-slice`.

## Test Philosophy (DHH Style)

### Default: One Test, Full Stack (UI → Database)

```typescript
// GOOD: CRUD test covers everything
it("creates todo and persists to database", async () => {
  await user.type(input, "Buy milk");
  await user.click(submitButton);

  // UI verification
  expect(screen.getByText("Buy milk")).toBeInTheDocument();

  // Database verification (REQUIRED)
  const todos = await t.run(async (ctx) => ctx.db.query("todos").collect());
  expect(todos[0].text).toBe("Buy milk");
});
```

### Exception: Unit Tests for Hard-to-Trigger Edge Cases

```typescript
// GOOD: Edge case hard to set up in e2e
it("handles 500 character limit", () => {
  expect(validateTitle("x".repeat(501))).toBe(false);
});
```

### FORBIDDEN Patterns

```typescript
// BAD: Separate backend test (redundant)
describe("todos.create", () => { ... });

// BAD: Separate frontend test (tests mock, not real behavior)
describe("TodoForm", () => { vi.mock("convex/react"); ... });

// BAD: Separate integration test (this IS the integration test)
describe("TodoList integration", () => { ... });
```

**Rule:** If a slice is CRUD, write CRUD tests (UI → DB). Don't split into layers.

## Executable Guardrails

| Guardrail | Enforcement | What It Blocks |
|-----------|-------------|----------------|
| `tdd-guard` hook | PreToolUse hook | Edit/Write on non-test files when tests failing |
| Pre-commit hook | Git pre-commit | `git commit` if any check fails |
| Vitest threshold | Coverage config | Tests fail if coverage drops below 100% |

**No philosophy. No red flags. Just hooks that block tools.**

## Pre-commit Hook Setup

If not already configured:

```bash
# .git/hooks/pre-commit (or use husky/lint-staged)
#!/bin/sh
set -e

npm run test:coverage          # Tests + 100% coverage
npx tsc --noEmit               # TypeScript diagnostics
npm run lint                   # Lint errors
```

## Key Constraints

1. **ONE slice per invocation** - Prevents context rot
2. **TDD hook must be active** - Can't circumvent with words
3. **100% coverage required** - No exceptions
4. **Must STOP after completion** - Human-in-the-loop
5. **RED before GREEN, commit at GREEN** - Hook enforces test-first; pre-commit hook ensures commits only when tree is green

## Git Workflow

Each TDD cycle (RED→GREEN) is committed together once tests pass:

```bash
# After GREEN — commit test + implementation together
git add src/components/TodoList.test.tsx src/components/TodoList.tsx
git commit -m "TODO-1: todo creation persists to database"
```

The pre-commit hook runs `npm run test:coverage`, so the commit only succeeds when the tree is green. No separate RED commits.

After slice complete:
```bash
git add .slices/ docs/specs/
git commit -m "Complete AUTH-1 slice"
```

## Relationship to Other Skills

```
/feather:work-slice (THIS SKILL)
     │
     ├── Invokes: /feather:create-spec
     ├── Invokes: /feather:derive-tests
     ├── Invokes: /feather:verify
     │
     ├── Requires: /feather:setup-tdd-guard active
     │
     ↓
STOP → User decides → /feather:work-slice (next slice)
                   → /feather:pause-slice (if stopping mid-way)
```

## Example Session

```
Claude: "I'm using work-slice to implement AUTH-1 with TDD."

Reading slices.json...
Next slice: AUTH-1 (Username/password authentication)
Dependencies: None (can start)

Creating spec...
→ docs/specs/auth-1/spec.md

Deriving tests...
→ 8 scenarios for AUTH-1

[User approves scenarios]

TDD Loop:

Scenario 1: Login form renders
  RED:   Test written, running... FAIL (expected)
  GREEN: Implementation written, running... PASS
  → Committed: "AUTH-1: login form renders"

Scenario 2: Login submits credentials
  RED:   Test written, running... FAIL (expected)
  GREEN: Implementation written, running... PASS
  → Committed: "AUTH-1: login submits credentials"

... [continues for all scenarios]

Verification:
  → Tests: 8 passed
  → Coverage: 100%
  → Types: OK
  → Lint: OK

## Test Checklist for AUTH-1

- [ ] Tests are full-stack (UI action → database verification)
- [ ] Database state verified in assertions
- [ ] No vi.mock("convex/react")
...

[User approves checklist]

✓ AUTH-1 complete. Next: PROJ-1. Run /feather:work-slice to continue.
```
