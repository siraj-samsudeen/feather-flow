---
name: feather-spec
description: >
  The Feather Spec framework reference. Use this skill when authoring
  capability.md, design.md, screen specs, component specs, or tasks.md
  for any capability. Also the canonical reference for vocabulary,
  stable IDs, EARS patterns, checklists, and change protocol.
  Invoked directly by feather-brainstorm (to produce spec artifacts
  from a brainstorm.md) and referenced by feather-execute-task (for
  task structure and test layering). Part of the feather-flow pipeline.
---

# Feather Spec

The requirements and delivery framework for Feather Flow.
Covers everything from file structure to task ordering to test layering.

---

## 1. Core Vocabulary

| Term | Definition |
|---|---|
| **Capability** | A coherent feature area — one domain concept with full lifecycle. One PR. |
| **Screen** | A routable page the user lands on. The primary spec unit. |
| **Component** | An embeddable UI fragment owned by exactly one capability. |
| **Action** | What a user can do on a screen. Carries a stable ID (A1, A2…). |
| **Task** | One developer work unit inside a capability's tasks.md. |
| **ADR** | System-wide architecture decision that cuts across capabilities. |

**Dropped terms:** Feature, Sub-feature, Epic, Story, Slice.
Cross-capability work gets its own capability folder — not a slice manifest.

---

## 2. File Structure

```
decisions/                         ← system-wide ADRs
  ├── 0001-convex-over-supabase.md
  └── 0002-tanstack-router.md

capabilities/
  └── <capability-name>/
        ├── brainstorm.md          ← optional — written by feather-brainstorm
        ├── capability.md          ← operating rules, current state
        ├── design.md              ← rationale, decisions, risks (always present)
        ├── tasks.md               ← developer task list
        ├── plans/                 ← per-task plan files (written by feather-execute-task)
        │     └── T3.2-build-upload-screen.md
        ├── screens/
        │     └── <screen>.md      ← one spec per routable screen
        └── components/
              └── <component>.md   ← one spec per embeddable fragment
```

**One PR per capability.**
**Specs evolve in place. Git is the audit trail.**
Removed actions are deleted, not deprecated. Renamed actions keep their stable ID.

---

## 3. ADRs vs design.md

| Scope | Lives in | Examples |
|---|---|---|
| Affects the whole system | decisions/ ADR | Chose Convex, defer Projects layer |
| Affects one capability only | capability/design.md | Chose SheetJS, store rows as records |
| Capability decision promoted to system-wide | ADR + pointer in design.md | Row format → ADR-0007 |

When a capability decision graduates to system-wide:
create the ADR, replace the design.md decision with a one-line pointer:

```markdown
### D3 — Row storage format
See ADR-0007. Applied here as: one document per row, cellData keyed by column name.
```

---

## 4. Stable IDs

Every named element carries a stable ID. IDs survive renames.

| Prefix | Scope | Example | Full reference |
|---|---|---|---|
| A | Action (screen) | A1, A2 | task-list#A1 |
| RQ | EARS requirement (action) | A1.RQ1 | task-list#A1.RQ1 |
| E | Error scenario (action) | A1.E1 | task-list#A1.E1 |
| S | State (screen) | S1, S2 | task-list#S1 |
| C | Calculation (capability) | C1, C2 | task-management#C1 |
| F | Cross-screen flow (capability) | F1, F2 | task-management#F1 |
| I | Integration (capability) | I1, I2 | task-management#I1 |
| D | Decision (design.md) | D1, D2 | task-management#D1 |
| R | Risk (design.md) | R1, R2 | task-management#R1 |

**Test references:**
```
task-list#A1.E1           ← screen#action.scenario
task-management#C1        ← capability#calculation
task-management#I3        ← capability#integration
```

**Cross-capability acknowledgments:**
```markdown
Consumes: task-management#I3
```

---

## 5. The Spec Artifacts

### 5.1 Screen (primary spec unit)

A screen deserves its own spec when it has:
- A distinct routable URL, OR
- A distinct user intent ("done" means something different here), OR
- A distinct entity being acted on

**Modals and panels are actions, not screens. Pages are screens.**

