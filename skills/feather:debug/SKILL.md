---
name: feather:debug
description: Use when encountering any bug, test failure, or unexpected behavior, before proposing fixes. Find root cause first - no fixes without investigation.
attribution: Based on superpowers systematic-debugging by Jesse Vincent (MIT). Investigation techniques and hypothesis framework incorporate ideas from gsd-debugger.
---

# Debug

## Overview

Random fixes waste time and create new bugs. Quick patches mask underlying issues.

**Core principle:** ALWAYS find root cause before attempting fixes. Symptom fixes are failure.

**Violating the letter of this process is violating the spirit of debugging.**

## The Iron Law

```
NO FIXES WITHOUT ROOT CAUSE INVESTIGATION FIRST
```

If you haven't completed Phase 1, you cannot propose fixes.

## When to Use

Use for ANY technical issue:
- Test failures
- Bugs in production
- Unexpected behavior
- Performance problems
- Build failures
- Integration issues

**Use this ESPECIALLY when:**
- Under time pressure (emergencies make guessing tempting)
- "Just one quick fix" seems obvious
- You've already tried multiple fixes
- Previous fix didn't work
- You don't fully understand the issue

**Don't skip when:**
- Issue seems simple (simple bugs have root causes too)
- You're in a hurry (systematic is faster than thrashing)
- Manager wants it fixed NOW (systematic is faster than thrashing)

## The Four Phases

You MUST complete each phase before proceeding to the next.

### Phase 1: Root Cause Investigation

**BEFORE attempting ANY fix:**

1. **Read Error Messages Carefully**
   - Don't skip past errors or warnings
   - They often contain the exact solution
   - Read stack traces completely
   - Note line numbers, file paths, error codes

2. **Reproduce Consistently**
   - Can you trigger it reliably?
   - What are the exact steps?
   - Does it happen every time?
   - If not reproducible, gather more data — don't guess

3. **Check Recent Changes**
   - What changed that could cause this?
   - Git diff, recent commits
   - New dependencies, config changes
   - Environmental differences

4. **Gather Evidence in Multi-Component Systems**

   **WHEN system has multiple components (CI → build → signing, API → service → database):**

   **BEFORE proposing fixes, add diagnostic instrumentation:**
   ```
   For EACH component boundary:
     - Log what data enters component
     - Log what data exits component
     - Verify environment/config propagation
     - Check state at each layer

   Run once to gather evidence showing WHERE it breaks
   THEN analyze evidence to identify failing component
   THEN investigate that specific component
   ```

5. **Trace Data Flow**

   **WHEN error is deep in call stack:**

   See `root-cause-tracing.md` in this directory for the complete backward tracing technique.

   **Quick version:**
   - Where does bad value originate?
   - What called this with bad value?
   - Keep tracing up until you find the source
   - Fix at source, not at symptom

6. **Check Your Freshness**

   Every 30 minutes of investigation, stop and ask: "If I started fresh right now, is this still the path I'd take?" If not, restart from the top of Phase 1 with what you now know. Sunk time is not a reason to continue a dead-end path.

### Phase 2: Pattern Analysis

**Find the pattern before fixing:**

1. **Find Working Examples**
   - Locate similar working code in same codebase
   - What works that's similar to what's broken?

2. **Compare Against References**
   - If implementing pattern, read reference implementation COMPLETELY
   - Don't skim — read every line
   - Understand the pattern fully before applying

3. **Identify Differences**
   - What's different between working and broken?
   - List every difference, however small
   - Don't assume "that can't matter"

4. **Understand Dependencies**
   - What other components does this need?
   - What settings, config, environment?
   - What assumptions does it make?

### Phase 3: Hypothesis and Testing

**Scientific method:**

1. **Generate Multiple Hypotheses First**
   - Before investigating anything, list at least 3 possible explanations
   - Don't fall in love with your first idea — it anchors your thinking
   - Only THEN pick the most likely one to test first

2. **Make It Falsifiable**
   - If you can't design a test to disprove your hypothesis, it's not useful
   - Bad: "Something is wrong with the state"
   - Good: "User state resets because component remounts on route change"
   - The difference is specificity — good hypotheses make testable claims

3. **Test Minimally**
   - Make the SMALLEST possible change to test hypothesis
   - One variable at a time
   - Don't fix multiple things at once

4. **Actively Seek Disconfirming Evidence**
   - Don't just look for evidence that supports your theory
   - Ask: "What would prove me wrong?" Then look for that
   - If you can't find disconfirming evidence after genuinely looking, your hypothesis strengthens

5. **Verify Before Continuing**
   - Did it work? Yes → Phase 4
   - Didn't work? Form NEW hypothesis
   - DON'T add more fixes on top

6. **When You Don't Know**
   - Say "I don't understand X"
   - Don't pretend to know
   - Ask for help
   - Research more

### Phase 4: Implementation

**Fix the root cause, not the symptom:**

1. **Create Failing Test Case**
   - Simplest possible reproduction
   - Automated test if possible
   - One-off test script if no framework
   - MUST have before fixing
   - Use the `feather:write-tests` skill for writing proper failing tests

2. **Implement Single Fix**
   - Address the root cause identified
   - ONE change at a time
   - No "while I'm here" improvements
   - No bundled refactoring

