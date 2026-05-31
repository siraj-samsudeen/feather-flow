---
name: siraj-openspec-execute-plan
description: >
  Execute one section of an OpenSpec change's tasks.md — write all the
  section's tests, see them go RED, write the impl, see them go GREEN,
  check coverage, tick every checkbox the section contains, and ask
  the user to approve one commit for the whole section. Halts at
  session start and routes the user to /siraj-openspec-create-plan if
  the next section's plan file is missing. Consumes plans; never
  writes them. Defaults to one section per session.
---

# OpenSpec Execute Plan

One section at a time. The plan exists upstream. The user owns the loop.

**Core philosophy:** execute-plan implements; it does not plan. The
upstream verb `siraj-openspec-create-plan` authors and commits the plan
file. This skill loads that plan, runs the section's RED-GREEN-coverage
sequence, ticks every checkbox the section contains, and asks the user
to approve one commit for the whole section.

---

## Where this fits

Stage 6 of the siraj-openspec pipeline — runs AFTER `siraj-openspec-create-plan` has authored and committed the per-section plan file (and after `review-document` / `siraj-openspec-stress-test-plan` have polished it). Consumes the plan file at `openspec/changes/<change>/plans/<N>-<slug>.md`. See `siraj-openspec-flow` for the full pipeline diagram and the disambiguation between this skill and `/siraj-openspec:apply` (which this skill replaces) or `/siraj-openspec:verify` (post-implementation CLI gate).

## When to use

- A change folder has a polished plan file for the next unticked section, and you're ready to land the implementation.
- The user says "execute the next section", "implement section N", "/siraj-openspec-execute-plan", or pauses on a plan file with no impl yet.

## When NOT to use

