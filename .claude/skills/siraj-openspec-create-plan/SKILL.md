---
name: siraj-openspec-create-plan
description: >
  Author or walk through a per-commit plan file for an OpenSpec change.
  Plan files live at openspec/changes/<change>/plans/<N>-<slug>.md
  and define the canonical 8-section shape that siraj-openspec-execute-plan
  consumes. Use this skill whenever Siraj asks to draft, walk, refine, or
  revise a per-commit plan. Trigger on phrasing like "draft the plan for
  commit N", "walk the plan", "author commit-N plan", "plan walkthrough",
  "let's walk through commit N", or whenever an OpenSpec change has
  tasks.md locked but no plan file for the next commit. Defaults to the
  most-recently-modified change folder if no name is given.
---

# OpenSpec Siraj — Create Plan

Plan files are the bridge between the across-the-change view (`tasks.md`)
and the per-commit execution (`siraj-openspec-execute-plan`). They describe
ONE commit at a time with enough orientation for an implementing agent or
human reviewer to load the slice into their head in 60 seconds.

The plan is the executor's source of truth. `tasks.md` carries cross-commit
checkbox traceability only — `## N. <subject>` header + `### Tasks` +
checkbox list per commit. No Before/After paragraphs, no "Lands…" framing,
no design hints in `tasks.md`. All commit-level detail lives in the plan.

This skill defines the canonical 8-section shape, plus the 6
sub-perspectives that live inside §1. Both were derived empirically.

**Where this fits:** Stage 6 of the siraj-openspec pipeline — invoked after `siraj-openspec-stress-test-tasks` clears, before `siraj-openspec-execute-plan` consumes the plan. Produces ONE per-commit plan file at a time. See `siraj-openspec-flow` for the full pipeline diagram, sibling-skills graph, and the artifact-tree convention. After authoring, run `/review-document` for the structural lens pass; for load-bearing commits, `siraj-openspec-stress-test-plan` empirically validates the plan before execution.

---

## When to use

- Authoring a fresh plan for a new commit after `tasks.md` is locked.
- Revising an existing plan when scope changes or YAGNI is discovered.
- After `siraj-openspec-stress-test-tasks` surfaces gaps that affect a
  specific commit's slice.
- Whenever Siraj says "draft commit N's plan" or "walk through the plan"
  or pauses on an OpenSpec change folder whose next commit lacks a plan.

## When NOT to use

- Authoring `tasks.md` (commit blocks across the change) — hand-edited
  per the tasks.md per-commit shape in `siraj-openspec-flow`.
- Authoring `spec.md` or `design.md` — separate concerns
  (`siraj-openspec-create-design` for design; spec is hand-edited per
  the numbering / title rules in `siraj-openspec-flow`).
- Pure structural polish on a finished plan — use `review-document`.

## What this skill does NOT do

- Does not validate the plan empirically — that's `siraj-openspec-stress-test-plan`.
- Does not implement anything — that's `siraj-openspec-execute-plan`.
- Does not author `tasks.md` (the cross-commit overview).
- Does not run the per-commit review gates by itself — author the plan, then invoke `review-document` and (optionally) `stress-test-plan` separately.
- Does not implement the section — that's `siraj-openspec-execute-plan`, which runs as its own pipeline step against a *committed* plan.

This skill DOES commit the plan file as a separate pipeline step before handing off to execute-plan. See §Commit the plan below.

---

## Pre-walk health check

Before drafting the plan, run the canonical test command once (per `docs/testing.md §Coverage`):

```
uv run pytest --cov --cov-branch --cov-report=term-missing
```

Expected: all tests GREEN + 100% line+branch coverage (modulo documented exclusions).

Also scan for coverage escape hatches (per `docs/testing.md §Coverage` policy):

```
grep -rn "pragma: no cover\|pragma: no branch" src/ tests/ 2>/dev/null
grep -n "\[tool.coverage" pyproject.toml .coveragerc 2>/dev/null
```

- **Clean baseline** → proceed to draft the plan.
- **Baseline RED, coverage gap, or unexpected exclusions** → surface to the user BEFORE drafting:

  > "Baseline observations before drafting §<N>'s plan:
  >   <RED tests | uncovered lines | exclusion list>
  > These will affect what §<N> can assume about test infra. Confirm to proceed, or pause to investigate first."