3. **Verify Fix**
   - Test passes now?
   - No other tests broken?
   - Issue actually resolved?

4. **If Fix Doesn't Work**
   - STOP
   - Count: How many fixes have you tried?
   - If < 3: Return to Phase 1, re-analyze with new information
   - **If >= 3: STOP and question the architecture (step 5 below)**
   - DON'T attempt Fix #4 without architectural discussion

5. **If 3+ Fixes Failed: Question Architecture**

   **Pattern indicating architectural problem:**
   - Each fix reveals new shared state/coupling/problem in different place
   - Fixes require "massive refactoring" to implement
   - Each fix creates new symptoms elsewhere

   **STOP and question fundamentals:**
   - Is this pattern fundamentally sound?
   - Are we "sticking with it through sheer inertia"?
   - Should we refactor architecture vs. continue fixing symptoms?

   **Discuss with your human partner before attempting more fixes**

   This is NOT a failed hypothesis — this is a wrong architecture.

## Research vs Reasoning

Before diving into code or searching the web, decide which mode fits your situation.

**Research (search web, check docs) when:**
- Error message you don't recognize — web search the exact message in quotes
- Library/framework behavior doesn't match expectations — check official docs, GitHub issues
- Domain knowledge gap — you need to understand OAuth, indexes, etc. before you can debug
- Platform-specific behavior — works in Chrome but not Safari, works on Mac but not Linux
- Recent ecosystem change — a package update or new framework version broke something

**Reason through code (read, trace, log) when:**
- Bug is in code you or your team wrote — read it, trace execution, add logging
- You have all the information needed — bug is reproducible, all relevant code is readable
- Logic error, not knowledge gap — off-by-one, wrong conditional, state management
- Answer is in behavior, not documentation — "What is this function actually doing?"

**Decision tree:**
```
Error message I don't recognize?
├─ YES → Search the error message
└─ NO ↓
Library behavior I don't understand?
├─ YES → Check docs and GitHub issues
└─ NO ↓
Code I/my team wrote?
├─ YES → Reason through it (log, trace, hypothesis test)
└─ NO ↓
Can I observe the behavior directly?
├─ YES → Add observability and reason through it
└─ NO → Research the domain first, then reason
```

**Balance:** Start with quick research (5-10 min). If no answers, switch to reasoning. If reasoning reveals knowledge gaps, research those specific gaps. Alternate as needed.

## Red Flags — STOP and Follow Process

If you catch yourself thinking:
- "Quick fix for now, investigate later"
- "Just try changing X and see if it works" — seeing symptoms ≠ understanding root cause
- "Add multiple changes, run tests" — can't isolate what worked, causes new bugs
- "Skip the test, I'll manually verify" — untested fixes don't stick, test first proves it
- "It's probably X, let me fix that" — first fix sets the pattern, do it right from the start
- "I don't fully understand but this might work"
- "Issue is simple, don't need process" — simple issues have root causes too, process is fast for simple bugs
- "Emergency, no time for process" — systematic debugging is FASTER than guess-and-check thrashing
- "Pattern says X but I'll adapt it differently" — partial understanding guarantees bugs, read it completely
- "Here are the main problems: [lists fixes without investigation]" — proposing solutions before tracing data flow
- **"One more fix attempt" (when already tried 2+)** — 3+ failures = architectural problem, question the pattern
- **Each fix reveals new problem in different place**

**ALL of these mean: STOP. Return to Phase 1.**

**If 3+ fixes failed:** Question the architecture (see Phase 4, step 5)

## Quick Reference

| Phase | Key Activities | Success Criteria |
|-------|---------------|------------------|
| **1. Root Cause** | Read errors, reproduce, check changes, gather evidence | Understand WHAT and WHY |
| **2. Pattern** | Find working examples, compare | Identify differences |
| **3. Hypothesis** | Generate 3+ theories, test one at a time, seek disconfirmation | Confirmed or new hypothesis |
| **4. Implementation** | Create test, fix, verify | Bug resolved, tests pass |

## Supporting Techniques

See these files in this directory:

- **`root-cause-tracing.md`** — Trace bugs backward through call stack to find original trigger
- **`defense-in-depth.md`** — Add validation at multiple layers after finding root cause
- **`condition-based-waiting.md`** — Replace arbitrary timeouts with condition polling
- **`investigation-techniques.md`** — 8 systematic techniques for narrowing down bugs (binary search, minimal reproduction, git bisect, etc.)
- **`hypothesis-and-verification.md`** — How to form testable hypotheses, debug your own code, and verify fixes actually hold

## When Process Reveals No Root Cause

If systematic investigation reveals issue is truly environmental, timing-dependent, or external:

1. You've completed the process
2. Document what you investigated
3. Implement appropriate handling (retry, timeout, error message)
4. Add monitoring/logging for future investigation

**But:** Most "no root cause" cases are incomplete investigation.

## Human Partner Signals

**Watch for these redirections from your human partner:**
- "Is that not happening?" — You assumed without verifying
- "Will it show us...?" — You should have added evidence gathering
- "Stop guessing" — You're proposing fixes without understanding
- "Ultrathink this" — Question fundamentals, not just symptoms
- Frustration or repeated clarification — Your approach isn't working

**When you see these:** STOP. Return to Phase 1.
