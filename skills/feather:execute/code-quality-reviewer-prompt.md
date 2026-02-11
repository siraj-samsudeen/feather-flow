# Code Quality Reviewer Prompt Template

Use this template when dispatching a code quality reviewer subagent.

**Purpose:** Verify implementation is well-built (clean, tested, maintainable)

**Only dispatch after spec compliance review passes.**

```
Task tool (general-purpose):
  description: "Review code quality for Task N"
  prompt: |
    You are reviewing the code quality of an implementation.

    ## What Was Implemented

    [From implementer's report]

    ## Task Requirements

    [Task description from plan]

    ## Commits to Review

    Base: [commit before task]
    Head: [current commit]

    ## Your Job

    Review the code changes for quality:

    **Strengths:**
    - What's done well?
    - Good patterns followed?
    - Clear naming?

    **Issues (by severity):**

    Critical (must fix):
    - Security vulnerabilities
    - Data corruption risks
    - Broken functionality

    Important (should fix):
    - Poor error handling
    - Missing edge cases
    - Confusing code

    Minor (nice to fix):
    - Style inconsistencies
    - Minor naming issues
    - Small optimizations

    **Testing:**
    - Do tests verify real behavior (not mocked props)?
    - Are tests comprehensive?
    - Will tests catch regressions?

    ## Report Format

    Strengths: [what's good]
    Issues:
    - Critical: [list with file:line]
    - Important: [list with file:line]
    - Minor: [list with file:line]

    Assessment: Approved / Needs fixes (Critical) / Needs fixes (Important)
```
