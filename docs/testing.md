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

### Canonical command

```
uv run pytest --cov --cov-branch --cov-report=term-missing
```

This is THE command. Use it for baseline health checks at session start, the GREEN+coverage gate after impl, and pre-commit verification. No plain-pytest variant — coverage is part of every test run.

### Coverage escape hatches — policy

"100% coverage" is meaningful only when exclusions are tracked.

**Agents MUST NOT add a coverage exclusion without explicit human approval.** This includes:

- `# pragma: no cover` and `# pragma: no branch` inline directives.
- `[tool.coverage.run] omit = [...]` in `pyproject.toml`.
- `[tool.coverage.report] exclude_lines = [...]` in `pyproject.toml`.
- Equivalent entries in `.coveragerc`.

The cost of writing one more test case is almost always lower than the cost of hidden untested code. When tempted to add an exclusion, ASK the user first.

**Every session must scan for existing exclusions** so an inheriting agent doesn't silently absorb a prior agent's exemption:

```
grep -rn "pragma: no cover\|pragma: no branch" src/ tests/ 2>/dev/null
grep -n "\[tool.coverage" pyproject.toml .coveragerc 2>/dev/null
```

Report the count + brief list at session start. If exclusions exist, "100% coverage" reads as "100% modulo these exclusions" — surface them so the user can confirm or push back. The next-session re-scan is what makes the policy robust against any single agent silently adding an exclusion: the diff between disk-state and what's been blessed gets caught independently each session.

### Tracker file threshold (deferred until >10 exclusions)

When the total exclusion count (inline pragmas + config entries combined) **exceeds 10**, the per-session ask-flow stops scaling — 25 re-asks per session is noise, not safety. At that threshold the agent should PROPOSE setting up a dedicated tracker file (`docs/coverage-exclusions.md`) where every approved exclusion gets a row with rationale, and per-session checks diff disk-state against the tracker (Option B from the planning discussion: approved entries are silent-pass; only unapproved deltas surface).

Until exclusion count > 10, the lighter session-start scan + ask is sufficient. Don't pre-build the tracker before it's earned its keep.

## Consult before reaching for non-default tools

Before reaching for a non-default testing tool or approach — snapshot tests, property-based testing (Hypothesis), filesystem mocking, integration tests against external systems — check with the user whether it fits this commit's needs. Don't presume; ask.
