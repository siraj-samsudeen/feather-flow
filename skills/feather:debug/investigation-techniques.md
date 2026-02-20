# Investigation Techniques

8 systematic techniques for narrowing down bugs. Use these during Phase 1 (Root Cause Investigation) and Phase 2 (Pattern Analysis) when you need a concrete method to isolate the problem.

## Choosing a Technique

Match the situation to the technique:

- **Large codebase, many files** → Binary Search
- **Confused about what's happening** → Rubber Duck, then Observability First
- **Complex system, many interactions** → Minimal Reproduction
- **Know the desired output** → Working Backwards
- **Used to work, now doesn't** → Differential Debugging or Git Bisect
- **Many possible causes** → Comment Out Everything or Binary Search
- **Always, before making any change** → Observability First

When unsure, start with Observability First — it never hurts and often reveals which other technique to use next.

## Binary Search

Cut the problem space in half repeatedly until you isolate the issue.

**Use when** the failure could be anywhere in a large codebase or long execution path.

**Process:**
1. Identify boundaries — where does it work, where does it fail?
2. Add logging or a test at the midpoint
3. Determine which half contains the bug
4. Repeat on that half until you find the exact line

**Example:** API returns wrong data
```
Data leaves database correctly? YES
Data reaches frontend correctly? NO
Data leaves API route correctly? YES
Data survives serialization? NO
→ Bug is in the serialization layer (4 tests eliminated 90% of code)
```

## Rubber Duck

Explain the problem in complete detail — out loud or in writing.

**Use when** you're stuck, confused, or your mental model doesn't match reality.

**Write or say these six things:**
1. "The system should do X"
2. "Instead it does Y"
3. "I think this is because Z"
4. "The code path is: A → B → C → D"
5. "I've verified that..." (list what you actually tested)
6. "I'm assuming that..." (list your assumptions)

You'll often spot the bug mid-explanation: "Wait, I never verified that B returns what I think it does." The act of articulating assumptions exposes the ones you haven't checked.

## Minimal Reproduction

Strip away everything until the smallest possible code reproduces the bug.

**Use when** the system is complex with many moving parts and it's unclear which part fails.

**Process:**
1. Copy failing code to a new file
2. Remove one piece (dependency, function, feature)
3. Does it still reproduce? YES → keep it removed. NO → put it back.
4. Repeat until you have the bare minimum
5. The bug is now obvious in the stripped-down code

**Example:**
```jsx
// Start: 500-line React component with 15 props, 8 hooks, 3 contexts
// After stripping down to minimal reproduction:
function MinimalRepro() {
  const [count, setCount] = useState(0);

  useEffect(() => {
    setCount(count + 1); // Bug: infinite loop, missing dependency array
  });

  return <div>{count}</div>;
}
// The bug was hidden in complexity. Stripping made it obvious.
```

## Working Backwards

Start from the desired end state and trace backwards to find where reality diverges.

**Use when** you know the correct output but don't know why you're not getting it.

**Process:**
1. Define the desired output precisely
2. What function produces this output?
3. Test that function with expected input — does it produce correct output?
   - YES → bug is earlier (wrong input). Move backwards.
   - NO → bug is here. Investigate this function.
4. Repeat backwards through the call stack
5. Find the divergence point — where expected vs actual first differ

**Example:** UI shows "User not found" when user exists
```
1. UI displays user.error → Is this the right field? YES
2. Component receives user.error = "User not found" → Should be null. WHY?
3. API returns { error: "User not found" } → WHY?
4. Database query: SELECT * FROM users WHERE id = 'undefined' → AH!
5. FOUND: User ID is 'undefined' (string) instead of a number
```

## Differential Debugging

Compare what works against what doesn't to find the difference that causes failure.

**Use when** something used to work and now doesn't, or works in one environment but not another.

**Time-based (worked before, broken now) — compare:**
- Code changes since it last worked
- Environment changes (Node version, OS, dependencies)
- Data changes
- Configuration changes

**Environment-based (works in dev, fails in prod) — compare:**
- Configuration values and environment variables
- Network conditions (latency, reliability)
- Data volume
- Third-party service behavior

**Process:** List all differences. Test each in isolation. Find the one that causes failure.

**Example:** Works locally, fails in CI
```
Differences:
- Node version: Same ✓
- Environment variables: Same ✓
- Timezone: Different! ✗

Test: Set local timezone to UTC (like CI)
Result: Now fails locally too
FOUND: Date comparison logic assumes local timezone
```

## Observability First

Add visibility before changing behavior. Always do this before attempting any fix.

**Process:**
1. Add logging at key decision points
2. Run the code
3. Read the output
4. Form a hypothesis based on what you observed
5. Only then make changes

**What to log:**
```javascript
// Inputs and outputs at function boundaries:
console.log('[handleSubmit] Input:', { email, password: '***' });
console.log('[handleSubmit] Validation result:', validationResult);
console.log('[handleSubmit] API response:', response);

// Assertions that will scream when violated:
console.assert(user !== null, 'User is null at handleSubmit!');
console.assert(user.id !== undefined, 'User ID is undefined!');

// Timing when performance is involved:
console.time('Database query');
const result = await db.query(sql);
console.timeEnd('Database query');

// Stack traces when you don't know who called this:
console.log('[updateUser] Called from:', new Error().stack);
```

Do not skip this step. The impulse to "just change something and see" is the impulse that leads to thrashing.

## Comment Out Everything

Eliminate code by commenting until the bug disappears, then add back until it returns.

**Use when** there are many possible interactions and it's unclear which code causes the issue.

**Process:**
1. Comment out everything in the function/file
2. Verify the bug is gone
3. Uncomment one piece at a time
4. Test after each uncomment
5. When the bug returns, you found the culprit

**Example:** Some middleware breaks requests, but you have 8 middleware functions
```javascript
app.use(helmet());                              // Uncomment, test → works
app.use(cors());                                // Uncomment, test → works
app.use(compression());                         // Uncomment, test → works
app.use(bodyParser.json({ limit: '50mb' }));    // Uncomment, test → BREAKS
// FOUND: Body size limit too high causes memory issues
```

## Git Bisect

Binary search through git history to find the exact commit that introduced a bug.

**Use when** a feature worked in the past and broke at an unknown commit.

**Process:**
```bash
git bisect start
git bisect bad              # Current commit is broken
git bisect good abc123      # This older commit worked
# Git checks out the middle commit — test it
git bisect bad              # or good, based on your test
# Repeat until git identifies the exact breaking commit
git bisect reset            # Return to your branch when done
```

100 commits between working and broken → ~7 tests to find the exact breaking commit. For automated bisect, provide a test script: `git bisect run npm test`.

## Combining Techniques

Techniques compose. A typical investigation flows through several:

1. **Differential debugging** to identify what changed
2. **Binary search** to narrow down where in the code
3. **Observability first** to add logging at that point
4. **Rubber duck** to articulate what you're seeing
5. **Minimal reproduction** to isolate just that behavior
6. **Working backwards** from the symptom to the root cause

Don't commit to a single technique. When one stops making progress, switch to another. The goal is convergence on the root cause, not loyalty to a method.
