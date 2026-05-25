# Testing approach

## Slicing and cadence

- **Slice vertically — one user-visible behavior per commit, end-to-end.** Each commit touches `core.py`, `cli.py`, and tests together. Don't slice by layer; layer-based commits ship dead code and can't be verified end-to-end until the last layer lands.
- **Test-first batch per slice.** For each commit: read scenario → write tests → confirm RED → write minimum impl → confirm GREEN → coverage check → human review → commit.
- **The RED step is load-bearing.** It catches "I wrote a test that doesn't exercise what I think it does."
- We batch tests per scenario (not strict red-green per assertion). We don't write code first and backfill tests.

## Test layering

- **Default: scenario tests in `cli_test.py`**, one per spec scenario, invoked via Typer's `CliRunner`. Test names mirror scenario titles verbatim. These give refactor-safety: internals can change as long as observable behavior holds.
- **Add unit tests in `core_test.py` only when CLI-level testing is awkward** — impure logic, combinatorial inputs that are tedious to exercise via CLI. If a function can be fully exercised by CLI scenarios, no unit test for it. Pure unit tests for everything is the trap that leads to "test by implementation."
- **One smoke test per surface (not per verb)** — subprocess-runs each verb's `--help`, asserts exit 0. Catches packaging regressions in-process `CliRunner` can't.

## Coverage

100% line + branch coverage repo-wide, enforced. If coverage is hard to hit, the impl probably has dead code — delete it rather than contort tests to reach it.

## Consult before reaching for non-default tools

Before reaching for a non-default testing tool or approach — snapshot tests, property-based testing (Hypothesis), filesystem mocking, integration tests against external systems — check with the user whether it fits this commit's needs. Don't presume; ask.
