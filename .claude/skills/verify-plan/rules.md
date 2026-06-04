# Planning Rules

Plans live in `docs/plans/PLANNNNN-slug.md`. Each plan is one logical unit broken into tasks. Tasks map 1:1 to commits.

---

## Commit grain — three simultaneous criteria

Each task must satisfy all three:

1. **Reviewable** — the diff makes sense on its own without the next task
2. **Green tree** — `npm run verify` passes after this task alone
3. **Coherent** — deserves its own commit message

When they conflict, split or coalesce until all three hold. Aim for 3–7 tasks per plan.

---

## First commit is the plan alone

Commit `docs/plans/PLANNNNN-*.md` with no implementation. The plan is a checkpoint — pause, re-read, refine before sinking time into code.

---

## Every task names how it will be verified

Pick the right shape for the task — don't default to one:

- **TDD red-green** — write the failing test first; green pass is the verification
- **Existing tests** — for structural/refactoring changes
- **Typecheck** — for type-only/config changes (`npm run build`, `tsc -p convex --noEmit`)
- **Manual click-through** — for UI work the test suite doesn't reach
- **Regression scan** — enumerate likely breakage points when touching shared code

Commands must produce observable output. "Convex picks up the file" is not a verification step.

---

## Task structure

```
### N. Brief task title

What this task achieves and why it's a coherent unit.

#### Na. Readable subtask title

**Files:** `path/to/file.ts`

Why this change is needed — the constraint, tradeoff, or design intent.
Code block if helpful.

#### Na. Verify

1. Command — expected observable output.
2. Second command if needed.
```

**Subtask rules:**
- Titles are human-readable verb phrases: `#### 1b. Create clearAll mutation` ✅ — `#### 1b. convex/clearAll.ts` ❌
- `**Files:**` at the subtask level, never aggregated at the task header
- Descriptions explain WHY — the constraint, tradeoff, or design intent. The what is in the code block.
- Single-step tasks may use flat structure (`**Files:**` + `**Verify:**` inline)

---

## Test ordering within a task

When a task has multiple test subtasks, order by confidence value:

1. **Integration tests first** — real behavior through the full stack; primary regression signal
2. **Backend unit tests second** — edge cases hard to reach via a rendered component
3. **Mock-based tests last** — last resort for states unreachable with a real backend

---

## Coverage annotations

No `/* v8 ignore next */` without explicit user approval. Before proposing one:

1. Confirm the branch genuinely cannot be reached in a test — not just unlikely
2. State the reason inline: `/* v8 ignore next -- reason */`

In `convex-test`, `testClient.run()` + `db.delete()` can reproduce almost any database state — the bar for "genuinely unreachable" is high.

---

## Plan context

The Context section must be self-contained. Describe what prior plans built in 2–3 bullets — the reader must not need to open another file. Don't mention things that don't exist in the codebase.

---

## Decisions

State every design decision with its alternative: "X because Y; the alternative Z would require W." An assertion without contrast ("X is the right model") is not a rationale.

---

## No Deviations section

The commit diff is the deviation record. If a task's execution reveals a load-bearing lesson, promote it to an ADR, auto-memory, or CLAUDE.md edit — not a section at the bottom of the plan.

---

## Commit messages

**Subject:** what changed, active voice, state the result.
**Body:** the why — context, trade-off, or decision not visible in the diff.
**Trailers:** `See docs/plans/PLANNNNN-*.md`, `Closes #N`, then `Co-Authored-By` when applicable.
