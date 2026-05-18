# Testing approach

How we test verbs in this rewrite. Locked 2026-05-18 during the `add-feather-init` design discussion. Applies to every verb unless the verb's design doc explicitly deviates.

## Two locked choices

### 1. Slicing — A: vertical, one user-visible behavior per commit

Each commit delivers one observable behavior end-to-end. A commit may touch `core.py` AND `cli.py` AND tests together. Each commit ends with a green test run and a working CLI that does something the previous commit didn't.

We do **not** slice by layer ("commit 1 = core skeleton, commit 2 = cli wiring, commit 3 = tests"). Layer-based commits ship dead code and can't be verified end-to-end until the last layer lands.

A "user-visible behavior" is usually one Requirement or one Scenario from the verb's `spec.md`. Group tightly-coupled scenarios into a single commit when separating them would be artificial; otherwise prefer smaller commits.

### 2. Cadence within a slice — II: test-first batch

For each commit:

```
1. Read the spec scenario(s) this commit will satisfy.
2. Write the tests for those scenarios against the not-yet-implemented code.
3. Run the tests → confirm RED with informative failure messages.
   (If a failure message is unclear, rewrite the test before proceeding.)
4. Write the minimum implementation across whatever files are needed.
5. Run the tests → confirm GREEN.
6. Run the coverage check → confirm 100% line+branch on the touched code.
7. Human reviews the diff (lines, files, anything).
8. Human commits.
```

The RED step is load-bearing. It catches "I wrote a test that doesn't actually exercise the code I think it does" — the single most common failure mode when the same author writes the test and the impl in one pass.

We do not do strict red-green per assertion. Batching tests per scenario gives near-equivalent signal at a fraction of the turns.

We do not write code first and backfill tests. Backfilled tests describe what the code does, not what the spec asks; they pass any refactor and catch nothing.

## Test layering — Option 4 (scenario-driven, with targeted units)

Two flavors of test, organized by what each can verify cheaply.

### `cli_test.py` — scenario tests via Typer's `CliRunner`

The default. One test per spec scenario. Each test:

- Sets up the relevant filesystem state in a `tmp_path` fixture.
- Invokes the verb via `CliRunner` with the scenario's args.
- Asserts the resulting filesystem state and the human-readable output.

Test names mirror scenario names verbatim. A reviewer reads `spec.md` and `cli_test.py` side by side and sees 1:1 correspondence.

These tests give refactor-safety: restructuring `core.py` internals never breaks them as long as observable behavior holds.

### `core_test.py` — focused unit tests for impure / combinatorial logic

Only for functions where CLI-level testing is awkward or coverage gaps remain:

- Functions whose behavior depends on hard-to-fake CLI inputs (e.g., `detect_source_path` needs `feather_etl.__file__` monkeypatched).
- Pure functions with combinatorial inputs that are tedious to exercise via CLI (e.g., a parser with many cases).

If a function can be fully exercised by CLI scenarios, do **not** add a unit test for it. Pure unit tests for everything is the trap that leads to "test by implementation" — tests that pin the shape of the code, not the behavior.

### Smoke test — once per surface, not per verb

A single test (in `test_cli_surface.py` or equivalent) subprocess-runs each verb's `--help` and asserts exit 0. This catches packaging and entry-point regressions that `CliRunner` (in-process) cannot. One line per verb in one file. Do not duplicate per-verb.

## Coverage

`pytest --cov=feather_etl.commands.<verb> --cov-branch --cov-fail-under=100`

100% line + branch coverage, enforced per verb. Coverage failures fail the test run.

This is the rewrite policy. It is achievable because every verb is small and every behavior is scenario-tested. If coverage is hard to hit, the impl probably has dead code or speculative branches; delete them rather than write tests that contort to reach them.

## What we don't do

- **No mocked filesystem (pyfakefs or similar).** Real `tmp_path` is fast and honest. Mocking the filesystem buys nothing here.
- **No mocked subprocess for cli-level tests.** Use `CliRunner` (in-process) for scenarios; use real `subprocess` only for the once-per-surface smoke test.
- **No integration test with a real `uv sync`** for the init verb at this stage. We assert the *content* of `pyproject.toml` is what `uv` would consume; testing `uv` is testing the wrong thing. When a later verb depends on the dep actually resolving, add a smoke test then.
- **No snapshot / golden-directory testing.** It hides intent ("this file should match these bytes" without saying why), is brittle to harmless formatting changes, and the auto-detected absolute path in `pyproject.toml` would need templating to be portable.
- **No property-based tests for v1.** Hypothesis is great when invariants are sharp; we add it on demand for verbs where it earns its keep (e.g., a parser, a CSV writer).

## When to deviate

A verb's design doc may add a strict red-green carve-out for a specific function if the function's logic is combinatorial enough to make batch test-writing brittle (off-by-one, ordering, edge cases). The carve-out is per-function, not per-verb.

Any deviation is recorded in the verb's design.md with a one-line justification.
