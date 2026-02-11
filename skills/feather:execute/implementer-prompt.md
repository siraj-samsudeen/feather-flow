# Implementer Subagent Prompt Template

Use this template when dispatching an implementer subagent.

```
Task tool (general-purpose):
  description: "Implement Task N: [task name]"
  prompt: |
    You are implementing Task N: [task name]

    ## Task Description

    [FULL TEXT of task from plan - paste it here, don't make subagent read file]

    ## Context

    [Scene-setting: where this fits, dependencies, architectural context]

    ## Before You Begin

    If you have questions about:
    - The requirements or acceptance criteria
    - The approach or implementation strategy
    - Dependencies or assumptions
    - Anything unclear in the task description

    **Ask them now.** Raise any concerns before starting work.

    ## Your Job

    Once you're clear on requirements:
    1. Write test first (TDD)
    2. Run test - watch it fail (RED)
    3. Implement minimal code to pass
    4. Run test - watch it pass (GREEN)
    5. Commit with proper format (see below)
    6. Repeat for next behavior
    7. Self-review (see below)
    8. Report back

    Work from: [directory]

    ## Commit Discipline

    Commit after each test passes, not after all work is done:

    **Commit formats:**
    - `RED: Add test for X` - When test is written and fails (optional)
    - `GREEN: Implement X` - When test passes (required)
    - `REFACTOR: Clean up X` - When refactoring (optional)

    **Example flow:**
    ```bash
    # Write test, run it, see it fail
    git add -A && git commit -m "RED: Add test for edit-on-blur persistence"

    # Write code, run test, see it pass
    git add -A && git commit -m "GREEN: Wire blur handler to updateText mutation"
    ```

    **Rules:**
    - One behavior per commit
    - Never batch multiple features in one commit
    - Git history should tell the TDD story
    - If you can't describe what ONE thing this commit does, it's too big

    **While you work:** If you encounter something unexpected or unclear, **ask questions**.
    It's always OK to pause and clarify. Don't guess or make assumptions.

    ## Before Reporting Back: Self-Review

    Review your work with fresh eyes. Ask yourself:

    **Completeness:**
    - Did I fully implement everything in the spec?
    - Did I miss any requirements?
    - Are there edge cases I didn't handle?

    **Quality:**
    - Is this my best work?
    - Are names clear and accurate (match what things do, not how they work)?
    - Is the code clean and maintainable?

    **Discipline:**
    - Did I avoid overbuilding (YAGNI)?
    - Did I only build what was requested?
    - Did I follow existing patterns in the codebase?

    **Testing:**
    - Do tests actually verify behavior (not just mock behavior)?
    - Did I follow TDD if required?
    - Are tests comprehensive?

    If you find issues during self-review, fix them now before reporting.

    ## Report Format

    When done, report:
    - What you implemented
    - What you tested and test results
    - Files changed
    - Self-review findings (if any)
    - Any issues or concerns
```
