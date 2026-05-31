---
name: verify-plan
description: "Review the current plan file using three parallel agents (sonnet, opus, haiku), then auto-fix unambiguous issues and consult the user one by one on anything requiring a decision."
allowed-tools:
  - Read
  - Agent
  - Bash
  - AskUserQuestion
  - Skill
---

<objective>
Launch three model reviewers in parallel against the current plan file, consolidate findings, auto-fix everything unambiguous in a background subagent, then consult the user on any item that requires a decision — one question at a time.
</objective>

---

## Step 1 — Find the plan file

Check in this order:
1. If plan mode is active, the plan file path is stated in the system prompt — use it.
2. Otherwise, list `docs/plans/` and pick the highest-numbered plan file.
3. If neither exists, ask the user: "Which plan file should I review?"

Read the full plan content. Also read `rules.md` from this skill's directory — reviewers must check the plan against its authoring rules.

---

## Step 1.5 — Run review-convex-tests on test code (files or plan-embedded)

**Check for existing test files first:**

```bash
grep -oE '[a-zA-Z0-9/_-]+\.test\.(ts|tsx)' <plan-file> | sort -u
```

For each path found, check whether it exists on disk (`ls <path>`).

- **If any exist** → invoke `Skill("review-convex-tests")` with those file paths as the target.
- **If none exist** → check whether the plan contains embedded test code blocks:

```bash
grep -c 'test(' <plan-file>
```

  If that returns > 0, invoke `Skill("review-convex-tests")` passing the **plan file path** as the target. The skill's plan mode extracts embedded test code blocks and reviews them against the checklist.

- **If neither** → skip and note: "No test code found in plan — skipping review-convex-tests."

Findings feed into the consolidated list in Step 3 — label them `[review-convex-tests]` so they're distinguishable from the plan-level review findings.

---

## Step 2 — Launch three agents in parallel

Send all three in a single message (one tool call each). Use this exact prompt for all three — the only difference is the model:

> Review the following implementation plan on two dimensions:
>
> **A. Execution correctness** — flag real execution risks, gaps, and ambiguities. Ignore theoretical concerns that wouldn't actually cause problems in practice.
>
> **B. Planning.md compliance** — check the plan against every rule in the PLANNING.md excerpt below. Flag violations of: task/commit granularity (three simultaneous criteria), verification shape (every task names commands with observable results), plan context (self-contained, no dead pointers, grounded in current codebase), decisions with alternatives, deviations-as-commits rule, subtask structure (Files at subtask level, human-readable titles, why not what), and `/* v8 ignore */` annotation policy.
>
> **`/* v8 ignore */` annotation policy (additional rule not in PLANNING.md):** Flag every `/* v8 ignore */` annotation in the plan. For each one, state whether it is justified — meaning the state it skips genuinely cannot be reproduced in a test (impossible runtime state, provider guarantee, non-deterministic timing). If the state can be mocked or seeded, the annotation is not justified and a test should be written instead. Note: in `convex-test`, `testClient.run()` + `withIdentity()` can reproduce almost any database state (including deleted rows), so the bar for "genuinely unreachable" is high.
>
> **Session DSL chaining (additional rule):** Integration tests that use the feather-testing Session DSL must chain calls rather than `await` each one separately. Flag any test code that does multiple sequential `await session.X()` calls — the correct form is a single `await session.X().Y().Z()` chain. Also flag any inline arrow function handlers in JSX (e.g. `onClick={() => ...}`) that are never triggered by a test action — these are separate V8 function coverage entries that will silently fail the 100% functions threshold.
>
> Report findings as a numbered list. For each item include: the issue, why it matters in practice, and a concrete fix. Label each `[BLOCKER]`, `[GAP]`, or `[IMPROVEMENT]`.
>
> ---
>
> **Planning rules:**
>
> [paste full rules.md content here]
>
> ---
>
> **Plan to review:**
>
> [paste full plan content here]

| Agent | model param |
|-------|-------------|
| Sonnet | `sonnet` |
| Opus   | `opus`   |
| Haiku  | `haiku`  |

Run all three with `run_in_background: true`, then wait — you will be notified when each completes.

---

## Step 3 — Consolidate

Once all three respond:

1. **Deduplicate** — merge findings that describe the same issue. Note which models flagged it.
2. **Filter** — drop items that are purely stylistic, theoretical, or that wouldn't block execution.
3. **Rank** — blockers first, then gaps, then improvements.
4. **Label each** — prefix with: `[BLOCKER]`, `[GAP]`, or `[IMPROVEMENT]`.

---

## Step 4 — Classify each finding

Split the consolidated list into two buckets:

### AUTO-FIX
Apply without asking the user. Criteria — the fix is unambiguous and the user will almost certainly agree:
- Mechanical rule violations with a single correct fix (wrong subtask title format, missing `**Files:**`, missing verify command, filename used as title, pre-emptive exclusion of a non-existent file)
- Dead or stale cross-references (wrong subtask numbers, broken `See RES000N` pointers)
- Session DSL chaining violations (sequential `await session.X()` calls instead of a chain)
- Test ordering violations (mock tests listed before integration or backend tests, per PLANNING.md ordering rule)
- `/* v8 ignore */` annotations that are provably reachable in `convex-test` (branch can be hit via `testClient.run()` + delete or insert)
- Missing tsconfig exclusions when the pattern is already established elsewhere in the plan
- Stale task counts in verify steps (e.g. "7 tests" when the plan now has 8)
- Decisions stated without an alternative when the alternative is obvious and non-controversial

### CONSULT
Ask the user. Criteria — a decision is needed, preferences differ, or there is a genuine trade-off:
- Multiple valid approaches with real trade-offs between them
- A finding where the user has already expressed a preference but reviewers flag a concern with it
- Scope or architecture changes (splitting/collapsing tasks, changing what is tested)
- Any finding where fixing it could introduce a regression or change observable behaviour
- Anything the agent is not confident about

---

## Step 5 — Execute AUTO-FIX items in a background subagent

Launch a single general-purpose subagent (`run_in_background: true`) with:
- The plan file path
- The full list of AUTO-FIX items with their concrete fixes
- Instruction to edit the plan file directly, applying all fixes

Tell the user: "Auto-fixing N unambiguous issues in the background while we go through the items that need your input."

Proceed immediately to Step 6 — do not wait for the subagent to finish.

---

## Step 6 — Consult the user on CONSULT items, one at a time

For each CONSULT item, use AskUserQuestion to present exactly one question. Include:
- A one-line summary of the finding
- Why it matters
- The concrete options (2–4 choices, each with a one-sentence description of the consequence)

Wait for the user's answer before presenting the next item.

After each answer:
- If the answer implies a plan edit, note it (collect all user-directed edits; apply them in a follow-up subagent after all questions are done, or apply them inline if small)
- If the answer is "skip" or "leave as is", record that and move on

---

## Step 7 — Apply user-directed edits

Once all CONSULT items are resolved, apply any edits the user's answers require — either inline or via a subagent. Wait for the background auto-fix subagent from Step 5 to finish first if it hasn't already.

---

## Step 8 — Summary

Report:
- What was auto-fixed (list of items, one line each)
- What was resolved through consultation and how
- Any items the user chose to skip

End with: "Plan updated. X auto-fixed, Y resolved via consultation, Z skipped."