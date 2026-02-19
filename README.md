# feather-flow

A lightweight, TDD-enforced development workflow for Claude Code.

## Why feather-flow

Most workflow tools for AI coding assistants are heavy — dozens of dependencies, complex configuration, steep learning curves. feather-flow takes the opposite approach: TDD-enforced quality gates at every step, so you ship working code instead of debugging your tooling. It ships as 21 skills with zero dependencies — pure markdown skills, zero runtime dependencies.

## Comparison

| | feather-flow | GSD |
|---|---|---|
| Skills/Commands | 23 | 35+ commands, 15 agents, 37 workflows |
| Install | `npx feather-flow` | npm + Node.js |
| Dependencies | Node.js 18+ (for install only) | Node.js, npm |
| Config needed | None | config.json with 20+ options |
| Best for | Quick projects, learning | Enterprise teams, complex orchestration |

## Install

```bash
npx feather-flow
```

This installs skills to `~/.claude/feather-flow/`, creates symlinks, and sets up background update notifications.

Restart Claude Code, then type `/feather:help` to get started.

<details>
<summary>Alternative: install from source</summary>

```bash
git clone https://github.com/siraj-samsudeen/feather-flow.git && cd feather-flow && ./install.sh
```

Note: The bash installer doesn't include manifest tracking or interactive merge on update.
</details>

<details>
<summary>Alternative: Vercel Skills CLI</summary>

```bash
npx skills add siraj-samsudeen/feather-flow
```

This installs skills directly from GitHub. You get working skills but not the manifest tracking, update notifications, or interactive merge that the npm installer provides.
</details>

## Quick Start

After installing, start with:

- `/feather:help` — See all available skills and when to use each
- `/feather:workflow` — The main entry point that guides you through the full development flow

## The Two Modes

**Simple Mode** — For small features (< 1 day):

`/feather:workflow` guides you through: design → spec → tests → plan → execute → verify → finish

**Slice Mode** — For larger projects (multi-day):

Break work into vertical slices, each independently shippable:

`/feather:slice-project` → `/feather:work-slice` (repeat) → `/feather:finish`

## All 23 Skills

| Skill | Phase | What it does |
|---|---|---|
| `/feather:workflow` | Guide | Main entry point — walks you through the full flow |
| `/feather:help` | Guide | Shows all skills with usage guidance |
| `/feather:create-design` | Design | Generate system design from requirements |
| `/feather:review-design` | Design | Review and critique a design |
| `/feather:create-ui-mockup` | Design | Create ASCII UI mockups |
| `/feather:create-spec` | Spec | Write behavioral specification with test hooks |
| `/feather:derive-tests` | Spec | Derive test cases from spec |
| `/feather:write-tests` | Test | Write tests from derived test cases (TDD) |
| `/feather:setup-tdd-guard` | Test | Configure TDD enforcement (any stack) |
| `/feather:setup-react-testing` | Test | Add React testing environment |
| `/feather:create-plan` | Plan | Create implementation plan from spec |
| `/feather:execute` | Execute | Execute plan with quality gates |
| `/feather:verify` | Verify | Run verification checkpoint |
| `/feather:finish` | Finish | Complete branch (merge/PR/keep) |
| `/feather:slice-project` | Slice | Break project into vertical slices |
| `/feather:work-slice` | Slice | Work on a single slice (TDD-enforced) |
| `/feather:add-slice` | Slice | Add a new slice to existing project |
| `/feather:pause-slice` | Slice | Pause current slice work |
| `/feather:resume-slice` | Slice | Resume paused slice |
| `/feather:isolate-work` | Utility | Create isolated workspace (worktree/branch) |
| `/feather:create-issue` | Utility | Create GitHub issue from context |
| `/feather:polish` | Utility | Quick quality improvements |
| `/feather:update` | Utility | Interactive update with local mod detection |

## Update / Uninstall

**Update:** Inside Claude Code, run `/feather:update` — it shows what changed, detects your local modifications, and walks you through an interactive merge.

You'll also see a notification at session start when an update is available.

**Uninstall:**

```bash
npx feather-flow uninstall
```

## Built on Superpowers

Several feather-flow skills are derived from [Superpowers](https://github.com/obra/superpowers) by Jesse Vincent (MIT). See [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md) for detailed attribution.

## License

MIT — see [LICENSE](LICENSE)
