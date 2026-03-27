---
name: feather:setup-convex-testing
description: "Checklist: Is Convex test infrastructure set up? Verify or create convex-test deps, vitest Convex settings, convex/test.setup.ts, and optional @convex-dev/auth support. Add-on to feather:setup-react-testing — run that first."
---

# Set Up Convex Testing

> **Testing Philosophy:** See [feather-testing-convex/TESTING-PHILOSOPHY.md](https://github.com/siraj-samsudeen/feather-testing-convex/blob/main/TESTING-PHILOSOPHY.md) for the integration-first approach, MECE framework, and decision tree.
>
> **API Reference & Examples:** See the [feather-testing-convex README](https://github.com/siraj-samsudeen/feather-testing-convex/blob/main/README.md) for fixtures, Session DSL, before/after examples, and setup files.

Verify or create the Convex-specific test infrastructure for React + Convex integration testing using [feather-testing-convex](https://www.npmjs.com/package/feather-testing-convex).

**Dual-purpose:** Use this checklist to set up Convex testing from scratch OR to diagnose why existing Convex tests won't run.

**Prerequisite:** `/feather:setup-react-testing` must be completed first. This skill adds Convex-specific pieces on top of the React testing environment.

## Checklist

### ☐ 0. Prerequisite: React testing environment set up

**Check:** Verify `/feather:setup-react-testing` has been completed:
- `vitest.config.ts` exists with `plugins: [react()]` and `environment: "jsdom"`
- `src/test/setup.ts` (or `src/test-setup.ts`) exists with jest-dom matchers
- `@testing-library/react`, `jsdom`, `@vitejs/plugin-react` are installed

**If not done:** Run `/feather:setup-react-testing` first, then return here.

### ☐ 1. Convex testing dependencies installed

**Check:**
```bash
npm ls convex-test feather-testing-convex
```

**If missing:**
```bash
npm install -D convex-test feather-testing-convex
```

### ☐ 2. vitest.config.ts has Convex-specific settings

**Check for these settings (in addition to what `feather:setup-react-testing` already set up):**
- `environmentMatchGlobs: [["convex/**", "edge-runtime"]]`
- `server: { deps: { inline: ["convex-test"] } }`

**If missing, add them to the existing vitest.config.ts `test` section:**
```typescript
test: {
  // ... existing settings from feather:setup-react-testing
  environmentMatchGlobs: [["convex/**", "edge-runtime"]],
  server: { deps: { inline: ["convex-test"] } },
},
```

**Complete config for reference** (combining React testing + Convex):
```typescript
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    environmentMatchGlobs: [["convex/**", "edge-runtime"]],
    server: { deps: { inline: ["convex-test"] } },
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
  },
});
```

**Common errors:**
- Missing `inline: ["convex-test"]` → `Cannot find module 'convex-test'`
- Missing `environmentMatchGlobs` → Convex functions fail because they run in jsdom instead of edge-runtime

### ☐ 3. convex/test.setup.ts exists with correct exports

**Check for:**
- `modules` glob export
- `createConvexTest(schema, modules)` → `test` export
- `renderWithConvex` re-export

**If missing, create:**
```typescript
/// <reference types="vite/client" />
import { createConvexTest, renderWithConvex } from "feather-testing-convex";
import schema from "./schema";

export const modules = import.meta.glob("./**/!(*.*.*)*.*s");
export const test = createConvexTest(schema, modules);
export { renderWithConvex };
```

### ☐ 4. (If using @convex-dev/auth) Auth testing support

**Skip this step if your project doesn't use `@convex-dev/auth`.**

**Install:**
```bash
npm install -D @convex-dev/auth
```

**Add vitest plugin** to `vitest.config.ts` plugins:
```typescript
import { convexTestProviderPlugin } from "feather-testing-convex/vitest-plugin";

export default defineConfig({
  plugins: [react(), convexTestProviderPlugin()],
  // ... rest unchanged
});
```
`ConvexTestAuthProvider` imports from an internal `@convex-dev/auth` path; the plugin adds a resolve alias so Vite can find it. Without it: `ERR_PACKAGE_PATH_NOT_EXPORTED`.
> Upstream fix requested: [convex-auth#281](https://github.com/get-convex/convex-auth/issues/281).

**Export `renderWithConvexAuth`** from `convex/test.setup.ts`:
```typescript
import { createConvexTest, renderWithConvex, renderWithConvexAuth } from "feather-testing-convex";
// ...
export { renderWithConvex, renderWithConvexAuth };
```

**Common mistakes:**
- Missing vitest plugin → `ERR_PACKAGE_PATH_NOT_EXPORTED`. Plugin goes in `vitest.config.ts`, not test files.
- Using `signIn`/`signOut` without `renderWithConvexAuth` — plain `ConvexTestProvider` doesn't wire auth actions.
- Expecting `signIn()` to trigger backend auth — it only toggles local UI state for component testing.

> **Auth Testing Examples:** See the [feather-testing-convex README](https://github.com/siraj-samsudeen/feather-testing-convex/blob/main/README.md#5-auth-state-testing) sections 5 (Auth State Testing), 6 (Sign In/Sign Out Flows), and 7 (Sign-In Error Simulation).

### ☐ 5. At least one test passes

**Run:**
```bash
npx vitest run
```

**If no tests exist, create one to verify the setup.**

Pick one example below. You don't need all of them.

#### Backend-only test (query + seed)

```typescript
// src/components/TodoList.test.ts
import { describe, expect } from "vitest";
import { test } from "../../convex/test.setup";
import { api } from "../../convex/_generated/api";

describe("Todos", () => {
  test("seed and query", async ({ client, seed }) => {
    await seed("todos", { text: "Buy milk", completed: false });

    const todos = await client.query(api.todos.list, {});
    expect(todos).toHaveLength(1);
    expect(todos[0].text).toBe("Buy milk");
  });
});
```

#### Integration test (React component + real backend)

```tsx
// src/components/TodoList.test.tsx
import { describe, expect } from "vitest";
import { screen } from "@testing-library/react";
import { test, renderWithConvex } from "../../convex/test.setup";
import { TodoList } from "./TodoList";

describe("TodoList", () => {
  test("shows seeded data", async ({ client, seed }) => {
    await seed("todos", { text: "Buy milk", completed: false });

    renderWithConvex(<TodoList />, client);

    expect(await screen.findByText("Buy milk")).toBeInTheDocument();
  });
});
```

#### Auth test (if step 4 was completed)

```tsx
import { test, renderWithConvexAuth } from "../../convex/test.setup";
import { screen } from "@testing-library/react";
import { expect } from "vitest";

test("shows authenticated view", async ({ client }) => {
  renderWithConvexAuth(<App />, client);
  expect(await screen.findByText("Welcome back")).toBeInTheDocument();
});
```

### ☐ 6. Coverage excludes Convex generated code (optional)

**Check:** `vitest.config.ts` coverage exclusions include `convex/_generated/`.

**If missing, add to coverage.exclude array:**
```typescript
coverage: {
  exclude: [
    // ... existing exclusions
    "convex/_generated/",
  ],
}
```

---

## Next Steps

- **Review test quality** (patterns, anti-patterns, best practices) → use `/feather:review-convex-tests`