- **The plan file for the next unticked section does not exist** — this skill halts at pre-flight; use `siraj-openspec-create-plan` first.
- The plan exists but hasn't been polished — run `review-document` and (for load-bearing sections) `siraj-openspec-stress-test-plan` first.
- Run-to-blocker bulk implementation without per-section review — use `/siraj-openspec:apply` (project-local policy avoids this; see orchestrator's "Avoided CLI verbs").
- Implementation done, checking against artifacts — use `/siraj-openspec:verify`.

## What this skill does NOT do

- **Does not author plan files.** That's `siraj-openspec-create-plan`. If the plan is missing, this skill halts and routes the user to create-plan.
- Does not validate cross-doc consistency — `siraj-openspec-review-change` does, pre-implementation.
- Does not check implementation against artifacts after the fact — `/siraj-openspec:verify` does.
- Does not commit without explicit user approval — Step 8 always asks.
- Does not push. Does not `git add -A`. Never.

---

## Execution Modes

Three modes for HOW the section's code gets written. The user picks at
session start (sticky for the session) and may override per section.
All subagent modes use `run_in_background: true` so the main chat
stays responsive while agents work.

- **Curated (default)** — Sonnet and Opus run in parallel in isolated
  worktrees, implementing the section independently. After both
  finish, the skill shows a side-by-side comparison and the user picks
  the winner (wholesale or hand-picked per file). Highest signal;
  ~2× time/cost of single-subagent mode.

- **Single subagent** — one subagent (Sonnet / Opus / Haiku) implements
  the section in a worktree. User reviews the output and accepts (or
  switches to inline for the revision). Model picked by section
  complexity:
  - **Haiku** for trivial sections (~30–60 s runtime).
  - **Sonnet** for typical sections — the in-mode default.
  - **Opus** for architecturally hard sections.

- **Inline** — the current-session agent writes the code directly in
  the working tree. No subagent, no worktree, no comparison. Fastest.

To switch mid-session: "Switch to single subagent with sonnet" /
"Go inline for this one" / "Curated for the rest." The skill confirms
the change and applies it from the next section forward.

**Auto-mode keeps Curated as default.** When the harness is in auto-mode
("Work without stopping for clarifying questions"), this skill still
spawns Sonnet + Opus in parallel — Siraj's stated preference is that
auto-mode favors quality over spend. The skill still shows the
side-by-side comparison and waits for an explicit pick rather than
auto-selecting silently. Picking a winner without showing the
comparison is never auto-mode behavior.

---

## Session Start

1. **Pick the change.** Run `openspec list --json`. If exactly one
   change has unticked tasks, auto-select. Otherwise use
   AskUserQuestion. Never guess.

2. **Identify the next unticked section.** Parse `tasks.md`; the first
   `## N. <subject>` whose `### Tasks` boxes are not all `[x]` is the
   section to implement this session.

3. **Pre-flight: does `plans/<N>-<slug>.md` exist?** If NO, **halt**
   with this banner and stop:

   ```
   ──────────────────────────────────────────────────────
     OpenSpec Execute — active: <change-name>

     Next section: ## <N>. <subject>
     Plan file expected at:
       openspec/changes/<change>/plans/<N>-<slug>.md

     ⚠ No plan file found.

     execute-plan does not author plans. Run
     /siraj-openspec-create-plan first to author and commit
     §<N>'s plan, then invoke execute-plan again.
   ──────────────────────────────────────────────────────
   ```

   Do not proceed to load context or execute. The user must run
   create-plan first.

4. **Load context once.** Read these in order:
   - `openspec/changes/<change>/plans/<N>-<slug>.md` (the plan).
   - `openspec/changes/<change>/tasks.md` — focus on §N's heading,
     `### Tasks` rows, `### Design decisions/hints` bullets.
   - `openspec/changes/<change>/specs/<capability>/spec.md` — full
     text of the scenarios §N pins.
   - `openspec/changes/<change>/design.md` — full text of the
     Decisions §N's hints reference.

   Do not re-shell on every step.

5. **Pre-flight: baseline tests + coverage clean + OpenSpec progress snapshot.** Run the canonical command (per `docs/testing.md §Coverage`):

   ```
   uv run pytest --cov --cov-branch --cov-report=term-missing
   ```

   Expected: all tests GREEN AND 100% line+branch coverage (modulo any documented exclusions).

   Scan for coverage escape hatches (per `docs/testing.md §Coverage` policy):

   ```
   grep -rn "pragma: no cover\|pragma: no branch" src/ tests/ 2>/dev/null
   grep -n "\[tool.coverage" pyproject.toml .coveragerc 2>/dev/null
   ```

   Capture OpenSpec progress for the post-tick delta check at Step 7:

   ```
   openspec instructions apply --change <change> --json
   ```

   Record `progress.total`, `progress.complete`, `progress.remaining`. Expected after §N's impl commit: `complete += K` (K = §N's checkbox count), `total` unchanged.

   Decision tree:
   - **All GREEN, 100% coverage, no exclusions** → proceed.
   - **All GREEN, 100% coverage, exclusions exist** → report and ask:
     > "Coverage exclusions in baseline: <list>. 'Baseline 100%' = 100% modulo these excluded lines/files. Inherit unchanged for §<N>? (y/n)"

     If the total exclusion count (inline pragmas + config entries) exceeds 10, also propose setting up `docs/coverage-exclusions.md` per `docs/testing.md §Coverage` tracker threshold.
   - **Any RED or coverage gap** → halt and ask:
     > "Baseline broken before §<N> starts: <details>. (a) Pre-existing — proceed; (b) Halt and investigate; (c) Stop. Which?"

   Do not skip this. The whole "informative RED" + "100% coverage" + "verify-tick" discipline rests on the baseline being known-clean.

6. **Note repo conventions.** Skim `git log -5 --oneline` once to
   learn the commit-message style for drafting §N's impl commit later.

7. **Announce orientation.**

   ```
   ──────────────────────────────────────────────────────
     OpenSpec Execute — active: <change-name>

     Implementing §<N>. <subject>
     Plan: plans/<N>-<slug>.md ✓
     Checkboxes in this section: <K> (all tick together on impl commit)

     Mode (sticky for session): <Curated|Single|Inline>

     Say 'start' to begin §<N>, or override the mode.
   ──────────────────────────────────────────────────────
   ```

   Wait for 'start' (or an explicit mode override + 'start') before
   doing anything.