### 5.2 Capability (operating rules)

Current state only — no rationale, no history:
- Screens and components index
- Data model and entity lifecycle
- Calculations
- Cross-screen flows
- Cross-cutting constraints
- Integration contracts
- Impact (schema, dependencies, routes)

Read on every implementation task. Must stay lean.
Pointer to design.md at the top.

### 5.3 Design (rationale)

Historical only — no operating rules:
- Why this capability exists (long form)
- Non-goals
- Decisions with alternatives considered
- Risks and trade-offs
- Open questions

Always present, even if thin. Read once during exploration.

### 5.4 Component (composition unit)

- Owned by exactly one capability
- Embeddable in screens of other capabilities
- Host screen references: `Embeds: <capability>#<component>`

### 5.5 Minimal Mode

A capability with **one screen, no integrations, no non-obvious decisions**
may collapse to a single capability.md with tasks folded in at the bottom.
Skip design.md and the screens/ folder.

**Test layering may also collapse in minimal mode:** the three top-level
test groups (T(last-2) integration, T(last-1) unit, T(last) E2E) may be
folded into a single test task with sub-checkboxes for each layer.
Promote back to three groups when the capability leaves minimal mode.

**Promote out of minimal mode the moment any of these become true:**
- Second screen added
- Non-obvious decision arises
- Any integration introduced
- Open question worth recording

Promotion is a split operation, not a rewrite. The single file's sections
map directly to the expanded structure.

---

## 6. Capability Types

| Type | Purpose | Distinguishing feature |
|---|---|---|
| **Standard** | Owns an entity and its lifecycle | Task Management, Projects |
| **Aggregation** | Aggregates across other capabilities | Search, Dashboard, Settings |
| **Cross-capability** | Coordinates a feature spanning multiple capabilities | Task Due Date |

**Single-capability rule:** Every screen belongs to exactly one capability.

Decision tree when a screen appears to need two parents:
```
1. Is it an aggregation screen?           → name the aggregation capability
2. Who is the primary owner?              → screen lives there
3. Does it need cross-cap behavior?       → embed via component
4. Would embedding produce incoherent UI? → only then split into two screens
```

---

## 7. Screen Spec Format

```markdown
# Screen: <Name>

**Capability:** <parent capability>
**Entry:** <how user arrives>
**Exit:** <where each action leads, or "none">

## Layout
**Last verified:** <YYYY-MM-DD> (<Figma link or: no design file>)
┌─────────────────────────────────┐
│  Zone labels only, not pixels   │
└─────────────────────────────────┘

## Actions

### A1 — <Action name>
WHEN <trigger>,                                         [A1.RQ1]
THE SYSTEM SHALL <response>.

#### A1.E1 — <Error scenario label>
IF <condition>,
THEN THE SYSTEM SHALL <response>.

| Input | Output | (Why?) |
|---|---|---|
| ...   | ...    |        |

## States

### S1 — Loading
WHILE data is being fetched,
THE SYSTEM SHALL display a loading skeleton.

### S2 — Empty
WHILE no records exist,
THE SYSTEM SHALL display <empty state message and primary action>.

### S3 — Error
IF data fetch fails,
THEN THE SYSTEM SHALL display an error message with a retry option.

### S4 — <Feature-specific state>
WHILE <condition>,
THE SYSTEM SHALL <behavior>.

## Data Model
(screen-local fields only — entity-level model lives in capability.md)

| Field | Type | Notes |
|---|---|---|

## Spec Checklist
- [ ] Event-driven (WHEN) — <list actions covered>
- [ ] Unwanted (IF/THEN) — <list error cases, or: deferred: <named items>>
- [ ] State-driven (WHILE) — loading · empty · error · <feature-specific>
- [ ] Ubiquitous — <always-true rules, or: n/a>
- [ ] Optional (WHERE) — <feature flags, or: n/a>
- [ ] Performance — <operation: threshold, or: n/a>
- [ ] Scale — <volume limit, or: n/a>
- [ ] Accessibility — <requirements, or: deferred: <named items>>
- [ ] Authorization — <role rules, or: n/a — any authenticated user>
- [ ] Internationalization — <translatable strings, or: n/a>
- [ ] Data model — <entity summary, or: see capability.md>
```

