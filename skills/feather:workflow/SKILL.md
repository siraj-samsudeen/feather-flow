---
name: feather:workflow
description: Master reference for the development workflow. Shows the complete flow, which skill to invoke at each step, checkpoints, and feedback tracking. Use when starting a new feature or when unsure which skill comes next.
---

# Feather Workflow

The complete development workflow with skills, checkpoints, and feedback tracking.

**New here?** Run `/feather:help` for a quick introduction and getting started guide.

**Use this skill:** When starting a new feature, or when unsure which skill comes next.

## Workflow Modes

### Simple Mode (Single Feature)

For single features that fit in one session:

```
┌─────────────────────────────────────────────────────────────────────┐
│                         SIMPLE WORKFLOW                              │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  1. REQUIREMENTS                                                    │
│     Skill: feather:create-design → feather:create-spec              │
│     Output: design.md, spec.md                                      │
│                                                                     │
│  2. DESIGN                                                          │
│     Skill: feather:ui-mockup                                        │
│     Output: UI mockups, user journey, UX touches                    │
│                                                                     │
│  3. ◆ CHECKPOINT 1                                                  │
│     Skill: feather:review-design                                    │
│     Gate: 5 artifacts present? Architecture sound?                  │
│     Feedback: Saved to .feedback/ if deferred                       │
│                                                                     │
│  4. TEST DERIVATION                                                 │
│     Skill: feather:derive-tests                                     │
│     Output: Gherkin scenarios grouped by spec IDs                   │
│                                                                     │
│  5. ◆ GHERKIN REVIEW                                                │
│     (Built into feather:derive-tests)                               │
│     Gate: User approves scenarios before implementation             │
│                                                                     │
│  6. PLANNING                                                        │
│     Skill: feather:create-plan                                      │
│     Output: implementation-plan.md (behavioral tasks, NO code)      │
│                                                                     │
│  7. ISOLATION                                                       │
│     Skill: feather:isolate-work                                     │
│     Output: Feature branch or worktree                              │
│                                                                     │
│  8. IMPLEMENTATION                                                  │
│     Skill: feather:execute                                          │
│     Constraint: Test is READ-ONLY CONTRACT                          │
│                                                                     │
│  9. ◆ CHECKPOINT 2                                                  │
│     Skill: feather:verify                                           │
│     Gate: Automated + manual verification                           │
│     Feedback: All issues saved to .feedback/                        │
│                                                                     │
│  10. COMPLETION                                                     │
│      Skill: feather:finish                                          │
│      Options: Merge / PR / Keep / Discard                           │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### Slice Mode (Multi-Feature Projects)

For larger projects with multiple features spanning multiple sessions:

```
┌─────────────────────────────────────────────────────────────────────┐
│                         SLICE WORKFLOW                               │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  SESSION 1: SLICE PROJECT                                           │
│  Skill: /feather:slice-project                                              │
│  Output: slices.json, STATE.md, DOMAIN.md, SLICES.md                │
│                                                                     │
│  - Takes vague request, generates best-guess slices                 │
│  - Shows first, refines later (no upfront questions)                │
│  - Offers feather-mockups for UI slices                             │
│  - Outputs structured plan, does NOT start implementation           │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  SESSION 2+: WORK ONE SLICE                                         │
│  Skill: /feather:work-slice (or /feather:resume-slice first)                        │
│                                                                     │
│  Per-slice flow:                                                    │
│  ┌──────────────────────────────────────────────────────────┐       │
│  │ 1. Create feather-spec → docs/specs/<slice-id>/spec.md   │       │
│  │ 2. Derive Gherkin → docs/specs/<slice-id>/gherkin.md     │       │
│  │ 3. TDD Loop (with tdd-guard hook):                       │       │
│  │    - RED: Write test, verify fails, commit               │       │
│  │    - GREEN: Write code, verify passes, commit            │       │
│  │    - Repeat for all scenarios                            │       │
│  │ 4. Verify: 100% coverage required                        │       │
│  │ 5. Present test checklist for human verification         │       │
│  │ 6. Update slices.json: passes = true                     │       │
│  │ 7. STOP (human-in-the-loop)                              │       │
│  └──────────────────────────────────────────────────────────┘       │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  MID-SLICE: PAUSE                                                   │
│  Skill: /feather:pause-slice                                                │
│  Creates: .slices/CONTINUE.md with TDD cycle position               │
│                                                                     │
│  - Captures exact phase (spec/gherkin/red/green/verify)             │
│  - Lists uncommitted changes                                        │
│  - Records decisions made                                           │
│  - Local-only (gitignored)                                          │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  AFTER BREAK: RESUME                                                │
│  Skill: /feather:resume-slice                                               │
│                                                                     │
│  - Detects CONTINUE.md (graceful) or recovers (abrupt cutoff)       │
│  - Shows position, progress, next action                            │
│  - Offers: continue or discard and start fresh                      │
│  - Routes to /feather:work-slice when ready                                 │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ANYTIME: ADD SLICES                                                │
│  Skill: /feather:add-slice                                                  │
│                                                                     │
│  - Add more slices to existing project                              │
│  - Reorder, remove, or split slices                                 │
│  - Requirements are iterative, always editable                      │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  DURING USE: CAPTURE ISSUES                                         │
│  Skill: /feather:create-issue                                                 │
│                                                                     │
│  - Quick capture of bugs/UX tweaks                                  │
│  - Appends to .slices/polish.md                                             │
│  - Address later with /feather:polish                                       │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  POLISH TIME                                                        │
│  Skill: /feather:polish                                                     │
│                                                                     │
│  - Work through polish queue with TDD                               │
│  - Lighter than full slice (skip spec/gherkin)                      │
│  - Still requires tests first                                       │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