---

## Per-section flow

```
For each unticked ## N. <subject> in tasks.md:

  ┌─ 1. Load ───────────────────────────────────────┐
  │ Read plan + tasks.md §N + spec scenarios +      │
  │ design.md Decisions (already done in            │
  │ Session Start Step 4 — just hold the context).  │
  ├─ 2. Write tests ────────────────────────────────┤
  │ Write ALL §N tests in one batch (the N.1…N.K-1  │
  │ test rows). Files: per docs/testing.md          │
  │ colocation rule + prior sections' precedent.    │
  ├─ 3. Verify RED ─────────────────────────────────┤
  │ Run pytest. Confirm every new test fails with   │
  │ an informative AssertionError, not a collection │
  │ or import error.                                │
  ├─ 4. Write impl ─────────────────────────────────┤
  │ Implement the work the plan specifies (the      │
  │ N.K impl row). Touch only files §N's plan       │
  │ names. Strict YAGNI per design decisions/hints. │
  ├─ 5. Verify GREEN ───────────────────────────────┤
  │ Run pytest. §N tests pass. All prior sections'  │
  │ tests still pass (no regression).               │
  ├─ 6. Verify coverage ────────────────────────────┤
  │ Run pytest --cov on touched modules. 100% line  │
  │ + branch on §N's touched files.                 │
  ├─ 7. Tick all §N checkboxes ─────────────────────┤
  │ Flip every `- [ ] N.x` to `- [x] N.x` in        │
  │ tasks.md, in one edit. All together.            │
  ├─ 8. Ask: commit impl ───────────────────────────┤
  │ Propose:                                        │
  │   "<change>: implementation for <N>. <subject>" │
  │   <body referencing spec scenarios + load-      │
  │    bearing additions named in design            │
  │    decisions/hints>                             │
  │ Stage explicit paths (impl files + tasks.md).   │
  │ Wait for explicit approval. After commit,       │
  │ run `git status`.                               │
  ├─ 9. Ask: continue / stop ───────────────────────┤
  │ Default: stop here. For §<N+1>, the user runs   │
  │ /siraj-openspec-create-plan first to author       │
  │ its plan, then invokes this skill again.        │
  └──────────────────────────────────────────────────┘
```

Per section: **one commit** from this skill (the impl). The plan
commit lands upstream from `siraj-openspec-create-plan`. Two commits
per section across the two skills.

---

## Step 1 — Load

Already done in Session Start Step 4. This step is the bookkeeping
anchor: confirm you have the plan + tasks.md §N + spec scenarios +
design.md Decisions held in context before moving to test-writing.