Don't draft silently against a broken baseline — the plan's test-file paths, fixtures, and coverage contract all depend on the baseline being meaningful.

If the total exclusion count (inline pragmas + config entries) exceeds 10, also mention that the per-session ask-flow doesn't scale — propose setting up `docs/coverage-exclusions.md` (per `docs/testing.md §Coverage` tracker threshold).

---

## The 8-section canonical shape

Every per-commit plan follows this shape. Sections appear in this order.
Sections §6–§8 are template stubs that fill in over the commit's life.

```
# N. <subject>

## 1. Full picture           ← multi-altitude orientation (6 sub-perspectives, see below)
## 2. Key terms              ← codebase-specific glossary; NOT Python language features
## 3. Key ideas              ← named design insights for THIS commit; count per audit, not a target (no DO-NOT lists)
## 4. Tests to write         ← H3 per scenario; BDD English; code as *Implementation hints*
## 5. Implementation outline ← H3 grouped by file with Layer prefix; floor-not-wall preamble
## 6. Open questions          ← template stub; "_None._" when empty
## 7. Decisions taken        ← template stub; filled when contracts get changed mid-walk
## 8. Deviations             ← template stub; filled by execute-plan at commit time
```

---

## §1 Full picture — 6 sub-perspectives

§1 is the load-bearing section. An agent who reads ONLY §1 (and skims §4)
should be able to start implementing. The 6 H3 sub-perspectives give
multi-altitude views — each is a question heading.

### `### What this commit ships?`

One-paragraph **executive summary** mixing observable behavior + key
patterns/ideas + load-bearing contribution. Written for the human reviewer
who reads only this paragraph before scrolling. The mixed framing is
deliberate: behavior alone doesn't explain why a commit matters;
architecture alone doesn't tell a reviewer what changes. Combine both.

Example from Commit 1 of `add-feather-init` (infra-introducing):

> This commit establishes the verb-template skeleton every later verb
> will inherit. The file contents are minimal — just enough to stamp
> `feather.yaml` into the working directory — but the *shape* (core/cli
> split, register-pattern, accumulator dataclass) is the load-bearing
> contract.

Example from Commit 2 (incremental, where pattern and behavior are
tightly coupled):

> This commit makes `feather init` idempotent for `feather.yaml` — and
> in doing so lands the **skip-if-exists pattern**, the **`messages`
> channel** on `InitResult`, and **cli's echo loop**. Three reusable
> patterns debut in one scenario; every later per-file stamper that uses
> reset-by-delete reuses one or more.

### `### What is in scope?`

Bullet list of spec scenarios this commit closes. Each bullet uses the
canonical `<capability>.<num><letter>` form + the verbatim scenario title:

> - `init.1a` (Init with no arg stamps files into CWD, if empty)
> - `init.2a` (Stamped feather.yaml content matches template)

### `### What is out of scope?`

The scope-guard sub-perspective. The body opens with a directive line,
then enumerates themes / artifacts / parameters deferred from this
commit.

**Themes only, not specific scenario IDs**, and **NO commit numbers**
(those are forward references to other plan files).

```
### What is out of scope?

**The following are deliberately deferred from this commit. DO NOT
include them in the implementation — each lands in the commit that
demands it.**

- skip-if-exists message
- `dir` arg resolution
- `pyproject.toml` stamping
- `messages` field on `InitResult`
- `echo` helper in cli
```

