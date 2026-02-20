# Defense-in-Depth Validation

When you fix a bug caused by invalid data, adding validation at one point feels sufficient. But that single check can be bypassed by different code paths, refactoring, or mocks.

**Don't just fix the bug — make the bug structurally impossible. Validate at every layer data passes through.**

## Why One Layer Isn't Enough

Different layers catch different failure modes:
- Entry validation catches most bugs from direct callers
- Business logic validation catches edge cases from internal paths
- Environment guards prevent context-specific dangers (tests modifying production data)
- Debug instrumentation helps when all other layers fail

A single validation point gives you "we fixed the bug." Multiple layers give you "we made the bug impossible."

## The Four Layers

### Layer 1: Entry Point Validation

Reject obviously invalid input at the API boundary — the first place data enters your system.

```typescript
function createProject(name: string, workingDirectory: string) {
  if (!workingDirectory || workingDirectory.trim() === '') {
    throw new Error('workingDirectory cannot be empty');
  }
  if (!existsSync(workingDirectory)) {
    throw new Error(`workingDirectory does not exist: ${workingDirectory}`);
  }
  if (!statSync(workingDirectory).isDirectory()) {
    throw new Error(`workingDirectory is not a directory: ${workingDirectory}`);
  }
  // ... proceed
}
```

### Layer 2: Business Logic Validation

Ensure data makes sense for this specific operation, even if it passed entry validation through a different code path.

```typescript
function initializeWorkspace(projectDir: string, sessionId: string) {
  if (!projectDir) {
    throw new Error('projectDir required for workspace initialization');
  }
  // ... proceed
}
```

### Layer 3: Environment Guards

Prevent dangerous operations in specific contexts. These catch bugs that are technically "valid" data but catastrophic in certain environments.

```typescript
async function gitInit(directory: string) {
  if (process.env.NODE_ENV === 'test') {
    const normalized = normalize(resolve(directory));
    const tmpDir = normalize(resolve(tmpdir()));

    if (!normalized.startsWith(tmpDir)) {
      throw new Error(
        `Refusing git init outside temp dir during tests: ${directory}`
      );
    }
  }
  // ... proceed
}
```

### Layer 4: Debug Instrumentation

Capture context for forensics — when the other layers miss something, this tells you what happened.

```typescript
async function gitInit(directory: string) {
  const stack = new Error().stack;
  logger.debug('About to git init', {
    directory,
    cwd: process.cwd(),
    stack,
  });
  // ... proceed
}
```

## How to Apply This After Finding a Root Cause

1. **Trace the data flow** — where does the bad value originate, and where is it consumed?
2. **Map every checkpoint** — list every function or boundary the data passes through
3. **Add validation at each layer** — entry, business logic, environment, debug
4. **Test each layer independently** — try to bypass Layer 1 and verify Layer 2 catches it. Mock Layer 2 and verify Layer 3 catches it.

## Worked Example

**Bug:** Empty `projectDir` caused `git init` to run in the source code directory.

**Data flow:**
1. Test setup → empty string
2. `Project.create(name, '')` → no validation
3. `WorkspaceManager.createWorkspace('')` → no validation
4. `git init` runs in `process.cwd()` → corrupts source code

**Four layers added:**
- Layer 1: `Project.create()` validates directory is non-empty, exists, and is writable
- Layer 2: `WorkspaceManager` validates `projectDir` is non-empty
- Layer 3: `WorktreeManager` refuses `git init` outside tmpdir in test environment
- Layer 4: Stack trace logging before every `git init` call

**Result:** All 1847 tests passed. The bug is impossible to reproduce — any code path that tries to pass an empty directory gets caught at multiple points.

## When to Use This

Apply defense-in-depth after every root cause fix, not just for "important" bugs. The 5 minutes spent adding validation at multiple layers saves hours of debugging when a different code path hits the same problem.

All four layers were necessary in the worked example. During testing, each layer caught bugs the others missed — different code paths bypassed entry validation, mocks bypassed business logic, edge cases on different platforms needed environment guards, and debug logging identified structural misuse patterns.
