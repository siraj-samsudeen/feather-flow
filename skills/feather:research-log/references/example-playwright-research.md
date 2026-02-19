# Research: Playwright Pattern for Per-Test Cleanup

**Date**: 2025-02-19
**Decision**: [DL-001: Auto-use Fixture for Per-Test Cleanup](../decisions/001-playwright-cleanup.md)

## Context

We have a Playwright e2e suite for a Convex app. After each test, we need to call a `clearAll` mutation to reset the database. Currently this lives in `e2e/fixtures.ts` as an auto-use fixture. We want to validate this is the right pattern.

Current implementation:

```typescript
// e2e/fixtures.ts
export const test = base.extend<{ _cleanup: void }>({
  _cleanup: [
    async ({}, use) => {
      await use();
      await client.mutation(api.testing.clearAll);
    },
    { auto: true },
  ],
});
```

Tests import `{ test, expect }` from `./fixtures` instead of `@playwright/test`.

**Constraints**: Cleanup must run after each test (not once per suite). Should be automatic — new test files shouldn't need to remember to add it. We use `workers: 1` (serial execution, shared DB).

## Questions

1. Which option does the Playwright team recommend for per-test cleanup?
2. Does `globalSetup`/`globalTeardown` support per-test hooks, or only per-suite?
3. Does Playwright have a `setupFiles` concept (like Vitest) where a file's hooks apply across all tests?

## Findings

### Q1: Auto-use fixture is the recommended Playwright pattern

**Verdict: The current implementation is correct and idiomatic. Keep it.**

The Playwright docs have a dedicated section titled "Adding global beforeEach/afterEach hooks" that shows exactly this pattern. From `docs/src/test-fixtures-js.md` lines 779+:

> `beforeEach` and `afterEach` hooks run before/after each test declared in the same file and same `describe` block (if any). If you want to declare hooks that run before/after each test globally, you can declare them as auto fixtures like this:

```typescript
// From the official Playwright docs — identical structure to our fixtures.ts
export const test = base.extend<{ forEachTest: void }>({
  forEachTest: [async ({ page }, use) => {
    // This code runs before every test.
    await use();
    // This code runs after every test.
  }, { auto: true }],
});
```

Key advantages over alternatives:
- **Enforced by the import** — tests import `test` from `./fixtures`, so if they use the wrong `test`, TypeScript/lint can catch it.
- **Composable** — can add more auto-use fixtures (logging, screenshots) without changing test files.

### Q2: `globalSetup`/`globalTeardown` is per-suite only

**Verdict: Cannot do per-test cleanup. Not suitable.**

`globalSetup` and `globalTeardown` are config-level hooks that run once before/after the entire suite. There is no per-test hook mechanism. They also lack fixture support, trace recording, and HTML report visibility.

### Q3: Playwright has no `setupFiles` concept

**Verdict: No equivalent exists. Fixtures are the mechanism.**

Unlike Vitest/Jest, Playwright has no config option to auto-run a file's hooks across all test files. The `test.afterEach()` call only applies to the file it's called in. You'd need every test file to `import './setup'` which triggers the side-effect registration. If any file forgets the import, cleanup is silently skipped with no error or enforcement mechanism.

## Summary

The auto-use fixture pattern is the officially documented Playwright approach for global per-test hooks. The two alternatives (`globalSetup` and shared `afterEach`) either don't support per-test execution or rely on fragile import side-effects.
