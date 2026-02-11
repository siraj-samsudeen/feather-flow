---
name: feather:polish
description: Work through the polish queue (bugs/UX tweaks) with TDD but skip spec/gherkin ceremony. Use when you have polish items to address from /feather:create-issue.
---

# Polish

## Overview

Work through bugs and UX tweaks captured in the polish queue. Uses TDD (test first) but skips the full spec/gherkin ceremony since these are small, well-defined fixes.

**Core principle:** Lighter than full slice, but TDD is still mandatory.

**Announce at start:** "I'm using polish to work through the queue."

## Prerequisites

- `.slices/feather:polish.md` exists with items
- TDD Guard hook active

## Process

### Step 1: Show Queue

Read `.slices/feather:polish.md` and display:

```markdown
## Polish Queue (5 items)

1. **BUG** Login fails with special chars in password
2. **UX** Save button contrast too low
3. **UX** Add loading spinner on form submit
4. **PERF** Todo list slow with 100+ items
5. **A11Y** Missing aria-label on delete button

Pick one to fix, or "all" to work through sequentially.
```

### Step 2: TDD Loop (Simplified)

For each polish item:

#### Write Test (RED)
```typescript
it("accepts special characters in password", async () => {
  await user.type(passwordInput, "p@ssw0rd!");
  await user.click(submitButton);

  expect(screen.queryByText("invalid password")).not.toBeInTheDocument();
  expect(await t.run(ctx => ctx.db.query("sessions").first())).toBeTruthy();
});
```

Run test, verify it fails, commit:
```bash
git commit -m "RED: login should accept special chars"
```

#### Fix (GREEN)
Implement the fix, run test, verify it passes, commit:
```bash
git commit -m "GREEN: handle special chars in password validation"
```

### Step 3: Mark Complete

After each fix:

1. Update `.slices/feather:polish.md`:
   ```markdown
   - [x] **BUG** Login fails with special chars - fixed 2025-02-04
   ```

2. Commit with descriptive message:
   ```bash
   git commit -m "fix: login accepts special characters in password"
   ```

### Step 4: Continue or Stop

After each fix, ask:
```markdown
✓ Fixed: Login accepts special chars

Polish queue: 4 items remaining

Continue to next item? (or "stop" to pause)
```

## Commit Message Convention

| Type | Prefix | Example |
|------|--------|---------|
| BUG | `fix:` | `fix: login special chars` |
| UX | `improve:` | `improve: save button contrast` |
| PERF | `perf:` | `perf: optimize todo list rendering` |
| A11Y | `a11y:` | `a11y: add aria-labels to buttons` |

## Polish vs Full Slice

| Aspect | /feather:polish | /feather:work-slice |
|--------|---------|-------------|
| Spec | Skip | Required |
| Gherkin | Skip | Required |
| TDD | Required | Required |
| Coverage | 100% | 100% |
| Commits | Per fix | RED/GREEN cycles |
| Verification | Quick check | Full /feather:verify |

## Key Constraints

- **TDD is mandatory** - No code without test, hook enforces this
- **Keep fixes small** - If it grows, make it a slice
- **One at a time** - Complete one fix before starting next
- **Still needs 100% coverage** - Same rules apply

## Example Session

```
Claude: "I'm using polish to work through the queue."

## Polish Queue (3 items)

1. **BUG** Login fails with special chars
2. **UX** Save button contrast too low
3. **UX** Add loading spinner

Pick one to fix, or "all" for sequential.

User: "1"

Fixing: Login fails with special chars

RED: Writing test...
→ Test fails (expected)
→ Committed: "RED: login should accept special chars"

GREEN: Implementing fix...
→ Test passes
→ Committed: "GREEN: handle special chars in validation"

Final commit: "fix: login accepts special characters in password"

✓ Fixed: Login accepts special chars

Marked complete in polish.md.

Polish queue: 2 items remaining. Continue?

User: "stop"

Paused. Run /feather:polish to continue later.
```

## Prioritization

When working through "all", suggested order:

1. **BUG** - Fix broken things first
2. **A11Y** - Accessibility is critical
3. **PERF** - Performance affects UX
4. **UX** - Polish last

User can override with specific selection.

## When to Escalate to Slice

If during a polish fix you discover:
- It affects multiple unrelated files
- It needs design discussion
- It's actually a new feature
- It will take more than an hour

Stop and suggest: "This should be a slice. Want me to run `/feather:add-slice`?"

## Relationship to Other Skills

```
/feather:create-issue (captures during development)
     │
     ├── Writes to: .slices/feather:polish.md
     │
     ↓
/feather:polish (THIS SKILL)
     │
     ├── TDD for each fix (tdd-guard enforced)
     │
     ↓
[All fixed]
     │
     ↓
/feather:finish (when ready to ship)
```
