---
name: siraj-openspec-next
description: >
  Single entry point for the siraj-openspec pipeline. Reads disk state for the
  current OpenSpec change, identifies which artifact or step is needed next,
  and invokes the right verb — either upstream CLI (`/siraj-openspec:new`,
  `/siraj-openspec:continue`) or a siraj custom skill (`create-design`,
  `create-plan`, `execute-plan`). Defaults artifact authoring to OpenSpec's
  `:continue` and overrides only where a siraj skill takes over entirely.
  Use this when you want "what do I do next?" without remembering the
  pipeline state table. Trigger on `/siraj-openspec-next`, "what's next",
  "where am I", or whenever a fresh agent session opens on an OpenSpec
  change folder.
---

# OpenSpec Siraj — Next

Single entry point for the per-change + per-section pipeline. Reads disk
state, picks the next verb, invokes it. No memorisation of the state
table required.

**Why this skill exists:** the siraj-openspec pipeline has ~8 verbs
(upstream CLI + custom skills) that fire in a specific sequence per
artifact and per section. Asking the user to remember which verb
comes next is friction. This skill collapses that mental model to one
command: `/siraj-openspec-next`.

---

## When to use

- Starting a session on an OpenSpec change and unsure which verb fires next.
- `/siraj-openspec-next` typed explicitly.
- The user says "what's next", "where am I in this change", "what should
  I run next" on a change folder.
- Proactively at session start when an OpenSpec change folder is the
  obvious active context — print the orientation banner and the next
  verb, then wait.

## When NOT to use

- The user explicitly asks for a SPECIFIC verb (e.g., "create the plan
  for §3" → go straight to `siraj-openspec-create-plan`, no need to
  detect state).
- The change is finished and all the user wants is a verify/archive
  decision — those are explicit CLI verbs the user will type directly.
- You're mid-flow inside another skill (e.g., already running
  `execute-plan`) — don't recurse.

## What this skill does NOT do

- Does not author any artifact itself — it routes to the verb that does.
- Does not auto-invoke the next verb without showing the user what's
  about to run; user gets one chance to redirect.
- Does not commit anything — committing belongs to the routed-to verb.
- Does not skip steps the user has temporarily disabled (e.g., if a
  stress-test wasn't run, `next` notes it but doesn't force it).

---

## State detection

Read disk + the OpenSpec CLI to identify exactly one current state:

```
1. openspec/ exists?
   NO  → state: NO_CHANGE  → /siraj-openspec:new <name>
   YES → continue

2. Active change folder identifiable? (most-recently-modified, or
   user-named)
   NO  → list candidates via `openspec list --json`, ask user
   YES → continue with <change>

3. Inside the change folder, walk artifact existence:
   proposal.md exists?              NO → state: NO_PROPOSAL  → /siraj-openspec:continue
   specs/<cap>/spec.md exists?       NO → state: NO_SPEC      → /siraj-openspec:continue
   design.md exists?                  NO → state: NO_DESIGN    → /siraj-openspec-create-design
   tasks.md exists?                   NO → state: NO_TASKS     → /siraj-openspec:continue

4. Per-section loop: parse tasks.md for the first ## N. <subject>
   whose checkboxes are not all [x]:
   plans/<N>-<slug>.md exists?       NO → state: NO_PLAN_§N    → /siraj-openspec-create-plan
   Plan file committed?              NO → state: PLAN_UNCOMMITTED  → run create-plan §Commit step
   Impl committed (any §N tick)?     NO → state: NO_IMPL_§N    → /siraj-openspec-execute-plan
   All §N boxes ticked AND committed? continue to next section

5. All sections complete (all tasks.md boxes ticked + committed)?
   YES → state: READY_VERIFY        → /siraj-openspec:verify, then :archive, then :sync
```

The user can override at any step by naming an explicit verb. This
skill's role is the default routing when the user is mid-pipeline
and wants the "what now?" answer.

---

