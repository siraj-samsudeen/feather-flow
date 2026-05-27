# 1. feather.yaml in CWD

## 1. Full picture

### What this commit ships?

This commit establishes the verb-template skeleton every later verb in this change (and every later verb in the PRD) will inherit. The observable behavior is intentionally tiny — `feather init` in an empty directory stamps `feather.yaml` with the spec template and exits 0 — but the *shape* is the load-bearing contribution: a `commands/<verb>/` package with a `core.py` / `cli.py` split, a `register(app)` Typer entry point wired from root `cli.py`, and a frozen `InitResult` accumulator returned from a single deep-module orchestrator. The four-line behavior is the excuse to land the shape.

### What is in scope?

- `init.1a` (Init with no arg stamps files into CWD, if empty)
- `init.2a` (Stamped feather.yaml content matches template)

### What MUST stay out of this commit?

_Deferred to later commits in this verb. Don't include them now._

- skip-if-exists + the `messages` field on `InitResult` (do NOT extend `InitResult` beyond `target` + `files` this commit)
- `dir` argument + target-dir resolution
- `pyproject.toml` stamping (default + `--dev`)
- `.env` / `.gitignore` touching
- source-wiring confirmation + smoke-test surface

### What files this commit creates/modifies?

```
pyproject.toml                          ← typer dep + console script entry
src/feather_etl/
  cli.py                                ← entry point users type
  commands/init/
    core.py                             ← does the init work
    cli.py                              ← terminal wrapper for init
    cli_test.py                         ← tests for this commit
```

_No `__init__.py` anywhere — PEP 420 namespace packages._

### What the user sees externally — Before/After state?

