---
name: feather:review-design
description: Review design documents for architectural soundness and verify Checkpoint 1 artifacts. Use when user says "review the design", "check the design", or before creating an implementation plan.
author: Claude
version: 2.0.0
---

# Review Design

Reviews design documents to catch issues that are **expensive to fix later** and verifies all Checkpoint 1 artifacts exist before implementation.

## Core Principle

> "Software architecture is those decisions which are both important and hard to change." — Martin Fowler

Design review catches what compilers, linters, and tests cannot: wrong abstractions, missing requirements, security model flaws, and unnecessary complexity.

## Checkpoint 1: Before Implementation

**All 5 artifacts must exist before proceeding to implementation.**

```markdown
## Checkpoint 1 Verification

| # | Artifact | Status | Notes |
|---|----------|--------|-------|
| 1 | UI Mockup | ✓ / ✗ / N/A | Visual representation or "N/A - no UI changes" |
| 2 | User Journey | ✓ / ✗ | Numbered steps of the user flow |
| 3 | UX Touches | ✓ / ✗ | Loading states, transitions, feedback, empty states |
| 4 | Acceptance Criteria | ✓ / ✗ | EARS format in feather-spec, grouped with IDs |
| 5 | Data Model | ✓ / ✗ / N/A | New/changed entities or "N/A - no data changes" |

**Result:** Ready to implement / Missing: [#, #]
```

### Artifact Details

**1. UI Mockup**
- Visual representation (HTML mockup, wireframe, or sketch)
- Shows key states (empty, loading, populated, error)
- Mark "N/A" only if feature has no UI component

**2. User Journey**
- Numbered steps from user perspective
- Example:
  ```
  1. User views "My Tasks"
  2. User sees input field at top
  3. User types task title
  4. User presses Enter
  5. Task appears in list
  6. Input clears
  ```

**3. UX Touches**
- Micro-interactions (hover, focus, active states)
- Loading indicators
- Transitions/animations
- Empty states
- Error feedback
- Success feedback

**4. Acceptance Criteria**
- EARS format in feather-spec
- Grouped by capability (QT-1, QT-2, etc.)
- User-perspective only (no edge cases)

**5. Data Model**
- New entities or fields
- Relationships
- Mark "N/A" if no schema changes

## Architecture Review

After verifying Checkpoint 1 artifacts, review architecture:

### Tier 1: Data & Consistency (will this system work?)

| Check | Question |
|-------|----------|
| **Data model correctness** | Can this model actually represent the domain? |
| **Reference integrity** | What happens when referenced records are deleted? |
| **Consistency boundaries** | What fails when operations fail mid-way? |
| **Security boundaries** | Can data leak across users/orgs/tenants? |

### Tier 2: Simplicity & Evolution (will this system evolve?)

| Check | Question |
|-------|----------|
| **Complection** | Are independent concerns braided together? (Hickey) |
| **Hard-to-change decisions** | What's being locked in that shouldn't be? |
| **Missing requirements** | What user journeys aren't covered? |
| **Over-engineering** | Is this "enterprise Java tutorial" design? (DHH) |

### Tier 3: Scale & Performance (will this system scale?)

| Check | Question |
|-------|----------|
| **Query patterns** | Will lookups become table scans at scale? |
| **N+1 patterns** | Are there loops that make queries per item? |
| **Bottlenecks** | What serializes everything through one point? |

## What to Skip

These are cheap to fix anytime — don't waste review cycles:

- Naming conventions, code style → linters catch
- Syntax, typos → compilers catch
- Logic bugs in specific functions → tests catch
- "Could be slightly more efficient" → profilers catch

**Rule of thumb**: If it's a one-line fix, mention it in one line or skip it.

## Review Process

### Quick Review (default)

1. Verify Checkpoint 1 artifacts (all 5)
2. Single pass on Tier 1 issues
3. Output verdict

### Deep Review

User says: "deep review", "thorough review", "full review"

