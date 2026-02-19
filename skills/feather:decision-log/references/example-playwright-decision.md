# DL-001: Auto-use Fixture for Per-Test Cleanup

**Date**: 2025-02-19
**Status**: Accepted
**Research**: [001-playwright-cleanup.md](../research/001-playwright-cleanup.md)

## Context

Our Playwright e2e suite tests a Convex app with a shared database (`workers: 1`, serial execution). After each test, we need to call a `clearAll` mutation to reset the database. This cleanup must be automatic — new test files should not need to remember to add it.

## Options Considered

### Option A: Auto-use fixture

Custom `test` export with a `{ auto: true }` fixture in `e2e/fixtures.ts`. Tests import `{ test, expect }` from `./fixtures` instead of `@playwright/test`.

- **Pro**: Officially documented Playwright pattern. Enforced by the import. Composable.
- **Con**: Tests must import from `./fixtures` instead of `@playwright/test`.

### Option B: `globalSetup` / `globalTeardown`

Config-level hooks in `playwright.config.ts`.

- **Pro**: Zero changes to test files.
- **Con**: Runs once per suite, not per-test. Does not meet the requirement.

### Option C: Shared `test.afterEach` via import side-effect

A setup file that registers `test.afterEach(cleanup)`, imported by all test files.

- **Pro**: Familiar pattern from Jest/Vitest.
- **Con**: Only applies to the file it's called in. Silent failure if import forgotten. No enforcement.

## Decision

**Chosen option: Option A — Auto-use fixture**

This is the exact pattern recommended in Playwright's official docs under "Adding global beforeEach/afterEach hooks." Option B doesn't support per-test hooks. Option C relies on fragile import side-effects. See [research Q1](../research/001-playwright-cleanup.md#q1-auto-use-fixture-is-the-recommended-playwright-pattern) for the full docs analysis.

## Consequences

**Positive:**
- Cleanup is automatic for any test file that imports from `./fixtures`.
- Adding new auto-use fixtures (logging, auth setup) requires no test file changes.

**Negative:**
- All test files must import `test` from `./fixtures` rather than `@playwright/test`.

**Action items:**
- Add an ESLint rule to flag direct `@playwright/test` imports in `e2e/` files.
