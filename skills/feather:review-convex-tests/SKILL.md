---
name: feather:review-convex-tests
description: "Checklist: Are your Convex tests correct? 10-point review for integration-vs-mock mistakes, missing seed(), unused Session DSL, stale UI assertions after mutations."
---

# feather:review-convex-tests

Review Convex test files against the feather testing philosophy.

## Checklist

Apply the **12-point review checklist** from [feather-testing-convex/TESTING-PHILOSOPHY.md](https://github.com/siraj-samsudeen/feather-testing-convex/blob/main/TESTING-PHILOSOPHY.md#test-review-checklist):

1. No mocked backend for data-display tests

2. No redundant backend-only tests

3. Mocks ONLY for transient states

4. Using `seed()` instead of raw DB inserts

5. Using Session DSL for form interactions

6. Session DSL chains are awaited

7. Not asserting stale UI after mutations

8. Using `findByText` for async data, not `getByText`

9. Multi-user tests use explicit `userId` with `seed`

10. MECE test design — Integration/Mock layers are MECE buckets (no overlap); E2E is a deliberate exception that intentionally overlaps integration tests to provide real-browser confidence for happy paths

11. No snapshot tests — assert specific user-visible values instead

12. Assertions verify user-visible behavior, not just execution — every test must contain at least one `findByText`, `findByRole`, or `getByText`

See the full checklist with examples and rationale in [TESTING-PHILOSOPHY.md](https://github.com/siraj-samsudeen/feather-testing-convex/blob/main/TESTING-PHILOSOPHY.md#test-review-checklist).
