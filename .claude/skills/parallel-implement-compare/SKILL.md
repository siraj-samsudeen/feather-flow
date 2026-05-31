---
name: parallel-implement-compare
description: >
  Run N subagents in parallel against the same prompt in isolated git
  worktrees, then produce a side-by-side comparison of their outputs.
  Composable mechanic invoked by siraj-openspec-execute-plan (Curated
  mode), siraj-openspec-stress-test-plan, and any caller that wants
  multiple model voices on the same task. Use directly when Siraj asks
  "fan out N agents on this", "compare what Sonnet and Opus do for X",
  or "run a 2-agent bake-off". Defaults to Sonnet + Opus (N=2), both
  in worktrees with `run_in_background: true`.
---

# Parallel Implement + Compare

A composable mechanic for "multiple model voices on the same task." N
subagents run in parallel against an identical prompt in isolated git
worktrees; this skill aggregates their outputs into a side-by-side
comparison report. What the caller DOES with the report (pick a
winner, push back to plan refinement, throw away the code) is the
caller's business — this skill just produces the comparison.

The skill exists because the same subagent dance appears in two
contexts:

1. **Curated execution** — you want production code, multiple voices
   compared, pick the best to commit.
2. **Stress-testing a plan** — you want divergences read as gap-signals,
   no commit, push back to plan refinement.

Centralizing the mechanic here keeps both callers DRY.

---

## When to use

- Caller is `siraj-openspec-execute-plan` Curated mode — needs N=2 production-quality outputs to pick from.
- Caller is `siraj-openspec-stress-test-plan` — needs N=2 or N=3 outputs to read for divergences.
- Direct invocation: Siraj says "fan out N agents on this" or "run a 2-agent comparison."

## When NOT to use

- Single subagent runs — use the `Agent` tool directly with `isolation: "worktree"`.
- Tasks that change shared state outside a worktree (CI runs, remote API calls, persistent DB writes) — isolation breaks.
- Pure text review of a doc (no code generation) — use `verify-plan` for parallel text review of a plan file.

---

## Inputs (from the caller)

| Input | Required | Default | Notes |
|---|---|---|---|
| Prompt template | yes | — | Exact text each subagent receives. Caller fills in task context, file paths, constraints. |
| Models | no | `["sonnet", "opus"]` | List of model strings. `["haiku", "haiku", "haiku"]` for cheap stress-testing; `["sonnet", "opus"]` for production winner-picking. |
| Pre-flight check | yes | — | A check the caller specifies — e.g., "plan + docs committed to origin/main." This skill runs it before launching. |
| Report orientation | no | `"winner-pick"` | `"winner-pick"` uses the browser viewer (Steps 3 + 6 below); `"gap-find"` stays text-in-chat (no viewer; emphasizes convergent failures + divergences as plan-issue signals). |
| Files to compare | no | auto-discover via diff | Caller can specify a list to restrict the comparison scope. |
| Viewer title | only if winner-pick | — | One-line header rendered in the viewer (e.g., "§2. feather.yaml idempotency"). Shows up next to "Parallel comparison —" in the page header. |
| Models ordering | no | first model = position A (baseline) | The first model in the list lands in slot `agents.a` and shows on the **left** panel — this is the *baseline*; the picker defaults to it. Convention: put the more-capable model first (`["opus", "sonnet"]`) so the auto-default is the strong baseline. |

---

## The flow

### Step 1 — Pre-flight

Run the caller-supplied pre-flight check. If it fails:

> "Pre-flight failed: <reason>. Cannot launch parallel agents without <prerequisite>. Address this and re-invoke."

Stop. Typical pre-flight requirements:

- **OpenSpec callers:** plan + supporting docs committed to `origin/main` (worktrees inherit from there, NOT from the dirty working tree).
- **Greenfield callers:** working tree clean OR stashable.

### Step 2 — Stash any working-tree changes

If the working tree is dirty, stash with a descriptive label:

```bash
git stash push -u -m "parallel-impl-pre-comparison-<timestamp>"
```

Record the stash ref for cleanup later. Skip if the working tree is already clean.

### Step 3 — Set up the viewer (winner-pick orientation only)

For `winner-pick` orientation, the load-bearing pick interface lives in
a browser-side HTML viewer — interactive side-by-side panels, always-editable
textareas, per-file pick radios, live updates as each agent lands. For
`gap-find` orientation, skip this step entirely (gaps are read in chat;
see Step 6 below).

3.1 **Pick a session directory.** `<repo_root>/.playground/compare/<YYYYMMDD-HHMMSS>/`.
    Add `.playground/` to repo's `.gitignore` if not present.

3.2 **Copy templates from this skill into the session dir:**
    - `viewer/server.py.template` → `<session>/server.py`
    - `viewer/compare.html.template` → `<session>/compare.html`

