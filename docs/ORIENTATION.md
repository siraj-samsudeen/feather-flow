# Orientation — feather-etl rewrite worktree

## What this worktree is

A from-scratch rebuild of feather-etl on the `code-reorg` branch,
intended to eventually replace main. The layout follows the V3 package
convention at [`conventions/package-layout.md`](conventions/package-layout.md):
three vertical axes (sources, destinations, commands) + horizontal
`core/` + repo-wide invariants in `tests/invariants/`.

Main works and has ~720 tests, but it carries structural debt from
iterative growth — particularly in `discover.py` (6-arm funnel,
boolean→string→dispatch flag round trips) and `sources/expand.py`
(two semantically-distinct branches crammed into one function). The
rewrite rebuilds each feature cleanly under the new convention rather
than refactoring file-by-file.

**This is a breaking redesign.** CLI surface, `feather.yaml` schema,
and state DB schema are all on the table. Client canaries will migrate
per a documented upgrade guide when the rewrite is ready to ship.

## How to use main as reference

Main lives at `../../..` from this worktree. When building a feature:

- **Read main** to understand existing behavior — what the feature
  does today, what bugs were fixed, what edge cases exist.
- **Never copy main's code.** Even files that look clean carry
  coupling and naming decisions that fit main's layout, not this
  rewrite's. Re-derive from behavior.
- **Design fresh from the feature's spec.** The spec (produced by
  `/brainstorming`) is the only input to implementation.

## Feature migration order

Features land in the order a real user exercises them end-to-end —
that's the only order testable end-to-end:

1. `feather init` — scaffold a project
2. `feather validate` — check `feather.yaml` + probe source connectivity
3. `feather discover` — read source catalogs, emit schema JSON
4. `feather run` — extract + transform per curated table list
5. `setup` / `status` / `history` / `cache` / `view` — any order after `run`

Each feature lands as its own branch off `code-reorg`, with its own
spec, plan, and PR. Don't start feature N+1 before N is merged.

---

## Ground rules for every feature

### 1. Spec-driven, no TDD ritual

Every feature starts with `/brainstorming` → a written spec you
approve. The spec has specific behavior expectations ("given X,
produce Y"). The agent implements those behaviors; tests assert them.

No strict red-green-refactor TDD ritual. Agents write implementation
and tests in the same session. **The spec is the anti-drift
constraint**, not TDD discipline.

### 2. 100% branch coverage — mandatory, but not sufficient

Every feature PR must hit 100% line + branch coverage on the code it
adds. Enforced in `pyproject.toml` via
`--cov-branch --cov-fail-under=100`.

**Coverage ≠ behavior testing.** Tests must assert on specific values,
not just "didn't raise":

```python
# Weak — coverage green, behavior not tested:
assert result is not None

# Strong — behavior pinned:
assert result.name == "orders"
assert result.row_count == 12
```

Reviewers enforce assertion quality. If weak-assertion drift becomes
visible, adopt mutation testing (`mutmut`) as a second-tier check.

### 3. One command per session

A session ships one command, not five. Limits the blast radius of
agent drift. Forces design attention to each feature's boundaries.

### 4. Three in-file rules

Every module in `src/feather_etl/`:

1. **Has a one-paragraph module docstring** — what the module does,
   and if non-obvious, who calls it. No CONTENTS blocks, no CALL ORDER
   ceremony.
2. **Orders code stepdown: public first, data types in the middle,
   private helpers last.** `from __future__ import annotations` at
   the top.
3. **Stays under ~150 lines, hard cap 200.** When a module grows past
   this, **split it** — don't add banners to organize sprawl. A file
   that needs a TOC is already too big.

Rules 1 and 3 are enforced by
[`tests/invariants/test_file_style.py`](../tests/invariants/test_file_style.py).
Rule 2 is a review rule.

### 5. Package placement follows the V3 convention

See [`conventions/package-layout.md`](conventions/package-layout.md).
The three axes (sources / destinations / commands) + `core/` determine
where files live. Tests colocate as `*_test.py` siblings or in
`scenarios/` folders; repo-wide invariants live in `tests/invariants/`.

---

## What's deliberately NOT in the scaffold

- **No test fixtures** — port from `../../../tests/fixtures/` when a
  test needs one.
- **No fixture generators** — port from `../../../scripts/` when you
  need to regenerate or extend a fixture.
- **No `docs/architecture/`, PRD, or personas** — consult main's copies
  for architectural context. The warehouse layer model is unchanged.
- **No `.claude/skills/` overrides** — use global superpowers skills.
- **No DHH-TOC file template** — replaced by the three in-file rules
  above.
- **No `hands_on_test.sh`** — deprecated; pytest is the authority.

Add these back **only when a concrete need appears**; don't pre-load
infrastructure.

---

## When the rewrite is done

`code-reorg` becomes main after:

- All four workflow features (`init`, `validate`, `discover`, `run`)
  pass their specs.
- At least one client canary has validated the upgrade guide
  end-to-end.
- `tests/invariants/` passes.
- 100% branch coverage across `src/feather_etl/`.

At that point: write the upgrade guide, cut a major-version release,
swap branches.