Launch **two agents in parallel**:

1. **Agent A (Data/Security focus)**: Tier 1 checks
2. **Agent B (Simplicity/Scale focus)**: Tier 2-3 checks

Synthesize findings, deduplicate, sort by cost-to-fix-later.

## Output Format

```markdown
## Design Review

### Checkpoint 1: Artifacts

| # | Artifact | Status | Notes |
|---|----------|--------|-------|
| 1 | UI Mockup | ✓ | HTML mockup in /docs/mockups/ |
| 2 | User Journey | ✓ | 6 steps documented |
| 3 | UX Touches | ✗ | Missing empty state |
| 4 | Acceptance Criteria | ✓ | 3 groups (QT-1, QT-2, QT-3) |
| 5 | Data Model | N/A | No schema changes |

**Checkpoint 1 Result:** Missing artifact #3

---

### Architecture Review

#### Must Fix (expensive to fix later)

| Issue | Impact | Recommendation |
|-------|--------|----------------|
| [Issue] | [What breaks if ignored] | [What to change] |

#### Consider (would improve design)

| Issue | Benefit | Priority |
|-------|---------|----------|
| [Issue] | [Why it helps] | High/Med/Low |

#### Solid

- [Brief bullets of what's well-designed]

---

**Verdict**: Ready for implementation / Needs work

**Next step**: [If issues found] Address missing artifacts and issues, then request another review.
```

## Feedback Collection at Checkpoint 1

When user reviews and provides feedback, capture it to `.feedback/`:

### Feedback Types at CP1

| Type | Example | Action |
|------|---------|--------|
| **Missing artifact** | "Need empty state in mockup" | Update design, re-review |
| **Design change** | "Add keyboard shortcut" | Save to .feedback/, decide scope |
| **New requirement** | "Also support due dates" | Save to .feedback/ as feature request |
| **Architecture concern** | "Won't scale" | Address before proceeding |

### Saving CP1 Feedback

For items that won't be addressed immediately (deferred to later):

```markdown
---
id: DESIGN-001
type: design-feedback | feature-request | scope-change
source: checkpoint-1
priority: P2 | P3
feature: [feature-name]
created: [date]
status: open
---

# [Short Title]

## Description
[What was requested]

## Source
- **Found during:** Checkpoint 1 design review
- **Feature:** [Feature Name]

## Decision
- [ ] Add to current scope
- [ ] Defer to Phase X
- [ ] Out of scope

## Notes
[Context for decision]
```

### CP1 Feedback Workflow

1. User reviews artifacts
2. User says: "Approved" / "Add X" / "Change Y"
3. If immediate fix → update design, re-review
4. If deferred → save to `.feedback/open/DESIGN-xxx.md`
5. Proceed only when "Approved"

## Critical: Review Output is for Design Updates Only

**When you produce review feedback:**
- The feedback identifies issues in the DESIGN DOCUMENT
- The author should UPDATE THE DESIGN DOCUMENT to address issues
- Then request ANOTHER REVIEW round
- Iterate until verdict is "ready for implementation"

**Do NOT:**
- Modify code based on design review feedback
- Create implementation artifacts before design is approved
- Skip the design update → re-review cycle
- Proceed with missing Checkpoint 1 artifacts

Design review is a gate. All artifacts must exist and design must pass before implementation begins.

## Key Questions to Ask

From the pioneers:

1. **Fowler**: "What decisions here are hard to change later?"
2. **Hickey**: "Is this simple (one thing) or complected (braided together)?"
3. **DHH**: "Is this over-engineered for the actual problem?"
4. **Beck**: "What's the simplest thing that could work?"

## Relationship to Other Skills

```
feather-spec  →  review-design (THIS)  →  derive-tests-from-spec  →  implementation
                       ↓
              Checkpoint 1 gate:
              All 5 artifacts present?
              Architecture sound?
```

**After this review passes**, proceed to `derive-tests-from-spec` to expand requirements into Gherkin scenarios.
