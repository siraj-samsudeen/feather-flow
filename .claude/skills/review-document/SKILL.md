---
name: review-document
description: >
  Review any agent-produced document — spec, design, plan, tasks, README,
  skill SKILL.md, ADR, RFC — against seven structural lenses and five
  rewrite operations. Use this skill whenever Siraj asks for a doc to be
  reviewed, polished, or made consistent. Trigger on phrasing like "review
  this doc", "check this doc", "is this clean?", "apply the lenses",
  "polish this", "lens pass", or proactively at the end of authoring any
  structured document. Use the checklist appendix on repeat passes.
---

# Review Document Skill

Siraj reads documents the way he reads code: top-down, looking for the
load-bearing idea, the canonical home of each fact, and the cost-vs-signal
tradeoff of each section. Documents that get produced by agents tend to
state the obvious, embed forward references, duplicate facts across
sections, and present prose where bullets would scan. This skill is the
review pass that catches those failure modes before a document is
considered final.

It's the **sibling** of authoring-specific skills (e.g.,
`siraj-openspec-create-plan`, future `spec-walkthrough` if/when it exists).
Authoring skills give structure-from-scratch; this skill is the polish
layer that sits on top.

---

## When to use

- After authoring a new document, as a self-check before handing it to Siraj.
- When Siraj asks for any document to be reviewed, polished, or made consistent.
- When two documents disagree (e.g., a plan says one thing, a tasks list says another) and you need to identify the canonical home.
- Before committing a final doc artifact.

## When NOT to use

- For purely conversational text or quick replies — the lenses are for *structured documents*.
- For code reviews — that's `code-review` or a similar code-focused skill.
- For first-draft brainstorming — let the draft exist before applying lenses.

---

## The Seven Lenses

Apply each as a question. If the answer surfaces an issue, name it.

### Lens 1 — Multi-altitude perspective

Does the document say the same thing at multiple abstraction levels, each in
language fit for that audience? A reader should be able to skim at one
altitude (concept, observable behavior, structure, dynamics, detail) and
then descend for depth where needed. Single-altitude documents force the
reader to derive other altitudes mentally.

**Symptom:** The document has only one view (e.g., only a file tree, or
only a prose paragraph). A reader who needs the observable-behavior view
has to compose it from scattered hints.

**Fix:** Add the missing altitude (e.g., add a Before/After state block
alongside a file tree, or a runtime flow trace alongside a layer table).

### Lens 2 — Bullet structure

For any list of 2+ items where each item has distinct sub-ideas: **bold
header per item, sub-bullets with one idea each.** No run-on prose, no
embedded code mid-sentence, no compound bullets that mash distinct ideas
together.

**Symptom:** Long sentences with semicolons or "and" chains. Code fragments
embedded in prose. Bullets that pack 3+ ideas into one line.

**Fix:** Split the compound bullet into a bold header + sub-bullets, each
carrying one idea.

### Lens 3 — Reference cleanliness

Forward references (`see §N`) and dead-link footnotes (`(Decision N)`
without inlined content) earn their place only when the reader is helped
by the pointer. Most don't.

**Symptom:** "see §N" within the same document; "(Decision N)" without any
explanation of what Decision N says; cross-doc references to sibling files
the reader has to open.

**Fix:** Inline the relevant fact where it's needed, OR drop the pointer.
Forward references to deferred sections, future commits, or future plan
files are forward references — drop them.

### Lens 4 — Don't state the obvious

Content that lives in agent training data, in tool documentation, or in a
canonical project doc (e.g., `testing.md` for cadence) doesn't need to be
restated in every artifact that touches it.

**Symptom:** Standard pytest invocations being explained step-by-step. The
TDD cadence being restated at the top of every plan. Standard library
methods being defined.

**Fix:** Drop the restating. Trust the reader's training data and the
canonical project doc.

### Lens 5 — Canonical home

Each fact has ONE home. Other docs reference it (or, better, are agnostic
to it). When the same fact lives in two places, they will drift.

**Symptom:** The same template body appears in spec.md, design.md, AND a
test fixture. The same scenario description appears with slightly
different wording in three places. The same detection mechanism is named
explicitly in spec.md (where it doesn't belong) and design.md (where it
does).

