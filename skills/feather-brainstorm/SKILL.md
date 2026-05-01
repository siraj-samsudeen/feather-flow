---
name: feather-brainstorm
description: >
  Horizon-expansion and structured capture skill. Use when a user has a
  vague requirement, a specific feature idea, or a mid-implementation
  pivot and wants to think it through before writing a spec. Produces
  brainstorm.md — a discussion log in the user's own words with a thin
  structural overlay. For visual layout decisions, generates HTML mockups
  the user can open directly in their browser. Output feeds into
  feather-spec. Part of the feather-flow pipeline.
---

# Feather Brainstorm

Expand horizons before narrowing to spec. The output is a structured
discussion log — not a reframing by the agent, but the user's own words
with a thin overlay that feather-spec can read and make sense of.

**Non-negotiable rule:** Quote the user. Do not paraphrase. Light cleanup
is allowed (typos, false starts). Reframing is not.

### What counts as light cleanup vs reframing

| Allowed (light cleanup) | Not allowed (reframing) |
|---|---|
| Fix a typo ("recieve" → "receive") | Change the user's word for a more precise one |
| Remove a false start ("I mean — actually what I want is X") | Summarise a long statement into a shorter one |
| Remove filler ("uh", "like", "you know") | Resolve a vague statement into something concrete |
| Fix punctuation | Combine two statements into one |

**The test:** Would the user recognise this as their own words? If not, it is reframing.

### Handling contradictions

When a user contradicts themselves, do not silently resolve it.

Procedure:
1. Quote both statements verbatim
2. Name the tension without editorialising
3. Ask the user to resolve in their own words

Format:
> "You said two things that pull in different directions:
>
> Earlier: '<exact quote A>'
> Later: '<exact quote B>'
>
> Which reflects what you actually want, or is it somewhere in between?"

Record the resolution verbatim. Keep both original quotes in brainstorm.md
with a note: "User resolved: second statement supersedes first."
Never delete the original — the contradiction and its resolution are part
of the discussion log.

---

## Entry Points

| Entry point | Signal | Opening move |
|---|---|---|
| **Vague** | "I want a todo app", pain point without shape | Start with discovery phase |
| **Specific** | Clear feature with known screens/actions | Skip discovery, go to horizon expansion |
| **Pivot** | Mid-implementation, scope changed | Capture what changed and why first, then expand |

Ask one clarifying question if the entry point is ambiguous.
Do not ask more than one question at a time.

---

## Phase 1 — Discovery (optional, skip if specific or pivot)

**Primary question — ask this first and listen fully before asking anything else:**

> "Walk me through how you do this today, step by step."

Let the user describe their current workflow completely. Do not interrupt.
After they finish, extract:

| What you heard | What it reveals |
|---|---|
| Steps in sequence | Screens and actions needed |
| Handoffs between people or tools | Integration points |
| Delays or waiting | Automation opportunities |
| Workarounds | Pain points to solve |
| Repetition | Template or default candidates |

**Follow-up questions — use only what's needed:**
- "What are you trying to accomplish?" → underlying goal
- "What makes this frustrating?" → pain points and priority
- "What would success look like?" → acceptance criteria in user's words
- "Where does this break down?" → edge cases and failure modes
- "What do you wish happened automatically?" → automation opportunities

Record the workflow exactly as the user described it. Number the steps.
Do not reframe. Do not summarise away specifics.

---

## Phase 2 — Horizon Expansion

### 2.1 Restate in user's terms

One sentence, pulled from what the user said:
> "So what you're describing is: [user's words]."

Confirm before moving on. If wrong, correct and restart.

### 2.2 Identify capability type

| Type | Description |
|---|---|
| Standard | Owns one entity with full lifecycle |
| Aggregation | Aggregates across other capabilities |
| Cross-capability | Coordinates a feature spanning two capabilities |

Name the type. If cross-capability, name both capabilities affected.

### 2.3 Surface screens

List the screens the capability will need. For each: name, user intent, entry point.

