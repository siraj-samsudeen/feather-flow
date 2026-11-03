# feather-control-plane — move the control-plane library into this repo as its own package

Created: 2026-11-03
Status: VERIFIED
Approved: Yes
Slice: —
Type: Refactor
Issue: siraj-samsudeen/feather-flow#80 (step 1)
Review: —

## Why

The `control_plane` library lives in `rama_dw` as a repo-local directory that nothing can
depend on. `rama_dw` has 32 top-level directories and no package configuration, so it cannot be
installed; a service that wants the library gets it by **copying the source tree**. Nine services
do, kept in step by a `vendor-sync.sh` script and a sameness test. Two more places copy
`business_day.py` on its own, as a single file.

That arrangement has a specific, measured cost. The vendored copies are what production runs, so
the library's own test suite went red in CI and stayed red without anyone noticing. Publishing it
as a real distribution is what #80 step 1 asks for, and it is what lets the nine copies be deleted
in step 2.

#80's framing is **lift and shift, not rewrite**. This plan honours that, with two exceptions
stated below — both are defects that publishing would otherwise make permanent.

## What lands

```
packages/feather-control-plane/
  pyproject.toml          distribution `feather-control-plane`, import name `control_plane`
  README.md
  src/control_plane/      __init__ business_day recovery run shipper status store wal
  tests/                  conftest test_unit test_integration
                          test_schema_concurrency test_dbt_ready_notify test_packaging
```

`packages/*` becomes a uv workspace in the root `pyproject.toml`.

## Decisions

### The distribution is renamed; the import name is not

`feather-control-plane` on PyPI, `import control_plane` in code. No `feather-flow-` prefix: this
is a sibling in the Feather family, not a part of feather-flow.

The split name is the point. Around forty call sites across nine services already read
`from control_plane import ControlPlane` / `from control_plane.business_day import ist_today`.
Keeping the import name makes step 2 a dependency substitution — add a requirement, delete a
directory — with no source edits at all. Renaming the import would turn a mechanical change into
forty reviewable ones for no gain.

### Workspace member, not a dependency

feather-flow declares no dependency on this package and nothing under `src/feather_flow` imports
it. Membership buys shared resolution and one lock file. `uv sync` at the root does not install
it; `uv run --package feather-control-plane` does.

The root also gains `testpaths = ["tests"]`. Without it, pytest from the repo root walks into
`packages/*/tests` and feather-flow's suite silently becomes "feather-flow plus whatever else is
in the workspace".

### `requires-python` drops from `>=3.12` to `>=3.10`

The `>=3.12` was the old host repo's interpreter, not a property of this code — nothing here uses
3.11+ syntax, and the suite passes on 3.10, 3.11, 3.12 and 3.13 (each verified by running it).

This is not housekeeping. **uv locks a workspace at the intersection of its members' ranges**, so
`>=3.12` on this member rewrote the root lock to `>=3.12` and dropped every 3.10 and 3.11 wheel —
while feather-flow still advertises `>=3.10`. Caught by reading the lock diff: 239 deleted lines,
all cp310/cp311 wheels.

### `psycopg` becomes an optional extra

See precondition A. It is the half of that fix which the lazy import cannot provide: laziness
stops the driver being *loaded*, only an extra stops it being *installed*.

**This is the one change an adopter must act on.** A service using the facade wants
`feather-control-plane[postgres]`; the bare name gets the stdlib half. Record it in the step-2
adoption notes.

## Preconditions fixed on the way through

### A — `__init__.py` forced a database driver on consumers that wanted a date

`__init__.py` did `from .run import ControlPlane, ...`; `run.py` imports psycopg at module level.
A submodule import runs its package first, so `from control_plane.business_day import ist_today`
died on `ModuleNotFoundError: No module named 'psycopg'`. That is exactly why `business_day.py`
is hand-copied into two trees rather than imported: the callers wanted a date helper and would not
take a driver for it. A library whose import graph makes copying easier than depending is broken.