3.3 **Write the initial `state.json`** in the session dir. Slot `a` holds the
    *baseline* model (left panel; picker defaults here). Convention: put the
    more-capable model in `a` so submitting without an explicit pick gives the
    strong default.

    ```json
    {
      "title": "<caller-supplied title — e.g. §2. feather.yaml idempotency>",
      "agents": {
        "a": {"name": "Opus", "status": "running", "duration_ms": null},
        "b": {"name": "Sonnet", "status": "running", "duration_ms": null}
      },
      "files": {}
    }
    ```

    `files` starts empty; entries are added as each agent finishes (Step 5).
    No `suggested_commit_message` field — the commit message is owned by the
    caller (chat-side), not the viewer.

3.4 **Start the server in background:**

    ```
    cd <session> && VIEWER_PORT=8765 python3 server.py &
    ```

    Use `Bash` with `run_in_background: true`. If port 8765 is taken, fall
    back through 8766, 8767… up to 8770; record the chosen port. Verify
    the server bound with `curl -fsS http://localhost:<port>/state.json`.

3.5 **Open the browser:**

    ```
    open http://localhost:<port>/
    ```

3.6 **Start the submission watcher.** Use `Bash` with
    `run_in_background: true` and `Monitor` for the completion line:

    ```
    until [ -f <session>/decision.json ]; do sleep 2; done; echo "DECISION_READY"
    ```

    The harness will notify when this background process emits
    `DECISION_READY` — that's the signal the user submitted in the browser.

### Step 4 — Launch N subagents in parallel

Use the `Agent` tool with `isolation: "worktree"` and `run_in_background: true` for each subagent.

**All N calls go in the SAME tool-use message** so they execute concurrently. Sequential calls (one Agent call per message) defeat the parallelism.

For each model in the list:

```
Agent(
  description: "<model> implementation of <task>",
  subagent_type: "general-purpose",
  model: <model>,
  isolation: "worktree",
  run_in_background: true,
  prompt: <caller's prompt template>,
)
```

### Step 5 — Sequenced reveal + state.json updates

As each subagent finishes (the harness emits a `task-notification`):

5.1 **Render a brief text report in chat** — files touched, RED step
    output, GREEN step output, coverage, deviations. This keeps the
    narrative/insight value the chat is good at (and lets the agent
    annotate with its own observations). Annotate which agents are
    still running.

5.2 **Read the agent's worktree files** — discover via `git diff
    <worktree-branch>..origin/main` per worktree, take the union across
    worktrees, read all from each worktree. (If the caller specified
    `files to compare`, read only those.)

5.3 **Update `<session>/state.json`** in place (for `winner-pick`):

    - Flip `agents.<side>.status` → `"done"`, set `duration_ms` and
      `tool_uses` from the completion notification.
    - For each file: set `files[<filename>].<side>` to the file content.
      Preserve the other side's entries from previous writes.

    The browser polls every 1.5s and updates DOM in-place, so the
    user's edits in textareas are preserved through the update.

Sequenced reveal beats "wait for all, dump everything" — the user can
start reading and editing the first result while the second is still
running. With N=2 (Sonnet + Opus), the faster model typically lands
1–2 minutes before the slower.

### Step 6 — Wait for user submission (winner-pick) OR render gap report (gap-find)

#### `"winner-pick"` orientation

After both agents are done and state.json reflects both:

- Do NOT render the big side-by-side comparison in chat. That lives in
  the browser now.
- Optionally render a 1-paragraph chat-text observation about the most
  notable divergence(s) (e.g., "core.py: Opus extracted `path = target /
  'feather.yaml'` to a local var; Sonnet kept it inline. cli.py: identical
  byte-for-byte.") — for the agent's own narrative and to seed the user's
  review. Keep it SHORT; the user is reviewing in the browser, not chat.
- Wait for the `DECISION_READY` notification from the Step 3.6 watcher.
- When it fires, read `<session>/decision.json` and proceed to Step 7.

#### `"gap-find"` orientation

Render in chat (no browser viewer used):

```markdown
# Plan-gap signals from parallel implementation — <plan ID>

## Convergent failures (BOTH agents got it wrong)

These signal the PLAN is unclear or invites the wrong default. **Fix the plan**, not the agents.

- **<topic>**: both agents <did wrong thing>. Plan says <X>; both interpreted as <Y>. Suggested plan rewording: <fix>.

## Divergent outcomes (agents disagreed)

These signal the plan didn't pin a choice that matters. **Either pin it in the plan or accept either output.**

- **<topic>**: <Model A> did <X>; <Model B> did <Y>. Plan says <nothing/ambiguous>. Decision needed.

## Convergent successes

Sanity check — parts of the plan that held cleanly. No action needed.

- <topic>: both agents produced <correct thing>.

## Recommendation
<Push back to create-plan to fix convergent failures + ambiguities. Re-run after polish.>
```

### Step 7 — Hand back to caller

For `winner-pick`: hand the caller the parsed `decision.json` payload —
a per-file map `{source: "opus"|"sonnet"|"edited", content: <string|null>}`.
The caller applies the chosen code (from worktree if `source` names an
agent; from `content` verbatim if `source == "edited"`), verifies, and
commits. The commit message is composed by the caller from spec/plan
context — the viewer does not collect it.

For `gap-find`: hand the caller the in-chat gap report. The caller
decides whether to push back to plan refinement.

### Step 8 — Cleanup (caller-driven; skill provides commands)

After the caller is done with the worktrees:

```bash
# If the caller wants to keep one worktree's code, copy files OUT first:
cp <chosen-worktree>/<file> <main-repo>/<file>