**Fix:** Pick the canonical home, trim the duplicates, replace with
references where genuinely needed. Contract → spec. Decisions → design.
Implementation guidance → plan or code.

### Lens 6 — Directive language where guardrails matter

When something is load-bearing, the wording should be directive (**DO NOT
add X this commit**), not permissive (X waits for...). Permissive wording
invites slip; directive wording catches it.

**Symptom:** YAGNI items described as "wait for the commit that needs
them" — agents pattern-match this as permission to land them now "to be
ready." Project conventions described as "preferred" — agents reach for
non-preferred alternatives.

**Fix:** Sharpen to directive. Use **DO NOT**, **MUST**, **SHALL** for
contractual; **avoid**, **prefer not to** for guidance.

### Lens 7 — Verbatim cross-doc anchors

When a doc references an artifact defined elsewhere (a spec scenario, a
test function name, an API symbol), use the canonical form *verbatim*.

**Symptom:** A plan's test name is `test_init_creates_yaml` but the spec
scenario it pins is "Init with no arg stamps files into CWD, if empty" —
no traceable relationship between the two. Tests passing tells you
nothing about which scenarios they cover.

**Fix:** Test names mirror scenario titles verbatim (snake-cased). Symbol
references use the exact name. Scenario IDs use the canonical
`<capability>.<num><letter>` form.

---

## The Five Rewrite Operations

When a lens surfaces an issue, these are the surgical responses.

### 1. Restructure

Same content, reorganized for navigability. Adding H3 sub-headings, splitting
a long section into named sub-sections, regrouping by file or by concept.
Triggers: Lens 1, Lens 2.

### 2. Sharpen

Replace descriptive prose with directive wording where guardrails matter.
Triggers: Lens 6.

### 3. Consolidate

Drop duplicates; pick the canonical home. Triggers: Lens 5.

### 4. Inline

Replace `see §N` pointers with the actual fact at the point of need.
Triggers: Lens 3.

### 5. Subordinate code to English

BDD English up top (Given/When/Then in plain words), code below as
*Implementation hints*. Triggers: Lens 2 (when code is mid-sentence) and
Lens 4 (when code restates training data).

---

## Tone rules

- Plain English. No hedging, no qualifiers that earn nothing.
- Call YAGNI as YAGNI. If a section lands unused code, say so plainly.
- No padding. Every sentence earns its place. Strip meta-commentary
  ("Let me orient before…", "I'll skip X because…").
- Cross-file connections explicit, not inferred.
- Question-style H3 headings where they help ("What this commit ships?")
  instead of label-style ("Overview").

---

## Checklist (for repeat-use scans)

Scan the document and tick each:

- [ ] **L1** — Multiple altitudes present where the doc warrants? (Concept, observable, structural, dynamic, detail — pick the relevant set.)
- [ ] **L2** — Lists of 2+ items use bold header + sub-bullets, one idea per bullet?
- [ ] **L3** — No `see §N` forward references within the doc? No dead-link `(Decision N)` footnotes? No forward refs to future commits / plans / files?
- [ ] **L4** — No restatement of agent training data (pytest commands, standard library, etc.)? No restatement of canonical project docs?
- [ ] **L5** — Each fact has ONE canonical home? Duplicates across docs identified and trimmed?
- [ ] **L6** — Guardrail items use directive language (**DO NOT**, **MUST**)?
- [ ] **L7** — Cross-doc anchors verbatim (scenario titles, symbol names, IDs)?
- [ ] **Tone** — No padding, no hedging, YAGNI called plainly?

Each unticked lens → identify the symptom, apply the matching rewrite operation.

---

## Anti-patterns

- **Applying lenses to a first-draft brainstorm.** The lenses are for
  structured documents; a brainstorm draft needs to exist first.
- **Mechanically inlining every `see §N`.** Some references genuinely
  help the reader. Inline only when the pointer carries no value or
  when the reference is forward.
- **Forcing multi-altitude on a single-altitude doc.** Specs are
  deliberately single-altitude (the contract). Don't add altitudes that
  the doc's purpose doesn't warrant.
- **Codifying every lens as a hard rule.** The lenses are *analytical
  questions*, not absolute rules. A pattern that violates a lens but
  serves the document's purpose is fine; document the why.
</content>