Fix: PEP 562 module-level `__getattr__` for the three `run` names, plus the `postgres` extra.
`status` stays out of the lazy table — `from control_plane import status` is a submodule import
the machinery already handles. `__all__` is unchanged and a `TYPE_CHECKING` block keeps the names
visible to type checkers.

Guarded by a test that imports the stdlib half in a **fresh subprocess** and asserts psycopg is
absent from `sys.modules`. psycopg is installed in the test environment, so "did it crash" proves
nothing; "was it loaded" is the real question.

### B — `ImportError: cannot import name 'ControlPlane' from 'control_plane' (unknown location)`

This is a src-layout collision, not a missing `__init__.py`.

The importable package is `control_plane/src/control_plane/`. The **project folder** around it was
also called `control_plane`. The CI lane cannot `pip install -e .` and substitutes
`PYTHONPATH=<repo root>`; on that path the first thing named `control_plane` is the project
folder, which contains `pyproject.toml`, `src/`, `tests/` and no modules. Python binds it as a
PEP 420 namespace package, so `import control_plane` *succeeds* and resolves to an empty
directory — hence `(unknown location)`, and hence every name fails, `store` and `status` included,
not just `ControlPlane`.

The diagnosis recorded in that workflow — "these directories have no `__init__.py`" — names the
mechanism and misprescribes the cure: adding `__init__.py` to the project folder makes it a
regular package whose submodules are still a level down under `src/`. Nothing was missing. A
src-layout package was never installed, and was being imported by accident of a sibling directory
sharing its name.

Fix: structural. The project folder is now `packages/feather-control-plane/`, which cannot shadow
the import name, and the package is a real installed workspace member. Guarded by a test asserting
a real `__init__.py` and a single `__path__` entry, which fails with a message naming the shadow
instead of "(unknown location)".

## What does not come across

`vendor-sync.sh` and `tests/test_vendored.py` exist only to maintain and police the nine copies.
Both say so in their own docstrings, and both resolve paths against the old repo root and its
service directories. They arrive dead and are deleted in their own commit. The member's standalone
`uv.lock` goes too — a workspace member does not own a lock.

## History

Moved with history: `git format-patch --root --relative=control_plane` in the source repo, then
`git am --directory=packages/feather-control-plane` here. 27 commits, from the library's first
appearance to its last change, with authors and dates intact. `git log -- packages/feather-control-plane`
reaches all of them. The resulting tree is byte-identical to the source (`diff -r`, excluding
`__pycache__`).

## Verification

| Check | Command | Result |
|---|---|---|
| feather-flow suite | `uv run pytest -q` | 1050 passed, 15 skipped |
| package suite | `uv run --package feather-control-plane pytest packages/feather-control-plane/tests -q` | 44 passed |
| wheel, adoption path | `uv run --isolated --with 'dist/…whl[postgres]' python -c …` | facade imports, WAL-only run completes |
| A, with psycopg absent | `uv run --isolated --no-project --with . python -c …` | `ist_today()` works, psycopg not in `sys.modules` |
| B, against the old tree | the guard run on a `git archive` copy | fails, naming the shadow |
| 3.10 – 3.13 | `uv run --isolated --python <v> --with './[postgres]' python -m pytest tests/test_unit.py` | 5 passed on each |
| build | `uv build --package feather-control-plane` | wheel + sdist |

33 of the 44 need a real Postgres and find one via `CONTROL_PG_DSN` → Docker `postgres:16` → a
local `pg_ctl` (`shutil.which`, then a direct glob of `/opt/homebrew/opt/postgresql@*/bin`, which
is why stripping `PATH` does not disable it). All 33 ran against a real server here; with none of
the three routes available they skip and the 11 pure-logic tests still run.

**`--package` selects the environment, not the tests.** pytest reads its config from the nearest
`pyproject.toml` above the working directory, so `uv run --package feather-control-plane pytest`
from the repo root collects feather-flow's 1050 tests and reports green without touching this
package. Name the path, or `cd` into the member.

## Not done here (step 2+)

Deleting the nine vendored copies in `rama_dw` and pointing each service at
`feather-control-plane[postgres]`. This repo was not pushed and no PR was opened.
