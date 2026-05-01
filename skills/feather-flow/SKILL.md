---
name: feather-flow
description: >
  The Feather Flow pipeline overview. Use when the user asks about the
  pipeline as a whole, is starting fresh and unsure where to enter, or
  wants to understand how brainstorm → spec → execute connect. Also use
  for mid-implementation pivots where scope has changed and the user
  needs to re-orient. For direct task execution, invoke feather-execute-task.
  For spec authoring, invoke feather-spec. For discovery and brainstorming,
  invoke feather-brainstorm.
---

# Feather Flow

End-to-end capability delivery. One pipeline of three stages —
brainstorm, spec, execute — plus this overview skill.

```
vague requirement / pain point / pivot
        │
        ▼
feather-brainstorm          ← expand horizons, capture in user's words
        │                      Output: brainstorm.md, mockups/ (optional)
        ▼
feather-spec                ← author all spec artifacts from brainstorm
        │                      Output: capability.md, design.md,
        │                              screens/, components/, tasks.md
        ▼
feather-execute-task        ← build one task at a time, gate at every step
        │                      Output: working code, plans/, notes.md
        ▼
       PR                   ← drafted by feather-execute-task, opened by user
```

---

## Starting Mid-Pipeline

The user does not have to start at the beginning.

| User has | Start here |
|---|---|
| Vague idea or pain point | feather-brainstorm |
| Mid-implementation pivot — scope changed | feather-brainstorm (pivot entry point) |
| Clear capability in mind, ready to spec | feather-spec |
| brainstorm.md already written | feather-spec |
| All spec artifacts, ready to build | feather-execute-task |
| Some tasks complete, resuming | feather-execute-task (reads step marker from tasks.md) |

**Orientation:** when tasks.md exists, invoke feather-execute-task — it
reads the filesystem and prints orientation automatically. feather-flow
does not duplicate that logic.

---

## The Three Skills

### feather-brainstorm

Expand horizons before narrowing to spec.

**Entry points:**
- Vague: "I want a todo app" → starts with workflow walk-through
- Specific: clear feature → skips discovery, goes to horizon expansion
- Pivot: scope changed mid-implementation → captures what changed first

**Output:** `capabilities/<name>/brainstorm.md`, `mockups/` (if visual questions arose)

---

### feather-spec

Author all spec artifacts from a brainstorm or from a clear capability.

**Produces:**
- `capability.md` — operating rules, current state
- `design.md` — rationale, always present even if thin
- `screens/<screen>.md` — one per routable screen
- `components/<component>.md` — one per embeddable fragment
- `tasks.md` — developer task list, prove-engine-first ordering

**Test ordering (load-bearing rule):**
Integration tests → unit tests (coverage gaps only) → E2E (~1 per flow)

---

### feather-execute-task

Build one task at a time. Gate at every step.

**Loop:** mini-plan → execute → verify → tick → ask

**Verification classifies findings:**
task fix · spec divergence · spec ambiguity · cross-cutting issue ·
scope creep · unrelated bug/improvement

**User owns the loop.** Agent never auto-continues.

---

## Artifact Map

```
capabilities/
  └── <name>/
        ├── brainstorm.md          ← feather-brainstorm (optional)
        ├── mockups/               ← feather-brainstorm visual companion (optional)
        │     └── <screen>/
        │           ├── 01-option-a-<slug>.html
        │           ├── 02-option-b-<slug>.html
        │           └── _README.md
        ├── capability.md          ← feather-spec (operating rules)
        ├── design.md              ← feather-spec (rationale, always present)
        ├── tasks.md               ← feather-spec (developer task list)
        ├── plans/                 ← feather-execute-task (per-task records)
        │     └── T2.1-<slug>.md
        ├── notes.md               ← feather-execute-task (deferred findings)
        ├── screens/
        │     └── <screen>.md
        └── components/
              └── <component>.md

decisions/
  └── <ADR>.md                     ← system-wide decisions
```

---

## Quick Reference

| Question | Answer |
|---|---|
| Where does a vague idea start? | feather-brainstorm |
| Where does a pivot start? | feather-brainstorm (pivot entry point) |
| Where does the user's language live? | brainstorm.md — never paraphrased |
| Where do rejected design options live? | mockups/<screen>/ — all variants preserved |
| Where do operating rules live? | capability.md |
| Where does rationale live? | design.md |
| Where do system-wide decisions live? | decisions/ ADRs |
| What is the first task in tasks.md? | Prove the engine — hardcoded UI, full pipeline |
| What is the test order? | Integration → unit (gaps only) → E2E |
| Where do verification findings live? | notes.md (bugs) or capability.md cross-cutting (accessibility, security) |
| Where do spec divergences get recorded? | plans/<task-id>.md → Divergences resolved |
| Who opens the PR? | The user. Always. |
| Who owns the execution loop? | The user. Agent serves the step. |