---

## 8. EARS Patterns

| Pattern | Keyword | Use for |
|---|---|---|
| Event-driven | WHEN | User actions, system events |
| Unwanted | IF/THEN | Errors, failures, rejections |
| State-driven | WHILE | Sustained states |
| Ubiquitous | THE SYSTEM SHALL (no trigger) | Always-true rules |
| Optional | WHERE | Feature flags |

**State-driven baseline:** every screen must address loading, empty, and error
as the minimum three states — plus feature-specific states on top.

---

## 9. Scenario Rules

### H4 labeled scenario — behavioral variants
Use when: different code path, different outcome shape, multi-line input,
or a design decision worth naming.

```markdown
#### A3.E1 — No confirmation
WHEN user clicks delete,
THE SYSTEM SHALL remove the task immediately without confirmation.
```

### Example table — data-driven variants
Use when: same code path, different input values.
Add "Why?" column when intent is not obvious from input/output alone.

```markdown
| Input | Saved as | Why? |
|---|---|---|
| `Buyer Mapping v1.xlsx` | `Buyer Mapping`  | version suffix stripped |
| `~$data.xlsx`           | rejected         | Office lock file        |
```

Mixed accept/reject: one table, `rejected` as sentinel.
If any cell exceeds one line → promote that row to an H4 scenario.

### No scenario
Use when: the EARS statement alone is fully precise.

---

## 10. Capability Doc Format

```markdown
# Capability: <Name>

> Design rationale in `design.md` — read before proposing changes
> to constraints, integrations, or lifecycle.

## Intent
<1-3 lines: what user goal does this capability serve>

## Screens
- <screen> — <one-line role>

## Components
- <component> — <one-line role>
(omit if none)

## Data Model

| Field | Type | Notes |
|---|---|---|

## Lifecycle
<state transitions>
e.g.: draft → active → completed → archived
(omit if not applicable)

## Calculations

### C1 — <Calculation name>
<formula>
If <condition>: <cap or fallback>.

### C2 — <Calculation name>
<formula>

(omit section if no calculations)

## Cross-screen Flows

### F1 — <Flow name>
**Entry:** <source>   **Exit:** <screen>
WHEN <trigger>, THE SYSTEM SHALL <behavior>.
**Failure:** IF <condition>, THEN THE SYSTEM SHALL <response>.

## Cross-cutting Constraints

### Security
### Accessibility
### Network & Sync
### Observability

(omit sub-sections that have nothing to say)

## Integrations

### I1 — Produces → <other capability>#<ID>
<one-line contract>

### I2 — Consumes → <other capability>#<ID>
<one-line contract>

## Impact
**Schema:** <new tables or changes>
**Dependencies:** <new packages or services>
**Routes:** <new URLs>

---

## Capability Checklist
- [ ] Intent stated
- [ ] All screens listed
- [ ] Data model complete
- [ ] Lifecycle defined — or confirmed n/a
- [ ] Calculations — defined or confirmed n/a
- [ ] Cross-screen flows — failure path covered per flow
- [ ] Cross-cutting: Security
- [ ] Cross-cutting: Accessibility
- [ ] Cross-cutting: Network & Sync
- [ ] Cross-cutting: Observability
- [ ] Integrations — all contracts named with stable IDs
- [ ] Impact — schema, dependencies, routes listed
- [ ] design.md exists and is current — or marked omitted (minimal mode)
```

---

## 11. Design Doc Format

```markdown
# Design: <Capability Name>

## Why this capability exists
<Long form: what users were doing without it, what pain it solves.
2-5 paragraphs. Preserve the workflow captured in brainstorm.md.>

## Non-goals
<What this capability deliberately does NOT do, and why.>

## Decisions

### D1 — <Decision title>
**Chose:** <what and why>
**Rejected:** <alternatives and why not>

## Risks

### R1 — <Risk title>
**Threat:** <what could go wrong>
**Mitigation:** <what we are doing about it>
**Accepted:** <what we are living with>

## Open Questions
<Delete this section when empty.>
```