**Before:** running `feather init` errors (command doesn't exist).
**After:** running `feather init` in an empty directory creates `feather.yaml` containing the template (`sample_threshold`, commented `sources:` placeholder). Process exits 0.

### How the runtime flows after this commit — what calls what?

1. **Operator** types `feather init` in an empty directory.
2. **Console script `feather`** (from `pyproject [project.scripts]`) resolves to `feather_etl.cli:app`.
3. **Root `cli.py` Typer app** dispatches the `"init"` sub-command.
4. **`commands/init/cli.py`** — `init()` callback runs with no args.
5. **`core.init_project(Path.cwd())`**, which:
   - allocates `files: dict[str, Literal["created"]] = {}`
   - calls `_stamp_feather_yaml(target, files)`, which:
     - writes `FEATHER_YAML_TEMPLATE` to `target/feather.yaml`
     - records `files["feather.yaml"] = "created"`
   - returns `InitResult(target=target, files=files)`
6. **`cli.py`** returns; **Typer exits 0**.

---

## 2. Key terms

_Codebase-specific patterns referenced below._

- **`register(app)` pattern.**
  - Each verb exports `register(app: typer.Typer)` that attaches its `@app.command(...)` to a Typer app passed in.
  - Adding a new verb is one line in root `cli.py`.
  - Alternative would be module-level `@app.command` decorators triggered by import; the `register` pattern avoids that import-side-effect spaghetti.

- **No-op `@app.callback()`.**
  - A no-op callback on the root Typer app.
  - Forces multi-command mode — without it, Typer collapses to a single-command app and `feather init` fails to dispatch.
  - **Looks like YAGNI; it isn't.**

- **`InitResult`.**
  - The `init_project` return value.
  - Frozen dataclass in `core.py`.
  - Carries `target` (where init ran) and the `files` outcome map.

- **`_stamp_<file>(target, files, ...)` accumulator pattern.**
  - Each per-file stamper mutates a shared `files` dict.
  - `init_project` then wraps that dict in `InitResult`.
  - Pattern only fully visible once a second stamper lands later in the verb.

---

## 3. Key insights

_Design intents — knowing them prevents over-engineering or premature generalization._

- **Verb-template skeleton.**
  - What matters in this commit is the *shape* every later verb copies — not the four lines of behavior.
  - `init_project` is tiny today; what matters is that those lines live in `core.py`, the Typer adapter lives in `cli.py`, and the root wires it through `register(app)`.
  - Every later verb (`source add`, `curate`, `extract`) copies this triple. Deviating requires explicit justification in that verb's `design.md`.

- **Mutation during construction, immutability after.**
  - `_stamp_feather_yaml` mutates `files` in place while `init_project` composes the result.
  - Once wrapped in frozen `InitResult`, it's locked.
  - Build freely, then seal.

- **Core hides orchestration; CLI stays thin.**
  - `cli.py` calls exactly one thing: `core.init_project(Path.cwd())`. It knows nothing about how many files exist, what order they stamp, or what messages to compose.
  - Ousterhout's deep-module rule applied: small interface, hidden complexity.
  - Adding a fifth stamped file later means zero changes to `cli.py`.

---

## 4. Tests to write

_Failing tests first (RED), then implementation (GREEN), then 100% line+branch coverage. Not strict TDD._

### Init with no arg stamps files into CWD, if empty (`init.1a`)

- **Given:** an empty working directory.
- **When:** the user runs `feather init` with no arguments.
- **Then:** the command exits 0 and a `feather.yaml` file exists in that directory.

### Stamped feather.yaml content matches template (`init.2a`)

- **Given:** same setup as above.
- **When:** the user runs `feather init` with no arguments.
- **Then:** the stamped `feather.yaml` contains the `sample_threshold: 100_000` line under `defaults:` and the commented `sources:` placeholder.

---

## 5. Implementation outline

_The minimum impl needed to pass the above tests — a floor, not a ceiling. Add more only if a test demands it._

### Verb (Core) — `core.py` — template + dataclass + stamper + orchestrator

- **YAML template constant.**
  - Module-level `FEATHER_YAML_TEMPLATE: str`.
  - Verbatim content from the `feather.yaml stamping` requirement in spec.md.
  - Uses `sample_threshold: 100_000`.
- **Frozen result dataclass.**
  - `@dataclass(frozen=True) class InitResult`.
  - Field 1: `target: Path`.
  - Field 2: `files: dict[str, Literal["created", "skipped"]]`.
- **Per-file stamper.**
  - `_stamp_feather_yaml(target, files)`.
  - Writes the template to `target/feather.yaml`.
  - Records `files["feather.yaml"] = "created"`.
- **Orchestrator.**
  - `init_project(target: Path) -> InitResult`.
  - Allocates `files = {}`.
  - Calls `_stamp_feather_yaml(target, files)`.
  - Returns `InitResult(target=target, files=files)`.

### Verb (CLI) — `commands/init/cli.py` — Typer adapter for the verb

- Exports `register(app)`.
- Decorates a parameterless `init()` function.
- Body calls `core.init_project(Path.cwd())`.

### Root — `src/feather_etl/cli.py` — root Typer app

- `app = typer.Typer(name="feather")`.
- **No-op `@app.callback()`.**
  - **Not YAGNI** — required this commit.
  - Without it, Typer collapses `feather init` into a single-command app.
- `register_init(app)` wires the verb in.

### `pyproject.toml` — console-script wiring

- `[project.scripts]`: `feather = "feather_etl.cli:app"`.

### Test — `commands/init/cli_test.py` — scenario tests

- **`monkeypatch.chdir(tmp_path)`** for CWD anchoring — `os.chdir` alternative leaks across tests.
- **`CliRunner()` with default settings** — Click 8.2+ captures stderr separately by default; do NOT pass `mix_stderr=False` (the flag was removed).
- **Substring assertions** on the two load-bearing lines for init.2a — do NOT snapshot the full file; any harmless comment edit would break a snapshot.

---

## 6. Open questions

_Record any open questions surfaced during plan walk; executor may add more mid-impl._

_None._

---

## 7. Decisions taken

_Record contract changes the plan walk surfaced; note any out-of-band follow-up._

_None._

---

## 8. Deviations

_Record any deviations to this plan, pre-commit with rationale._

_None._
