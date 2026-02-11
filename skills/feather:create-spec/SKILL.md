---
name: feather:create-spec
description: Lightweight, AI-native requirements specification framework for defining features with minimal redundancy and maximum traceability. Use when writing feature specifications, requirements documents, or any documentation intended to drive implementation. Outputs docs/specs/<feature>/spec.md.
---

# Create Spec (Feather-Spec)

A complete one-pager: minimal yet comprehensive. Anyone - client, developer, AI - reads one document and gets the full picture.

**The 30-second rule:** If a client can't skim the spec and sign off in 30 seconds, it's too heavy.

## Process Overview

```
1. Discover    →  Trace workflow, identify pain points
2. Decompose   →  Extract features from workflow
3. Specify     →  Write feather-spec per feature
4. Validate    →  EARS completeness check
5. Build       →  Implement from approved spec
```

Skip steps as needed. Minimum path: Specify → Build.

## Step 1: Discover (Optional)

When requirements are vague, trace the workflow:

**Ask:** "Walk me through how you do this today, step by step."

A workflow reveals:
- **Steps** → Features needed
- **Handoffs** → Integration points
- **Delays** → Automation opportunities
- **Workarounds** → Pain points to solve
- **Repetition** → Templates/defaults candidates

## Step 2: Decompose

Extract features from workflow:

```
Workflow: "I get tasks from emails, track on sticky notes,
          log hours at end of day, invoice monthly"

Features:
├── Task management (create, edit, delete, complete, list)
├── Time tracking (start, stop, edit hours)
└── Reporting (hours by client, export)
```

Each feature = one feather-spec. A feature contains multiple related capabilities.

## Step 3: Specify

### Document Structure

```markdown
# Feature: [Name]

> Context: (optional - from discovery)
> [relevant workflow excerpt]
> Pain points: [what's broken]
> Opportunity: [what we're solving]

**Goal:** [single sentence - why this exists]

**Scope**
- In: [included capabilities]
- Out: [explicitly excluded]

**Dependencies** (if applicable)
- Requires: [must exist first]
- Triggers: [side effects]
- Blocked by: [build order]

---

## XX-1: [Capability Name]

- WHEN [trigger] THE SYSTEM SHALL [response]
- IF [condition] THEN THE SYSTEM SHALL [response]

**Examples:**
| Input | Result |
|-------|--------|
| [concrete data] | [concrete outcome] |

---

## XX-2: [Capability Name]

- WHEN [trigger] THE SYSTEM SHALL [response]
- WHILE [state] THE SYSTEM SHALL [behavior]

**Examples:**
| Before | Action | After |
|--------|--------|-------|
| [state] | [action] | [new state] |

---

## Rules (include only sections that apply)

**Business Validation**
- BV1: [rule]
- BV2 (critical): [critical rule]

**Calculations**
- C1 (critical): [formula]

**Permissions**
- Standard ownership (user can only access own [resources])

**Input Validation**
- IV1: [constraint]

**State Rules**
- S1: [reactive behavior]

---

## Data Model

**T1: [table]**
- [field] ([client-friendly type])

---

## Out of Scope

- [explicitly excluded]
```

### Grouped Capabilities

Group acceptance criteria by **user capability**, not technical component. Each group gets a short ID that traces through to tests, bugs, and sign-off.

```markdown
GOOD (user perspective):
  TM-1: Task Creation
  TM-2: Task Completion
  TM-3: Task List

BAD (technical perspective):
  TM-1: TaskForm Component
  TM-2: Status Toggle API
  TM-3: ListView Query
```

**ID format:** Short feature prefix + number (QT-1, TM-2, CP-3).
Use across bugs ("CP-2 failed"), reviews ("approve TM-1 through TM-3"), and tests.

### Capturing Examples

When users describe what they want, they use concrete data:

> "I want to add a task like 'Buy milk' and then mark it done"

Capture these as example tables. They serve triple duty:
1. **Client sign-off** - "Yes, that's exactly what I mean"
2. **Test data** - becomes test fixtures and seed data
3. **Disambiguation** - resolves ambiguity better than prose

**Example table formats:**

For creation (input → result):
```markdown
| Input | Result |
|-------|--------|
| "Buy milk" | Task created, incomplete |
```

For state changes (before → action → after):
```markdown
| Before | Action | After |
|--------|--------|-------|
| "Buy milk" (incomplete) | toggle | "Buy milk" (complete) |
```

For calculations (inputs → output):
```markdown
| Subtotal | Coupon | Discount | Total |
|----------|--------|----------|-------|
| $100 | 20% off | $20 | $80 |
| $100 | 20% off, max $15 | $15 | $85 |
```

### EARS Syntax Reference

