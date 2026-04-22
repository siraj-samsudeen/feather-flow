---
description: Research the outside world before design — ecosystem / feasibility / comparison modes, dispatch parallel subagents with Context7 + web, produce a confidence-calibrated research doc
argument-hint: '<short description of what to research, e.g. "SCD-2 patterns for DuckDB">'
disable-model-invocation: true
allowed-tools: Read, Write, WebSearch, WebFetch, Bash(mkdir:*), Bash(date:*), Bash(npx:*), Agent, AskUserQuestion
---

You are running the `/research_external` command. Produce a **confidence-calibrated** research doc about the outside world — libraries, patterns, SOTA vs deprecated, ecosystem conventions — for the area the user wants to investigate. Not a recommendation brief. Not a decision. A map of what the world currently does, with honest uncertainty marked.

The research doc is the input to a later `/brainstorming` session. Its sibling `/research_codebase` maps the inside; this one maps the outside. Run whichever you need — or both.

User's request: `$ARGUMENTS`

If `$ARGUMENTS` is empty, stop and ask what the user wants researched.

---

## Phase 0 — Clarify, then classify

### 0a. Clarity check (skip if input is already specific)

Before classifying, look at `$ARGUMENTS` for vagueness signals:

- **Missing intent verb** — input doesn't make clear whether the user wants to *explore*, *compare*, or *check feasibility*
- **Missing or too-broad domain** — e.g. "research scheduling" (scheduling for what?), "look at caching" (caching where?)
- **Missing scope boundary** — nothing to stop a subagent from returning a textbook chapter
- **Multiple plausible readings** — the input could fit two or more modes
- **Very short** — fewer than ~6 meaningful words

