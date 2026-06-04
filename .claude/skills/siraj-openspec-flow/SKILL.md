---
name: siraj-openspec-flow
description: >
  The OpenSpec-Siraj pipeline overview. Canonical orchestrator for the
  siraj-openspec-* family of skills (create-design, create-plan,
  review-change, stress-test-tasks, stress-test-plan, execute-plan) plus
  the /siraj-openspec:* CLI commands (new, verify, archive, sync, etc.).
  Use this skill to orient on the full pipeline (proposal → spec →
  design → tasks → per-commit plans → execution → verify → archive), to
  figure out which verb fires next given an OpenSpec change folder's
  current state, or as the canonical reference for numbering, scenario
  titles, cross-file references, renumbering, tasks.md commit-block
  shape, and artifact-tree conventions. Trigger on phrasing like "what's
  the openspec workflow", "where am I in this change", "walk me through
  the openspec pipeline", "start a new openspec change", "how do I
  author tasks.md", or whenever the user pauses on an
  openspec/changes/<X>/ folder and asks what's next. For lightweight
  greenfield app/feature builds use feather-flow instead; the two are
  peer pipelines, not variants. Defaults to the most-recently-modified
  change folder if no name is given.
---

# OpenSpec-Siraj Flow

End-to-end pipeline for OpenSpec changes with full review rigor. Two
families of verbs cooperate:

- **Project-local `/siraj-openspec:*` commands** — thin wrappers around
  the `openspec` CLI from `@fission-ai/openspec`. Handle artifact
  lifecycle (create, verify, archive, sync).
- **Global `siraj-openspec-*` SKILL skills** — author the artifacts
  themselves and run the review gates between them.

**Sibling pipeline:** `feather-flow` covers lightweight greenfield work
(project-level brainstorm → 15-section spec → mini-plan + execute). Use
feather-flow when there's no spec yet and you're exploring. Use this
pipeline when behavior is precise enough to spec, and you want the
review gates (create-design / review-change / stress-tests) that catch
ambiguity before it becomes code.

---

## When invoked

Three invocation patterns. **Always quietly check what's in
`openspec/changes/` first** — that tells you the pipeline state and
routes the response.

| Input shape | Behaviour |
|---|---|
| **Bare** (skill invoked alone) | If a change folder exists with active state → show the **state-aware orientation** (below). If `openspec/` is empty or absent → show the **pipeline diagram**. Do not ask "what are you working on" — the filesystem answers. |
| **Help-shaped** ("how does this work?", "walk me through the openspec pipeline") | Show the **pipeline diagram**. Even with active state, the user is asking to learn, not to resume. |
| **Work description** ("start a new openspec change for X", "I want to add Y as a verb") | Route directly to `/siraj-openspec:new` with a derived kebab-case name. Do NOT show the pipeline diagram first — they're past it. |

**Quietly** means: don't display `ls` output to the user. State checks
are for routing, not user-facing chatter.

---

## The pipeline

