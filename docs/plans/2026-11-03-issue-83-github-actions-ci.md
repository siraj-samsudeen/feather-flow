# GitHub Actions CI for feather-flow

Created: 2026-11-03
Status: IN PROGRESS
Approved: Yes
Slice: issue-83
Type: Feature
Review: (none yet)

## Problem

This repo has no `.github/workflows/` at all. On 03-Nov-2026 `main` could not even
**collect** tests: `.python-version` was `3.14`, Python 3.14 removed
`ssl.PROTOCOL_TLSv1`, and `mysql-connector-python` dereferences it at import time.
Zero tests ran and nobody noticed, because nothing was watching.

It matters now because #80 moves `feather-control-plane` (#82) and
`feather-cdc-postgres` here from `JeyaramaGroup/data-warehouse`. Part of the stated
rationale was that this repo would give that code a gate. A repo with no CI does not
give anything a gate.

## What ships

1. **`.python-version` → 3.13** (first commit). Without it CI on `main` is red before
   it starts.

2. **`.github/workflows/ci.yaml`** — `pull_request` + `push` to `main`.
   - `astral-sh/setup-uv` with `enable-cache: true`.
   - Matrix `uv sync --all-extras` → `uv run pytest -q`.
   - **Blocking:** 3.11, 3.12, 3.13 — all measured green on this codebase.
   - **Non-blocking:** 3.10 (`continue-on-error: true`) — measured 22 failed /
     1028 passed / 15 skipped.

3. **`scripts/check_pinned_python_version.py`** — fails when `.python-version` falls
   outside `requires-python` in `pyproject.toml`. Its own CI job, plus a unit test at
   `tests/unit/test_check_pinned_python_version.py`.

## Why 3.10 is non-blocking rather than dropped or fixed

Three options, and only one of them is ours to take.

- **Drop 3.10** — the package is published to PyPI as `feather-flow` 0.2.0 with
  `requires-python = ">=3.10"` and a 3.10 classifier. Retracting a published support
  claim is the owner's call, not a CI PR's.
- **Fix the 22 failures** — out of scope here, and they are not a 3.10 *language*
  incompatibility. Every one is in `tests/unit/sources/test_sqlserver.py`, every one is
  `AttributeError: module 'feather_flow.sources' has no attribute 'sqlserver'`, the file
  **passes when run alone on 3.10**, and fails when `tests/unit/sources/` runs as a
  directory. That is import-order pollution between test modules, it deserves its own
  issue, and bundling it would make this PR un-reviewable.
- **Blocking and permanently red** — the explicit anti-pattern named in #83. A required
  check that is always red trains people to ignore every check.

So 3.10 runs on every PR and stays visible in the checks list, but cannot block a merge.
When the pollution is fixed, deleting `continue-on-error` is a one-line promotion.

## Why a checked-in script rather than inline YAML

The guard is the regression test for the exact 3.14 breakage, so it needs to be testable,
and inline `run:` YAML cannot be unit-tested or run locally. It is stdlib-only — no new
dependency for a check whose whole job is to run before the dependencies are trustworthy.

It parses `requires-python` as a **version specifier set** (`>=`, `>`, `<=`, `<`, `==`,
`!=`, `~=`, and `.*` wildcards), not by string matching: a `>=3.10` substring test would
have happily accepted `3.14`, which is the bug we are guarding against.

`tomllib` is stdlib only from 3.11, and the matrix runs 3.10, so the module falls back to
a scan of the `[project]` table when `tomllib` is missing — otherwise the guard's own unit
test would add a 24th failure to the 3.10 job.

## Explicitly out of scope

- **No workspace/member job.** `packages/` does not exist on `main`; it arrives with #82,
  which rebases onto this branch and brings its own member job (plus the PostgreSQL
  service container its 44 tests need).
- **No change to `requires-python` or the classifiers.**
- **No fix for the 3.10 `test_sqlserver` pollution.**
- **"Does a red `main` block merging?"** (the last box in #83) is a branch-protection
  setting in repo admin, not a file in this repo. This PR makes the checks exist so that
  question can be answered.

## Verification

- `actionlint .github/workflows/ci.yaml`
- `python -c "import yaml; yaml.safe_load(...)"`
- Guard proved both ways: passes at `3.13`, fails at `3.14` and at `3.9`.
- `uv sync --all-extras && uv run pytest -q` green locally.
- Not verifiable locally: that the workflow actually triggers, that the uv cache keys
  work, and the exact pass/skip counts on a runner with no local PostgreSQL/MySQL (more
  skips than here, still exit 0).