If **any** signal fires, do NOT guess a mode. Use `AskUserQuestion` (or a plain chat prompt if that's easier) to ask up to three clarifying questions in a single batch:

1. **What kind of answer would be most useful?**
   - "I'm exploring what exists in the ecosystem" → points to ecosystem mode
   - "I have a shortlist and need to pick one" → points to comparison mode
   - "I'm checking whether something is possible at all" → points to feasibility mode
   - "Unsure — help me figure out the right zoom"

2. **What zoom level fits?**
   - "The whole domain — kickoff-level survey" (broad ecosystem)
   - "How to implement this specific feature — standard playbook" (narrow ecosystem)
   - "A single decision between known options" (comparison)

3. **In one sentence, what decision or action would this research help you make?** (free text — this becomes the final scope statement)

Synthesize the answers into a revised one-sentence scope + a provisional mode. Echo it back in one line:

> "Rewriting your scope as: _<sentence>_. Mode: _<mode>_. Proceed?"

Wait for the user's explicit OK — or another round of clarification — before moving to 0b. If the user answers Q1 with "Unsure", help them decide by reflecting on what `$ARGUMENTS` implied; do not force them to pick blind.

If `$ARGUMENTS` is already specific (no signals fire), skip 0a entirely and go straight to 0b.

### 0b. Classify the research mode

Using the clarified scope, pick one mode:

- **Ecosystem** (default) — "what exists for X?" Output is a landscape: standard stack, capabilities, patterns, pitfalls. Use when the problem domain is broad.
- **Feasibility** — "can we do X?" Output is YES / NO / MAYBE with required tech, blockers, risks.
- **Comparison** — "A vs B vs C" Output is a 5-column comparison table with conditional recommendations.

Announce your mode choice and a one-line rationale. Let the user override (`switch to feasibility`, etc.). Wait for their response before proceeding. Do not proceed without mode confirmation.

---

## Phase 1 — Generate questions / facets (in chat)

Shape depends on mode.

### Ecosystem mode — fixed facets, confirm focus
You will fan out to **four parallel subagents**, one per facet:
1. **Stack** — what libraries / frameworks the ecosystem converges on
2. **Capabilities** — what these tools make easy vs hard
3. **Architecture** — common patterns, component boundaries, integration shapes
4. **Pitfalls** — known failure modes, deprecated approaches, "don't hand-roll this" items

Draft a 1–2 line focus statement for each facet tailored to `$ARGUMENTS`, and ask the user to approve / edit / narrow. Wait for their response.

### Feasibility mode — scope the question
Draft 3–6 factual sub-questions whose combined answers determine the verdict (e.g. "does library X support Y?", "what's the read-throughput ceiling?"). Present the list; let the user approve / edit. Wait.

### Comparison mode — enumerate the candidates
Confirm the set of options to compare (no more than five). If the user gave two, ask whether there are obvious third options worth including. Wait for the final list.

Do not proceed until the user has explicitly approved the Phase 1 output.

---

## Phase 2 — Dispatch research (parallel, calibrated)

Once Phase 1 is approved:

1. **Dispatch subagents in parallel** — single message, multiple `Agent` tool calls — using `subagent_type: general-purpose`. Ecosystem = 4 subagents, Feasibility = 1 subagent covering all sub-questions, Comparison = one subagent per candidate option.

2. **Shared discipline — every subagent prompt MUST include this preamble verbatim:**
   ```
   You are researching the ecosystem outside this codebase. Your job is to report
   what the world currently does, grounded in current sources. You are not
   deciding anything for this project.

   Treat your training data as a HYPOTHESIS, not a fact:
   - Training is 6–18 months stale. Verify before asserting.
   - Prefer current sources over memory.
   - Flag uncertainty honestly — "I could not find" is a useful answer.

   Tool priority order:
   1. Context7 first, for library documentation:
      - If `mcp__context7__*` is available, use it directly.
      - Otherwise fall back to: `npx --yes ctx7@latest library <name> "<query>"`
        then `npx --yes ctx7@latest docs <libraryId> "<query>"`
   2. Official docs via WebFetch (use exact doc URLs, not marketing pages).
   3. WebSearch last, for ecosystem discovery and community consensus.

   Every factual claim MUST carry a confidence tag:
   - HIGH — multiple current authoritative sources agree
   - MEDIUM — one current authoritative source, or Context7 only
   - LOW — inferred, training-data only, or sources conflict

   Never pad findings. Never state unverified claims as fact. If sources
   contradict, surface the contradiction — do not pick a winner silently.
   ```

3. **Mode-specific instructions append to that preamble:**

   **Ecosystem subagent** (one per facet):
   ```
   Facet: <facet name + focus statement from Phase 1>
   Scope: <$ARGUMENTS, trimmed to the goal — NOT any solution the user is leaning toward>

   Produce a markdown section for your facet. Cover what the current ecosystem
   does, with enough concreteness (names, versions, approximate adoption signals)
   to be actionable. Tag every claim HIGH / MEDIUM / LOW. Cite the source URL
   (or Context7 library ID) that supports each HIGH / MEDIUM claim.
   Length budget: 300–600 words.
   ```

   **Feasibility subagent:**
   ```
   Scope: <$ARGUMENTS>
   Sub-questions: <approved list>

   For each sub-question, answer factually with confidence tag + source. Then
   produce a verdict section: YES / NO / MAYBE, required technology list,
   known blockers, risks. Do not rank or recommend.
   ```

   **Comparison subagent** (one per candidate):
   ```
   Option: <candidate name>
   Scope: <$ARGUMENTS>

   Produce a single row of a 5-column table: Option | Pros | Cons | Complexity
   (surface + risk, not time) | Recommendation (conditional — "rec if X").
   Follow with a 2–4 sentence rationale grounded in current sources. Tag every
   claim HIGH / MEDIUM / LOW.
   ```

4. **What subagent prompts MUST NOT contain:**
   - Any solution the user is currently leaning toward ("we're thinking of using X")
   - Framing that invites confirmation bias ("confirm that X is the right choice")
   - Hints about the phase / plan / ticket the research will feed

   External research *needs* the goal, unlike codebase research. It does not need — and must not receive — the user's preferred answer.

---

## Phase 3 — Collate, calibrate, save, commit

1. **Merge** subagent outputs into one markdown doc using named sections (not verbatim questions). Templates below.

2. **Strip recommendations** that don't belong. A researcher suggesting "we should use X" is out of scope — either delete the sentence or rephrase as a fact ("X is the dominant choice in the ecosystem, HIGH"). The Comparison mode's "Recommendation" column is allowed; free-form "you should" sentences elsewhere are not.

3. **Audit confidence tags.** Every factual claim should have one. If a subagent forgot, add LOW. If two subagents disagree on the same fact, note the contradiction rather than picking one.

4. **Determine the slug** — kebab-case of `$ARGUMENTS`, max ~5 words, prefixed `ext-` (e.g. `ext-scd2-duckdb`, `ext-scheduling-libraries`). The prefix keeps external docs visually distinct from codebase docs in the same folder.

5. **Get today's date:**
   ```bash
   date +%Y-%m-%d
   ```

6. **Create the research directory if needed:**
   ```bash
   mkdir -p docs/superpowers/research
   ```

7. **Write the doc** to `docs/superpowers/research/<YYYY-MM-DD>-<slug>.md` with this header and one of the mode-specific section sets:

   ```markdown
   # <Human-readable title> — External Research

   **Date:** YYYY-MM-DD
   **Mode:** ecosystem | feasibility | comparison
   **Scope:** <one-sentence description of what was researched>

   ## Confidence Legend
   - HIGH — multiple current authoritative sources agree
   - MEDIUM — one current authoritative source
   - LOW — inferred, training-data only, or sources conflict
   ```

   **Ecosystem sections:** `## Standard Stack`, `## Capabilities`, `## Architecture Patterns`, `## Common Pitfalls`, `## Don't Hand-Roll`, `## Sources`.

   **Feasibility sections:** `## Verdict`, `## Required Technology`, `## Blockers`, `## Risks`, `## Sub-question Findings`, `## Sources`.

   **Comparison sections:** `## Options` (5-column table), `## Rationale`, `## Sources`.

   A date header (not a commit SHA) is enough here — external research is about the world's state on a date, not the repo's state.

8. **Announce** in chat — do NOT commit. The user reviews first, then commits themselves if they want the doc tracked:

   > External research saved to `docs/superpowers/research/<YYYY-MM-DD>-<slug>.md`. Review it, then commit if you want it tracked:
   >
   > ```bash
   > git add docs/superpowers/research/<YYYY-MM-DD>-<slug>.md
   > git commit -m "docs(research): <slug>"
   > ```
   >
   > Run `/brainstorming` when you're ready to start design — pass this path (and any codebase research doc) as context.

---

## What this command does NOT do

- Decide anything for this project — it reports what the world does, not what we should do
- Propose architecture or design
- Write or invoke a plan
- Invoke `brainstorming`, `writing-plans`, or any implementation skill
- Modify source code, run the test suite, or clone external libraries to test them
- Edit any file outside `docs/superpowers/research/`
- Commit the research doc, or any other file — review and commit are the user's job

The command ends after Phase 3.
