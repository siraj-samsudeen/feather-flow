# feather-etl — code-reorg rewrite worktree

This is a **rewrite branch** of feather-etl, not main. The main branch
has a working ETL tool with ~720 tests; this branch starts empty and
rebuilds it feature-by-feature under the V3 package-layout convention.

**Read first:** [`docs/ORIENTATION.md`](docs/ORIENTATION.md).

**Placement convention:** [`docs/conventions/package-layout.md`](docs/conventions/package-layout.md).

**Reference source:** the `main` branch at `../../..` holds the existing
implementation. Open it to study *behavior* — never to copy code.

## Current status

- [ ] Feature 1 — `feather init`
- [ ] Feature 2 — `feather validate`
- [ ] Feature 3 — `feather discover`
- [ ] Feature 4 — `feather run`
- [ ] Rest — `setup` / `status` / `history` / `cache` / `view`

## Before you write a single line of code

```bash
uv sync
uv run pytest -q
```

At scaffold time, three tests run (two invariants + one CLI smoke
test). All must pass. If pytest reports anything else — or any fail —
stop and investigate before proceeding.

## Dev commands

- `uv run pytest -q` — full suite with 100% branch coverage gate.
- `uv run poe tw` — watch mode with testmon (Jest-like; re-runs only
  affected tests on save). No coverage gate — use for iteration.
- `uv run poe tw-all` — watch mode without testmon (full suite on
  every save). Fall back here if `tw` skips tests it shouldn't.
- `uv run poe cov-all` — HTML coverage report, opens in browser.

---

# Workflow preferences

Same as main's superpowers conventions:

- **After spec approval**, use Subagent-Driven Development. Announce
  and proceed:
  > "Plan complete. Proceeding with Subagent-Driven execution."
  Inline execution only on explicit user request.

- **Sub-worktrees for feature specs** use branch `feature/<slug>` where
  `<slug>` = the plan filename minus the `YYYY-MM-DD-` prefix and
  `.md` suffix. The `using-git-worktrees` skill handles naming.

- **Spec review gate is preserved.** `/brainstorming` writes a spec;
  the user approves it before any plan or implementation. The
  autopilot starts only after approval.

No `.claude/skills/` overrides in this worktree — use the global
superpowers skills. If a patched behavior becomes necessary, copy the
vendored skill from main at `../../../.claude/skills/`.