# Worktree cleanup:
git stash drop <stash-ref>                              # if stash was created in Step 2
git worktree remove -f -f <worktree-path-A>             # per agent
git worktree remove -f -f <worktree-path-B>
git worktree prune
git branch -D <worktree-branch-A> <worktree-branch-B>

# Viewer server cleanup (winner-pick only):
kill $(cat <session>/server.pid)                        # stops the HTTP server
# The session dir (<repo>/.playground/compare/<timestamp>/) is intentionally
# preserved by default — gitignored, useful to re-inspect compare.html later.
# Callers MAY rm -rf the session dir if they want zero residue.
```

---

## Subagent prompt template — caller's responsibility

This skill does NOT prescribe prompt content. The caller is responsible for:

- The task ID / what to implement.
- Context files to read (plan, spec, design, testing.md, etc.).
- Constraints (YAGNI directive, floor-not-wall, language conventions).
- The structured report-back format the caller expects.

See `siraj-openspec-execute-plan` Curated mode for an example OpenSpec-style prompt.

**Identical prompt for all N agents.** The comparison only makes sense when every agent receives the same instructions; differences in output reflect model voice, not prompt skew.

---

## Anti-patterns

- **Run N Agent calls sequentially.** All N must go in the SAME tool-use message to execute concurrently. One-per-message = sequential launch = defeats the point.
- **Use `run_in_background: false` for parallel agents.** Blocks the main chat. Always `true` for N ≥ 2.
- **Skip the pre-flight check.** Worktrees inherit from `origin/main` (or the caller-specified base). If plan / docs aren't committed there, agents read STALE state — comparison becomes uninformative.
- **Try to peek at agents' progress mid-flight.** The agent transcript file is too large to safely read. Wait for the completion notification.
- **Auto-pick a winner without waiting for user submission.** For `winner-pick`, the user submits via the browser viewer — never bypass with an agent-generated pick.
- **Render the per-file side-by-side comparison as a chat-text wall.** For `winner-pick`, the comparison lives in the browser (`compare.html`). Chat may include a 1-paragraph observation about the most notable divergence, but the load-bearing pick UI is the browser.
- **Forget cleanup.** Worktrees accumulate disk space + locked branches; viewer servers accumulate as background processes. Cleanup runs at the caller's chosen post-action point (see `siraj-openspec-execute-plan`'s automatic post-commit cleanup as the canonical example).
- **Auto-replace state.json instead of merging.** When writing state.json after each agent lands, *merge* with prior state (preserve the other agent's entries) rather than overwriting. Overwriting loses the first agent's data the moment the second writes.
- **Read decision.json before the `DECISION_READY` notification fires.** The watcher is the trigger; polling the file from chat-side is wasted work (the harness already does the notification).
- **Skip the viewer because "the chat-text reports look fine."** For `winner-pick`, the viewer is the pick UI. Chat-text reports per agent (Step 5.1) are the *narrative* layer; the viewer is the *decision* layer. Both exist; neither replaces the other.
- **Vary the prompt across agents.** Identical prompt is the apples-to-apples discipline. Different prompts produce different outputs that you can't attribute to the model.

---

## Integration with callers

This skill is invoked BY other skills, typically. Division of concerns:

| Concern | Lives in |
|---|---|
| Subagent launch mechanics (worktree, parallel, background) | this skill |
| Sequenced reveal of completion notifications | this skill |
| Viewer setup (server, HTML, state.json updates) for `winner-pick` | this skill |
| Submission watcher (Bash + Monitor on decision.json) | this skill |
| Gap-find chat-text report rendering | this skill |
| Cleanup commands | this skill |
| What pre-flight to require | caller |
| What prompt to send | caller |
| What models to use | caller |
| `title` and `suggested_commit_message` for the viewer header / commit textarea | caller |
| Applying the parsed `decision.json` (cp from worktree or write `content`) | caller |
| Whether to commit, verify, apply to main | caller |

### Viewer files (subdirectory)

For `winner-pick` orientation, this skill ships two templates that are
copied into the session directory at Step 3.2:

- `viewer/server.py.template` — ~85-line stdlib HTTP server. Two routes:
  `GET /<file>` serves session files (compare.html, state.json, etc.);
  `POST /decision` writes the user's submission to `decision.json`.
  Writes `server.pid` for cleanup.
- `viewer/compare.html.template` — single-file viewer. Inline CSS+JS,
  no external deps. Polls `/state.json` every 1.5s, renders per-file
  side-by-side panels with always-editable textareas, LCS-based unified
  diff (collapsible), per-file pick radio, editable commit-message
  textarea, Submit button that POSTs the decision payload.

Both templates are self-contained — the chat copies them verbatim; no
templating substitution happens at copy time. Per-session configuration
(title, agent names, file contents, commit message) flows through
`state.json`, which the chat writes/updates and the browser polls.

That separation IS the point. The mechanic lives here; the orientation lives in the caller.
