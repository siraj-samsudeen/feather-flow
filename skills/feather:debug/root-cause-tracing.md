# Root Cause Tracing

Bugs often manifest deep in the call stack — git init in the wrong directory, a file created in the wrong location, a database opened with the wrong path. The instinct is to fix where the error appears, but that's treating a symptom.

**Never fix where the error appears. Trace backward through the call chain until you find the original trigger, then fix at the source.**

## When to Use

Use this technique when:
- The error happens deep in execution, not at the entry point
- The stack trace shows a long call chain
- It's unclear where invalid data originated
- You need to find which test or caller triggers the problem

## The Tracing Process

### 1. Observe the Symptom

Start with the exact error:
```
Error: git init failed in /Users/jesse/project/packages/core
```

### 2. Find the Immediate Cause

What code directly produces this error?
```typescript
await execFileAsync('git', ['init'], { cwd: projectDir });
```

### 3. Ask: What Called This?

Trace up the call stack:
```typescript
WorktreeManager.createSessionWorktree(projectDir, sessionId)
  → called by Session.initializeWorkspace()
  → called by Session.create()
  → called by test at Project.create()
```

### 4. Keep Tracing — Follow the Bad Value

At each level, ask "What value was passed, and where did it come from?"
- `projectDir = ''` (empty string!)
- Empty string as `cwd` resolves to `process.cwd()`
- That's the source code directory — not a temp directory

### 5. Find the Original Trigger

Where did the empty string originate?
```typescript
const context = setupCoreTest(); // Returns { tempDir: '' }
Project.create('name', context.tempDir); // Accessed before beforeEach ran!
```

The root cause isn't `git init` failing — it's accessing `tempDir` before the test lifecycle initialized it.

## When You Can't Trace Manually

Add instrumentation to capture the call chain at runtime:

```typescript
async function gitInit(directory: string) {
  const stack = new Error().stack;
  console.error('DEBUG git init:', {
    directory,
    cwd: process.cwd(),
    nodeEnv: process.env.NODE_ENV,
    stack,
  });

  await execFileAsync('git', ['init'], { cwd: directory });
}
```

**Use `console.error()` in tests** — `console.log()` and custom loggers are often suppressed by test frameworks.

**Log before the operation, not after it fails.** If the operation crashes, your "after" log never runs.

Run and capture:
```bash
npm test 2>&1 | grep 'DEBUG git init'
```

Analyze the stack traces — look for test file names, line numbers, and patterns (same test? same parameter path?).

## Worked Example

**Symptom:** `.git` directory created in `packages/core/` (source code) during tests.

**Trace:**
1. `git init` runs in `process.cwd()` ← empty `cwd` parameter
2. `WorktreeManager` called with empty `projectDir`
3. `Session.create()` passed empty string
4. Test accessed `context.tempDir` before `beforeEach` ran
5. `setupCoreTest()` returns `{ tempDir: '' }` initially — it's populated later in `beforeEach`

**Root cause:** Top-level variable initialization accessing a value that isn't set until `beforeEach`.

**Fix:** Made `tempDir` a getter that throws if accessed before `beforeEach` completes.

**Also added defense-in-depth** (see `defense-in-depth.md`):
- Layer 1: `Project.create()` validates directory is non-empty
- Layer 2: `WorkspaceManager` validates `projectDir` is non-empty
- Layer 3: `NODE_ENV` guard refuses `git init` outside tmpdir in tests
- Layer 4: Stack trace logging before every `git init`

## Key Discipline

When you find the error site, don't fix there. Ask "What called this?" and keep going up. The root cause is almost always several layers above where the symptom appears. Fixing at the symptom means someone else will hit the same bug from a different call path.