```
                            ┌─────────────────────────────────────────────┐
                            │ /siraj-openspec:*   — thin CLI wrappers     │
                            │ siraj-openspec-*    — authoring + gates     │
                            └─────────────────────────────────────────────┘

STAGE 0 — Optional thinking
        /siraj-openspec:explore
        (think through the problem before committing to a change)

STAGE 1 — Create the change folder
        /siraj-openspec:new <change-name>
        → openspec/changes/<X>/proposal.md       (scaffold)
        → openspec/changes/<X>/specs/<cap>/spec.md   (skeleton)

STAGE 2 — Author spec
        /siraj-openspec:continue  (or invoke via /siraj-openspec-next)
        → openspec/changes/<X>/specs/<cap>/spec.md
        (OpenSpec produces the spec; user reviews + redirects per
         §Numbering + §Scenario title shape conventions if needed)

STAGE 3 — Walk design decisions
        /siraj-openspec-create-design
        → design.md (Format A — Question / Alternatives / Recommendation)
                    │
                    ▼
        /review-document on design.md
        (7-lens structural polish)

STAGE 4 — Author tasks.md
        /siraj-openspec:continue  (or invoke via /siraj-openspec-next)
        → openspec/changes/<X>/tasks.md
        (OpenSpec produces tasks.md in the thin shape — see
         §tasks.md per-commit shape; no post-transformation)

STAGE 5 — Pre-implementation gates
        /siraj-openspec-review-change
        (3-subagent cross-doc audit)
                    │
                    ▼
        /siraj-openspec-stress-test-tasks
        (N-agent fanout; auto-applies convergent fixes)
                    │
                    ▼  (maybe re-run review-change if many edits landed)

STAGE 6 — Per-section loop (repeat for each ## N. <subject> in tasks.md)
        /siraj-openspec-create-plan
        → plans/<N>-<slug>.md            (8-section shape)
                    │
                    ▼
        /review-document on the plan
                    │
                    ▼
        /siraj-openspec-stress-test-plan
        (optional — load-bearing sections only; pushes back to create-plan)
                    │
                    ▼
        git commit (plan)
        "<change>: plan for <N>. <subject>"
        (plan file only; user-approved; landed by create-plan §Commit)
                    │
                    ▼
        /siraj-openspec-execute-plan
        (per-section flow: write all §N tests → RED → write impl →
         GREEN → coverage → tick all §N boxes; halts at pre-flight
         if the plan isn't committed yet; Curated = Sonnet+Opus
         parallel default)
                    │
                    ▼
        git commit (impl)
        "<change>: implementation for <N>. <subject>"
        (impl files + tasks.md ticks; user-approved)

STAGE 7 — Verify + archive
        /siraj-openspec:verify
        (checks implementation matches artifacts)
                    │
                    ▼
        /siraj-openspec:archive
                    │
                    ▼
        /siraj-openspec:sync
        (delta specs → openspec/specs/)
```

### Avoided CLI verbs (project-local discipline)

| Verb | Why avoided |
|---|---|
| `/siraj-openspec:apply` | Run-to-blocker loop. **Replaced** by `siraj-openspec-execute-plan`'s per-section flow with explicit user approval at the plan-commit (via create-plan) and impl-commit (via execute-plan) gates. |
| `/siraj-openspec:propose` | Auto-generates ALL artifacts in one step; conflicts with the one-at-a-time discipline. Use `/siraj-openspec-next` instead, which delegates to `:continue` artifact-by-artifact. |
| `/siraj-openspec:ff` | Same — fast-forward variant of `:propose`. |

`/siraj-openspec:continue` is the **default produce-the-next-artifact verb** that `siraj-openspec-next` delegates to for spec.md and tasks.md authoring. It is NOT in the user-facing autocomplete (hidden via the project-local `commands-archive/` move) because `next` is the canonical entry; users type `/siraj-openspec-next`, not `:continue` directly.

`/siraj-openspec:bulk-archive`, `/siraj-openspec:onboard`, and `/siraj-openspec:sync` are situational — bulk-archive for cleanup, onboard for a new collaborator's tour, sync for moving delta specs after archive. None is part of the regular per-change flow; all three are hidden from autocomplete by default.

---

## State-aware orientation

When invoked bare on a project with an active change, derive state from
which artifacts exist on disk and print the banner matching the next
verb:

| State on disk | Next verb |
|---|---|
| No `openspec/` at all | `/siraj-openspec:new <name>` |
| `openspec/` exists, no `changes/<X>/` | `/siraj-openspec:new <name>` |
| `proposal.md` only, no `specs/<cap>/spec.md` | `/siraj-openspec:continue` (delegated by `/siraj-openspec-next`; produces spec.md) |
| `proposal.md + spec.md` exist, no `design.md` | `/siraj-openspec-create-design` (siraj override — Format A walk-through) |
| `design.md` exists, no `tasks.md` | `/siraj-openspec:continue` (delegated by `/siraj-openspec-next`; produces tasks.md in thin shape) |
| `tasks.md` exists, not yet reviewed | `/siraj-openspec-review-change` (optional) |
| Reviewed, not yet stress-tested | `/siraj-openspec-stress-test-tasks` (optional — rare past format-finalization) |
| Stress-tested, no `plans/<N>-*.md` for next unticked section | `/siraj-openspec-create-plan` |
| Plan exists for next section, not yet polished | `/review-document` then (optional) `/siraj-openspec-stress-test-plan` |
| Plan polished but not yet committed | create-plan §Commit the plan step (one-file commit, plan only) |
| Plan committed, no implementation for that section | `/siraj-openspec-execute-plan` |
| All sections done (all `tasks.md` boxes ticked) | `/siraj-openspec:verify` → `:archive` → `:sync` |

