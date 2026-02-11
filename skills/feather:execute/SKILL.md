---
name: feather:execute
description: Use when executing implementation plans with independent tasks in the current session. Dispatches fresh subagent per task with two-stage review.
attribution: Based on superpowers:executing-plans by Jesse Vincent (MIT)
---

# Execute With Agents

Execute plan by dispatching fresh subagent per task, with two-stage review after each: spec compliance review first, then code quality review.

**Core principle:** Fresh subagent per task + two-stage review (spec then quality) = high quality, fast iteration

## When to Use

- Have an implementation plan (from `feather:create-plan`)
- Tasks are mostly independent
- Want to stay in this session (subagent-driven execution)

## The Process

```
1. Read plan, extract all tasks with full text, create TodoWrite
2. For each task:
   a. Dispatch implementer subagent with full task text + context
   b. If implementer asks questions → answer, then continue
   c. Implementer implements, tests, commits, self-reviews
   d. Dispatch spec reviewer → verify code matches spec
   e. If issues → implementer fixes → re-review
   f. Dispatch code quality reviewer
   g. If issues → implementer fixes → re-review
   h. Mark task complete
3. After all tasks: dispatch final reviewer
4. Use feather:verify skill
5. Use feather:finish skill
```

## Prompt Templates

### Implementer Subagent

See `./implementer-prompt.md`

### Spec Compliance Reviewer

See `./spec-reviewer-prompt.md`

### Code Quality Reviewer

See `./code-quality-reviewer-prompt.md`

## Key Principles

**Efficiency:**
- Controller extracts all tasks upfront (subagent doesn't read plan file)
- Controller provides full text and context
- Questions surfaced before work begins

**Quality gates:**
- Self-review catches issues before handoff
- Spec review: did they build what was requested?
- Code review: is it well-built?
- Review loops ensure fixes actually work

**Cost:**
- More subagent invocations (implementer + 2 reviewers per task)
- But catches issues early (cheaper than debugging later)

## Red Flags

**Never:**
- Skip reviews (spec compliance OR code quality)
- Proceed with unfixed issues
- Dispatch multiple implementation subagents in parallel (conflicts)
- Make subagent read plan file (provide full text instead)
- Skip scene-setting context
- Accept "close enough" on spec compliance
- **Start code quality review before spec compliance passes**
- Move to next task while either review has open issues

**If subagent asks questions:**
- Answer clearly and completely
- Don't rush them into implementation

**If reviewer finds issues:**
- Implementer fixes them
- Reviewer reviews again
- Repeat until approved

## After All Tasks Complete

1. Dispatch final code reviewer for entire implementation
2. Use `feather:verify` skill (Layer 1-3 verification)
3. Use `feather:finish` skill (merge/PR/keep/discard)

## Relationship to Other Skills

```
feather:create-plan  →  feather:execute  →  feather:verify  →  feather:finish
                               ↓
                    (per task: implement → spec review → code review)
```

**Alternative:** Run `feather:execute` in a separate session for parallel execution with human checkpoints between batches.
