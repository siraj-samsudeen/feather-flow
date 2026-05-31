---
name: siraj-openspec-create-design
description: Walk through OpenSpec design decisions one at a time using Format A — Question / Alternatives with pros and cons / Recommendation. Identifies material implementation choices the spec leaves open, drills into each with interactive pushback, and accumulates them into design.md. Includes a "smaller choices" mode for low-impact decisions that the user wants to accept in bulk without drill-in. Use when authoring design.md for a new OpenSpec change after the spec is locked, when the user says "walk me through the design decisions", "let's design [the verb/feature]", "what design choices do we have", or pauses on an OpenSpec change with no design.md yet. Defaults to the most recently modified change folder if no name is given.
---

# Create design for an OpenSpec change in Format A

This skill formalises the design-walkthrough rhythm from feather-etl's `add-feather-init` session: surface every material implementation choice the spec leaves open, drill into each one as its own decision (Format A — Question / Alternatives with pros and cons / Recommendation), invite pushback, lock the outcome, and accumulate them into a clean `design.md`.

**Where this fits:** Stage 3 of the siraj-openspec pipeline — invoked after `spec.md` is locked (per the numbering + scenario title conventions in `siraj-openspec-flow`), before `tasks.md` is hand-authored. See `siraj-openspec-flow` for the full pipeline diagram and sibling-skills graph.

## When to use

- A new OpenSpec change has a locked `spec.md` and needs `design.md` authored.
- The user says "walk me through the design decisions", "let's design [the verb]", "what design choices do we have", or "/siraj-openspec-create-design".
- Proactively: after spec authoring wraps, suggest *"Spec is locked. Want me to walk through the design decisions one at a time in Format A?"* — take a no gracefully.

## When NOT to use

- The user is implementing, reviewing, or troubleshooting — this skill is design-authoring only.
- `design.md` already exists and is locked — use `/review-document` or `siraj-openspec-review-change` for polish, not this skill.
- The "decisions" you'd surface just restate the spec — flag and skip; they don't earn a Format A walkthrough.

## What this skill does NOT do

