---
description: Research the codebase before design — generate questions, dispatch parallel ticket-blind subagents, produce a factual research doc
argument-hint: '<short description of the area to research, e.g. "how database sources are structured">'
disable-model-invocation: true
allowed-tools: Read, Grep, Glob, Write, Bash(git:*), Bash(mkdir:*), Bash(date:*), Agent, AskUserQuestion
---

You are running the `/research_codebase` command. Produce a **purely factual** research doc for the area of the codebase the user wants to investigate. No opinions. No recommendations. No design proposals. No suggestions about what "should" happen next.

The research doc is the input to a later `/brainstorming` session. Your job is to map the terrain, not to plan the route.

User's request: `$ARGUMENTS`

If `$ARGUMENTS` is empty, stop and ask what the user wants researched.

---

## Phase 1 — Generate questions (in chat)

Draft 5–10 research questions that, if answered, would map the area of the codebase relevant to the upcoming work.

Good questions are:
- **Factual** — "how does X work?" not "should we do Y?"
- **Specific** — name modules, protocols, contracts, file paths where you can
- **Coverage-oriented** — together they should cover the integration surface the upcoming work is likely to touch

Present the questions as a numbered list. Then ask the user to approve, edit, reword, add, or remove questions before dispatch. Wait for their response. Do not proceed until the user has explicitly approved the question list.

---

## Phase 2 — Dispatch research (parallel, ticket-hidden)

Once questions are approved:

1. **Cluster** related questions into 2–4 logical groups (e.g. "protocol and base class," "registry," "test fixtures"). Each cluster becomes one subagent.

2. **Dispatch the subagents in parallel** — single message, multiple `Agent` tool calls — using `subagent_type: general-purpose`.

3. **Critical discipline:** each subagent prompt contains ONLY the cluster's questions and a factual framing. It MUST NOT contain:
   - The user's original `$ARGUMENTS`
   - What is being built or why
   - Any hint of the upcoming work's direction or goal
   - Any framing like "we are about to add X" or "in preparation for Y"

   This is the whole point of the command: ticket-blind research returns facts. Ticket-aware research returns opinions disguised as facts.

   **Subagent prompt template (use this verbatim, just substitute the questions):**
   ```
   You are answering factual questions about the current codebase. Return pure
   observations: what the code does today, which files/functions/lines are
   involved, what contracts and invariants exist. No opinions. No
   recommendations. No suggestions about what should change. If a question is
   ambiguous, make the ambiguity explicit. If a question cannot be answered
   from the codebase, say so plainly.

   Questions to answer:

   [insert this cluster's questions, numbered]

   For each question, report:
   - What the code does today
   - Which files / functions / lines are relevant (use file:line references)
   - Any noteworthy contracts, invariants, or gotchas
   - Short code snippets where they clarify the answer

   Length budget: aim for ~50–150 words per question. Quote sparingly.
   ```

---

## Phase 3 — Collate, save, commit

1. **Merge** subagent outputs into one markdown doc. One section per **original (approved) question**, in the order the user approved them. Use the question itself as the section heading.

2. **Strip opinions.** Read the merged doc once. If a subagent slipped in a recommendation ("you should...", "the cleanest approach would be...", "consider..."), either delete the sentence or reword to a fact. This is a hard rule.

3. **Determine the slug** — kebab-case derivation of `$ARGUMENTS`, max ~5 words (e.g. `database-sources`, `change-detection-watermarks`).

4. **Get today's date and the current commit SHA:**
   ```bash
   date +%Y-%m-%d
   git rev-parse --short HEAD
   ```

5. **Create the research directory if needed:**
   ```bash
   mkdir -p docs/superpowers/research
   ```

6. **Write the doc** to `docs/superpowers/research/<YYYY-MM-DD>-<slug>.md` with this header:

   ```markdown
   # <Human-readable title> — Research Notes

   **Date:** YYYY-MM-DD
   **Commit at time of research:** <short-sha>
   **Scope:** <one-sentence description of what was researched>

   ---

   ## <Question 1, verbatim>

   <Answer.>

   ## <Question 2, verbatim>

   <Answer.>

   ...
   ```

   The header's commit SHA exists so a future reader can tell whether the doc is still fresh.

7. **Announce** in chat — do NOT commit. The user reviews first, then commits themselves if they want the doc tracked:

   > Research saved to `docs/superpowers/research/<YYYY-MM-DD>-<slug>.md`. Review it, then commit if you want it tracked:
   >
   > ```bash
   > git add docs/superpowers/research/<YYYY-MM-DD>-<slug>.md
   > git commit -m "docs(research): <slug>"
   > ```
   >
   > Run `/brainstorming` when you're ready to start design — pass this path as context.

---

## What this command does NOT do

- Propose approaches, designs, or architecture
- Write or invoke a plan
- Invoke `brainstorming`, `writing-plans`, or any implementation skill
- Edit any file outside `docs/superpowers/research/`
- Run tests or modify source code
- Commit the research doc, or any other file — review and commit are the user's job

The command ends after Phase 3.
