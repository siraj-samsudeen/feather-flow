# 3. Target-dir resolution

## 1. Full picture

### What this commit ships?

This commit teaches `feather init` *where* to stamp. Until now the verb
always targeted the current working directory; this commit adds an
optional `dir` argument so `feather init rama_dw` resolves the target to
`./rama_dw`, creating it if absent and reusing it if present.

The behavior is small — one positional arg, one `mkdir` — but it lands
the **target-resolution split** that every later file-touching verb
inherits: cli turns the operator's `str | None` into a concrete `Path`
(a UI concern), and core ensures that `Path` exists before stamping (a
filesystem concern). The create-or-reuse distinction collapses entirely
into a single idempotent `mkdir(parents=True, exist_ok=True)` — no
branch, so coverage stays honest.

### What is in scope?

- `init.1b` (Init with new dir name creates the dir and stamps files inside)
- `init.1c` (Init reuses an existing sub-directory, ignoring sibling files)

### What is out of scope?

**The following are deliberately deferred from this commit. DO NOT
include them in the implementation — each lands in the commit that
demands it.**

- the `--dev` flag and the `dev` parameter on `init()` / `init_project()`
  (design.md Decision 2 shows the final `init(dir, dev)` signature — this
  commit adds only `dir`; `dev` waits for the `pyproject.toml` commits)
- `pyproject.toml`, `.env`, `.gitignore` stamping
- `_validate_target_name` (the `^[A-Za-z0-9_-]+$` check earns its keep
  only once `pyproject.toml`'s `{name}` substitution exists — design.md
  Decision 5 binds it to that later commit, not this one)
- re-asserting the loud-skip message for an existing `feather.yaml`
  (already locked byte-for-byte by `init.2b`)

### What files this commit creates/modifies?

```
src/feather_etl/
  commands/init/
    core.py        ← init_project ensures target exists before stamping
    cli.py         ← adds the dir argument; resolves it to a Path
    cli_test.py    ← adds init.1b + init.1c tests
```

_No new files. Root `src/feather_etl/cli.py` unchanged._

### What the user sees externally — Before/After state?

**Before:**
- `feather init` only ever writes into the current directory.
- There is no way to name a sub-directory; `feather init rama_dw` errors
  on the unexpected argument.

**After:**
- `feather init rama_dw` creates `./rama_dw/` (and any missing parents)
  and stamps `feather.yaml` inside it; process exits 0.
- Re-running against an existing `./rama_dw/` reuses it — missing init
  files are stamped, files unrelated to init (e.g. `README.md`,
  `notes.txt`, `data/`) are neither inspected nor modified.
- `feather init` with no argument is unchanged — targets the cwd.

### How the runtime flows after this commit — what calls what?

**Delta from §2 — what changes inside the flow:**

1. `commands/init/cli.py`'s `init` now accepts `dir: str | None`.
   - `dir is None` → `target = Path.cwd()` (unchanged from before).
   - `dir` given → `target = Path.cwd() / dir`.
2. `init` calls `core.init_project(target)` with the resolved path.
3. `init_project` now calls `target.mkdir(parents=True, exist_ok=True)`
   as its first step, before any `_stamp_*` helper runs.
   - Absent target → created (with parents).
   - Existing target → reused untouched (`exist_ok=True`).
4. `_stamp_feather_yaml` and the rest run exactly as in §2 — they operate
   on `target`, oblivious to whether it was just created or pre-existed.

Dispatch chain (root `cli.py` → `commands/init/cli.py` → `core.init_project`)
is unchanged.

---

## 2. Key terms

_Codebase-specific patterns referenced below._

- **Target-dir resolution.**
  - The contract for turning the operator's argument into the directory
    init stamps into: no arg → cwd; one arg `DIR` → `./DIR`, created if
    absent.
  - Split across two layers: cli does `str | None → Path`; core does the
    `mkdir`. cli never touches the filesystem; core never parses the
    argument.
  - Every later verb that writes into a project directory inherits this
    same split.

---

## 3. Key ideas

_Design intents — knowing them prevents over-engineering._

- **One primitive, both scenarios.**
  - `mkdir(parents=True, exist_ok=True)` satisfies `init.1b` (create) and
    `init.1c` (reuse) with no conditional.
  - Resisting the urge to branch on `target.exists()` keeps core
    branch-free here, which keeps the 100% branch-coverage gate cheap.

- **Unrelated files are untouched by construction, not by code.**
  - init names the four files it stamps and never enumerates the target
    directory's contents.
  - So `init.1c`'s "files unrelated to init are neither inspected nor
    modified" needs zero implementation — it falls out of the design.