## When to Use Which Mode

| Situation | Mode | Start With |
|-----------|------|------------|
| Single feature, one session | Simple | `feather:create-design` |
| Multiple features, spans sessions | Slice | `/feather:slice-project` |
| Vague idea, need to explore | Slice | `/feather:slice-project` |
| Well-defined single feature | Simple | `feather:create-spec` |
| Existing slice project | Slice | `/feather:resume-slice` |

## Quick Reference: Which Skill When?

### Simple Mode Skills

| Step | Trigger | Skill | Output |
|------|---------|-------|--------|
| Start feature | "Let's build X" | `feather:create-design` | design.md |
| Write requirements | After design | `feather:create-spec` | spec.md |
| Create UI design | Need visual | `feather:create-ui-mockup` | mockup |
| Review before coding | Have design docs | `feather:review-design` | approval |
| Expand to tests | After CP1 passes | `feather:derive-tests` | Gherkin |
| Plan implementation | After Gherkin approved | `feather:create-plan` | plan.md |
| Start coding | Before first commit | `feather:isolate-work` | branch |
| Execute plan | Have approved plan | `feather:execute` | code |
| Verify feature | Implementation done | `feather:verify` | approval |
| Finish up | All checks pass | `feather:finish` | merged |

### Slice Mode Skills

