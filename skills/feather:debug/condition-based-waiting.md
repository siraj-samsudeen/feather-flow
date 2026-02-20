# Condition-Based Waiting

Flaky tests often guess at timing with arbitrary delays. This creates race conditions where tests pass on fast machines but fail under load or in CI.

**Wait for the actual condition you care about, not a guess about how long it takes.**

## When to Use

Use this technique when:
- Tests have arbitrary delays (`setTimeout`, `sleep`, `time.sleep()`)
- Tests are flaky — pass sometimes, fail under load
- Tests timeout when run in parallel
- You're waiting for async operations to complete

Do not use when testing actual timing behavior (debounce intervals, throttle delays). If you must use an arbitrary timeout, document WHY it's necessary.

## The Core Pattern

```typescript
// BAD: Guessing at timing
await new Promise(r => setTimeout(r, 50));
const result = getResult();
expect(result).toBeDefined();

// GOOD: Waiting for the actual condition
await waitFor(() => getResult() !== undefined);
const result = getResult();
expect(result).toBeDefined();
```

## Common Conditions

Apply these patterns directly — each waits for the thing you actually care about:

- **Wait for an event:** `waitFor(() => events.find(e => e.type === 'DONE'))`
- **Wait for state:** `waitFor(() => machine.state === 'ready')`
- **Wait for count:** `waitFor(() => items.length >= 5)`
- **Wait for a file:** `waitFor(() => fs.existsSync(path))`
- **Wait for complex condition:** `waitFor(() => obj.ready && obj.value > 10)`

## Implementation

A generic polling function you can drop into any project:

```typescript
async function waitFor<T>(
  condition: () => T | undefined | null | false,
  description: string,
  timeoutMs = 5000
): Promise<T> {
  const startTime = Date.now();

  while (true) {
    const result = condition();
    if (result) return result;

    if (Date.now() - startTime > timeoutMs) {
      throw new Error(`Timeout waiting for ${description} after ${timeoutMs}ms`);
    }

    await new Promise(r => setTimeout(r, 10)); // Poll every 10ms
  }
}
```

## Mistakes to Avoid

**Polling too fast** (`setTimeout(check, 1)`) wastes CPU and can starve the event loop. Poll every 10ms — fast enough for tests, light enough on resources.

**No timeout** means the test hangs forever if the condition is never met. Always include a timeout with a clear error message that says what you were waiting for.

**Stale data** happens when you read state before the loop and never refresh it. Call the getter inside the loop on every iteration to get fresh data.

## When an Arbitrary Timeout IS Correct

Sometimes you genuinely need to wait for a timed behavior, not a condition. This is acceptable when:

1. You first wait for a triggering condition (don't guess when it starts)
2. The delay is based on a known timing constant (not a guess)
3. You document why

```typescript
// Tool ticks every 100ms — need 2 ticks to verify partial output
await waitForEvent(manager, 'TOOL_STARTED'); // First: wait for condition
await new Promise(r => setTimeout(r, 200));   // Then: wait for timed behavior
// 200ms = 2 ticks at 100ms intervals — documented and justified
```

## Impact

From a real debugging session: applying this technique fixed 15 flaky tests across 3 files. Pass rate went from 60% to 100%, execution time dropped 40%, and race conditions were eliminated entirely.
