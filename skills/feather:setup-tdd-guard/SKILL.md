---
name: feather:setup-tdd-guard
description: Set up TDD Guard with Vitest to enforce test-first development. Blocks implementation code until tests exist.
---

# Set Up TDD Guard

## Overview

TDD Guard is a Claude Code hook that prevents writing implementation code until tests exist. It uses a Vitest reporter to track test state and blocks Edit/Write tools on non-test files when tests are failing or missing.

## When to Use

- New projects where you want to enforce TDD
- Projects with Vitest already configured (or willing to add it)
- When you want Claude to follow strict test-first discipline

## Git Workflow

**Before starting:**
```bash
git status  # Must be clean (no uncommitted changes)
```

**After completing all steps:**
```bash
git add -A
git commit -m "Set up TDD Guard with Vitest"
```

This ensures setup changes are atomic and reversible.

## Prerequisites

- Node.js project with `package.json`
- Vitest (will be installed if missing)

## Installation Steps

### 1. Install Dependencies

```bash
npm install -D vitest jsdom @testing-library/react @testing-library/jest-dom tdd-guard-vitest
```

### 2. Create vitest.config.ts

```typescript
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "path";
import { VitestReporter } from "tdd-guard-vitest";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    reporters: ["default", new VitestReporter(__dirname)],
    setupFiles: ["./src/test/setup.ts"],
    coverage: {
      provider: "v8",
      reporter: ["text", "json", "html"],
      exclude: [
        "node_modules/",
        "src/test/",
        "**/*.test.{ts,tsx}",
        "**/*.config.{ts,js}",
        "convex/_generated/",
      ],
      thresholds: {
        lines: 100,
        branches: 100,
        functions: 100,
        statements: 100,
      },
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
```

**Key parts:**
- `VitestReporter(__dirname)` - Reports test state to `.claude/tdd-guard/data/test.json`
- `globals: true` - Enables `describe`, `it`, `expect` without imports
- `setupFiles` - For jest-dom matchers
- `coverage.thresholds` - **Fails tests if coverage drops below 100%**
- `coverage.exclude` - Ignores test files, config, and generated code

### 3. Create Test Setup File

Create `src/test/setup.ts`:

```typescript
import "@testing-library/jest-dom/vitest";
```

### 4. Install Coverage Support

```bash
npm install -D @vitest/coverage-v8
```

### 5. Add npm Scripts

In `package.json`:

```json
{
  "scripts": {
    "test": "vitest",
    "test:run": "vitest run",
    "test:coverage": "vitest run --coverage"
  }
}
```

### 6. Add to .gitignore

```
# Test coverage
coverage/

# TDD Guard runtime data
.claude/tdd-guard/
```

### 7. Create Claude Hook

Create `.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Write|Edit|MultiEdit|TodoWrite",
        "hooks": [
          {
            "type": "command",
            "command": "tdd-guard"
          }
        ]
      }
    ]
  }
}
```

### 8. Restart Claude Code Session

The hook only loads on session start. Run `/clear` or exit and reopen.

### 9. Smoke Test

This test proves the guard actually works — not just that files are in place. **Do not skip it.**

#### 9a. Pre-flight checks

Run this to verify all pieces are in place. Every check must pass before continuing to 9b.

```bash
echo "=== TDD Guard Pre-flight ===" && PASS=0 && FAIL=0 && \
check() { if eval "$2" > /dev/null 2>&1; then echo "  PASS: $1"; PASS=$((PASS+1)); else echo "  FAIL: $1"; FAIL=$((FAIL+1)); fi; } && \
check "vitest installed" "[ -d node_modules/vitest ]" && \
check "tdd-guard-vitest installed" "[ -d node_modules/tdd-guard-vitest ]" && \
check "@testing-library/react installed" "[ -d node_modules/@testing-library/react ]" && \
check "@testing-library/jest-dom installed" "[ -d node_modules/@testing-library/jest-dom ]" && \
check "@vitest/coverage-v8 installed" "[ -d node_modules/@vitest/coverage-v8 ]" && \
check "vitest.config.ts exists" "[ -f vitest.config.ts ]" && \
check "vitest.config.ts has VitestReporter" "grep -q 'VitestReporter' vitest.config.ts" && \
check ".claude/settings.json exists" "[ -f .claude/settings.json ]" && \
check "Hook matcher includes Write|Edit" "grep -q 'Write|Edit' .claude/settings.json" && \
check "Hook command is tdd-guard" "grep -q 'tdd-guard' .claude/settings.json" && \
check "Test setup file exists" "[ -f src/test/setup.ts ] || [ -f src/test-setup.ts ]" && \
check "test script in package.json" "node -e \"const p=require('./package.json'); process.exit(p.scripts?.test ? 0 : 1)\"" && \
check "test:coverage script in package.json" "node -e \"const p=require('./package.json'); process.exit(p.scripts?.['test:coverage'] ? 0 : 1)\"" && \
check "coverage/ in .gitignore" "grep -q 'coverage/' .gitignore" && \
check ".claude/tdd-guard/ in .gitignore" "grep -q '.claude/tdd-guard/' .gitignore" && \
check "tdd-guard CLI available" "[ -x node_modules/.bin/tdd-guard ]" && \
echo "" && echo "Results: $PASS passed, $FAIL failed" && \
if [ $FAIL -eq 0 ]; then echo "Pre-flight complete."; else echo "Fix failures before continuing."; fi
```

