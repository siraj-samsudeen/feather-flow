# Hypothesis and Verification

How to form testable hypotheses, debug your own code without blind spots, and verify that fixes actually hold. Use this during Phase 3 (Hypothesis and Testing) and Phase 4 (Implementation) of the debugging process.

## Debugging Your Own Code

When you wrote the code that's broken, you're fighting your own mental model. This is harder than debugging someone else's code because:

- You made the design decisions — they feel obviously correct
- You remember your intent, not what you actually implemented
- Familiarity breeds blindness to your own bugs

**To counteract this:**
1. **Read your code as if someone else wrote it.** Don't let memory of intent fill in gaps.
2. **Treat your design decisions as hypotheses, not facts.** You chose an approach — maybe you chose wrong.
3. **Trust the code's behavior over your mental model.** What the code does is truth; what you think it does is a guess.
4. **Suspect the code you touched most recently.** If you modified 100 lines and something breaks, those lines are prime suspects.
5. **Admit the hardest thing:** "I implemented this wrong." Not "requirements were unclear" — you made an error.

## Forming Good Hypotheses

A useful hypothesis must be falsifiable — you must be able to design a test that could prove it wrong.

**Unfalsifiable (useless):**
- "Something is wrong with the state"
- "The timing is off"
- "There's a race condition somewhere"

**Falsifiable (useful):**
- "User state is reset because the component remounts when the route changes"
- "The API call completes after unmount, causing a state update on an unmounted component"
- "Two async operations modify the same array without locking, causing data loss"

The difference is specificity. Useful hypotheses make specific, testable claims.

**Process for forming hypotheses:**
1. **Observe precisely.** Not "it's broken" but "counter shows 3 after clicking once — should show 1"
2. **Ask "What could cause this?"** List every possible cause without judging yet
3. **Make each specific.** Not "state is wrong" but "state updates twice because handleClick fires twice"
4. **Identify evidence.** For each hypothesis: what would support it? What would refute it?

## Designing Experiments

For each hypothesis, design a test before running it:

1. **Prediction:** If my hypothesis is true, I will observe X
2. **Test setup:** What do I need to do?
3. **Measurement:** What exactly am I measuring?
4. **Success criteria:** What result confirms the hypothesis? What result refutes it?
5. **Run** the test
6. **Observe** what actually happened — record it before interpreting
7. **Conclude:** Does evidence support or refute the hypothesis?

**Test one hypothesis at a time.** If you change three things and the bug disappears, you don't know which change fixed it — and two of those changes might introduce new bugs.

**Design experiments that differentiate competing hypotheses.** A single well-placed log statement can rule out multiple theories at once:

```javascript
// Problem: Form submission fails intermittently
// Competing hypotheses: network timeout, validation, race condition, rate limiting

try {
  console.log('[1] Starting validation');
  const validation = await validate(formData);
  console.log('[1] Validation passed:', validation);

  console.log('[2] Starting submission');
  const response = await api.submit(formData);
  console.log('[2] Response received:', response.status);

  console.log('[3] Updating UI');
  updateUI(response);
  console.log('[3] Complete');
} catch (error) {
  console.log('[ERROR] Failed at stage:', error);
}

// Observe results:
// - Fails at [2] with timeout → Network
// - Fails at [1] with validation error → Validation
// - Succeeds but [3] has wrong data → Race condition
// - Fails at [2] with 429 status → Rate limiting
// One experiment, differentiates four hypotheses.
```

## Evaluating Evidence

**Act on strong evidence, not weak signals.**

Strong evidence looks like:
- Directly observable: "I see in logs that X happens"
- Repeatable: "This fails every time I do Y"
- Unambiguous: "The value is definitely null, not undefined"
- Independent: "Happens even in a fresh browser with no cache"

Weak evidence looks like:
- Hearsay: "I think I saw this fail once"
- Non-repeatable: "It failed that one time"
- Ambiguous: "Something seems off"
- Confounded: "Works after restart AND cache clear AND package update"

