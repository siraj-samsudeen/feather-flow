# Example: Task Management (To-Do List)

A simple single-user to-do list. Used as the canonical simple example
for feather-spec. Shown in minimal mode (one screen, no integrations)
and then expanded to illustrate the full structure.

---

## Minimal Mode (one screen, no integrations)

When a capability is simple enough — one screen, no integrations,
no non-obvious decisions — the entire capability collapses to one file.

```markdown
# Capability: Task Management

> Simple single-user to-do list. No design.md needed at this scale.

## Intent
Allow a user to capture, complete, and delete personal tasks
from a single screen without losing items across sessions.

## Screens
- Task List — the only screen; all actions live here

## Data Model

| Field | Type | Notes |
|---|---|---|
| id | unique identifier | system-generated |
| name | text | trimmed on save |
| status | active / completed | default: active |
| createdAt | timestamp | for top-of-list ordering |

## Tasks

### T1 — Data model + backend
- [ ] T1.1 Schema: Task table (id, name, status, createdAt)
- [ ] T1.2 Mutations: addTask, completeTask, uncompleteTask, deleteTask
- [ ] T1.3 Query: listTasks with status filter
- [ ] T1.4 Verify: mutations write correctly, query returns filtered results

### T2 — Task List screen
- [ ] T2.1 UI: list, filter tabs, checkbox, delete button
- [ ] T2.2 Wire addTask mutation + listTasks query
- [ ] T2.3 Wire completeTask / uncompleteTask / deleteTask
- [ ] T2.4 Empty state: "No tasks yet — add one above"
- [ ] T2.5 Verify: add → complete → delete flow works end to end

### T3 — Integration tests
- [ ] T3.1 Test matrix: Task List (empty · has tasks · filter active · filter completed)
- [ ] T3.2 Integration tests per matrix
- [ ] T3.3 Unit tests: pure functions (none in this case)
- [ ] T3.4 E2E: add task → complete → appears under Completed tab
- [ ] T3.5 Verify: lint passes, all states covered

## Capability Checklist
- [x] Intent stated
- [x] All screens listed
- [x] Data model complete
- [ ] Lifecycle — n/a (status field carries state, no complex lifecycle)
- [ ] Calculations — n/a
- [ ] Cross-screen flows — n/a (one screen)
- [ ] Cross-cutting: Security — any authenticated user, no roles
- [ ] Cross-cutting: Accessibility — deferred: keyboard nav on checkbox and delete
- [ ] Cross-cutting: Network & Sync — n/a (no offline requirement)
- [ ] Cross-cutting: Observability — n/a at this scale
- [ ] Integrations — n/a
- [ ] Impact — Schema: tasks table. Dependencies: none new. Routes: / only.
- [ ] design.md — omitted (minimal mode, no non-obvious decisions)
```

---

## Screen Spec (when expanded out of minimal mode)

If Task Management grows — say, adding a Task Detail screen or
integrating with a Calendar capability — promote to full structure.
The Task List screen spec then looks like this:

```markdown
# Screen: Task List

**Capability:** Task Management
**Entry:** App landing page / nav
**Exit:** None — all actions stay on this screen

## Layout
**Last verified:** 2026-05-01 (no design file)
┌─────────────────────────────────┐
│  "My Tasks"        [+ Add]      │
├─────────────────────────────────┤
│  [All] [Active] [Completed]     │
├─────────────────────────────────┤
│  ☐  Buy groceries          [x] │
│  ☐  Call dentist           [x] │
│  ☑  Submit report          [x] │
└─────────────────────────────────┘

## Actions

### A1 — Add task
WHEN user types a task name and confirms,                       [A1.RQ1]
THE SYSTEM SHALL add it to the top of the list as active,
with the name trimmed of leading and trailing whitespace.

#### A1.E1 — Empty name
IF user confirms with an empty or whitespace-only name,
THEN THE SYSTEM SHALL reject it and keep the input focused.

| Input | Saved as | Why? |
|---|---|---|
| `Buy groceries` | `Buy groceries` | no change |
| `  Buy groceries  ` | `Buy groceries` | trimmed |
| `   ` | rejected | whitespace-only |

### A2 — Complete task
WHEN user checks a task,                                        [A2.RQ1]
THE SYSTEM SHALL mark it completed and move it to the bottom.

#### A2.E1 — Undo complete
WHEN user unchecks a completed task,
THE SYSTEM SHALL restore it to active and move it to the top.

### A3 — Delete task
#### A3.E1 — No confirmation
WHEN user clicks delete,
THE SYSTEM SHALL remove the task immediately without confirmation.

### A4 — Filter by status
WHEN user selects a filter tab,                                 [A4.RQ1]
THE SYSTEM SHALL show only tasks matching that status.
THE SYSTEM SHALL display the count of matching tasks on each tab.

## States

### S1 — Loading
WHILE data is being fetched,
THE SYSTEM SHALL display a loading skeleton.

### S2 — Empty list
WHILE no tasks exist,
THE SYSTEM SHALL display "No tasks yet — add one above."

### S3 — Error
IF data fetch fails,
THEN THE SYSTEM SHALL display an error message with a retry option.

### S4 — Active filter
WHILE a non-All filter tab is selected,
THE SYSTEM SHALL scope the task count to that status only.

## Spec Checklist
- [x] Event-driven (WHEN) — Add, Complete, Uncheck, Delete, Filter
- [x] Unwanted (IF/THEN) — Empty name rejection
- [x] State-driven (WHILE) — loading · empty · error · active filter
- [x] Ubiquitous — tab counts, top-of-list ordering
- [ ] Optional (WHERE) — n/a
- [ ] Performance — n/a (client-side, negligible data)
- [ ] Scale — n/a (personal list, no pagination needed)
- [ ] Accessibility — deferred: keyboard nav on checkbox and delete, ARIA labels
- [ ] Authorization — n/a — any authenticated user
- [ ] Internationalization — n/a (English only)
- [x] Data model — Task (id, name, status, createdAt) — see capability.md
```
