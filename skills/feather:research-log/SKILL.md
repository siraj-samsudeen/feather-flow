---
name: feather:research-log
description: Documents technical research — investigations into how something works, whether an approach is viable, or what the trade-offs are between options. Triggered when researching a library, pattern, or tool, investigating source code or API behavior, comparing approaches before committing, or on "research this", "investigate", "how does X work", "is X viable".
argument-hint: <topic or question to research>
---

# Feather Research Log

Capture technical investigations so the findings are reusable and not lost in chat history.

## Process Overview

```
1. Frame        →  What are we trying to find out?
2. Investigate  →  Source code, docs, benchmarks, experiments
3. Document     →  Research log with questions and findings
4. Place        →  docs/research/NNN-slug.md
```

Skip steps as needed. Minimum path: Document → Place.

## Step 1: Frame the Research

Every research log starts with clear questions. Without them, findings become a dump with no structure.

**Ask if unclear:** "What specific questions are you trying to answer?"

Good research questions:
- "Does X work in environment Y?" (compatibility)
- "Which approach is better for our constraints?" (comparison)
- "How does X work internally?" (understanding)
- "What are the performance characteristics of X?" (benchmarking)

### Context Section

Before the questions, include enough background so the findings make sense on their own:
- What system or project is this for?
- What's the current implementation (if any)?
- What constraints matter?

## Step 2: Investigate

Read source code, check docs, run experiments, benchmark. This step is open-ended — the skill prescribes how to *capture* results, not how to find them.

## Step 3: Document the Findings

Use `references/template.md` as the starting point. Key sections: Context → Questions → Findings (verdict-first per question) → Summary.

### Key Principles

1. **Questions drive structure** — Each question gets its own finding. No question, no section.
2. **Verdict first** — Lead each finding with the one-line answer. Details follow for those who need them.
3. **Show your work** — Include source code excerpts, doc references, benchmark numbers. This is the "proof" section.
4. **Standalone** — The research log should make sense without reading the decision log. Someone might find it searching for "does ConvexHttpClient work in Node.js" without caring about the decision.
5. **Link to decision if one exists** — Add the `**Decision**:` header when a feather:decision-log was created from this research.

### Writing Guidelines

**Title**: Describe the investigation, not the conclusion. Good: "Playwright Shared Test Setup Patterns". Bad: "Why We Use Auto-use Fixtures".

**Questions**: Number them. Keep them specific. "How does X work?" is too broad. "Does X use browser-only APIs?" is answerable.

**Findings**: Start with the verdict, then expand. A reader skimming verdicts should get the full picture. Someone who needs proof reads the details.

**Source references**: Include file paths and line numbers when citing source code. These go stale, but they're invaluable when fresh.

## Step 4: Place the Research Log

### File naming

```
docs/research/NNN-short-slug.md
```

- `NNN` — Zero-padded sequential number (001, 002, ...)
- `short-slug` — Kebab-case summary of the topic
- Use the same `NNN` as the decision log if one exists

### Linking

- If a decision log exists: add `**Decision**: [DL-NNN](../decisions/NNN-slug.md)` to the research header
- If no decision yet: omit the Decision line. It can be added later.

## Quick Reference

### When to Write a Research Log

- You investigated source code or library internals
- You ran benchmarks or compatibility checks
- Findings took real effort and shouldn't be lost
- Multiple questions were answered in one investigation

### When NOT to Write One

- A quick docs lookup answered the question
- The finding is a single sentence (just put it in the decision log)
- It's common knowledge that doesn't need "proof"

### Anti-Patterns

| Don't | Do |
|----------|-------|
| Dump findings without questions | Frame questions first, then answer them |
| Bury the answer in paragraphs | Lead with verdict, then details |
| Skip source references | Include file paths + line numbers |
| Write only for today-you | Write for 6-months-from-now-you |
| Mix decision rationale into findings | Keep findings factual, decisions go in decision log |

## Success Criteria

- Research log exists at `docs/research/NNN-slug.md`
- Every listed question has a corresponding finding with a verdict
- Document is standalone — makes sense without reading any decision log
- Source references included where applicable (file paths, doc links, benchmark numbers)

## Examples

- `references/example-playwright-research.md` — Multi-question research on Playwright patterns