```
Screen 1: Upload — user submits an xlsx file
Screen 2: Dataset List — user browses what they've imported
```

Ask: "Does this match what you're picturing, or are there screens missing?"

### 2.4 Surface options and alternatives

For each non-obvious design decision, present 2-3 options with trade-offs.
Present before recommending. The user sees the full space first.

```
Decision: How should the upload confirm flow work?

Option A — Navigate to dataset list after success
  Pro: closes the import loop, user sees result in context
  Con: extra navigation to import again

Option B — Stay on upload screen with success message
  Pro: easy to import multiple files in sequence
  Con: user must navigate to see data
```

After presenting: "Which direction feels right?"

### 2.4.5 Visual Companion (when the decision is visual)

**When to use:** decide per-question, not per-session.

| Use the browser | Use the terminal |
|---|---|
| UI mockups — wireframes, layouts, navigation | Requirements and scope questions |
| Side-by-side layout comparisons | Conceptual A/B/C choices in words |
| Design polish — spacing, visual hierarchy | Tradeoff lists, pros/cons |
| Spatial relationships — flowcharts, state machines | Technical decisions — API, data model |

A question *about* a UI topic is not automatically visual.
"What kind of upload flow?" → terminal.
"Which of these three upload layouts?" → browser.

**How to generate and show mockups:**

1. Write each option as a self-contained HTML file to
   `capabilities/<name>/mockups/<screen>/NN-option-<slug>.html`

2. Open it in the user's default browser:
```bash
# macOS
open capabilities/<name>/mockups/<screen>/01-option-a-<slug>.html

# Linux
xdg-open capabilities/<name>/mockups/<screen>/01-option-a-<slug>.html
```

   If the open command fails, print the absolute path so the user can
   open it manually:
```
Mockup written. Open in your browser:
  /path/to/capabilities/<name>/mockups/<screen>/01-option-a-<slug>.html
```

3. Write all options before asking for feedback. Open each in sequence.
   Say: "Three options are open in your browser — take a look and tell
   me which direction feels right."

4. **Save every option — including rejected ones.** The rejected mockups
   are the visual equivalent of "agent presented A, B, C; user chose B."
   The fact that A and C existed is part of the record.

