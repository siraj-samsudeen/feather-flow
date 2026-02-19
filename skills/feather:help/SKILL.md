---
name: feather:help
description: Introduction to the Feather development workflow. Start here if you're new. Shows available skills, when to use them, and how to get started.
---

# Feather Help

Welcome to the Feather workflow - a lightweight, TDD-enforced development system.

## Quick Start

**Building something small (single feature)?**
```
/feather:create-design    → Clarify what you're building
/feather:create-spec      → Write requirements
/feather:workflow         → See the full process
```

**Building something bigger (multiple features)?**
```
/feather:slice-project    → Break into vertical slices
/feather:work-slice       → Build one slice at a time
```

**Resuming previous work?**
```
/feather:resume-slice     → Restore context, see where you left off
```

## The Two Modes

### Simple Mode (Single Feature)

For features that fit in one session:

```
feather:create-design → feather:create-spec → feather:create-ui-mockup → feather:derive-tests → implement → feather:verify
```

**Use when:** One well-defined feature, clear scope, fits in a session.

### Slice Mode (Multi-Feature Projects)

For larger projects spanning multiple sessions:

```
/feather:slice-project    → Define all features as slices
/feather:work-slice       → Build ONE slice (TDD), then STOP
/feather:work-slice       → Build next slice, STOP
...repeat...
```

**Use when:** Vague idea, multiple features, spans sessions, needs structure.

## Available Skills

### Starting Work

| Skill | When to Use |
|-------|-------------|
| `/feather:slice-project` | Vague idea → ordered slices |
| `/feather:create-design` | Clarify intent for single feature |
| `/feather:create-spec` | Write lightweight requirements |

### Slice Workflow

| Skill | When to Use |
|-------|-------------|
| `/feather:work-slice` | Execute one slice with TDD |
| `/feather:add-slice` | Add more slices to project |
| `/feather:pause-slice` | Stop mid-slice, save position |
| `/feather:resume-slice` | Restore context after break |

### Polish & Issues

| Skill | When to Use |
|-------|-------------|
| `/feather:create-issue` | Capture bug/UX tweak quickly |
| `/feather:polish` | Fix items in polish queue |

### Design & Planning

| Skill | When to Use |
|-------|-------------|
| `/feather:create-ui-mockup` | Quick visual HTML mockup |
| `/feather:derive-tests` | Spec → Gherkin scenarios |
| `/feather:create-plan` | Plan implementation steps |

### Execution & Verification

| Skill | When to Use |
|-------|-------------|
| `/feather:setup-tdd-guard` | Enable TDD enforcement (core, any stack) |
| `/feather:setup-react-testing` | Add React testing environment |
| `/feather:write-tests` | TDD for single feature |
| `/feather:verify` | Checkpoint 2 verification |
| `/feather:finish` | Complete and merge work |

### Reference

| Skill | When to Use |
|-------|-------------|
| `/feather:workflow` | Full workflow documentation |
| `/feather:help` | This help (you are here) |
| `/feather:update` | Check for updates, interactive merge |

## Core Principles

1. **Feather = Lightweight** - Quick specs, quick mockups, iterate fast
2. **TDD Enforced** - Hooks block code without tests (not just advice)
3. **One Slice at a Time** - Prevents context rot, human stays in control
4. **Show First, Refine Later** - Don't ask questions upfront, propose then adjust

## Example Sessions

### "I want to build a todo app"

Small and clear → Simple Mode:
```
User: "I want to build a todo app"
Claude: I'll use /feather:create-design to clarify the scope...
```

### "I want to build something like Basecamp"

Vague and big → Slice Mode:
```
User: "I want to build something like Basecamp"
Claude: This sounds like a multi-feature project.
        I'll use /feather:slice-project to break it into vertical slices...
```

### "Where were we?"

Resuming work:
```
User: "Where were we?"
Claude: I'll use /feather:resume-slice to restore context...
```

## Setup Required

Before using slice workflow, ensure TDD infrastructure:

```bash
/feather:setup-tdd-guard      # Core: hooks + coverage (required for all projects)
/feather:setup-react-testing  # React: jsdom + testing-library (if React project)
```

Core enables:
- `tdd-guard` PreToolUse hook (blocks impl without tests)
- 100% coverage thresholds
- Pre-commit checks

## Getting Help

| Question | Answer |
|----------|--------|
| "What skill should I use?" | `/feather:workflow` shows the decision tree |
| "How does TDD work here?" | `/feather:setup-tdd-guard` explains enforcement |
| "How do I test React components?" | `/feather:setup-react-testing` adds component testing |
| "What's a slice?" | Vertical cut of functionality (UI → database) |
| "Why STOP after each slice?" | Prevents context rot, keeps human in control |

## Philosophy

**Feather** = lightweight artifacts that enable fast iteration
- Feather-spec (not heavyweight PRD)
- Feather-mockup (not Figma designs)
- Feather-tests (full-stack, not layered)

**Ralph loop** = one task, complete stop, human decides next
- No autonomous multi-feature runs
- Human stays in the loop
- Context stays fresh

**TDD Guard** = executable enforcement, not advice
- Hooks block tools, can't circumvent with words
- 100% coverage or build fails
- RED before GREEN, always
