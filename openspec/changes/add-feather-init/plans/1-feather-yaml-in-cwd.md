# 1. feather.yaml in CWD

## 1. Full picture

### What this commit ships?

This commit establishes the verb-template skeleton every later verb will inherit. The file contents are minimal — just enough to stamp `feather.yaml` into the working directory — but the *shape* (core/cli split, register-pattern, accumulator dataclass) is the load-bearing contract.

### What is in scope?

- `init.1a` (Init with no arg stamps files into CWD, if empty)
- `init.2a` (Stamped feather.yaml content matches template)

### What is out of scope?

- skip-if-exists
- `dir` resolution
- `pyproject.toml` stamping
- `.env`
- `.gitignore`
- source-wiring

### What files this commit creates/modifies?

    src/feather_etl/
      cli.py                 ← entry point users type
      commands/init/
        core.py              ← does the init work
        cli.py               ← terminal wrapper for init
        cli_test.py          ← tests for this commit

### What the user sees externally — Before/After state?

**Before:** running `feather init` errors (command doesn't exist).
**After:** running `feather init` in an empty directory creates `feather.yaml` containing the template (`sample_threshold`, commented `sources:` placeholder). Process exits 0.

### What's still missing after this commit?

- skip-if-exists message
- `dir` arg resolution
- `pyproject.toml` stamping
- `.env` stamping
- `.gitignore` stamping
- surface-invariant test + final coverage gate

### How the runtime flows after this commit — what calls what?

    CLI receives "feather init"
        → cli.py root app dispatches to init command
        → init() calls core.init_project(Path.cwd())
            → _stamp_feather_yaml writes FEATHER_YAML_TEMPLATE to cwd/feather.yaml
            → records files["feather.yaml"] = "created"
        → returns InitResult; process exits 0

### The layer map — which file owns which responsibility?

| Layer       | File           | Responsibility                                                                                                                                                                        |
| ----------- | -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Test        | `cli_test.py`  | • Pins `init.1a` and `init.2a`<br>• Uses `CliRunner` to invoke root `app` in-process<br>• Asserts `exit_code == 0` and file/content state                                             |
| Verb (CLI)  | `init/cli.py`  | • Exports `register(app)` — attaches `init` command to a Typer app<br>• Defines `init()` — the command body<br>• Body calls `core.init_project(Path.cwd())`                           |
| Verb (Core) | `init/core.py` | • `FEATHER_YAML_TEMPLATE` — YAML body, module constant<br>• `_stamp_feather_yaml` — writes template to `target/feather.yaml`<br>• `init_project` — orchestrator, returns `InitResult` |
| Root        | `cli.py`       | • `typer.Typer(name="feather")` — the root app<br>• No-op `@app.callback()` — forces multi-command parsing<br>• `register_init(app)` — wires the `init` verb in                       |

## 2. Key terms

Terms specific to this codebase that recur in later sections — not Python language features.

- **`register(app)` pattern.**
  - Each verb exports `register(app: typer.Typer)` that attaches its `@app.command(...)` to a Typer app passed in.
  - Adding a new verb is one line in root `cli.py`.
  - Alternative would be module-level `@app.command` decorators triggered by import; the `register` pattern avoids that import-side-effect spaghetti.
- **Root `@app.callback()`.**
  - A no-op callback in the root `cli.py`.
  - Forces Typer into multi-command mode even when only one verb is registered.
  - Without it, `feather init` parses as a single-command app and collapses.
- **`FEATHER_YAML_TEMPLATE`.**
  - Module-level YAML body in `core.py`.
  - The verbatim text stamped into `feather.yaml` on init.
  - Module constant (not a packaged-data file) for visibility in source.
- **`InitResult`.**
  - The `init_project` return value.
  - Frozen dataclass in `core.py`.
  - Carries `target` (where init ran) and the `files` outcome map.
- **`_stamp_<file>(target, files, ...)` accumulator pattern.**
  - Each per-file stamper mutates a shared `files` dict.
  - `init_project` then wraps that dict in `InitResult`.
  - Pattern only visible once a second stamper lands later in the verb.

## 3. Key ideas

Design intents — knowing them prevents over-engineering or premature generalization.

- **Verb-template skeleton.**
  - What matters in this commit is the *shape* every later verb copies — not the 4 lines of behavior.
  - `init_project` is 4 lines today; what matters is that those 4 lines live in `core.py`, the Typer adapter lives in `cli.py`, and the root wires it through `register(app)`.
  - Every later verb (`source add`, `curate`, `extract`) copies this triple.
- **Mutation during construction, immutability after.**
  - `_stamp_feather_yaml` mutates `files` in place while `init_project` composes the result.
  - Once wrapped in frozen `InitResult`, it's locked.
  - Build freely, then seal.
- **YAGNI lean — no future-proofing.**
  - `init_project` takes only `target: Path` this commit.
  - **DO NOT add `dev`, `dir`, `feather_etl_source_path`, the `messages` field, or the `echo` helper this commit.** Each lands in the commit that demands it.

## 4. Tests to write

Failing tests written first (RED). These pin the contract for the scenarios listed in §1.

Location: `src/feather_etl/commands/init/cli_test.py`. Both tests use an in-process Typer test runner with the working directory switched to a fresh temporary directory per test. (Click 8.2+ removed the `mix_stderr` flag from the runner, so we don't pass it; stderr is captured separately by default in current versions.)

### Init with no arg stamps files into CWD, if empty (`init.1a`)

- **Given:** an empty working directory.
- **When:** the user runs `feather init` with no arguments.
- **Then:** the command exits 0 and a `feather.yaml` file exists in that directory.
- *Implementation hints:*
  - Set up with `monkeypatch.chdir(tmp_path)` and `CliRunner()`.
  - Invoke as `runner.invoke(app, ["init"])`.
  - Assert `result.exit_code == 0` and `(tmp_path / "feather.yaml").is_file()`.

### Stamped feather.yaml content matches template (`init.2a`)

- **Given:** same setup as above.
- **When:** the user runs `feather init` with no arguments.
- **Then:** the command exits 0 and the stamped `feather.yaml` contains both the `sample_threshold: 100_000` line under `defaults:` and the commented `sources:` placeholder.
- *Implementation hints:*
  - Read the file: `content = (tmp_path / "feather.yaml").read_text()`.
  - Assert: `assert "defaults:\n  sample_threshold: 100_000" in content`.
  - Assert: `assert "# sources:" in content`.

## 5. Implementation outline

**Floor, not wall:** bullets below describe the smallest impl that satisfies §4 tests.

### Directory layout

- Create `src/feather_etl/commands/init/` as a PEP-420 namespace.
- Contains `core.py` and `cli.py` as siblings.
- No empty `__init__.py` per project convention.

### `core.py` — template + dataclass + stamper + orchestrator

- **YAML template constant.**
  - Module-level `FEATHER_YAML_TEMPLATE: str`.
  - Verbatim content from spec Req 2.
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

### `commands/init/cli.py` — Typer adapter for the verb

- Exports `register(app)`.
- Decorates a parameterless `init()` function.
- Body calls `core.init_project(Path.cwd())`.

### `src/feather_etl/cli.py` — root Typer app

- `app = typer.Typer(name="feather")`.
- **No-op `@app.callback()`.**
  - **Not YAGNI** — required this commit.
  - Without it, Typer collapses `feather init` into a single-command app.
- `register_init(app)` wires the verb in.

### `pyproject.toml` — console-script wiring

- `[project.scripts]`: `feather = "feather_etl.cli:app"`.

## 6. Open question

_None._

## 7. Decisions taken

Decisions that change a previously-stated contract or convention. Note any out-of-band follow-up.

_None._

## 8. Deviations

Executor deviations from this plan, recorded before commit with a one-line rationale.

_None._