- **Resolution lives at the edge; core stays deep.**
  - cli owns argument-to-path (presentation); core owns ensure-exists
    (behavior). core's interface stays "hand me a target, I'll make sure
    it exists and stamp it" — matching Decision 3's deep-module shape.

---

## 4. Tests to write

_Failing tests first (RED), then implementation (GREEN), then 100%
line+branch coverage. Not strict TDD._

### Init with new dir name creates the dir and stamps files inside (`init.1b`)

- **Given:** an empty working directory in which `./rama_dw/` does not
  exist.
- **When:** the operator runs `feather init rama_dw`.
- **Then:** the command exits 0, `./rama_dw/` now exists as a directory,
  and `./rama_dw/feather.yaml` is a file.
- *Implementation hints:*
  - `monkeypatch.chdir(tmp_path)`; `runner.invoke(app, ["init", "rama_dw"])`.
  - Assert `result.exit_code == 0`, `(tmp_path / "rama_dw").is_dir()`,
    `(tmp_path / "rama_dw" / "feather.yaml").is_file()`.

### Init reuses an existing sub-directory, ignoring sibling files (`init.1c`)

- **Given:** a working directory in which `./rama_dw/` already exists and
  contains an unrelated file (e.g. `README.md`) with sentinel content.
- **When:** the operator runs `feather init rama_dw`.
- **Then:** the command exits 0, the missing `feather.yaml` is stamped
  inside `./rama_dw/`, and the unrelated file is byte-identical to its
  sentinel.
- *Implementation hints:*
  - Pre-create `(tmp_path / "rama_dw").mkdir()` and write a sentinel into
    `(tmp_path / "rama_dw" / "README.md")`.
  - Invoke `["init", "rama_dw"]`; assert exit 0, the stamped
    `feather.yaml` exists, and `README.md`'s content is unchanged.
  - This test deliberately does NOT re-assert the loud-skip path for an
    existing init file — that contract is owned by `init.2b`. Here the
    focus is resolution-into-an-existing-dir plus unrelated-untouched.

---

## 5. Implementation outline

_The minimum impl needed to pass the above tests — a floor, not a
ceiling. Add more only if a test demands it._

### Verb (CLI) — `commands/init/cli.py` — add the `dir` argument; resolve to a Path

- **`dir` parameter on `init`.**
  - Signature becomes
    `def init(dir: str | None = typer.Argument(default=None)) -> None`.
  - The explicit `typer.Argument(default=None)` is REQUIRED — a bare
    `str | None = None` default makes Typer expose `dir` as a `--dir`
    option, not an optional positional, so `feather init rama_dw` exits 2
    with "Got unexpected extra argument(s)". (Verified empirically: both
    parallel-impl agents hit this and fixed it identically.)
  - Name it `dir` to match design.md Decision 2's shown signature, even
    though it shadows the builtin (the locked convention wins).
- **Resolution.**
  - `target = Path.cwd() if dir is None else Path.cwd() / dir`.
  - Pass `target` to `core.init_project(target)`.
  - The existing `for m in result.messages: typer.echo(m, err=True)` loop
    is unchanged.

### Verb (Core) — `commands/init/core.py` — ensure target exists before stamping

- **`init_project` first step.**
  - Add `target.mkdir(parents=True, exist_ok=True)` as the first line of
    the body, before `_stamp_feather_yaml`.
  - No new branch, no new parameter — `init_project(target)` keeps its
    current signature.

### Test — `commands/init/cli_test.py` — add init.1b + init.1c

- Both new tests use the same `monkeypatch.chdir(tmp_path)` + `CliRunner()`
  setup as the existing scenarios.
- Existing tests (1a, 2a, 2b) invoke `["init"]` with no argument, so they
  continue to exercise the `dir is None` arm; the two new tests exercise
  the `else` arm — both branches of the cli resolution are covered.

---

## 6. Open questions

_Record any open questions surfaced during plan walk; executor may add
more mid-impl._

_None._

---

## 7. Decisions taken

_Record contract changes the plan walk surfaced; note any out-of-band
follow-up._

_None._

---

## 8. Deviations

_Record any deviations to this plan, pre-commit with rationale._

- **`dir` declared as `typer.Argument(default=None)`, not a bare
  `= None`.** §5's original hint claimed a bare `str | None = None`
  default would be treated as an optional positional; it is not — Typer
  exposes it as a `--dir` option and `feather init rama_dw` exits 2.
  Both parallel-impl agents (Opus + Sonnet) hit and fixed this
  identically. §5 has been corrected to match. Observable behavior is
  exactly as the plan intended; only the wiring detail changed.