**Don't memorise the table** — invoke `/siraj-openspec-next`, which reads disk state and prints the orientation banner naming the next verb. The table is the truth `next` executes against.

Banner shape:

```
──────────────────────────────────────────────────────
  OpenSpec-Siraj — active: <change-name>

  Last completed: <verb> on <date>
  Next verb:      <verb>
  Why:            <one-line — what state on disk triggered this>
──────────────────────────────────────────────────────
```

After showing the banner, **stop and wait**. Never auto-invoke the next
verb. The user owns the loop.

---

## Artifact tree

```
openspec/
  changes/
    <change-name>/
      proposal.md             ← /siraj-openspec:new scaffolds
      specs/
        <capability>/
          spec.md             ← hand-authored; numbering per §Numbering
      design.md               ← /siraj-openspec-create-design writes
      tasks.md                ← hand-authored; shape per §tasks.md per-commit shape
      plans/
        1-<slug>.md             ← /siraj-openspec-create-plan writes
        2-<slug>.md
        ...
  specs/
    <capability>/
      spec.md                 ← /siraj-openspec:sync moves deltas here after archive
```

A change folder is the unit of work. One change = one PR's worth of
behavior. Multiple changes can be in flight in parallel.

---

## Git commit conventions

Each step in the pipeline lands as one commit. Commit messages name the
change and the artifact (or per-commit operation):

| Pipeline step | Commit message |
|---|---|
| Proposal | `<change-name>: proposal` |
| Spec | `<change-name>: spec` |
| Design | `<change-name>: design` |
| Tasks | `<change-name>: tasks` |
| Per-commit plan (create-plan output) | `<change-name>: plan for <N>. <section subject>` |
| Per-commit implementation (execute-plan output) | `<change-name>: implementation for <N>. <section subject>` |

Where `<N>` is the tasks.md section number and `<section subject>`
mirrors the `## N. <subject>` heading verbatim. Example:

```
add-feather-init: proposal
add-feather-init: spec
add-feather-init: design
add-feather-init: tasks
add-feather-init: plan for 1. feather.yaml in CWD
add-feather-init: implementation for 1. feather.yaml in CWD
add-feather-init: plan for 2. feather.yaml idempotency
add-feather-init: implementation for 2. feather.yaml idempotency
```

Squash any rework / typo fixes into the home commit before pushing
(`git commit --fixup=<sha>` + `git rebase -i --autosquash`) so the
final history reads as one commit per pipeline step.

---

## Numbering convention

Requirements and scenarios carry position numbers inside their headings:

```markdown
### Requirement: 1. <Name>

#### Scenario: 1a. <Scenario name>
- **WHEN** ...
- **THEN** ...

#### Scenario: 1b. <Scenario name>
...

### Requirement: 2. <Name>

#### Scenario: 2a. <Scenario name>
```

- **Requirements:** integers from `1`, monotonic within a spec file.
- **Scenarios:** `<requirement-number><letter>`, lowercase from `a`.
- **Separator:** period (`.`) between number and name.
- **Scope:** numbers reset to `1` in every spec file (per capability).

**Why inside the heading.** OpenSpec parses requirements by matching
`### Requirement: <name>` and uses `<name>` as canonical ID. Putting the
number inside the name keeps it visible in any markdown render AND
honors the parsing contract.

**Why local-per-spec.** Inserting at `init.3` shifts only `init`'s
subsequent numbers. Other capabilities untouched. Global numbering would
propagate the edit across every later spec file in the project.

### Renumbering protocol

When inserting / removing / reordering requirements:

