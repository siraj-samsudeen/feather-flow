# feather-flow

An opinionated capability-delivery workflow for Claude Code: from a vague
idea to a merged PR, gated at every step.

## Why feather-flow

Most workflow tools for AI coding assistants either give you a single
mega-prompt or a sprawling library of micro-skills. feather-flow is in
between: **one pipeline, four skills**, each with a specific job, designed
to keep the human in the loop at decision points and the agent fast at
execution.

The four skills:

| Stage | Skill | What it does |
|---|---|---|
| **Discover** | `feather-brainstorm` | Expands a vague idea into a structured discussion log — captures your words verbatim, no paraphrasing, with optional HTML mockups for layout questions |
| **Specify** | `feather-spec` | Authors capability/design/screen/component specs and a prove-engine-first task list — using EARS patterns and stable IDs |
| **Build** | `feather-execute-task` | Runs one task at a time with a gate at every step: mini-plan → execute → verify → tick → ask. Six-category finding classifier surfaces divergences instead of silently fixing them |
| **Orient** | `feather-flow` | Pipeline overview and starting point if you're entering mid-flow |

## Install

```bash
npx feather-flow
```

That's it. Restart Claude Code and start with `/feather-flow` for the
overview, or describe what you want to build and Claude will pick the
right skill automatically.

**Update:** You'll see a notification at session start when a new version
is available. Run `npx feather-flow` again to upgrade — it preserves any
local skill modifications.

**Uninstall:** `npx feather-flow uninstall`

## Where to start

| You have | Run |
|---|---|
| A vague idea or pain point | `/feather-brainstorm` |
| A clear feature in mind | `/feather-spec` |
| `brainstorm.md` already written | `/feather-spec` |
| All spec artifacts, ready to build | `/feather-execute-task` |
| Mid-implementation pivot | `/feather-brainstorm` (pivot entry point) |

## What feather-flow values

- **Your words over rephrasing.** brainstorm.md quotes you verbatim.
  Reframing is explicitly disallowed.
- **Specs as user-perspective. Tasks as developer-perspective.**
  The spec describes what the user can do; the task list describes
  what the developer builds.
- **Prove the engine first.** Task ordering puts a hardcoded UI
  triggering the full pipeline before any UX polish.
- **Verification classifies findings.** Test failure, spec divergence,
  spec ambiguity, cross-cutting issue, scope creep, unrelated bug —
  each gets a different response, never silently absorbed.
- **The user owns the loop.** Agent never auto-continues to the next
  task. One task per cycle, ask before the next.

## Built on Superpowers

The visual companion pattern in `feather-brainstorm` is adapted from
[Superpowers](https://github.com/obra/superpowers) by Jesse Vincent
(MIT). See [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md) for attribution.

## License

MIT — see [LICENSE](LICENSE)
