---
name: siraj-openspec-stress-test-tasks
description: Stress-test a locked tasks.md against a simulated implementer before any code is written. Dispatches N parallel Plan-agents (1/2/3 model fan-out, default 2 = Sonnet + Haiku) to produce implementation plans for all commits and surface real gaps — points where the implementer would have to guess. Convergent gaps (flagged by ≥2 agents) and sharp single-model gaps from Sonnet/Opus get auto-applied to source docs or tasks.md; low-priority and Haiku-only items are surfaced for the user only when judgment matters. Use after `siraj-openspec-create-design` produces design.md + tasks.md and after `siraj-openspec-review-change` clears, before `siraj-openspec-execute-plan` begins implementation. Also invoke when the user says "stress-test the tasks", "are the tasks ready for implementation", "/siraj-openspec-stress-test-tasks". Distinct from `siraj-openspec-review-change` (which audits cross-doc consistency, not implementability). Defaults to the most-recently-modified change folder.
---

# Stress-test tasks.md against a simulated implementer

This skill fills the gap between "design + tasks locked" and "start
implementing." It asks the question `siraj-openspec-review-change` does not:
*given everything written, can an implementing agent produce a correct plan,
or does it still have to guess?* If the implementer would guess, the gap
belongs in a source doc or as a pointer in tasks.md — better surfaced now
than discovered during execute-plan.

**Where this fits:** Stage 5 of the siraj-openspec pipeline — second of two pre-implementation gates. Runs after `siraj-openspec-review-change` clears, before per-commit work begins. See `siraj-openspec-flow` for the full pipeline diagram and disambiguation between this skill (tasks.md implementability), review-change (cross-doc consistency), and stress-test-plan (per-commit plan clarity).

## When to use

- `siraj-openspec-review-change` has cleared and the change is otherwise ready to implement.
- The user says "stress-test the tasks", "are the tasks ready for implementation", or invokes the skill explicitly.
- Proactively after review-change clears; phrase the suggestion as *"Review passed. Want me to stress-test against a simulated implementer before you start execute-plan?"* — take a no gracefully.

## When NOT to use

- `tasks.md` is mid-author — the fanout cost is wasted if the artifact isn't stable.
- You want cross-doc consistency audit (not implementability) — use `siraj-openspec-review-change`.
- You want to validate a per-commit plan empirically — use `siraj-openspec-stress-test-plan`.
- You want post-implementation verification — use `/siraj-openspec:verify`.

## Step 1 — Discover the change folder

If the user named a change, use it. Otherwise:

```bash
ls -td openspec/changes/*/ 2>/dev/null | head -5
```

Pick the most-recently-modified. Confirm if multiple recent candidates.

## Step 2 — Locate artifacts and supporting docs

The agents must read:

- `openspec/changes/<name>/proposal.md`
- `openspec/changes/<name>/design.md`
- `openspec/changes/<name>/specs/<capability>/spec.md` (all of them)
- `openspec/changes/<name>/tasks.md`
- `openspec/conventions.md` (if present — typically thin or absent; conventions live in `siraj-openspec-flow` skill)
- `openspec/config.yaml` (if present — optional)
- `CLAUDE.md` / `AGENTS.md` / `GEMINI.md` (whichever exists)
- `docs/testing.md` or equivalent
- The `siraj-openspec-flow` skill itself, for the canonical numbering / scenario title / tasks.md commit-block conventions (subagents should treat it as authoritative for shape questions)

If tasks.md is absent, stop and say so — this skill needs tasks.md to
stress-test.

## Step 3 — Choose model fan-out

Three modes. Default is **2 agents**. Confirm with the user only if it is
not obvious from context.

| Fan-out | Models | When to use |
|---|---|---|
| **1 agent** | Sonnet | Quick sanity pass after a small tasks.md tweak. |
| **2 agents** | Sonnet + Haiku | Standard pre-implementation gate. Cheap + divergent perspectives. |
| **3 agents** | Sonnet + Haiku + Opus | Template-setting changes (first verb in a rewrite, foundational APIs); diminishing returns past 3. |

**Why Sonnet + Haiku for the default 2:** Sonnet is the sharpest format
critic; Haiku is surprisingly sharp on edge-case gaps (blank-line rules,
silent-skip assertions, partial-state observations). Opus's plans are
longer and more dilute; it earns its keep only when downstream work will
inherit the change's conventions.

## Step 4 — Dispatch agents in parallel

All agents in a **single message** for parallel execution. Use the `Plan`
subagent type with explicit `model:` override per call. Identical prompt
across agents — only the model differs.

The prompt body (template; substitute file paths from Step 2):