| Step | Trigger | Skill | Output |
|------|---------|-------|--------|
| Start project | Vague multi-feature idea | `/feather:slice-project` | .slices/*, DOMAIN.md |
| Add features | New slice needed | `/feather:add-slice` | Updated slices.json |
| Work on slice | Ready to implement | `/feather:work-slice` | Complete slice |
| Stop mid-slice | Need to pause | `/feather:pause-slice` | CONTINUE.md |
| Resume work | Starting session | `/feather:resume-slice` | Context restored |
| Capture bug | Found issue | `/feather:create-issue` | polish.md entry |
| Fix issues | Have polish items | `/feather:polish` | Fixed code |

## The Three Checkpoints

### Checkpoint 1: Before Implementation
**Skill:** `feather:review-design`

| Artifact | Required |
|----------|----------|
| UI Mockup | ✓ (or N/A) |
| User Journey | ✓ |
| UX Touches | ✓ |
| Acceptance Criteria | ✓ |
| Data Model | ✓ (or N/A) |

**User says:** "Approved" → proceed | "Add X" → update or save to .feedback/

### Checkpoint: Gherkin Review
**Skill:** `feather:derive-tests` (built-in)

**User reviews:** Generated scenarios match intent?

**User says:** "Approved" → proceed | "Add/Remove/Change scenario"

### Checkpoint 2: After Implementation
**Skill:** `feather:verify`

| Check | Type |
|-------|------|
| Tests pass | Automated |
| Types/lint pass | Automated |
| Coverage 100% | Automated |
| Manual steps work | User performs |

**User provides:** Failed steps, issues found → saved to .feedback/

## Executable Guardrails (Slice Mode)

| Guardrail | Enforcement | What It Blocks |
|-----------|-------------|----------------|
| `tdd-guard` hook | PreToolUse hook | Edit/Write on non-test files when tests failing |
| Pre-commit hook | Git pre-commit | `git commit` if any check fails |
| Vitest threshold | Coverage config | Tests fail if coverage drops below 100% |

**Setup:** Run `/feather:setup-tdd-guard` before starting slice work.

## Feedback Tracking

All feedback saved to `.feedback/` folder:

```
.feedback/
├── INDEX.md              # Dashboard (auto-generated)
├── open/                 # Unresolved
│   ├── BUG-001-xxx.md
│   ├── DESIGN-002-xxx.md
│   └── UX-003-xxx.md
├── in-progress/          # Being worked on
└── resolved/             # Done (keep for reference)
```

## Anti-Patterns

| Don't | Do |
|-------|-----|
| Skip feather:create-design | Always clarify intent first |
| Put code in plans | Plans are behavioral, not code |
| Let implementer modify tests | Test is READ-ONLY CONTRACT |
| Claim done without CP2 | Always run feather:verify |
| Lose feedback | Always save to .feedback/ |
| Work multiple slices at once | One slice, STOP, human decides |
| Skip TDD in polish | TDD is mandatory even for fixes |

## Starting Work

### New Single Feature
```
User: "Let's add quick task"

Claude: "I'm using feather:workflow to guide this feature.
         Starting with feather:create-design to understand requirements."

→ invoke: feather:create-design
→ invoke: feather:create-spec
→ invoke: feather:create-ui-mockup (if UI)
→ invoke: feather:review-design (CP1)
→ invoke: feather:derive-tests
→ [Gherkin Review]
→ invoke: feather:create-plan
→ invoke: feather:isolate-work
→ invoke: feather:execute
→ invoke: feather:verify (CP2)
→ invoke: feather:finish
```

### New Multi-Feature Project
```
User: "Build a project management system like Basecamp"

Claude: "This sounds like a multi-feature project.
         I'm using /feather:slice-project to break it into vertical slices."

→ invoke: /feather:slice-project → slices.json, DOMAIN.md
→ User picks first slice
→ invoke: /feather:work-slice → Complete one slice
→ STOP
→ User: "/feather:work-slice" → Continue to next
```

### Resuming Work
```
User: "Where were we?"

Claude: "I'm using /feather:resume-slice to restore context."

→ invoke: /feather:resume-slice
→ Shows progress, position, next action
→ Routes to appropriate skill
```

## Relationship to Other Skills

```
feather:workflow (THIS - the map)
     │
     ├── Simple Mode
     │   ├── feather:create-design      → design.md
     │   ├── feather:create-spec        → spec.md
     │   ├── feather:create-ui-mockup   → mockup
     │   ├── feather:review-design      → (CP1)
     │   ├── feather:derive-tests       → Gherkin
     │   ├── feather:create-plan        → plan.md
     │   ├── feather:isolate-work       → branch
     │   ├── feather:execute            → code
     │   ├── feather:verify             → (CP2)
     │   └── feather:finish             → merged
     │
     └── Slice Mode
         ├── /feather:slice-project             → .slices/*
         ├── /feather:add-slice                 → Updated slices
         ├── /feather:work-slice                → Complete slice
         ├── /feather:pause-slice               → CONTINUE.md
         ├── /feather:resume-slice              → Context restored
         ├── /feather:create-issue                → polish.md entry
         └── /feather:polish                    → Fixed issues
```

This skill is the **reference**. Invoke the specific skill for each step.