The bold directive line is load-bearing. Empirical evidence from earlier
shape iterations: when scope-guards were permissive ("X waits for the
commit that needs it"), Opus added 4 fields to `InitResult` in Commit 1
instead of the intended 2. When the directive was binding ("DO NOT add
X, Y, Z"), Opus added exactly 2. Keep the wording binding.

This sub-perspective is the canonical home for the "what not to add" list;
§3 (Key ideas) stays insights-only and does NOT carry DO-NOT directives
that duplicate scope.

### `### What files this commit creates/modifies?`

Tree-shaped file listing with human-concrete role labels (NOT
symbol/code-level). A non-Python reviewer should grasp ownership:

```
src/feather_etl/
  cli.py                 ← entry point users type
  commands/init/
    core.py              ← does the init work
    cli.py               ← terminal wrapper for init
    cli_test.py          ← tests for this commit
```

### `### What the user sees externally — Before/After state?`

Observable behavior change. Zero internal symbols. Example:

> **Before:** running `feather init` errors (command doesn't exist).
> **After:** running `feather init` in an empty directory creates
> `feather.yaml` containing the template (`sample_threshold`, commented
> `sources:` placeholder). Process exits 0.

The exact wording of any user-facing message goes here — the contract is
byte-for-byte; both production code and test code reference the same
literal string.

### `### How the runtime flows after this commit — what calls what?`

Numbered ordered list with nested bullets — viewport-robust (wraps gracefully on narrow widths) and syntax-highlighted via inline-code spans. Mid-detail: name the symbols being called (unlike §1's file tree which is concept-level). Example:

1. CLI receives `feather init`.
2. `cli.py` root app dispatches to `init` command.
3. `init()` calls `core.init_project(Path.cwd())`:
   - `_stamp_feather_yaml` writes `FEATHER_YAML_TEMPLATE` to `cwd/feather.yaml`.
   - Records `files["feather.yaml"] = "created"`.
4. Returns `InitResult`; process exits 0.

This is the only §1 sub-perspective that synthesizes information not
present elsewhere in the change folder — tasks.md / spec.md / design.md
don't carry symbol-level execution flow. Uniquely load-bearing.

**Two structural views (1.4 file tree, 1.6 runtime trace), one observable
view (1.5 Before/After), one concept view (1.1 What ships), one scenario
view (1.2 In scope), one scope-guard view (1.3 Out of scope). Six angles,
each earning its place by serving a different reader.**

### Optional: layer-map table for cross-layer commits

For commits that touch **5+ files OR 3+ distinct layers with cross-layer
dependencies**, optionally add a layer-map table after §1.6 as a
side-by-side overview. For the typical commit (the 90% case), the Layer
prefix on §5's H3 headings carries the same information at the point of
need; the table is decoration.

When added, the table follows this shape (Layer / File / Responsibility
with bulleted cells):

| Layer       | File          | Responsibility |
|-------------|---------------|----------------|
| Verb (Core) | `init/core.py`| • Template constant<br>• Stamper writer<br>• Orchestrator |

The optional/default-off treatment is empirically grounded: a 4-agent
implementation stress test (Sonnet + Opus × Commit 1 + Commit 2) all
landed correct code without the layer-map table, with three of four
agents independently flagging the same caveat — "for a commit touching
5+ files across 3+ layers, the table view might earn its keep."

---

## §2 Key terms

Codebase-specific terms, decorators, types, or patterns the reader will
encounter in later sections. **NOT Python language features** — assume
the reader knows `@dataclass`, `Path.cwd()`, etc.

Each term gets a bold header + sub-bullets, one idea per bullet:

```
- **`register(app)` pattern.**
  - Each verb exports `register(app: typer.Typer)` that attaches its
    `@app.command(...)` to a Typer app passed in.
  - Adding a new verb is one line in root `cli.py`.
  - Alternative would be module-level `@app.command` decorators triggered
    by import; the `register` pattern avoids that import-side-effect
    spaghetti.
```

If a term recurs from a previous commit's plan, link back rather than
restating: `"See §N's plan for the `_stamp_*` pattern; same shape."`

If this commit introduces no new terms, skip §2 — leave the header with
`_No new terms this commit._` as the body.

---

## §3 Key ideas

Named design insights for THIS commit — count per audit, not a target (per [[skill-authoring]] §"Risk-driven, not count-driven"). **Insights, not directives** — each names a pattern, points to where it lives, delivers a verdict.

**No DO-NOT lists.** Scope-deferral directives belong in §1.3 (Out of
scope) which carries the binding force; §3 stays purely insight-focused.

Same bullet style as §2:

```
- **Verb-template skeleton.**
  - What matters in this commit is the *shape* every later verb copies —
    not the 4 lines of behavior.
  - `init_project` is 4 lines today; what matters is that those 4 lines
    live in `core.py`, the Typer adapter lives in `cli.py`, and the root
    wires it through `register(app)`.
  - Every later verb (`source add`, `curate`, `extract`) copies this triple.

- **Mutation during construction, immutability after.**
  - `_stamp_feather_yaml` mutates `files` in place while `init_project`
    composes the result.
  - Once wrapped in frozen `InitResult`, it's locked.
  - Build freely, then seal.
```

If a commit has no genuine design insights worth naming (rare — most non-trivial commits do), set the section body to `_No new design ideas this commit._` and keep moving. Do NOT add an in-document cross-section ref (e.g., `see §1.3`) — per [[skill-authoring]] §"No in-document cross-section references", such refs are navigation noise that breaks on renumbering.

---

## §4 Tests to write

H3 heading per scenario, using the **verbatim spec.md title** + scenario
ID in parens. Each scenario gets BDD English Given/When/Then up top,
code in an *Implementation hints* sub-bullet:

```markdown
### Init with no arg stamps files into CWD, if empty (`init.1a`)

- **Given:** an empty working directory.
- **When:** the user runs `feather init` with no arguments.
- **Then:** the command exits 0 and a `feather.yaml` file exists in that
  directory.
- *Implementation hints:*
  - Set up with `monkeypatch.chdir(tmp_path)` and `CliRunner()`.
  - Invoke as `runner.invoke(app, ["init"])`.
  - Assert `result.exit_code == 0` and `(tmp_path / "feather.yaml").is_file()`.
```

**English is the contract. Code is the suggestion.** An agent reading
the Then line should know what to assert before reading the
*Implementation hints*. The hints are only there because writing pytest
commands from scratch is friction.

Test function names mirror scenario titles verbatim (snake-cased) — per
`docs/testing.md`. The H3 heading already gives the agent the right
function name by snake-casing.

---

## §5 Implementation outline

Two structural rules govern §5:

**Floor, not ceiling** — open with an italicized lead-in (matching §§2–8 convention):

> _The minimum impl needed to pass the above tests — a floor, not a ceiling. Add more only if a test demands it._

This converts §5 from prescription ("you must write these") into floor
("nothing less; more if the test demands"). Without this lead-in,
agents (and reviewers) read §5 as a ceiling — every item required.

**H3 grouped by file with Layer prefix** — each H3 takes the form:

```
### <Layer> — <file path> — <one-line role>
```

The Layer prefix carries the architectural classification that used to
live in a separate §1.8 layer-map table. Established labels:

- `Verb (Core)` — pure-logic core of a verb (e.g., `commands/init/core.py`)
- `Verb (CLI)` — Typer adapter for a verb (e.g., `commands/init/cli.py`)
- `Root` — root Typer app (e.g., `src/feather_etl/cli.py`)
- `Test` — test files (e.g., `commands/init/cli_test.py`,
  `commands/init/core_test.py`)

For files that don't sit cleanly inside a layer (e.g., `pyproject.toml`,
`docs/testing.md`, repo-level config), **omit the Layer prefix** and use
the simpler form:

```
### <file path> — <one-line role>
```

Inside each H3, deliverables use bold-header + sub-bullets when more than
one deliverable lives in the file; flat bullets when only one:

```markdown
### Verb (Core) — `core.py` — template + dataclass + stamper + orchestrator

- **YAML template constant.**
  - Module-level `FEATHER_YAML_TEMPLATE: str`.
  - Verbatim content from spec Req 2.
  - Uses `sample_threshold: 100_000`.
- **Frozen result dataclass.**
  - `@dataclass(frozen=True) class InitResult`.
  - Field 1: `target: Path`.
  - Field 2: `files: dict[str, Literal["created", "skipped"]]`.
- **Per-file stamper.**
  - `_stamp_feather_yaml(target, files)`.
  - Writes the template to `target/feather.yaml`.
  - Records `files["feather.yaml"] = "created"`.
- **Orchestrator.**
  - `init_project(target: Path) -> InitResult`.
  - Allocates `files = {}`.
  - Calls `_stamp_feather_yaml(target, files)`.
  - Returns `InitResult(target=target, files=files)`.

### Verb (CLI) — `commands/init/cli.py` — Typer adapter for the verb

- Exports `register(app)`.
- Decorates a parameterless `init()` function.
- Body calls `core.init_project(Path.cwd())`.

### `pyproject.toml` — console-script wiring

- `[project.scripts]`: `feather = "feather_etl.cli:app"`.
```

(Note the absence of Layer prefix on the `pyproject.toml` H3 — it's not
a layer, just a file the commit touches.)

**Mix of bold-headers and flat bullets is principled, not lazy** — use
bold when sub-ideas need separating, flat when a single deliverable's
ideas read as one bullet list.

When a §5 deliverable LOOKS like YAGNI but actually isn't, call it out
explicitly inline (e.g., the no-op `@app.callback()` is required this
commit to keep Typer in multi-command mode — an agent told "strip YAGNI"
would otherwise delete it).

---

## §6 Open questions — template stub

```markdown
## 6. Open questions

_Record any open questions surfaced during plan walk; executor may add more mid-impl._

_None._
```

If there's a genuine open choice (e.g., literal vs metadata-based
version pin), use a single bullet stating the question + the
provisionally-chosen default + an invitation to push back. Otherwise the
section stays empty.

---

## §7 Decisions taken — template stub

```markdown
## 7. Decisions taken

Decisions that change a previously-stated contract or convention. Note
any out-of-band follow-up.

_None._
```

Use when the plan walk surfaces a needed change to spec / design /
testing / tasks docs. Each entry names the change + the out-of-band
follow-up (e.g., "update `spec.md` Req 2 from `100000` to `100_000`").
This is how plan-walk findings cascade out to the rest of the change
folder.

---

## §8 Deviations — template stub

```markdown
## 8. Deviations

Executor deviations from this plan, recorded before commit with a
one-line rationale.

_None._
```

This section is filled by `siraj-openspec-execute-plan`, not by the plan
author. If the executor adds a deliverable, drops one, renames a symbol,
or otherwise diverges from §5 during impl, they record the deviation
here before the commit lands.

---

## Tone rules

- **Question-style H3 headings** throughout §1 ("What this commit ships?"
  not "Overview").
- **§1.1 mixes behavior + architecture** — executive-summary tone; do not
  try to discipline it into "architecture only" or "behavior only."
- **§1.3 leads with a binding directive line** ("**The following are
  deliberately deferred from this commit. DO NOT include them in the
  implementation…**") so the scope guard has force.
- **§3 is insights only** — no DO-NOT directives, no scope deferrals.
  Those belong in §1.3.
- **§5 H3s use `<Layer> — <file> — <role>` for layered files**, omit the
  Layer prefix for non-layer files (e.g., `pyproject.toml`).
- **Preferred bullet style:** bold header + sub-bullets, one idea per
  bullet. Apply wherever a list has 2+ items with distinct sub-ideas.
- **BDD English up top, code as *Implementation hints* below.** §4's
  Given/When/Then is the contract; code is the convenience.
- **Floor-not-wall preamble** at the top of §5.
- **No forward references** — to other plans, other commits, future
  sections. `tasks.md` carries cross-commit links (via the checkbox list);
  `design.md` carries Decision N anchors; the plan stays self-contained.
- **Empty stubs in §6/§7/§8** are templates that fill in over the
  commit's life. Don't delete them just because they're empty.
- **Cross-doc anchors verbatim** — spec.md scenario titles word-for-word;
  symbol names exact; scenario IDs in canonical form.

---

## Checklist (for repeat-use scans)

After drafting, scan and tick:

**Section presence:**
- [ ] §1 Full picture — present, with all 6 H3 sub-perspectives?
- [ ] §2 Key terms — present (or `_No new terms this commit._`)?
- [ ] §3 Key ideas — only named insights that earn their slot (per audit), no DO-NOT lists?
- [ ] §4 Tests to write — H3 per scenario with verbatim title + scenario ID?
- [ ] §5 Implementation outline — floor-not-wall preamble + H3 per file with Layer prefix?
- [ ] §6 Open questions — present (`_None._` if empty)?
- [ ] §7 Decisions taken — present (`_None._` if empty)?
- [ ] §8 Deviations — present (`_None._` if empty)?

**Sub-perspective coverage in §1:**
- [ ] What this commit ships? — mixed behavior + architecture exec summary
- [ ] What is in scope? — scenario IDs + verbatim titles
- [ ] What is out of scope? — themes, no commit numbers, leads with binding directive line
- [ ] What files this commit creates/modifies? — tree with human-concrete labels
- [ ] What the user sees externally — Before/After state? — observable, zero internals
- [ ] How the runtime flows — what calls what? — indented symbol-level trace

**Optional in §1 (add only when warranted):**
- [ ] Layer-map table — only if commit touches 5+ files OR 3+ layers with cross-layer dependencies

**Structural rules:**
- [ ] §1.3 opens with the binding directive line (`DO NOT include them in the implementation`)?
- [ ] §3 carries insights only — no DO-NOT directives, no scope deferrals?
- [ ] §4 has BDD English up top, code as *Implementation hints* sub-bullet?
- [ ] §5 has the "Floor, not ceiling" italicized lead-in?
- [ ] §5 H3s use `<Layer> — <file> — <role>` for layered files, omit Layer for non-layer files?
- [ ] §5 deliverables use bold-header + sub-bullets where they have multiple sub-ideas?
- [ ] No forward references (`see §N`, "Commit M does X")?
- [ ] No dead-link footnotes (`(Decision N)` without inline content)?
- [ ] No restatement of training data (pytest commands, etc.)?
- [ ] No restatement of `docs/testing.md` cadence?
- [ ] Each fact has ONE canonical home — not duplicated in spec / design / tasks?

**After ticking:** run `review-document` for the final lens pass.

---

## Anti-patterns

- **"Land final shape now" trap.** Decision N in design.md shows the
  across-the-change target shape; an agent (or human) reads this as
  "land all of it in Commit 1." This is the single biggest failure
  mode. Counter with §1.3's binding directive line and per-commit
  specifics in tasks.md's checkbox text.
- **DO-NOT directives in §3.** §3 is insights only. Scope-deferral
  directives belong in §1.3 — putting them in §3 splits the scope
  guard across two sub-sections and weakens both.
- **Empty section noise.** Template stubs in §6/§7/§8 are useful;
  empty bullets elsewhere are noise. Delete content-free sub-sections
  (e.g., §2 when no new terms; §3 when no new design ideas).
- **Forward references to other plans.** "See Commit 2's plan" creates
  an inherited expectation that breaks when Commit 2's plan doesn't
  exist yet. Each plan stands alone.
- **Over-detailing what `testing.md` covers.** RED→GREEN→coverage
  cadence lives in `testing.md`. Don't restate per commit.
- **Stating training data.** pytest invocations, standard library
  methods, Python conventions — drop. Trust the reader's training.
- **Code embedded in prose.** §4's Given/When/Then in English; code in
  *Implementation hints* sub-bullets. Don't mix.
- **Run-on paragraphs in §3 or §5.** Always bullet when distinct ideas
  exist.
- **Layer-map table for small commits.** The §1 layer-map table is
  optional, default-off. Adding it to a 3-file commit is decoration —
  §5's Layer-prefix H3s carry the same content. Add the table only
  when the commit's surface earns it (5+ files OR 3+ layers).

---

## Section-by-section role for the executor

The plan file feeds `siraj-openspec-execute-plan`. Each section serves a different read by the executor:

- **§1** orients (file tree, runtime flow, before/after, scope guard).
  An executor who's read §1 loads the slice into head before any test
  or code. §1.3 is binding: nothing on its list lands this commit.
- **§2** provides the glossary for unfamiliar patterns mid-impl.
- **§3** gives design intent — the named insights that shape *how* the
  executor approaches the work (not what they MUST NOT add — that's §1.3).
- **§4** is the test list — executor writes these first (RED), runs them
  to confirm RED, then GREEN after impl.
- **§5** is the impl checklist. The Layer prefix on each H3 tells the
  executor exactly which file each deliverable lives in.
- **§6** lets the executor surface a question that wasn't resolved at
  plan-walk time.
- **§7** captures any contract changes the executor needs to make
  upstream (spec.md tweaks, testing.md updates).
- **§8** records any divergence from §5 before committing.

If `execute-plan` can't find a plan for the next section, it halts at pre-flight and routes the user back here. `execute-plan` never authors plans inline — that's this skill's job, and the plan must be committed before execute-plan will run.

---

## Commit the plan

After the plan is authored, ticked through the structural checklist,
and (optionally) polished via `/review-document` and validated via
`/siraj-openspec-stress-test-plan`, this skill commits the plan file
as its own pipeline step.

1. **Stage explicit path only:**
   ```
   git add openspec/changes/<change>/plans/<N>-<slug>.md
   ```
   Never `git add -A`. The plan file is the ONLY thing in this commit
   — `tasks.md`, `spec.md`, `design.md` should already be committed
   separately.

2. **Confirm staged set** is exactly the one plan file:
   ```
   git diff --cached --name-only
   ```

3. **Propose the commit message** to the user:

   > `<change>: plan for <N>. <subject>`

   Where `<subject>` mirrors §N's heading in `tasks.md` verbatim (matches
   `siraj-openspec-flow §Git commit conventions`).

4. **Ask:**

   > "§<N>'s plan ready. Commit as
   > `<change>: plan for <N>. <subject>`?
   > (a) commit now (default)
   > (b) revise the message first
   > (c) stop here without committing
   > Which?"

   Never commit without explicit approval. After committing, run
   `git status` once to confirm the working tree is clean.

5. **Hand off:**

   > "Plan committed. Next: `/siraj-openspec-execute-plan` will
   > implement §<N>'s tests + code in a separate commit."

   Do not auto-invoke execute-plan. The user owns the loop.

Per-section accounting: ONE commit from this skill (plan file only),
ONE commit from `siraj-openspec-execute-plan` (impl files + tasks.md
ticks). Two commits per section, mirroring the `## N. <subject>`
boundary in `tasks.md`.

---

## History

**May 2026 — 8 → 6 §1 sub-perspectives.** Two sub-perspectives were
dropped from §1 after empirical stress-testing:

- **§1.6 "What's still missing after this commit?"** — duplicated
  tasks.md's across-change arc view. Verified by a 4-agent
  plan-generation test (Sonnet + Opus × Commit 1 + Commit 2) that
  reconstructed equivalent plans from spec/design + a stripped tasks.md
  without using §1.6 content. Tasks.md is the canonical arc view; the
  per-commit plan does not duplicate it.

- **§1.8 "The layer map"** — duplicated content carried by §5 H3
  headings once the Layer prefix convention was adopted. Verified by a
  4-agent implementation test (same model split) that landed correct
  code with 100% line+branch coverage, zero deviations, and zero layer
  confusion. Three of four agents independently flagged the same caveat:
  "for a commit touching 5+ files across 3+ layers, the table might
  earn its keep" — so §1.8 was preserved as an optional sub-perspective
  for cross-layer commits, not the default.

**Same revision — §3 reshape.** YAGNI **DO NOT** directives moved from
§3 to §1.3. The empirical agent-discipline evidence (Opus R1 adding 4
fields when the directive was permissive vs Opus R2 adding 2 fields
when the directive was binding) was preserved by giving §1.3 directive
language in its body lead-in. §3 became insights-only — named design
ideas, no scope-deferral lists.

**Same revision — §5 Layer prefix.** §5's H3 headings gained the form
`### <Layer> — <file> — <role>` for layered files (Verb (Core),
Verb (CLI), Root, Test) and stayed `### <file> — <role>` for non-layer
files (pyproject.toml, docs/testing.md). This compensates for §1.8's
removal: the Layer information that lived in the §1.8 table now sits
inline with each impl-deliverable group.

---

**Next:** `/review-document` on the freshly-authored plan (structural lens pass), then for load-bearing sections `/siraj-openspec-stress-test-plan` (empirical validation), then the §Commit the plan step above, then `/siraj-openspec-execute-plan` for the implementation. See `siraj-openspec-flow` for the full Stage 6 pipeline.