**HTML mockup format — self-contained, no external dependencies:**

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Option A — Full-page dropzone</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 900px;
         margin: 2rem auto; padding: 0 1rem; background: #f8fafc; color: #0f172a; }
  .label { font-size: 11px; text-transform: uppercase; letter-spacing: .05em;
           color: #64748b; margin-bottom: .5rem; }
  .mockup { background: #fff; border: 1px solid #e2e8f0; border-radius: 8px;
            padding: 1.5rem; margin-bottom: 1rem; }
  /* Add layout-specific styles here */
</style>
</head>
<body>
  <p class="label">Option A — Full-page dropzone</p>
  <div class="mockup">
    <!-- mockup content here -->
  </div>
  <p style="color:#64748b; font-size:.875rem;">
    Pro: large drag target, familiar pattern<br>
    Con: name input feels secondary
  </p>
</body>
</html>
```

Scale fidelity to the question — wireframes for layout, more polish
for look-and-feel questions. Use real content when it matters.

**`_README.md` inside each screen folder:**
```markdown
# upload/ mockups

Layout options presented during brainstorming on 2026-05-01.

- 01-option-a-full-page-dropzone — large drag target (rejected)
- 02-option-b-compact-card — dropzone + name side by side (CHOSEN)

User reasoning: "<exact quote>"
```

**Cross-reference in brainstorm.md Decisions section:**
```markdown
### Upload screen layout
Options presented (visual mockups):
- Option A: full-page dropzone → mockups/upload/01-option-a-full-page-dropzone.html
- Option B: compact card → mockups/upload/02-option-b-compact-card.html

User chose: > "<exact quote>"
```

### 2.5 Surface risks and unknowns

Name what could go wrong or is unclear. Not to solve — just to surface.

Ask: "Are any of these blockers, or acceptable deferred scope?"

### 2.6 Scope boundary

State explicitly what is in and what is out.

Ask: "Does this match your intent, or should anything move?"

---

## Phase 3 — Capture

Produce `brainstorm.md`. Strict rules:

1. **User words are primary.** Direct quotes wherever the user stated something clearly.
2. **Agent contributions are labeled.** Anything the agent surfaced is marked "suggested by agent."
3. **Decisions are recorded verbatim.** Quote the user's choice exactly.
4. **Nothing is invented.** If the user didn't say it and the agent didn't surface it explicitly, it doesn't appear.

### brainstorm.md format

```markdown
# Brainstorm: <Capability Name>

**Date:** <YYYY-MM-DD>
**Entry point:** vague / specific / pivot
**Feeds into:** capability.md, design.md

---

## Workflow (user's words)

> "<exact quote of workflow description>"

Steps extracted:
1. <step>
2. <step>

Pain points (from workflow):
- <pain point in user's words>

---

## What we're building

> "<user's exact statement of the capability>"

Capability type: <Standard / Aggregation / Cross-capability>

---

## Screens

| Screen | User intent | Entry |
|---|---|---|
| <name> | <what "done" means> | <how user gets here> |

Confirmed by user: yes / pending

---

## Decisions

### <Decision title>
Options presented by agent:
- Option A: <description> — Pro: <> Con: <>
  → mockups/upload/01-option-a-<slug>.html (if visual)
- Option B: <description> — Pro: <> Con: <>
  → mockups/upload/02-option-b-<slug>.html (if visual)

User chose: > "<exact quote>"

---

## Risks and unknowns

Surfaced by agent:
- <risk>

User response: > "<exact quote>"

---

## Scope

In: <list>
Out: <list>

Confirmed by user: yes / pending

---

## Open questions

- <anything unresolved>
```

---

## Phase 4 — Handoff

### Mockup curation (if visual companion was used)

Before the spec-authoring prompt, if any mockups were created:

> "During this session I created mockups for these decisions:
>   - upload/ — 2 options (you chose option B)
>   - dataset-list/ — 3 options (you chose option A)
>
> Choose:
>   (1) Commit all mockups — keeps every option as a record of
>       what was considered.
>   (2) Keep only chosen options, delete the rest — leaves only
>       the option you picked per decision.
>   (3) Delete all mockups — visuals were for thinking, not the record.
>       brainstorm.md will note they were deleted.
>
> Default: (2). Which?"

If user picks (2): delete unchosen variants, renumber survivors,
update brainstorm.md links.
If user picks (3): delete all variants, replace brainstorm.md mockup
links with `→ (mockup deleted at user request)`.

### Spec authoring handoff

> "brainstorm.md is written to capabilities/<name>/brainstorm.md.
> Ready to author the spec? I'll produce capability.md, design.md,
> and screen specs from this. Say 'write the spec' to continue,
> or add anything you want to change first."

Do not auto-proceed. The user confirms.

When confirmed, hand off to feather-spec. feather-spec consumes
brainstorm.md and produces all spec artifacts (capability.md, design.md,
screens/, components/, tasks.md). feather-brainstorm does not write any
of those itself.

---

## Anti-Patterns

| ❌ Don't | ✅ Do |
|---|---|
| Paraphrase the user's workflow | Quote it directly |
| Silently resolve a contradiction | Surface both quotes, ask user to resolve |
| Delete a contradicted statement | Keep it with a resolution note |
| Recommend before presenting options | Present options first |
| Ask multiple questions at once | One question at a time |
| Auto-proceed to spec after brainstorm | Wait for user confirmation |
| Invent screens the user didn't mention | Surface as "suggested by agent" and confirm |
| Use visual companion for non-visual questions | Reserve it for layout, navigation, screen-flow |
| Show mockups and discard rejected ones | Save every mockup generated |
| Skip the curation step at session end | Always offer the three commit choices |
| Commit all mockups silently | Curation is a user decision |
