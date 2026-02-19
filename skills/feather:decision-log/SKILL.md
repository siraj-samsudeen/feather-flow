---
name: feather:decision-log
description: Captures technical decisions with enough context to understand them 6 months later. Triggered when documenting why a particular approach was chosen, converting research findings into a decision record, or comparing options before committing to one. Trigger keywords — "write a decision log", "document this decision", "record why we chose", "decision log".
argument-hint: <decision topic, e.g. "auto-use fixture for cleanup">
---

# Feather Decision Log

Capture technical decisions with enough context to understand them 6 months later.

## Process Overview

```
1. Identify    →  What decision was made (or needs to be made)?
2. Gather      →  Context, constraints, options considered
3. Write       →  Decision log in standard format
4. Place       →  docs/decisions/NNN-slug.md
```

Skip steps as needed. Minimum path: Write → Place.

## Step 1: Identify the Decision

Every decision log answers one question: **"Why did we choose X over Y?"**

Sources of decisions:
- **Research log** — a feather:research-log doc that led to a choice (link to it)
- **Conversation** — user just decided something and wants to capture it
- **PR/code review** — a choice was made, needs to be recorded
- **Retrospective** — documenting a past decision for the record

**Ask if unclear:** "What's the decision you want to capture?"

## Step 2: Gather Context

Extract or ask for:

| Element | Question | Required? |
|---------|----------|-----------|
| **Problem** | What situation required a decision? | Yes |
| **Constraints** | What limits the solution space? | Yes |
| **Options** | What alternatives were considered? | Yes (minimum 2) |
| **Evidence** | Research log, benchmarks, docs that informed the choice? | If available |
| **Decision** | Which option was chosen and why? | Yes |
| **Trade-offs** | What are we accepting by choosing this? | Yes |

## Step 3: Write the Decision Log

Use `references/template.md` as the starting point. Key sections: Context → Options Considered → Decision → Consequences (positive, negative, action items).

### Companion Research Log

When a decision was informed by non-trivial investigation, link to the feather:research-log doc rather than embedding findings:

```
docs/research/NNN-slug.md     ← full research (feather:research-log skill)
docs/decisions/NNN-slug.md    ← decision log (this skill)
```

Use the same `NNN` number for both. The decision log links to the research in its header. The research log links back to the decision.

**When to link research:**
- A feather:research-log doc already exists for this topic
- You investigated source code, APIs, or ran benchmarks
- Findings are too detailed for the decision log

**When to skip the link:**
- Decision was based on team experience or preference
- A quick docs check answered the question
- No feather:research-log doc exists and findings are simple

### Key Principles

1. **Decision-focused** — The decision and its rationale are the core. Research findings support, not lead.
2. **Enough context to disagree** — A reader should understand *why* you chose X and form their own opinion.
3. **Options must be real** — Each option should be something you genuinely considered, not a strawman.
4. **Trade-offs are explicit** — Every decision has costs. Name them.
5. **Concise over comprehensive** — One page is ideal. Two pages max. Link to research for details.

### Writing Guidelines

**Title**: Use the form "Use X for Y" or "X pattern for Y". Good: "Auto-use fixture for per-test cleanup". Bad: "Playwright testing decision".

**Context**: Write for someone who joins the team in 6 months. Include the constraints that shaped the decision, not just the problem.

**Options**: Lead with how the option works, then trade-offs. Use a comparison table if there are 3+ options with clear differentiators.

**Decision**: State the choice first, then the reasoning. Reference the research log for detailed evidence.

**Consequences**: Split into positive, negative, and action items. Action items make the decision log actionable, not just a record.

### Converting Research to Decision Log

When a feather:research-log doc already exists:

1. **Find the decision** — What question did the research answer? That's your title.
2. **Extract options** — The research likely compared approaches. Those are your options.
3. **Identify the verdict** — The research conclusion becomes your Decision section.
4. **Link, don't duplicate** — Reference the research log for detailed findings. Summarize only what's needed to justify the choice.
5. **Add consequences** — Research often skips this. Ask: "What changes because of this decision?"

## Step 4: Place the Decision Log

### File naming

```
docs/decisions/NNN-short-slug.md    ← decision log
docs/research/NNN-short-slug.md     ← companion research (if applicable)
```

- `NNN` — Zero-padded sequential number (001, 002, ...). Same number for both files.
- `short-slug` — Kebab-case summary of the decision
- Auto-detect next number from existing files in `docs/decisions/`

### Status lifecycle

```
Proposed → Accepted → [Deprecated | Superseded by DL-xxx]
```

- **Proposed**: Decision is documented but not yet agreed upon
- **Accepted**: Decision is in effect
- **Deprecated**: Decision is no longer relevant
- **Superseded**: A newer decision log replaces this one (link to it)

Most entries go straight to `Accepted` when the decision is already made.

## Quick Reference

### When to Write a Decision Log

- You chose between 2+ viable approaches
- The choice isn't obvious from the code
- Future-you will ask "why did we do it this way?"
- A feather:research-log doc led to a conclusion worth recording

### When NOT to Write One

- The choice is obvious or industry-standard (e.g., "use HTTPS")
- It's a temporary/throwaway decision
- It's a style preference with no real trade-offs

### Anti-Patterns

| Don't | Do |
|----------|-------|
| "We decided to use React" (no why) | "We chose React over Svelte because..." |
| Strawman alternatives nobody considered | Real options you evaluated |
| Duplicate research findings in the decision log | Link to feather:research-log doc |
| Status always "Proposed" and never updated | Set to "Accepted" when decision is final |
| 5-page comprehensive analysis | 1-page decision, link to research for details |

## Success Criteria

- Decision log exists at `docs/decisions/NNN-slug.md`
- At least 2 real options considered (no strawmen)
- Decision section states the choice and why
- Consequences section includes positive, negative, and action items
- Companion research linked if non-trivial investigation was done

## Examples

- `references/example-playwright-decision.md` — Decision log with companion research link
- `references/example-short.md` — Minimal decision log, no research needed