## The orientation banner

After detecting state, print one banner. Then **stop and wait** —
the user owns the loop; this skill never auto-invokes the routed-to
verb.

```
──────────────────────────────────────────────────────
  OpenSpec-Siraj — active: <change-name>

  Last completed: <last artifact / commit on disk>
  Current state:  <STATE_NAME from §State detection>
  Next verb:      <verb name>
  Why:            <one-line — what state on disk triggered this routing>

  Run that verb? (y / pick a different verb / stop)
──────────────────────────────────────────────────────
```

Wait for the user's response. Do NOT auto-fire the verb.

---

## Routing table (the override map)

| Disk state | Default verb | Why this routing |
|---|---|---|
| No `openspec/` dir | `/siraj-openspec:new <name>` | Upstream CLI scaffolds the folder + proposal skeleton |
| `proposal.md` only | `/siraj-openspec:continue` | Upstream produces spec.md in its default shape; no siraj override needed |
| Proposal + spec, no `design.md` | `/siraj-openspec-create-design` | **Siraj override** — Format A walk-through replaces upstream's generic decisions section |
| Proposal + spec + design, no `tasks.md` | `/siraj-openspec:continue` | Upstream produces tasks.md in minimal shape — accepted as-is per the [[skill-authoring]] principle that tasks.md is the thin index |
| Tasks exist, no `plans/<N>-*.md` for next unticked § | `/siraj-openspec-create-plan` | **Siraj override** — 8-section canonical plan shape |
| Plan exists for §N, not yet committed | create-plan's §Commit step | Plan commit is its own pipeline step, distinct from impl |
| Plan committed, no impl for §N | `/siraj-openspec-execute-plan` | **Siraj override** — per-section RED-GREEN-coverage flow with explicit user-approved impl commit |
| All `tasks.md` boxes ticked + committed | `/siraj-openspec:verify` → `:archive` → `:sync` | Upstream CLI verbs are the canonical finish |

**Routing principle:** delegate to upstream `:continue` by default; route
to a siraj skill ONLY where a custom shape, walkthrough, or per-section
discipline takes over entirely. See the orchestrator `siraj-openspec-flow`
for the full pipeline diagram + the rationale for each override.

---

## Optional pre-flight: health check

Before routing, OPTIONALLY run the baseline test + coverage scan from
`docs/testing.md §Coverage`:

```
uv run pytest --cov --cov-branch --cov-report=term-missing
```

Skip if:
- Just-after a successful impl commit (the test suite was green ~minutes
  ago; redundant to re-run).
- The user explicitly typed an artifact-authoring verb (no test infra
  involved).

Run if:
- Fresh session start.
- Routing to `siraj-openspec-execute-plan` (its own Session Start does
  the same scan; running here gives the user advance warning if the
  baseline is broken).

If RED or unexpected coverage exclusions: surface to user BEFORE
showing the routing banner, per the [[skill-authoring]] escape-hatch
policy.

---

## Anti-patterns

| ❌ Don't | ✅ Do |
|---|---|
| Auto-invoke the routed-to verb | Print the banner; wait for user 'y' or a different verb |
| Skip state detection because "it's obvious" | Always read disk first; assumptions about state lose to silent drift |
| Skip the upstream `:continue` delegation for spec/tasks | Upstream owns those artifacts unless a siraj override exists |
| Re-implement what `siraj-openspec-flow`'s state table already encodes | This skill IS the state table made executable; keep the same routing in sync |
| Run the test baseline scan on every invocation | Skip when redundant (just after impl commit) |
| Halt on a state the table doesn't cover | Fall back to "what's the change folder showing me? Tell me what verb you want" — graceful unknown |

---

## Where this fits

Meta to the whole pipeline. Not a stage; the entry that picks the right
stage. See `siraj-openspec-flow` for the canonical pipeline diagram and
the underlying state table this skill executes.

**Next:** whichever verb the routing table picked, run that next.