```
You are simulating a coding agent picking up the <change-name> OpenSpec
change. Your job is **not** to write code — produce implementation plans
for all commits in tasks.md and give critical format feedback.

## What to read first (in this order)

<list of absolute paths from Step 2>

Also skim the implementation directory to know current state.

## Report shape

**(1) Per-commit plans — N sections, ≤120 words each.** For each commit:
file paths touched, key signatures/constants, test names. Just enough to
prove understanding of the slice; no full code shapes.

**(2) Real gaps — single combined list across all commits.** Only items
where you had to guess, infer, or extrapolate from outside the docs. For
each: what the gap is, what you assumed, which doc should have answered
it, suggested one-line pointer to close it. Be ruthless: don't pad.

**(3) Format critique — ~400 words.** Did the tasks.md shape give you the
signal you needed? What was redundant, missing, or hard to parse? What
would you change? Be candid; this is a stress test.

Constraints:
- Do NOT write or edit any files.
- Do NOT look at reference-only branches named in CLAUDE.md.
```

## Step 5 — Triage and apply

Once all agents return, classify every finding. The triage rule prioritises
applying clearly-correct fixes over asking the user about trivialities.

### Auto-apply (do not ask the user)

- **Convergent gaps** flagged by ≥2 agents.
- **Sharp single-agent gaps** from Sonnet or Opus when the fix is one line
  AND the gap is in a doc the agent could reasonably have been expected to
  find. Haiku-only gaps need a higher bar (Haiku's catches are sometimes
  edge-case noise; verify before applying).
- **Convergent format critique** — promote any unanimous structural finding
  into the `siraj-openspec-flow` skill (canonical home for tasks.md
  per-commit shape, numbering, scenario titles, etc.) as a refinement of
  the relevant section.

### Surface for decision

- Single-agent gaps where the fix involves a meaningful design choice
  (e.g., adding a new Decision, locking a wording the spec deliberately
  left open, restructuring a commit).
- Format critique that suggests a structural change to the convention
  itself, not just a refinement.

### Dismiss with reason

- Gaps the agent flagged but are actually pinned somewhere the agent did
  not look (e.g., a rule that lives in testing.md but the agent only read
  tasks.md). Apply a tasks.md pointer if the gap was reasonable to
  surface; otherwise dismiss.
- Implementation-detail gaps (e.g., "how to construct a synthetic
  directory layout") — those belong in the test file, not in
  source-of-truth docs.

### Where to apply

| Gap type | Patch where |
|---|---|
| Test-assertable wording with no normative source | `design.md` Smaller choices |
| Missing behavioural detail (what happens when X) | `spec.md` requirement or `design.md` Decision |
| Cross-verb test conventions | `docs/testing.md` |
| Single-commit pointer / clarification | `tasks.md` only |
| Structural format guidance flagged by ≥2 agents | `siraj-openspec-flow` skill (canonical home) |

Report applied fixes in a short summary. Do NOT ask the user to apply
mechanical fixes — apply them. Ask only when judgment is required.

## Step 6 — Optional confirmation pass

If many fixes landed (≥5 source-doc edits, or ≥2 new Decisions / Smaller
choices), optionally re-run with a **1-agent** Sonnet pass to confirm
closure. Otherwise the skill is done.

Do **not** loop more than twice in one session. Diminishing returns; if
gaps persist after two rounds, the underlying spec or design probably has
a real ambiguity the user must resolve.

## Step 7 — When to re-trigger review-change

Stress-test fixes can introduce drift the consistency review did not see.
Re-run `siraj-openspec-review-change` if any of:

- **≥2 source docs** were edited in the apply phase (e.g., design.md AND
  testing.md, or design.md AND spec.md). Multi-doc edits are where
  contradictions creep in.
- **A new term or renamed entity** was introduced (new exception class,
  new helper name, new flag, renamed concept). Renames are the #1 source
  of stale references.
- **A new Decision or Smaller choice** was added that earlier decisions
  might now contradict.

If none of the above triggers fire, the apply phase is the last gate
before `siraj-openspec-execute-plan`.

## What this skill does not do

- Does not write code.
- Does not commit.
- Does not check implementation against artifacts (that's
  `openspec-verify-change`).
- Does not check cross-doc consistency (that's
  `siraj-openspec-review-change` — orthogonal lens, different findings).
- Does not author tasks.md (that's `siraj-openspec-create-design` plus
  user editing).
- Does not run A/B format experiments — the tasks.md shape is fixed in
  `siraj-openspec-flow` (canonical home). A/B was a one-time meta-exercise;
  routine stress-testing reads the single locked shape.

## Cost

| Fan-out | Wall time | Tokens (combined) |
|---|---|---|
| 1 agent | ~30 s | ~20–30k |
| 2 agents | ~60 s | ~50–70k |
| 3 agents | ~90 s | ~80–110k |

One-time per implementation push, not per edit.

---

**Next:** Stage 6 of the pipeline begins — `/siraj-openspec-create-plan` to author the per-commit plan for commit 1. See `siraj-openspec-flow` for the full Stage 6 loop (create-plan → review-document → stress-test-plan → execute-plan, repeated per commit).