---

## 12. Component Spec Format

```markdown
# Component: <Name>

**Owned by:** <capability>
**Embeddable in:** any screen via `<capability>#<component>`

## Props
| Prop | Type | Required | Notes |
|---|---|---|---|

## Emits
| Event | Payload | When |
|---|---|---|
(omit if emits nothing)

## Actions

### A1 — <Action>
WHEN ..., THE SYSTEM SHALL ...

## States

### S1 — Loading
### S2 — Empty
### S3 — Error

## Spec Checklist
- [ ] Event-driven (WHEN)
- [ ] Unwanted (IF/THEN)
- [ ] State-driven (WHILE) — loading · empty · error
- [ ] Ubiquitous
- [ ] Optional (WHERE)
- [ ] Accessibility
- [ ] Authorization
- [ ] Internationalization
- [ ] Emits documented
- [ ] Data model
```

Host screen reference:
```markdown
Embeds: comments#thread (entityId: task.id)
```

---

## 13. Tasks Format

### Core principle
**Specs are user-perspective. Tasks are developer-perspective.**

A spec says: "The analyst can upload an xlsx."
A task says: "Prove the import engine by ingesting a hardcoded dataset."

If a task heading is stated from the developer's perspective ("Set up routing"),
it is a sub-task checkbox, not a task heading.

### Task ordering — prove the engine first

```
T1 — Prove the engine
     Hardcoded UI triggers the full pipeline end-to-end.
     After T1, the backend is known-good and untouched by later tasks.

T2+ — Build real UX
     Replace hardcoded trigger with real interactions.
     Backend is proven; these tasks are pure frontend enrichment.

T(n) — Screen tasks
     Thin vertical slices. Each delivers one analyst capability
     end-to-end: the analyst does something and sees a result.

T(last-2) — Integration tests
T(last-1) — Unit tests (coverage gaps only)
T(last)   — E2E tests (happy path, ~1 per flow)
```

### Task file format

```markdown
# Tasks: <Capability Name>

## T1 — Prove the engine — <one-line verification description>
- [ ] T1.1 Schema: <entity> table (<fields>)
- [ ] T1.2 Mutations: <list>
- [ ] T1.3 Queries: <list>
- [ ] T1.4 Hardcoded UI: trigger full pipeline, confirm data lands in DB
- [ ] T1.5 Verify: <specific observable check>

## T2 — <Screen name> — <one-line verification description>
- [ ] T2.1 UI: <what screen shows>
- [ ] T2.2 Wire <mutation/query>
- [ ] T2.3 Verify: <specific observable check>

## T3 — Integration tests
- [ ] T3.1 Install: vitest, @edge-runtime/vm, convex-test,
           @testing-library/react, @testing-library/user-event, jsdom
- [ ] T3.2 Fixture: <xlsx or seed data>
- [ ] T3.3 Test matrix: <screen> — write BEFORE tests.
       Always list states. Add approach × what-to-verify columns when
       behavior varies by approach (e.g. keyboard vs. mouse, optimistic
       vs. confirmed). Single-axis matrix is fine for simple screens.
- [ ] T3.4 Integration tests per matrix
- [ ] T3.5 Verify: all states covered, no mocked component tests