If any check fails, go back and fix the corresponding step. Do not continue to 9b.

#### 9b. Functional test — prove the guard blocks

This creates a failing test, runs vitest, then attempts to write implementation code. **The hook must block the write.** That block is the signal that the guard works.

**Step 1.** Create a failing test using Bash (bypasses the hook — we need this file to exist first):

```bash
mkdir -p src/__tests__
cat > src/__tests__/tdd-guard-smoke.test.ts << 'EOF'
import { describe, it, expect } from 'vitest';
describe('TDD Guard Smoke', () => {
  it('fails on purpose', () => {
    expect(true).toBe(false);
  });
});
EOF
```

**Step 2.** Run vitest to populate test.json with failing state:

```bash
npx vitest run 2>&1 || true
```

**Step 3.** Verify test.json reflects the failure:

```bash
grep -q 'failed' .claude/tdd-guard/data/test.json && echo "PASS: test.json shows failed state" || echo "FAIL: test.json not updated — VitestReporter may not be configured"
```

**Step 4.** Now use the **Write tool** to create `src/tdd-guard-smoke-probe.ts` with this content:

```typescript
export const probe = true;
```

**EXPECTED: The hook BLOCKS this write.** You should see a block message from tdd-guard.

- If blocked → **PASS.** Print: `"Smoke test PASSED — guard blocked implementation file write."`
- If NOT blocked → **FAIL. STOP IMMEDIATELY.** Print: `"Smoke test FAILED — guard did not block. Do not proceed."` Then investigate:
  - Did you restart the Claude Code session after step 8? (Run `/clear` or exit and reopen)
  - Is `.claude/settings.json` loaded? (Check with `/hooks`)
  - Run `npx vitest run` and check `.claude/tdd-guard/data/test.json` exists

**Step 5.** Verify runtime files are gitignored:

```bash
git status --porcelain | grep -q '\.claude/tdd-guard' && echo "FAIL: .claude/tdd-guard/ runtime files visible in git status" || echo "PASS: runtime files are gitignored"
```

**Step 6.** Clean up:

```bash
rm -f src/__tests__/tdd-guard-smoke.test.ts src/tdd-guard-smoke-probe.ts
rmdir src/__tests__ 2>/dev/null || true
npx vitest run 2>&1 || true
```

**Setup is complete only when step 4 blocks the write and step 5 shows files are gitignored.**

## File Structure After Setup

```
project/
├── .claude/
│   ├── settings.json          # Hook configuration
│   └── tdd-guard/
│       └── data/
│           └── test.json      # Test state (auto-generated)
├── src/
│   └── test/
│       └── setup.ts           # Jest-dom matchers
├── vitest.config.ts           # Vitest + TDD Guard reporter
└── package.json               # Test scripts
```

## Pre-commit Hook (Enforce Coverage)

Add a git pre-commit hook to block commits if coverage < 100%:

```bash
# Create the hook
cat > .git/hooks/pre-commit << 'EOF'
#!/bin/sh
# Pre-commit hook: Enforce 100% test coverage
# Bypass with: git commit --no-verify

npm run test:coverage
EOF

# Make it executable
chmod +x .git/hooks/pre-commit
```

**Why this matters:** Without enforcement, coverage checks get skipped. This was discovered when an agent committed code without running coverage first.

**Legitimate bypass cases:**
- WIP commits: `git commit --no-verify -m "WIP: incomplete"`
- Docs-only changes: `git commit --no-verify -m "Update README"`
- Emergency fixes: `git commit --no-verify -m "Hotfix: ..."`

The `--no-verify` flag is standard git behavior and requires explicit intent to skip.

---

## Troubleshooting

### Hook not working
- Restart Claude Code session after creating `.claude/settings.json`
- Verify `tdd-guard` is in PATH: `npx tdd-guard --help`

### Tests not being tracked
- Run `npm test` at least once to generate test.json
- Check `.claude/tdd-guard/data/test.json` exists

### Wrong environment errors
- Ensure `environment: "jsdom"` for React component tests
- Use `environmentMatchGlobs` for different test types (see Convex testing skill)