1. **Edit the spec.md** with new numbering applied to every affected
   requirement + its scenarios.
2. **Update any references** in the same change folder — `tasks.md`,
   `design.md`, sibling spec files, test names that embed numbers.
3. **Commit the renumber as one atomic change** — e.g.,
   `spec(init): renumber after inserting req 3`. Reviewers see one
   consistent diff.
4. **`/siraj-openspec:sync` treats renumbers as renames.** Expect a
   RENAME block for every renumbered requirement when the change is
   eventually synced into main specs. Manageable cost.

Early-stage specs renumber often; mature specs almost never. The
renumber cost is concentrated in the design window where churn is
expected.

---

## Scenario title shape

Every scenario title states what was *asserted*, not just what triggered
the assertion. (Requirement titles are noun phrases naming a capability
— no such rule.)

### The rule

```
<subject> <verb> <outcome>
```

When the trigger needs disambiguation, append `, if <precondition>` or
`, when <condition>` — after the outcome, not before.

**Good titles:**

```markdown
#### Scenario: 1a. Init with no arg stamps files into CWD, if empty
#### Scenario: 3a. Default mode writes pyproject.toml with PyPI version pin
#### Scenario: 4b. Existing .env is preserved silently
```

**Weak titles (avoid):**

```markdown
#### Scenario: 1a. Init in current directory       ← WHEN only, no THEN
#### Scenario: 6a. Default mode confirmation        ← label, no assertion
#### Scenario: 4a. Create empty .env when absent    ← imperative mood
```

### Three principles

1. **Subject + verb + outcome.** Every title names the actor, action,
   and result. A bare WHEN or a bare label fails this.
2. **Self-contained — name the artifact.** A reader scanning a flat
   list of titles should know which file/feature each scenario
   concerns. Say `feather.yaml`, `pyproject.toml`, `.env` in the title —
   unless the scenario is genuinely artifact-agnostic.
3. **Declarative mood throughout one spec.** Pick declarative
   (`X is preserved`, `X writes Y`). Imperative
   (`Preserve X`, `Create Y`) reads like a TODO, not an assertion.

### Litmus test

Before committing a title, read it as `test_<title>_PASSED`. If you can
answer "what was verified?" from that line alone, the title is good. If
you have to open the spec, the title is too thin — revise until you can.