## T4 — Unit tests (coverage gaps only)
- [ ] T4.1 Unit tests for pure functions (<list>)
- [ ] T4.2 Run vitest --coverage; identify uncovered lines
- [ ] T4.3 Unit tests for uncovered lines (loading states, error states,
           edge cases integration can't produce)
- [ ] T4.4 Document any v8 ignore exceptions with justification
- [ ] T4.5 Verify: 100% coverage (or justified exceptions)

## T5 — E2E tests
- [ ] T5.1 Install @playwright/test; add config and npm run test:e2e
- [ ] T5.2 One smoke test per critical user journey
- [ ] T5.3 Verify: passes against running dev server
```

### Task sizing rules
- Simple screen → one task covers UI + wiring
- Complex screen → T(n): backend, T(n+1): frontend
- Data model always comes first as its own task
- Navigation / routing scaffolding → sub-checkbox inside first task that needs it
- Empty states and loading states → minor affordances within a screen task, not own tasks
- Each task's last checkbox is always a verification step

### Three-layer test rules
- **Integration tests** — workhorse. Real in-memory backend + real React.
  One test per component state (MECE). Write test matrix BEFORE writing tests.
  These replace both backend-only tests and mocked component tests.
- **Unit tests** — fill gaps only. Run coverage first, then fill.
  Not the default. Only for pure functions and states integration can't produce.
- **E2E tests** — ~1 per critical flow. Real browser, real backend.
  Deliberate overlap with integration tests for critical journeys is acceptable.

### Cross-capability tasks.md
When a capability has no screens, tasks.md spans affected systems
in producer-first order:

```markdown
# Tasks: Task Due Date

## T1 — Task Management side
- [ ] T1.1 Add dueDate field to Task schema
- [ ] T1.2 Update mutations to accept dueDate
- [ ] T1.3 Surface dueDate on Task Form (task-management#A2)
- [ ] T1.4 Verify: task created with due date → stored correctly

## T2 — Calendar side
- [ ] T2.1 Query tasks with dueDate for current month
- [ ] T2.2 Render task chips on Calendar day cells
- [ ] T2.3 Verify: task with due date appears on correct calendar day

## T3 — Integration tests
## T4 — Unit tests (coverage gaps only)
## T5 — E2E tests
(Standard test layering applies — sub-checkboxes omitted here for brevity.
See § 13 task file format.)
```

---

## 14. Anti-Patterns

| ❌ Don't | ✅ Do |
|---|---|
| One spec per CRUD operation | One spec per screen |
| Delete button as its own spec | Delete is A3 inside its screen spec |
| "Persist data" as a constraint | Obvious — omit |
| DB schema or API paths in screen spec | Schema in capability.md, paths in tasks.md |
| SHALL for style ("button SHALL be blue") | Design in Layout or Figma |
| Decisions or risks in capability.md | Those live in design.md |
| Operating rules in design.md | Those live in capability.md |
| System-wide decisions in design.md | Those live in decisions/ as ADRs |
| Repeating cross-cutting rules per screen | Cross-cutting lives in capability.md once |
| Multi-parent screens | Aggregation capability or embedded component |
| Cross-capability work without own capability folder | Create a capability folder |
| Task stated from developer perspective | Rewrite as analyst capability |
| Building UI before backend is proven | T1 always proves the engine first |
| Writing tests before the test matrix | Matrix first, tests second |
| Mocked component tests | Use integration tests instead |
| Layout ASCII with no verified date | Add Last verified date |
| Leaving Open Questions when empty | Delete the section |

---

## 15. Change Protocol

**Renaming:** update the name, keep the stable ID.
**Removing:** delete, don't deprecate. Note in design.md if non-obvious.
**Modifying:** edit in place, git diff is the record.
**Adding:** next available ID number. Update checklist.
**Promoting deferred item:** write EARS statements, check the box, remove deferral annotation.
**Promoting capability decision to ADR:** create ADR, replace decision body with pointer.

---

## 16. Quick Decision Reference

| Question | Answer |
|---|---|
| Screen or action? | Own URL → screen. Otherwise action inside parent. |
| Which capability owns this screen? | What entity is the screen about? That capability. |
| Cross-capability work? | New capability folder. Producer ships first. |
| Constraint or obvious behavior? | Would a competent developer choose differently without it? No → omit. |
| H4 scenario or table? | Different code paths → H4. Different input values, same path → table. |
| Screen-level or cross-cutting? | Applies to more than one screen → capability.md. |
| capability.md or design.md? | Current state → capability.md. Rationale → design.md. |
| design.md or decisions/? | One capability → design.md. System-wide → ADR. |
| Renaming a screen? | Rename file + H1. Keep stable IDs. Update capability.md Screens list. |
| Removing a screen? | Delete file. Update capability.md. Note in design.md if non-obvious. |
| Removing an action? | Delete from screen spec. Update checklist. Note in design.md if non-obvious. |