If something in the plan looks wrong (e.g., the plan's test list
disagrees with `tasks.md` §N's `### Tasks` rows), surface it
immediately and pause:

> "Plan and tasks.md disagree about §<N>'s tests:
> Plan says: <list>
> tasks.md says: <list>
> Three ways to reconcile:
> (a) Re-author the plan via create-plan
> (b) Trust the plan and proceed
> (c) Trust tasks.md and proceed
> Which?"

---

## Step 2 — Write tests

Write ALL of §N's test rows in one batch. The plan's "Tests to write"
section is the authoritative list (it's the plan's expansion of
tasks.md's `Write test for <spec-ref> — <verbatim title>.` rows).

Each test's location follows:

- `docs/testing.md` colocation rule (`<module>_test.py` siblings).
- Prior sections' precedent for already-established test files
  (if §1 put init's CLI tests in `cli_test.py`, §2's CLI tests extend
  the same file).

Do NOT touch impl code in this step. The discipline of "tests first,
then RED before impl" is what makes Step 3's verification meaningful.

No tick. No commit. The work product is just the test code.

---

## Step 3 — Verify RED

Run pytest restricted to the §N tests. Read the output:

- **Informative RED** (good): tests run, assertions fail because the
  impl doesn't exist yet. Failure messages name what's missing
  (`AttributeError: 'InitResult' object has no attribute 'messages'`,
  `AssertionError: expected 'created' got 'skipped'`).
- **Uninformative RED** (bad): collection error, import error, syntax
  error, NameError on a fixture, etc. The test didn't really run; the
  failure doesn't prove anything about behavior. Fix and re-RED before
  proceeding.

If RED is informative, proceed to Step 4. If not, fix the test code
(missing import, wrong fixture path, typo) and re-run until informative.

---

## Step 4 — Write impl

Implement the work the plan specifies. Touch only files §N's plan
names. Strict YAGNI per the design decisions/hints — `InitResult`
gets only the fields THIS section needs, not the final shape; `init()`
takes only the parameters THIS section exercises.

Mode-specific dispatch (per §Execution Modes above):

### Curated mode

Delegates the subagent dance + comparison rendering to
[`parallel-implement-compare`](../parallel-implement-compare/SKILL.md).
This skill provides the OpenSpec-specific orchestration around it:
pre-flight, prompt template, post-comparison apply/verify/commit.

1. **Pre-flight.** Plan + supporting docs (spec, design, testing.md)
   must be committed to `origin/main` (worktrees inherit from there).
   If uncommitted docs exist:
   > "Curated mode needs the plan + docs committed and pushed first.
   > Commit + push these now? (y/n)"
   Wait for approval. Use `git push origin main` after commit.

2. **Invoke `parallel-implement-compare`** with:
   - **Models:** `["opus", "sonnet"]` — Opus is the baseline (position A,
     left panel, picker default).
   - **Pre-flight check:** "plan + supporting docs committed to origin/main."
   - **Report orientation:** `"winner-pick"` — uses the browser viewer.
   - **Viewer title:** `"§<N>. <subject>"` (e.g., `"§2. feather.yaml idempotency"`).
   - **Prompt template:** see Subagent Prompt Template below.

   The composable skill handles: viewer-session setup
   (`.playground/compare/<timestamp>/`), server start, browser open,
   agent launch, per-agent state.json updates, submission watcher.

3. **Sequenced chat-text reveal continues.** Even though the side-by-side
   comparison now lives in the browser, the composable skill still
   renders each agent's text report (RED step, GREEN step, coverage,
   deviations) in chat as that agent finishes. Annotate insights here.
   The browser is the *decision* surface; chat remains the *narrative*
   surface.

4. **User reviews + submits in the browser.** The composable skill is
   blocked on the `DECISION_READY` watcher notification. When it fires,
   the user has clicked Submit. The composable skill reads
   `<session>/decision.json` and hands the parsed payload back.

5. **Apply the user's decision.** For each file in `decision.files`:
   - If `source == "opus"` or `source == "sonnet"`: `cp <that-agent's-worktree>/<file> <main-repo>/<file>` with explicit paths (not `rsync -a`).
   - If `source == "edited"`: `Write <main-repo>/<file>` with `content` verbatim — the user's text, not the agent's.
   - The commit message is composed at Step 8 of the per-section flow from the plan + spec context (standard `<change>: implementation for <N>. <subject>` pattern). The viewer does not collect commit messages.

6. **Verify GREEN + coverage in main repo** (Steps 5 + 6 of the
   per-section flow run here).

7. **Cleanup deferred to per-section Step 8 tail.** Worktrees stay live
   through GREEN+coverage AND through commit approval (so the user can
   re-examine if needed). They are deleted automatically AFTER the
   commit lands — see Step 8's commit rule. The viewer server is also
   killed at that point (`kill $(cat <session>/server.pid)`); the
   session dir is kept (gitignored) for later inspection.

### Single subagent mode

1. Pre-check (same as Curated).
2. Stash impl (same).
3. Launch ONE subagent: chosen model, `isolation: "worktree"`,
   `run_in_background: true`. Same prompt template.
4. Wait for the single completion report.
5. **Brief review.** Read the worktree's code, summarize what changed
   in chat (one paragraph per file).
6. User accepts OR asks for changes. If changes: either relaunch the
   subagent with revised prompt or switch to inline for this revision.
7. Apply (copy worktree → main).
8. Verify GREEN + coverage in main repo.
9. Cleanup deferred to per-section Step 8 tail (post-commit, automatic).

### Inline mode

1. Agent writes code in the current session (in the working tree).
2. Show the diff.
3. Say:
   > "Review the changes above. Ready to verify GREEN + coverage?"
4. Wait for explicit confirmation before running verification.

### Subagent Prompt Template (Curated + Single)

Each subagent gets the SAME prompt structure (identical text in Curated
mode so the comparison is apples-to-apples):

> You are implementing **§<N>. <subject> of the <change-name> change**
> in the feather-etl repo. Your worktree is `<worktree-path>`.
>
> ## Read these files first (in order)
> 1. `openspec/changes/<change>/plans/<N>-<slug>.md` — primary brief.
> 2. `openspec/changes/<change>/tasks.md` — §N's `### Tasks` + `### Design decisions/hints`.
> 3. `openspec/changes/<change>/specs/<capability>/spec.md` — scenarios.
> 4. `docs/testing.md` — cadence (vertical slicing, test-first batch).
> 5. `openspec/changes/<change>/design.md` — decisions referenced by §N.
>
> ## Your task
> Implement §<N> per the plan. Follow the test-first cadence: write
> ALL §N tests → confirm RED → write impl → confirm GREEN → run
> coverage. All §N checkboxes tick together at the end.
>
> ## Constraints
> - Strict YAGNI per plan + tasks.md's design decisions/hints.
> - Floor-not-wall per the plan's impl outline.
> - PEP-420 namespace packages — no empty `__init__.py` markers.
> - Use `uv run` for all Python invocations.
>
> ## Report back (under 400 words)
> 1. Files created/modified — full paths.
> 2. RED step result — exact failures seen at collection time.
> 3. GREEN step result — test names + pass/fail.
> 4. Coverage report — line + branch percentages on touched files.
> 5. Deviations recorded in plan §8 with one-line rationale.
> 6. Consult moments — any decision where you considered consulting
>    the user per `docs/testing.md` "consult before reaching for
>    non-default tools."

In ALL modes: do not auto-commit. Do not tick checkboxes. Both happen
in Steps 7 + 8 after GREEN + coverage pass.

---

## Step 5 — Verify GREEN

Run pytest restricted to (a) §N's tests, (b) all prior sections' tests.
Both must pass.

- If §N's tests fail: impl is incomplete or wrong. Iterate on impl
  (back to Step 4) until §N tests pass.
- If prior sections' tests fail: §N's impl broke an earlier guarantee.
  Halt and surface — this is a regression, not an iteration.

If everything passes BUT something subtle (tests pass yet spec/code
diverge in behavior), classify per the Finding classifier below before
moving on.

---

## Step 6 — Verify coverage

Run `pytest --cov=<package> --cov-branch` for the modules §N touched.
Expect 100% line + branch on §N's touched files.

If coverage < 100%: missing test cases. Add tests for uncovered
branches and re-verify GREEN + coverage. Do not lower the bar.

Also run `openspec validate` once per session (not per section) to
catch doc-side drift.

### Finding classifier

Classify every finding from Step 5 or Step 6 before acting.

| Finding | Action |
|---|---|
| Verification failed (test/lint/coverage) | Fix inline, re-verify before continuing |
| Code and spec disagree (divergence) | Divergence conversation (below) |
| Spec is ambiguous — unstated choice made | Ambiguity conversation (below) |
| Cross-cutting issue (affects > 1 capability) | Surface to user before continuing |
| Scope creep on current section | Three-option prompt (below) |
| Unrelated bug or improvement noticed | Mention once, then drop — not this section's job |

Treat "tests pass" as proof of what tests assert — not proof of
correctness. A divergence means the tests followed the wrong source.

### Divergence conversation

> "While verifying §<N>, I noticed a divergence between code and spec:
>
> **Spec (`<capability>.<N><letter>`):** <what spec says>
> **Code:** <what code does>
> **Tests:** <whether they caught it or followed the code>
>
> Three ways to reconcile:
> (a) Fix code to match spec — <concrete change>
> (b) Update spec to match code — <concrete change>
> (c) Update both — <concrete change>
>
> Which?"

After user decides: make the change, re-verify before continuing.

### Ambiguity conversation

> "Implementing §<N> surfaced an ambiguity in `<spec-ref>`: <what was
> unclear>. I implemented <what was chosen>. Want me to update the spec
> to lock this in, or do you want different handling?"

### Scope creep prompt

> "§<N> reveals it also needs <X>, which isn't in the section.
> (a) Add it to §<N> now (and update tasks.md / plan)
> (b) Add a new section §<N+0.5> for it
> (c) Defer to §<later>
> Which?"

Stop until decided.

---

## Step 7 — Tick all §N checkboxes, then verify

After Steps 3–6 all pass, flip every `- [ ] N.x` in `tasks.md` to
`- [x] N.x` **in a single edit**. All of §N's checkboxes tick together
because §N is the unit of work; the impl that makes them pass is one
piece of work.

```markdown
- [x] N.1 Write test for <spec-ref> — <title>.
- [x] N.2 Write test for <spec-ref> — <title>.
- [x] N.K Implement <shape> so N.1 + N.2 pass.
```

Do NOT tick individual checkboxes during Steps 2–6. Tick is a
post-GREEN-and-coverage event for the whole section.

Then **independently verify the parser absorbed the ticks** — the agent's "I ticked §N's boxes" report is the agent grading their own homework; OpenSpec's parser is the second source of truth that catches typos, indent slips, and numbering mismatches:

```
openspec validate <change>
openspec instructions apply --change <change> --json
```

Expected:
- `openspec validate` reports `Change '<change>' is valid`.
- Post-tick progress matches the Session Start Step 5 snapshot: `complete = baseline_complete + K`, `total` unchanged, `remaining = baseline_remaining - K` (where K = §N's checkbox count).

If validate fails OR delta doesn't match, halt before Step 8:

> "OpenSpec parser didn't see the expected tick delta:
>   baseline: <X> complete / <Y> total
>   after tick: <Z> complete / <Y> total
>   expected:  <X+K> complete
>
> Likely causes: (a) typo in the `- [x]` form, (b) checkbox indent pushed it out of root, (c) numbering mismatch.
>
> Inspect `tasks.md` §N before commit — do not commit a tick edit the parser can't see."

---

## Step 8 — Ask: commit impl

Propose the commit:

> "§<N>. <subject> done.
> Proposed message:
>
>     <change>: implementation for <N>. <subject>
>
>     Implements <change>.<spec-ref> + <spec-ref>. <one line per
>     load-bearing addition the section introduced, per its design
>     decisions/hints — e.g. 'Introduces InitResult.messages + the
>     loud-skip pattern + cli echo loop.'>
>
> (a) commit now (default)
> (b) revise the message first
> (c) stop here without committing
> Which?"

### Commit rule

- The agent **may commit** when the user picks (a) or otherwise explicitly approves.
- The agent **never commits without explicit approval**. Default is ask.
- Approval forms that count: "commit", "go ahead and commit", "yes commit", explicit yes to "shall I commit?". Anything ambiguous = ask again.
- The user may edit the proposed message before approving.
- **Stage explicit paths only** — `git add` the impl files + `tasks.md` (for the ticks). Never `git add -A`.
- After committing, run `git status` once to confirm the working tree is clean.
- **Worktree + viewer cleanup runs automatically post-commit (Curated + Single modes).** After `git status` confirms clean, immediately run — no user prompt, no "here are the commands you can run" code block:
  - `git worktree remove -f -f <each-worktree-path>` for each agent's worktree
  - `git worktree prune`
  - `git branch -D <each-worktree-branch>` for each agent's branch
  - (Curated mode only — kill the viewer server: `kill $(cat .playground/compare/<session>/server.pid)`. The session dir itself stays — gitignored, useful for re-opening `compare.html` after the fact.)
  - (If a stash was created during pre-flight: `git stash drop <stash-ref>`)

  Rationale: the committed code on main is the canonical artifact; the worktree branches and viewer server are throwaway by construction (worktrees created from `origin/main` for this single comparison, server bound to one localhost port for the same duration). Surfacing the commands to the user converts a default into a chore and leaks worktrees + servers session-over-session. Inline mode has no worktrees, no viewer, and skips this step.
- Never `git push`. The user pushes.

---

## Step 9 — Ask: continue or stop

Default closing prompt:

> "§<N> committed.
> (a) stop here (default) — to do §<N+1>, run /siraj-openspec-create-plan
>     first to author its plan, then invoke this skill again.
> (b) continue to §<N+1> if its plan file already exists.
> Which?"

If user picks (b) and §<N+1>'s plan file exists, restart at Session
Start Step 2 for §<N+1>. If user picks (b) and §<N+1>'s plan does
NOT exist, halt with the pre-flight banner from Session Start Step 3.

---

## End of Change

When all sections' checkboxes are ticked:

> "All sections complete. Want me to run `/siraj-openspec:verify` to
> check implementation against the spec? Once that's clean, archive
> with `/siraj-openspec:archive`."

Do not auto-run either.

**Next:** `/siraj-openspec:verify` to validate, then
`/siraj-openspec:archive`, then `/siraj-openspec:sync` to move delta
specs into `openspec/specs/`. See `siraj-openspec-flow` for the full
Stage 7 pipeline.

---

## Anti-Patterns

| ❌ Don't | ✅ Do |
|---|---|
| Author a plan file inside execute-plan | Halt at pre-flight; route the user to `/siraj-openspec-create-plan` |
| Treat each checkbox as its own commit cycle | The section is the unit of work; all §N checkboxes tick together at GREEN |
| Tick checkboxes individually during Steps 2–6 | One tick edit AFTER Steps 5 + 6 both pass |
| Skip the RED step ("I know it'll fail") | RED is the proof tests can actually fail — uninformative RED hides real bugs |
| Treat passing tests as proof of correctness | Tests prove what they assert; classify divergences per the Finding classifier |
| Pick a curated winner silently | Always render Sonnet + Opus side-by-side; wait for the user's pick |
| Launch curated subagents without committing the plan first | Plan + supporting docs must be committed to `origin/main` (worktrees inherit from `origin/main`, not the dirty working tree) |
| Auto-pick a winner in auto-mode | Auto-mode keeps Curated as default; it does NOT change the "wait for user's pick" gate |
| Commit without explicit user approval | Always ask, even when context "obviously" wants a commit |
| Silently fix code when spec says otherwise | Surface as divergence — three options |
| Silently update spec when code is convenient | Same — divergence is a conversation |
| Run `git push` | Never push; the user pushes |
| Surface worktree cleanup as a "here are the commands you can run" code block | Run cleanup commands directly, automatically, immediately post-commit — no prompt. The user shouldn't have to think about throwaway worktrees |
| Cleanup worktrees BEFORE the commit lands | Cleanup runs in Step 8's tail (post-commit, post-`git status`). Worktrees stay live through commit approval so the user can re-examine if the commit is rejected |
| Use `git add -A` | Stage explicit paths only (impl files + `tasks.md`) |
| Re-shell `openspec instructions apply` per iteration | Two intentional calls: Session Start Step 5 (baseline snapshot) and Step 7 (verify-tick delta). Not per checkbox / per loop iteration |
| Invent spec references | If unclear, halt and ask |
| Continue to §<N+1> without checking its plan exists | Always check first; halt + route to create-plan if missing |
| Lower the coverage bar when 100% is annoying | Add the missing tests; the coverage gate is non-optional |
| Skip baseline test + coverage check at Session Start | Non-negotiable; halts on RED, asks on existing exclusions (per `docs/testing.md §Coverage`) |
| Add `# pragma: no cover` or other coverage exclusion to pass the gate | Never. Write the missing test. If genuinely untestable, ASK before adding (per `docs/testing.md §Coverage` escape-hatch policy) |
| Silently inherit existing coverage exclusions | Report them at Session Start; user confirms inheritance explicitly |
| Trust your own "I ticked §N's boxes" report and commit | Re-run `openspec instructions apply --change <X> --json` after the tick; halt if delta ≠ baseline_complete + K |