This matters because test names mirror scenario titles verbatim (each
project's `docs/testing.md` enforces this). The title becomes the
pass/fail line in pytest output:

```
test_init_with_no_arg_stamps_files_into_cwd_if_empty PASSED
test_default_mode_confirmation PASSED              ← what was verified?
```

### Quick reference

| Pattern | Example |
|---|---|
| `<actor> <verb> <artifact>` | `3a. Default mode writes pyproject.toml with PyPI version pin` |
| `<artifact> <state-verb>` | `2b. feather.yaml present is preserved` |
| `<actor> <verb> <outcome>, <condition>` | `1a. Init with no arg stamps files into CWD, if empty` |
| `<actor> <verb> <outcome>` (failure path) | `3c. --dev without local checkout exits non-zero` |

---

## Cross-file references

Use `<capability>.<N><letter>`:

- `init.1a` — scenario `1a` in `specs/init/spec.md`
- `source-add.3` — whole requirement `3` in `specs/source-add/spec.md`
- `init` — the whole capability (the directory name)

In-file refs just use the position (`1a`, `3b`). Cross-file refs should
be rare; if you reach for them often, the requirements probably belong
in the same spec.

---

## tasks.md per-commit shape

`tasks.md` is the **thin index** — one section per git commit, each section just a heading + the checkbox list of test+impl tasks for that commit. **NO per-commit detail** (Before/After, pattern paragraphs, design decisions/hints, scope guards, "Lands…" framing). All that depth lives in the per-commit plan file at `plans/<N>-<slug>.md` and is regenerable from spec + design — see `siraj-openspec-create-plan`.

This is upstream OpenSpec's `:continue` default output shape; the siraj pipeline accepts it as-is and does NOT post-transform. No `create-tasks` custom skill exists; `:continue` is enough.

The standing cadence (write tests → RED → impl → GREEN → coverage) lives in each project's `docs/testing.md` §2 and does NOT appear per-commit — restating it would be noise.

### The shape

```
## N. <subject>

- [ ] N.1 Write test for <spec-ref> — <verbatim scenario title from spec.md>.
- [ ] N.2 Write test for <spec-ref> — <verbatim scenario title from spec.md>.
- [ ] N.M Implement <high-level impl shape> so N.1 + N.2 pass.
```

Per commit: one `## N. <subject>` heading, blank line, K checkbox rows. That's the whole shape.

### Rules

1. **H2 heading** mirrors the git commit subject. Format: `## N. <subject>` (NO `Commit N — ` prefix). `N` matches the Nth commit of this change; the same subject appears in the commit message per §Git commit conventions.
2. **Test rows** name the spec ref (e.g., `init.1a`) + paste the scenario title verbatim from `spec.md` (no paraphrase). One row per scenario the commit pins.
3. **Impl row** uses one-noun granularity for what changes — function name, pattern name, or short description. End with `so N.1 + N.2 pass.` to reference the test rows. For commits with no spec scenarios (e.g. a smoke-test commit), the impl row stands alone.
4. **No `### Tasks` subheading.** Checkboxes sit directly under the H2 — the parser doesn't care, the reader doesn't need it, and the upstream `:continue` default doesn't produce one.
5. **No per-commit narrative.** Before/After, "Lands…" framing, design decisions/hints, pattern paragraphs, scope guards — all of these live in the plan file (`plans/<N>-<slug>.md`), NOT in `tasks.md`. Plans get the depth; tasks.md gets the index.
6. **No restating of the standing cadence.** Don't echo "RED → GREEN → coverage" in commit blocks. That's a project-level invariant in `docs/testing.md` §2.
7. **Cross-doc references** use slug-based form for scenarios/requirements, numeric form only for design.md Decisions — but those refs belong in the plan file, not tasks.md. See `[[skill-authoring]]` §"Cross-doc reference convention" for the policy.

### Why this shape

Empirically validated via stress tests: planning agents (Sonnet + Opus, 4 runs) reconstructed rich per-commit plans from stripped tasks.md (commit header + checkboxes only) + spec + design + the canonical plan-shape skill. "Before/After paragraphs", "Lands…/Introduces…" framing, and "Design decisions/hints" sub-sections in tasks.md were ALL reconstructible by the planning agent — duplication was paying no rent.

Implication: when duplication exists between tasks.md and the per-commit plan, TRIM tasks.md. The plan is the implementing agent's source of truth; tasks.md is cross-commit checkbox traceability only.

See [[skill-authoring]] §"Tasks.md is the index; plan is the source of truth" for the broader principle this section enforces.

---

## Sibling-skills graph

| Skill / verb | Stage | One-line job |
|---|---|---|
| `siraj-openspec-next` | meta | Single entry point — reads disk state, picks the next verb, prints orientation banner, waits for user's go |
| `/siraj-openspec:explore` | 0 | Think through ideas before committing to a change |
| `/siraj-openspec:new` | 1 | Creates `openspec/changes/<X>/` with proposal + skeleton spec |
| `/siraj-openspec:continue` | 2, 4 | Produces spec.md (Stage 2) and tasks.md (Stage 4) — delegated by `next` because no siraj custom override exists for these artifacts |
| `siraj-openspec-create-design` | 3 | Walks design decisions in Format A → `design.md` (siraj override of `:continue`'s default decisions section) |
| `review-document` | 3, 6 | 7-lens structural pass on any agent-produced doc |
| `siraj-openspec-review-change` | 5 | 3-subagent audit: consistency / right-sizing / structural lenses |
| `siraj-openspec-stress-test-tasks` | 5 | N-agent plan fanout; finds tasks.md gaps; auto-applies convergent fixes |
| `siraj-openspec-create-plan` | 6 | Authors per-commit plan files (canonical 8-section shape) |
| `siraj-openspec-stress-test-plan` | 6 | N-agent code fanout; reads divergences as plan-gap signals; pushes back to create-plan |
| `siraj-openspec-execute-plan` | 6 | Per-section flow (write all §N tests → RED → impl → GREEN → coverage → tick all §N boxes → commit), Curated default (Sonnet+Opus parallel worktrees); halts at pre-flight if §N's plan isn't committed |
| `/siraj-openspec:verify` | 7 | Validates implementation matches artifacts |
| `/siraj-openspec:archive` | 7 | Finalizes the change |
| `/siraj-openspec:sync` | 7 | Moves delta specs into `openspec/specs/` |
| `parallel-implement-compare` | 6 | Composable mechanic invoked by stress-test-plan and execute-plan Curated mode |

### Disambiguation between similar-sounding skills

These three are often confused. They run in **sequence**, not as
alternatives:

| Skill | What it audits | When it fires | What it produces |
|---|---|---|---|
| `siraj-openspec-review-change` | Cross-doc consistency (spec ↔ design ↔ tasks contradictions, stale refs, scenario→commit mapping) | After tasks.md is hand-authored, before stress-testing | Findings categorized as Apply / Surface for decision / Dismiss |
| `siraj-openspec-stress-test-tasks` | Implementability of tasks.md (would an agent know what to do, or have to guess?) | After review-change clears, before per-commit work begins | Per-commit plans + gap-find report; convergent gaps auto-applied |
| `siraj-openspec-stress-test-plan` | Per-commit plan clarity (does an agent following the plan land strict-YAGNI correct code?) | After create-plan + review-document polish the plan; load-bearing commits only | Convergent failures + divergences as plan-refinement signals (NOT winner-picking) |

Two more often-confused pairs:

| Pair | What sets them apart |
|---|---|
| `verify-plan` (generic) vs `siraj-openspec-stress-test-plan` | verify-plan: 3 parallel agents review the plan TEXT; stress-test-plan: N agents IMPLEMENT the plan and divergences signal plan gaps |
| `/siraj-openspec:verify` (CLI) vs `siraj-openspec-review-change` (skill) | `:verify` checks IMPLEMENTATION matches artifacts (post-impl gate); `review-change` checks ARTIFACT consistency (pre-impl gate) |

---

## Discovery defaults

All `siraj-openspec-*` skills that take a change-name argument default
to the most-recently-modified change folder when none is given. The
canonical snippet:

```bash
ls -td openspec/changes/*/ 2>/dev/null | head -5
```

Pick the top one if exactly one recent candidate. Confirm with the user
if multiple recent candidates.

---

## Anti-patterns

| ❌ Don't | ✅ Do |
|---|---|
| Run `ls openspec/changes/` and dump output to the user on bare invocation | Quietly inspect state; show the orientation banner |
| Skip the orientation when bare; jump straight to "what do you want to do?" | Bare invocation = state banner first; routing comes only after a work description |
| Auto-invoke the next verb after one completes | Always stop. The user owns the loop; they say "go" to continue |
| Restate the pipeline diagram inside each sub-skill's SKILL.md | This orchestrator is the canonical home; sub-skills point here |
| Restate the numbering / title / tasks-shape conventions inside each sub-skill | Same — canonical home is here |
| Use `/siraj-openspec:apply` for production work | Use `siraj-openspec-execute-plan` (per-section flow with explicit user approval at plan-commit and impl-commit gates) instead |
| Use `/siraj-openspec:propose` or `:ff` to bootstrap a change | They auto-generate ALL artifacts at once, conflicting with the one-at-a-time discipline. Use `/siraj-openspec-next` instead — it delegates to `:new` + `:continue` (for spec/tasks) + `create-design` (for design) one artifact at a time |
| Confuse review-change with stress-test-tasks | They run in sequence; see disambiguation table |
| Treat stress-test-plan as winner-picking | Orientation is gap-finding; the code is throwaway; refinement goes back to create-plan |
| Tick a checkbox before verification passes | Per execute-plan's per-section flow, tick all §N boxes only after GREEN + coverage pass — verification is non-negotiable |
| Pick a Curated-mode winner silently under auto-mode | Always render the side-by-side and wait for the user's explicit pick |
