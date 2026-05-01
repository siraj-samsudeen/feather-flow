---
name: feather-execute-task
description: >
  Task execution skill. Use when a capability has a tasks.md and the
  user is ready to build. Prints orientation based on current state
  (reads tasks.md and plans/ folder — no separate state file needed).
  Runs one task at a time: mini-plan → execute → verify → record → ask.
  Classifies verification findings into six categories. Produces per-task
  plan files and drafts a PR description when all tasks are complete.
  Part of the feather-flow pipeline: runs after feather-spec.
---

# Feather Execute Task

One task per cycle. A gate at every step. The user owns the loop.

**Core philosophy:** The agent is fast at writing code. The discipline
is at the boundaries. A mini-plan before code catches "about to write
the wrong thing" cheaply. A verification step after code confirms it
worked.

---

## Execution Modes

| Mode | Default | Behavior |
|---|---|---|
| **user-execute** | ✅ Yes | Agent produces mini-plan + reference. User writes the code. Agent verifies. |
| **agent-execute** | Opt-in | Agent produces mini-plan, user approves, agent writes code, user reviews diff. |

Set mode at session start. Default is user-execute.
To switch: "Switch to agent-execute mode" or "Switch to user-execute mode."

---

## Session Start — Orientation

On every invocation, read current state from the filesystem and print
an orientation message before doing anything else.

**State inference (no separate state file):**

```
1. Is there a tasks.md in any capability folder?
   No  → "No active capability found."

2. Are all checkboxes ticked?
   Yes → "All tasks complete."

3. Is there an unchecked checkbox with <!-- step: <x> --> marker?
   Yes → resume from that step

4. Otherwise → first unchecked checkbox = next task
```

**Orientation message format:**

```
No tasks.md found:
──────────────────────────────────────────────────────
  Feather Execute — no active capability found.

  To start: run feather-brainstorm (vague idea or pain point)
         or feather-spec (clear capability, ready to write specs)
──────────────────────────────────────────────────────

All tasks ticked:
──────────────────────────────────────────────────────
  Feather Execute — <capability> is fully built.

  All tasks complete. Want me to draft the PR description?
──────────────────────────────────────────────────────

Active task, step marker present:
──────────────────────────────────────────────────────
  Feather Execute — active: <capability>

  Task:  <task-id> — <task name>
  Step:  <mini-plan / waiting for your code / waiting to verify>

  Say '<next action>' to continue.
──────────────────────────────────────────────────────

Next task ready (no step marker):
──────────────────────────────────────────────────────
  Feather Execute — active: <capability>

  Next: <task-id> — <task name>
  <N> tasks remaining.

  Say 'start' to begin, or name a specific task.
──────────────────────────────────────────────────────
```

---

## Test Ordering (load-bearing rule)

Tests always follow this sequence — never reverse it:

```
1. Integration tests   — workhorse. Real backend + real React.
                          One test per component state (MECE).
                          Write test matrix BEFORE writing tests.
                          Replaces mocked component tests.

2. Unit tests          — fill coverage gaps ONLY. Run coverage first.
                          Only for pure functions and states that
                          integration tests can't produce.

3. E2E tests           — ~1 per critical flow. Real browser, real backend.
                          Deliberate overlap with integration is acceptable.
```

---

## The Gate Loop (one cycle per task)

```
┌─────────────────────────────────────────┐
│  1. Mini-plan                           │
│     Agent describes what will be built  │
│     User reviews and approves           │
├─────────────────────────────────────────┤
│  2. Execute                             │
│     user-execute: user writes code      │
│     agent-execute: agent writes code    │
│     → user reviews diff                 │
├─────────────────────────────────────────┤
│  3. Verify                              │
│     Run verification commands           │
│     Classify any findings               │
│     Resolve before tick                 │
├─────────────────────────────────────────┤
│  4. Tick and record                     │
│     Mark checkbox + remove step marker  │
│     Write plan file to plans/           │
│     Append to notes.md if needed        │
├─────────────────────────────────────────┤
│  5. Stop and ask                        │
│     "Continue to T2.2, or stop here?"   │
└─────────────────────────────────────────┘
```