- Does not author `spec.md` (hand-edited per orchestrator conventions).
- Does not author `tasks.md` (hand-edited per the tasks.md per-commit shape in `siraj-openspec-flow`).
- Does not validate cross-doc consistency (that's `siraj-openspec-review-change`).
- Does not commit anything — Step 5 writes `design.md`; user commits.

## What it produces

A `design.md` at the change folder containing:

- **`## Decisions`** section with each big decision in Format A (Question / Alternatives with pros and cons / Recommendation, sometimes plus a "what this locks" note for implementer guidance)
- **`## Smaller choices`** section with one-line statements for decisions accepted without drill-in
- **`## Risks / Trade-offs`** section listing accepted trade-offs from the decisions above

The format matches the project's `rules.design` in `openspec/config.yaml` (every project that uses this skill should have Format A referenced there).

## Step 1 — discover the change folder and its spec

If the user named a change, use it. Otherwise:

```bash
ls -td openspec/changes/*/ 2>/dev/null | head -3
```

Pick the most-recently-modified. Confirm if multiple recent candidates.

Read `proposal.md` and `specs/<capability>/spec.md`. The spec is the source of behavioral truth — material design choices are anything the spec leaves open (architecture, code structure, framework, error handling, data shapes, output conventions, etc.).

## Step 2 — build the candidate decision list

Scan the spec for what it does NOT decide. Common categories the spec usually leaves open:

- **CLI / interface framework** — Typer, Click, argparse, etc.
- **Verb / endpoint registration pattern** — flat vs nested vs hybrid
- **API shape of the core module** — one function vs many vs class vs pipeline
- **Return-value contract** — dict vs dataclass vs Pydantic vs typed events
- **Template / resource management** — string constants vs files vs Jinja
- **Output streams** — stdout vs stderr split for status verbs and data verbs
- **Atomicity / partial-failure semantics** — best-effort vs two-phase vs rollback
- **Mode flags** (e.g., `--dev`, `--strict`) — when behavior splits along an operator-chosen axis
- **Error-handling pattern** — typed exceptions vs Python defaults
- **Source path / location detection logic** (where applicable)

Build a numbered candidate list. Surface to the user:

```
I see these as material decisions for design.md:

1. CLI framework
2. Verb registration pattern
3. core/cli API shape
4. Return value shape (dataclass / dict / events)
5. Template management
6. stdout vs stderr split
7. Atomicity / partial-failure semantics

Plus these candidate smaller choices that may not warrant full drill-in:

A. Output formatting (plain vs Rich)
B. Color / TTY handling
C. Help-text auto-generation
D. Error-message wording

Confirm the big list (add/remove/reorder), and tell me which smaller
choices you want as separate items vs lumped under "Smaller choices."
```

Wait for the user's confirmation before drilling in. They may add decisions you missed, remove ones the spec actually settles, or reorder.

## Step 3 — drill into each big decision in Format A

For each decision in the confirmed order:

1. **Present in Format A.** Always three sections:
   - **The question** (one paragraph: what we're deciding and why it matters). Include why-this-matters consequences — what changes if we pick wrong, what later verbs inherit.
   - **Alternatives** (real options, each labeled, with concise pros AND cons). At least 2 alternatives. Don't manufacture straw-man options just to have a third.
   - **Recommendation** (which one + specific reasons that survived audit — count is an output, not a target + name the trade-offs being accepted). Optionally a "what this locks" code sketch when the implementer needs concrete guidance.

2. **Explicitly invite pushback.** End the decision with something like *"Confirm Option X and I'll bring the next decision. Or push back on any pro/con, or propose a different option."*

3. **When pushed back:**
   - **Audit honestly.** Don't defend reflexively. Re-read your own pros/cons through the user's lens.
   - **Concede when wrong.** Explicit reversal is better than silent capitulation. ("You're right and I was overcomplicating. Re-recommending Option C with this reasoning…")
   - **Steelman the user's option** before recommending against it, if you still disagree.
   - Re-present the (possibly revised) recommendation and re-invite pushback.

4. **When the user proposes a fifth option** you hadn't considered:
   - Add it to the alternatives list with honest pros/cons.
   - Re-assess the recommendation.

5. **Lock the decision** when the user says "agreed", "lock it", "yes", or picks an option. Add to your running tally. Move to the next.

## Step 4 — smaller choices panel

When the big decisions are done, surface the smaller choices as a panel:

```
Pending smaller decisions — quick panel

| # | Decision | My recommendation | Worth drilling in? |
|---|---|---|---|
| 9 | Output formatting library — plain vs Rich | Plain — no dep, status verbs don't need styling | Maybe |
| 10 | Version pin floor in pyproject template | Read from package's __version__ at runtime | No — implementation detail |
| 11 | Color / TTY handling | Follow framework defaults + NO_COLOR | No — micro-decision |
...

TL;DR: drill into #9 if you care about visual polish; accept the rest.
If you accept all, design is done and we move to the design.md write.
```

Take any "drill into N" requests as a return to Step 3 for that item. Otherwise lock all accepted smaller choices.

## Step 5 — write design.md

Once everything is locked, write a single consolidated `design.md` at `openspec/changes/<change>/design.md`:

```markdown
## Decisions

Brief one-paragraph preamble explaining the format being used and the rough
ordering (e.g., "foundational decisions first, specific decisions later;
later decisions build on earlier ones").

### Decision 1 — <title>

#### The question

<paragraph>

#### Alternatives

**A. <name>**
- **Pros:** ...
- **Cons:** ...

**B. <name>**
- **Pros:** ...
- **Cons:** ...

#### Recommendation

**B — <name>.** <reasons>

#### What this locks (optional, when needed)

<code sketch or specific guidance for the implementer>

---

### Decision 2 — ...

[same shape]

---

## Smaller choices

These are implementation details accepted without full Format A walkthrough.

**9. <title>** — <one-line decision + brief rationale>.
**10. <title>** — <one-line decision + brief rationale>.
...

## Risks / Trade-offs

- **<risk>** — <mitigation or acceptance reason>
- ...
```

## Step 6 — propose a review

After design.md is written, suggest two polish steps before committing the change folder:

1. **`review-document`** on design.md — the structural lens pass. Catches forward references, dead-link footnotes (e.g., `(Decision N)` without inlined content), run-on Recommendation paragraphs, smaller-choices duplication, and the seven structural lens issues generally. Run this FIRST — fixes are local to design.md.
2. **`siraj-openspec-review-change`** — cross-doc consistency audit (spec ↔ design ↔ tasks). Catches contradictions, stale references, scenario-to-commit mapping integrity. Run SECOND — once design.md is structurally clean, cross-doc audit isn't noisy.

Both catch different failure modes; the structural pass before the cross-doc audit means cross-doc findings aren't drowned out by formatting noise.

## Don'ts

- **Don't surface every micro-decision as a full Format A walkthrough.** Use the smaller-choices panel for things accepted without alternative-drilling. The user's time is finite.
- **Don't write design.md until decisions are locked.** Premature writing risks rework if the user changes their mind during drill-in.
- **Don't pre-commit edits to the change folder mid-walkthrough.** Hold all design.md edits until Step 5; surface them as a single review-ready diff.
- **Don't dispatch subagents for the walkthrough itself.** The walkthrough is interactive with the user; subagents are for pre-commit review (different skill).
- **Don't fold in spec restatements as decisions.** A "decision" that just repeats what's already in the spec doesn't earn a slot — flag it and skip.

## Cost

A full walkthrough for ~8 big decisions typically takes 30-60 minutes of conversation. Smaller-choices panel adds maybe 5 minutes. Design.md write is one tool call.

---

**Next:** `/review-document` on `design.md` (structural lens pass — Step 6 above), then hand-author `tasks.md` per the tasks.md per-commit shape in `siraj-openspec-flow`, then `/siraj-openspec-review-change`. See `siraj-openspec-flow` for the full pipeline.