**Before acting on a hypothesis, confirm you can answer YES to all four:**
1. Do you understand the mechanism — not just "what fails" but "why it fails"?
2. Can you reproduce reliably, or do you understand the trigger conditions?
3. Do you have direct evidence, not just a theory?
4. Have you ruled out alternative explanations?

If any answer is no, keep investigating. "I think it might be X" is not enough to start fixing.

## When a Hypothesis Is Wrong

Being wrong quickly is better than being wrong slowly. When evidence disproves your hypothesis:

1. **Acknowledge it explicitly:** "This hypothesis was wrong because [evidence]"
2. **Extract the learning:** What did this rule out? What new information did you gain?
3. **Update your mental model** with the new facts
4. **Form new hypotheses** based on what you now know
5. **Don't get attached.** Wrong hypotheses aren't wasted — they narrow the search space.

## Verifying Fixes

A fix is not verified until ALL of these are true:

1. **Original issue no longer occurs.** Run the exact reproduction steps — they now produce correct behavior.
2. **You understand why the fix works.** You can explain the mechanism. "I changed X and it worked" is not verification — it's luck.
3. **Related functionality still works.** Run regression tests. Check adjacent features. Anything that shares code with your fix could be affected.
4. **Fix is stable.** It works consistently, not "worked once." For intermittent bugs, run the reproduction multiple times.

**If you can't reproduce the original bug before fixing:** You can't verify the fix. Maybe the bug is still there. Maybe your fix did nothing. If in doubt, revert the fix — if the bug comes back, you've confirmed the fix addresses it.

## Test-First Debugging

Write a failing test that reproduces the bug, then fix until the test passes. This is the strongest form of verification.

**Why this works:**
- Proves you can reproduce the bug precisely
- Gives you automatic, repeatable verification
- Prevents regression forever — the test stays in the suite
- Forces you to understand the bug well enough to encode it

**Process:**
```javascript
// 1. Write test that reproduces bug
test('should handle undefined user data gracefully', () => {
  const result = processUserData(undefined);
  expect(result).toBe(null); // Currently throws TypeError
});

// 2. Run test — it fails (confirms it reproduces the bug)
// ✗ TypeError: Cannot read property 'name' of undefined

// 3. Fix the code
function processUserData(user) {
  if (!user) return null;
  return user.name;
}

// 4. Run test — it passes (confirms the fix works)
// ✓ should handle undefined user data gracefully

// 5. This test is now permanent regression protection
```

## Stability Testing for Intermittent Bugs

When the bug doesn't happen every time, passing once means nothing. Run the reproduction repeatedly:

```bash
# Run the specific test 100 times — if it fails even once, it's not fixed
for i in {1..100}; do
  npm test -- specific-test.js || echo "Failed on run $i"
done
```

For race conditions, add randomized delays to expose timing sensitivity:

```javascript
async function testWithRandomTiming() {
  await randomDelay(0, 100);
  triggerAction1();
  await randomDelay(0, 100);
  triggerAction2();
  await randomDelay(0, 100);
  verifyResult();
}
// Run this 1000 times. If it ever fails, the race condition still exists.
```

## Verification Red Flags

Your verification might be wrong if:
- You can't reproduce the original bug anymore (forgot how, or environment changed)
- The fix is large or complex (too many moving parts to be confident)
- You're not sure why it works
- It only works sometimes — "seems more stable" is not verified
- You can't test in production-like conditions

**Phrases that indicate insufficient verification:** "It seems to work", "I think it's fixed", "Looks good to me"

**Phrases that indicate real verification:** "Verified 50 runs — zero failures", "All tests pass including new regression test", "Root cause was X, fix addresses X directly, here's why"

**Assume your fix is wrong until proven otherwise.** Ask yourself: "How could this fix fail?", "What haven't I tested?", "What am I assuming?", "Would this survive production?"