---

## Step 1 — Mini-Plan

**Before writing the mini-plan, read the relevant spec.** This is what
keeps "Spec references" in the plan honest rather than guessed at:

- The screen spec for any screen the task touches
  (`capabilities/<name>/screens/<screen>.md`)
- The capability.md cross-cutting section if the task involves
  authorization, accessibility, security, or observability
- design.md only if the task requires a decision the user already
  recorded there (rare — design.md is read once during exploration,
  not per task)

If a spec reference is missing or unclear, surface it before writing the
plan rather than inventing the reference.

Produce a mini-plan before any code is written. Even for small tasks.
Add a step marker to the tasks.md checkbox:

```markdown
- [ ] T2.1 UI: dropzone + name input + Confirm button  <!-- step: mini-plan -->
```

Mini-plan format:

```markdown
**T2.1 — Mini-plan**

What I will build:
- <specific file or function>: <what it does>

What I will NOT touch:
- <files or systems out of scope>

Verification: <how we confirm this task is done>

Spec references: <screen#action or capability#integration>
```

In user-execute mode:
> "Plan above. Your turn — let me know when you're done and I'll verify."

Update step marker to `<!-- step: execute -->`.

In agent-execute mode:
> "Plan above. Shall I proceed?"
Wait for approval before writing any code.
Update marker to `<!-- step: execute -->` only after approval.

If the plan reveals the task is larger than the checkbox implies:
> "This looks larger than one task. Want to split T2.1 into T2.1a and
> T2.1b before starting?"
Stop until decided.

---

## Step 2 — Execute

### user-execute mode
Wait. When user signals done, update marker to `<!-- step: verify -->`.

### agent-execute mode
Write code, show diff, say:
> "Review the changes above. Ready to verify?"
Update marker to `<!-- step: verify -->` only after user confirms.

Do not auto-commit. Do not proceed to verification without confirmation.

---

## Step 3 — Verify

Run the verification step. Then classify every finding before acting.

### Finding classifier

| Finding | Action |
|---|---|
| Task verification failed (lint, test) | Fix inline, re-verify before tick |
| Code and spec disagree (divergence) | Divergence conversation (below) |
| Spec is ambiguous — unstated choice was made | Ambiguity conversation (below) |
| Cross-cutting issue (affects more than one screen) | Update capability.md cross-cutting section with dated note |
| Scope creep on current task | Three-option prompt (below) |
| Unrelated bug or improvement noticed | Append to notes.md under Open Issues |

Do not silently pick a side on a divergence.
Do not silently absorb scope creep.
Do not rewrite spec content without surfacing to the user.
Treat "tests pass" as proof of what tests assert — not proof of correctness.
A divergence means tests followed the wrong source.

### Divergence conversation