| Pattern | Keyword | Template | Use For |
|---------|---------|----------|---------|
| Event-driven | WHEN | WHEN [trigger] THE SYSTEM SHALL [response] | User actions, events |
| State-driven | WHILE | WHILE [state] THE SYSTEM SHALL [behavior] | Behavior during states |
| Unwanted | IF/THEN | IF [condition] THEN THE SYSTEM SHALL [response] | Errors, failures |
| Ubiquitous | (none) | THE SYSTEM SHALL [constraint] | Always-true rules |
| Optional | WHERE | WHERE [feature] THE SYSTEM SHALL [behavior] | Feature flags |

### Key Principles

1. **Complete One-Pager** - Everything needed to build, in one document
2. **30-Second Skim** - Client reads grouped headings + examples, signs off
3. **User Perspective** - Business language, not technical jargon
4. **Grouped + Traceable** - Capability IDs trace to tests, bugs, sign-off
5. **Examples are Data** - Concrete examples from user's own words
6. **Omit Empty Sections** - No placeholders for sections that don't apply
7. **Omit Obvious** - Don't state auth required, record must exist

### ID Reference

| Prefix | Meaning |
|--------|---------|
| XX-N | Capability group (EARS criteria + examples) |
| BV | Business Validation |
| C | Calculation |
| P | Permission |
| IV | Input Validation |
| S | State Rule |
| T | Table |

Mark critical rules with `(critical)` suffix (e.g., `BV2 (critical)`, `C1 (critical)`) for extra test coverage.

## Step 4: Validate (EARS Completeness)

Review each capability group against EARS patterns:

| Pattern | Question | Check |
|---------|----------|-------|
| **WHEN** | All user actions covered? | Each action has a WHEN |
| **WHILE** | State-dependent behavior? | Add WHILE if needed |
| **IF/THEN** | All error cases user cares about? | Key failures have IF/THEN |
| **Ubiquitous** | Always-true constraints? | Security, validation rules |
| **WHERE** | Feature variations? | Optional features |

Also check: Does every capability group have at least one example table?

If a pattern is missing, either add criteria or confirm not applicable.

## Step 5: Build

The approved spec is the contract. AI uses it to:
- Implement all capabilities (XX-1, XX-2, etc.)
- Generate tests from example tables
- Apply validation rules (IV, BV)
- Implement calculations (C)
- Enforce permissions
- Create data model (T)

## Example: TodoList Feature

```markdown
# Feature: TodoList

**Goal:** Let users track tasks with create, view, toggle, and delete.

**Scope**
- In: Create tasks, view list, toggle completion, delete tasks
- Out: Due dates, priorities, multiple lists, authentication

---

## TL-1: Create Task

- WHEN I type a task and press Enter, THE SYSTEM SHALL add it to my list

**Examples:**
| Input | Result |
|-------|--------|
| "Buy milk" | Task appears, unchecked |
| "Call dentist" | Task appears, unchecked |

---

## TL-2: View Tasks

- WHEN I open the app, THE SYSTEM SHALL show all my tasks
- WHILE no tasks exist, THE SYSTEM SHALL show "No todos yet"

**Examples:**
| State | Display |
|-------|---------|
| No tasks | "No todos yet" message |
| Has tasks | List with checkboxes |

---

## TL-3: Toggle Complete

- WHEN I click a task's checkbox, THE SYSTEM SHALL toggle its completion

**Examples:**
| Before | Action | After |
|--------|--------|-------|
| "Buy milk" unchecked | click | "Buy milk" checked |
| "Buy milk" checked | click | "Buy milk" unchecked |

---

## TL-4: Delete Task

- WHEN I click a task's delete button, THE SYSTEM SHALL remove it

**Examples:**
| Before | Action | After |
|--------|--------|-------|
| List has "Buy milk" | click delete | "Buy milk" gone |

---

## Data Model

**T1: todos**
- text (string)
- completed (yes/no)
```

## When to Split Features

- **One spec:** Related capabilities sharing data model (CRUD on same entity)
- **Split:** Different entities, different permissions, or complex enough to warrant separation

## Anti-Patterns

| Dont | Do |
|------|-----|
| Flat list of 15 EARS criteria | Group by capability with IDs |
| EARS criteria without examples | Every group has example table |
| Vague criteria ("system handles errors") | Specific EARS + concrete data |
| Technical group names ("API Integration") | User capability names ("Task Creation") |
| API endpoints in spec | Action names, map later |
| Technical types (uuid, jsonb) | Business terms (globally unique ID, list) |
| "Record must exist for delete" | Omit obvious behavior |
| "Must be authenticated" | Assume auth, state exceptions |

## Git Workflow

**Before writing spec:**
```bash
git status  # Verify clean state
```

**After spec is complete:**
```bash
git add docs/specs/<feature>/spec.md
git commit -m "Add <feature> spec"
```

Each artifact gets its own atomic commit for clean history and easy rollback.

## What Happens Next

```
create-spec (you are here)
     ↓
review-design (Checkpoint 1: verify all artifacts)
     ↓
derive-tests-from-spec (expands to Gherkin + edge cases)
     ↓
Review Gherkin (checkpoint before implementation)
     ↓
Implementation
```