> "While verifying T2.1, I noticed a divergence between code and spec:
>
> **Spec (<screen#ID>):** <what spec says>
> **Code:** <what code does>
> **Tests:** <whether they caught it or followed the code>
>
> Three ways to reconcile:
> (a) Fix code to match spec — <concrete change>
> (b) Update spec to match code — <concrete change>
> (c) Update both — <concrete change>
>
> Which? Once decided I'll record the resolution in the plan file."

After user decides: make the change, record in plan file under
`## Divergences resolved`, re-verify before ticking.

### Ambiguity conversation

> "Implementing T2.2 surfaced an ambiguity in <screen#ID>: <what was
> unclear>. I implemented <what was chosen>. Want me to add this to
> the spec, or do you want different handling?"

If accepted: update spec inline, record in plan file under
`## Spec ambiguities resolved`, continue.

### Scope creep prompt

> "T2.3 reveals it also needs <X>, which isn't in the task.
> (a) Add it to T2.3 now
> (b) Add a new T2.3b for it
> (c) Defer to <later task>
> Which?"

Stop until decided.

---

## Step 4 — Tick and Record

Remove the step marker. Tick the checkbox:

```markdown
- [x] T2.1 UI: dropzone + name input + Confirm button
  - Plan: dropzone component + auto-fill name from filename
  - Method: integration-test + manual
  - Result: pass · 2026-05-01
```

Write plan file to `capabilities/<name>/plans/<task-id>-<slug>.md`:

```markdown
# Plan: T2.1 — Build upload screen

**Date:** 2026-05-01
**Task:** T2.1 UI: dropzone + name input + Confirm button
**Mode:** agent-execute

## What was planned
- UploadPage component: dropzone (click + drag), filename display, name input
- deriveDatasetName utility: strip extension + version suffix + trim
- Confirm button: enabled when file + name present; "Importing…" during action

## What was not touched
- convex/datasets.ts (backend proven in T1)

## Spec references
- upload#A1 (file selection)
- upload#A2 (confirm action)

## Verification
**Method:** integration-test + manual
**Result:** pass
**Lint:** ✓  **Tests:** 8 passed
**Manual:** drop file → name auto-fills → confirm → dataset in list

## Divergences resolved
- <screen#ID>: <what diverged> → <resolution>
(delete section if none)

## Spec ambiguities resolved
- <screen#ID>: <what was unclear> → <resolution>
(delete section if none)
```

If a finding was unrelated, append to `capabilities/<name>/notes.md`.
Create the file the first time a finding needs to be recorded — do not
create an empty notes.md preemptively at session start:

```markdown
# Notes: <Capability Name>

## Open Issues

- <YYYY-MM-DD> (during <task-id> verify): <what was noticed>.
  Not blocking PR. → consider <future task or capability>.

## Spec Ambiguities Resolved

- <YYYY-MM-DD> (<task-id>): <what was unclear> → <resolution>.

## Divergences Resolved

- <YYYY-MM-DD> (<task-id>): <what diverged> → <resolution>.
```

notes.md is capability-scoped. If a finding affects another capability,
note it here with "also affects <other-capability>."

---

## Step 5 — Stop and Ask

> "T2.1 done. Continue to T2.2, or stop here?"

Default: stop and wait. Do not auto-continue.

---

## End of Capability — PR Draft

When all tasks are ticked:

> "All tasks complete. Want me to draft a PR description?"

```markdown
# <Capability Name>

## What
<2-3 lines from capability.md → Intent>

## Why
<1-2 lines from design.md → Why this capability exists>

## Changes by task

### T1 — <Group name>
- T1.1: <one-line summary from plan file>

## Verification
<Test counts, lint status, manual checks from plan files.>

## Decisions worth surfacing for review
<D1, D2… from design.md as one-line summaries.>

## Spec changes made during implementation
<From plan files: Divergences resolved + Spec ambiguities resolved.>

## Known issues (not addressed in this PR)
<From notes.md Open Issues. Omit section if empty.>

## Out of scope
<From design.md → Non-goals.>
```

After draft:
> "Draft above. Want me to write it to a file or copy to clipboard?
> You open the PR."

---

## Anti-Patterns

| ❌ Don't | ✅ Do |
|---|---|
| Skip mini-plan because "task is small" | Even a one-line task gets a mini-plan |
| Batch multiple tasks into one cycle | One task per cycle |
| Auto-continue after tick | Always ask before next cycle |
| Write code in user-execute mode | Wait for the user |
| Tick without recording verification | Verification line is non-optional |
| Silently fix code when spec says otherwise | Surface as divergence, three options |
| Silently update spec when code is convenient | Same — divergence is a conversation |
| Skip divergence prompt ("spec was probably wrong") | Spec wins until user says otherwise |
| Drop findings into chat | Append to notes.md with date and source task |
| Spawn task for every cross-cutting issue | Update capability.md cross-cutting section |
| Treat "tests pass" as proof of correctness | Tests prove what they assert |
| Commit on the user's behalf | The user commits |
| Open the PR | The user opens the PR |
| Proceed agent-execute without approval | Wait for explicit approval after mini-plan |
