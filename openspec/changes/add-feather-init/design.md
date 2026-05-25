## Decisions

Each decision below uses the same shape: **The question** (what we're deciding and why it matters) · **Alternatives** (real options with their pros and cons) · **Recommendation** (what we picked and why). After-the-fact reading should be as informative as the live walkthrough was.

The eight decisions are ordered from foundational (CLI framework) to specific (`--dev` mode). Later decisions build on earlier ones.

### Decision 1 — CLI framework

#### The question

What Python library do we use to build the `feather` command-line interface? When an operator types `feather init` in their terminal, something has to parse that text, route it to our code, generate `--help`, validate flag types. Picking it locks syntax for declaring every verb and shapes how `--help` looks. Switching later means rewriting every verb's wiring.

#### Alternatives

**A. Typer** — modern, type-hint-driven CLI library. You write a normal function with type-hinted parameters; Typer auto-generates the CLI from the signature.

- **Pros:** Less boilerplate — function's type hints become CLI flag types automatically. Auto-generated `--help` with colors. Modern. main already uses Typer, so the operator familiarity carries over.
- **Cons:** Newer than Click; smaller community. External dependency.

**B. Click** — the mature library Typer is built on. Decorator-based; types declared explicitly via `@click.option`.

- **Pros:** Battle-tested; large ecosystem.
- **Cons:** More boilerplate; type info duplicated between decorator and function signature.

**C. argparse** — Python stdlib.

- **Pros:** Zero external dependency.
- **Cons:** Most boilerplate of the three. No `--help` colors. Sub-command support awkward (matters when `feather source add` ships). Manual type conversion.

#### Recommendation

**A — Typer.**

- Type hints are the project's style; Typer turns them into the CLI for free.
- main already uses Typer, so continuity reduces friction.
- Across ~10 verbs, Typer saves ~100–200 lines of boilerplate vs Click, more vs argparse.
- Click's ecosystem doesn't earn anything we need — Typer IS Click underneath.
- argparse would have us re-implementing what Typer already does.

---

### Decision 2 — Verb registration pattern

#### The question

Given we're using Typer, how does each verb plug into the parent `feather` app? Some verbs in the PRD have sub-commands (`feather source add`, `source test`, `explore log`, `explore replay`); that constrains the choice. The pattern init uses becomes the convention every later verb copies.

#### Alternatives

**A. Flat `register(app)` function per verb** (main's current pattern). Each command module exports a `register(app)` helper that calls `@app.command()` on the parent.

- **Pros:** Minimal boilerplate per verb.
- **Cons:** Awkward for verbs with sub-commands — either flatten into `source-add` (loses `--help` grouping), or handle differently from flat verbs (two patterns).

**B. Nested `app.add_typer(sub_app, name="verb")` uniformly.** Each verb defines its own Typer app; parent attaches it.

- **Pros:** One pattern for flat AND sub-commanded verbs. Each verb is a self-contained Typer app, importable in isolation.
- **Cons:** ~3-5 lines of extra boilerplate per verb. Flat verbs need a small `callback(invoke_without_command=True)` shim.

**C. Hybrid via uniform `register(app)` entry point.** Each verb's `cli.py` exports a `register(app)` function. Inside `register`: flat verb uses `@app.command()`; sub-commanded verb builds a sub-Typer and calls `app.add_typer(sub_app, name=...)`.

- **Pros:** Entry point is uniform; verbs with sub-commands get them where they need them without forcing flat verbs to pay the cost. Boilerplate-minimal where possible.
- **Cons:** Two internal mechanics depending on whether the verb has sub-commands — but the entry point shape is shared, so it's "one entry point, two implementations as needed," not "two patterns."

#### Recommendation

**C — Hybrid via `register(app)`.**

- Apply sophistication only where it pays off (core-earns-complexity).
- Flat verbs get the simplest treatment.
- Sub-commanded verbs use nested Typer apps because they actually need them.
- Uniform external shape, situational internal mechanics.

**Init's `register(app)` looks like:**

```python
# src/feather_etl/commands/init/cli.py
def register(app: typer.Typer) -> None:
    @app.command(name="init")
    def init(dir: str | None = None, dev: bool = False) -> None:
        ...

# src/feather_etl/cli.py
app = typer.Typer(name="feather")
from feather_etl.commands.init.cli import register as register_init
register_init(app)
```

A verb with sub-commands (future `source`) follows the same entry-point shape but builds a Typer sub-app inside its `register`.

---

### Decision 3 — `core.py` ↔ `cli.py` API shape

#### The question

`core.py` contains the file-stamping logic; `cli.py` is the Typer wrapper. How does `cli.py` call core — one function, multiple functions, a class? This decides who knows about orchestration (the order of file stamping, per-file rules) — core or cli. Ousterhout's "deep modules" principle pushes us toward simple interfaces hiding complexity; that means cli.py shouldn't need to know about the order of file stamping or per-file message composition.

#### Alternatives

**A. One function: `core.init_project(target, dev=False) -> InitResult`.** cli.py calls one thing; core does orchestration internally.

- **Pros:** Deepest interface. cli.py knows nothing about file count, order, or per-file rules. Adding a new file = change core only. One test entry point per scenario.
- **Cons:** Core grows as files are added — but stays one cohesive concern.

**B. Multiple small functions, cli.py orchestrates.** `core.resolve_target()`, `core.stamp_feather_yaml()`, etc.; cli.py calls them in sequence and collects results.

- **Pros:** Each core function is tiny and unit-testable in isolation.
- **Cons:** **Shallow.** cli.py knows the file list, order, message-collection logic. Adding a new file = edit core AND cli. Every later verb with a similar shape duplicates the orchestration pattern.

**C. Class-based: `Initializer(target).run() -> Result`.** Carry state explicitly.

- **Pros:** Sub-methods individually testable.
- **Cons:** Class for one-shot use is heavier than a function. Constructing-then-calling is two steps where one would do.

**D. Two-phase: `plan(target) -> Plan` then `execute(plan) -> Result`.** Enables dry-run / preview.

- **Pros:** Future-proofs `--dry-run`.
- **Cons:** Adds real complexity for a feature we haven't asked for. YAGNI. Easy to add later by splitting A.

#### Recommendation

**A — One function.**

- Deepest available interface — cli.py says "init this target, tell me what happened" and knows nothing more.
- When init grows internally (new files in future), the public interface doesn't change.
- Matches Ousterhout's deep-module rule: small interface, hidden complexity.
- Decomposing into per-file functions (B) is the "look how modular" trap — total complexity is the same, smeared across two files instead of one, with cli.py picking up orchestration that used to be local in core.

---

### Decision 4 — `InitResult` shape

#### The question

`init_project()` returns something. What? This decides what tests assert against, how cli.py formats output, and how easily we extend (adding a fifth file later, say).

#### Alternatives

**A. Plain dict.** Zero ceremony.

- **Pros:** Simplest.
- **Cons:** No type safety. Typos in keys are runtime errors. IDE can't help.

**B. Frozen dataclass with `files` dict + `messages` list.**

```python
@dataclass(frozen=True)
class InitResult:
    target: Path
    files: dict[str, Literal["created", "skipped"]]
    messages: list[str]
    feather_etl_source_path: Path | None = None
```

- **Pros:** Type-checked. IDE-friendly. Frozen prevents accidental mutation. Tests do `assert result.files["feather.yaml"] == "created"`. Extensible by adding fields.
- **Cons:** One class to define. The split between `files` (programmatic) and `messages` (text) is a design choice — see "the split" note below.

**C. List of typed events** (`FileCreated`, `FileSkipped`, etc.).

- **Pros:** Most flexible; streaming-friendly.
- **Cons:** Multiple classes. cli.py needs `match`/`isinstance` dispatch. Overkill for init's 4-line output.

**D. Pydantic model.**

- **Pros:** Runtime validation; built-in JSON serialization.
- **Cons:** External dependency. Init constructs the result itself from known-good values; runtime validation is no-op overhead. Init has no `--json`; serialization buys nothing.

#### Recommendation

**B — Frozen dataclass.**

- Right-sized type safety for init's structured-but-small output.
- Two fields, two consumers: tests want `files` (outcomes); cli wants `messages` (text).
- Splitting `files` and `messages` means tests don't parse strings and cli doesn't re-derive presentation logic.
- Pydantic earns its rent in data-emitting verbs that need JSON serialization — for init, no rent earned.

#### The split — where messages come from

**Core composes per-file messages; cli.py prints them verbatim.** `files` is the programmatic source of truth (4 entries for init: feather.yaml, pyproject.toml, .env, .gitignore). `messages` is the human-readable output, one entry per line cli will print. Three rules govern which files contribute a message:

- **Create path → no message.** A freshly-stamped file contributes only to `files` (`"created"`); it does not append to `messages`. The source-wiring confirmation (Req 6) is the only happy-path output; per-file create lines would be noise.
- **Loud-skip → one message.** Files governed by the reset-by-delete rule (`feather.yaml`, `pyproject.toml`, `.gitignore`) append one entry naming the file and the reset instruction.
- **Silent-skip → no message.** `.env` is the silent-skip case (Req 4b); it contributes neither to `messages` nor produces any other output.

This keeps cli truly thin (`for m in result.messages: typer.echo(m, err=True)`) and puts presentation strings next to the file-stamping rules that govern them (single source of truth for per-file behaviour).

The **next-step source-wiring confirmation** (the "feather-etl: pinned to PyPI" / "feather-etl: editable from …" line, spec Req 6) is NOT part of `messages` — it's composed by cli.py using the `feather_etl_source_path` field on `InitResult`.

Single-detection pattern (avoid double calls and races): core calls `feather_etl_source_path()` once during `init_project`, uses it to substitute `--dev` template paths, and stores the same resolved `Path | None` on `InitResult.feather_etl_source_path`. cli reads the field to compose the confirmation line. Default mode leaves the field at `None` and cli prints the PyPI form; `--dev` mode populates it and cli prints the editable form.

Blank-line rule (spec Req 6 says "separated by a blank line"): cli prints a blank line before the confirmation only when `result.messages` is non-empty. When no per-file lines printed (the all-fresh case), there is nothing to separate from — the confirmation prints alone with no leading blank.

---

### Decision 5 — Template management

#### The question

Init stamps `feather.yaml` (multi-line YAML), `pyproject.toml` (with one `{name}` variable, plus `--dev`-mode variant), `.gitignore` (one line). Where do these templates live, and how do we substitute the variable?

#### Alternatives

**A. Module-level string constants in `core.py`** with `str.format()` for substitution.

- **Pros:** Zero indirection. All templates visible by reading `core.py`. Stdlib only. Tests import constants directly.
- **Cons:** `core.py` grows (~30 lines of templates for init). String-escape gotcha for `{` / `}` (rare in YAML/TOML, non-issue here).

**B. Separate `.template` files loaded via `importlib.resources`.**

- **Pros:** Editor syntax highlighting; templates editable without touching Python.
- **Cons:** Two places to look per template. Requires build config (package data inclusion). Tests do real I/O on package data or mock `importlib.resources`.

**C. Jinja2.**

- **Pros:** Full template language.
- **Cons:** External dependency. Massive overkill for one substitution.

**D. TOML library (`tomli_w` or `tomlkit`) for pyproject; strings for the rest.**

- **Pros:** Guaranteed-valid TOML output; can round-trip with `tomlkit`.
- **Cons:** External dependency. Loses precise control over formatting. We never need round-trip (write once, never read+modify).

#### Recommendation

**A — Module-level string constants in `core.py`.**

- Templates are tiny (~30 lines total).
- No template engine, resource loader, or TOML library earns its keep at that volume.
- One place to look — `core.py` shows every template inline.
- Tests import constants directly.

#### Lightweight validation — required

`pyproject.toml`'s `name` field is substituted via `str.format()`. If `target.name` contains characters that break TOML strings (quotes, backslashes), the file is invalid. Validate `target.name` against `^[A-Za-z0-9_-]+$` before substituting; raise a clear `ValueError` if it fails. Fail fast — before writing any file.

The validation only earns its keep once `pyproject.toml`'s `{name}` substitution exists. It lands in the same commit that first stamps `pyproject.toml` (Commit 4 in tasks.md); earlier commits omit it to keep coverage honest (no untested branches).

#### Future opening

If a later verb (`feather source add`?) needs larger templates with multiple variables or conditionals, B or C may earn their place. Migrating from A is cheap — extract the string into a `.template` file. Don't pre-pay now.

---

### Decision 6 — stdout vs stderr conventions

#### The question

Init produces per-file outcome lines, a source-wiring confirmation, and (on failure) errors. Which stream does each go to? The convention init sets propagates to every later verb — including data-emitting verbs (sample, sql, compare) that have real "data" to emit.

#### Alternatives

**A. Everything on stdout** (Typer's default). Errors only on stderr.

- **Pros:** Simplest. Naive `feather init > log.txt` captures all human output.
- **Cons:** Inconsistent with data verbs later — they MUST split (rows → stdout, status banners → stderr). Status verbs and data verbs would follow different rules.

**B. Everything human-readable on stderr; stdout reserved for data.**

- **Pros:** **One rule across the CLI.** stdout = data; stderr = status/human. Init produces no stdout (no data); data verbs emit on stdout and status on stderr. Same rule for every verb.
- **Cons:** `err=True` ceremony on every `typer.echo()` call. `feather init > log.txt` doesn't capture status (needs `2> log.txt`).

**C. Hybrid by purpose** — per-file lines on stdout (the "result"); hint on stderr; errors on stderr.

- **Pros:** Per-file outcomes feel like "results."
- **Cons:** "What counts as data vs informational" becomes interpretive for every verb. Two streams in one output complicates redirection.

#### Recommendation

**B — Everything human-readable on stderr.**

- Single rule for the whole CLI: stdout = data; stderr = status/human-readable.
- Status verbs (init, use, source add, source test) are silent on stdout.
- Data verbs (sample, sql, compare) emit rows on stdout, banners on stderr — same rule.
- No per-verb judgment calls.
- `feather sample | jq` works cleanly because status banners are on stderr, not interleaved with data.

For init: a one-line helper at the top of cli.py keeps the syntax tidy:

```python
def echo(msg: str) -> None:
    typer.echo(msg, err=True)
```

Then `echo("feather.yaml: created")` reads naturally without per-call `err=True`.

---

### Decision 7 — Atomicity and partial-failure semantics

#### The question

`init_project()` stamps four files in sequence. If it crashes partway (disk full mid-write, permission flipped, SIGKILL), what state is left on disk and whose job is the cleanup?

#### Alternatives

**A. Best-effort: let exceptions propagate, leave partial state, idempotent re-run recovers.** Whatever was written stays; not-yet-attempted files don't get created. Operator re-runs init to top up missing files (skip-if-exists rules preserve what's there).

- **Pros:** Simplest. **No cleanup code at all.** Re-run pattern (locked in the spec) IS our atomicity guarantee — operator's recovery is one re-run.
- **Cons:** Mid-failure inspection shows a half-init project; operator has to know "re-run to finish."

**B. Two-phase commit: write to staging dir, atomic rename if all succeed.**

- **Pros:** Operator's directory is either fully init'd or untouched.
- **Cons:** **Not actually atomic** — multi-file rename isn't a filesystem transaction; if rename #3 fails after #1/#2, partial state again. ~30-50 lines of staging/rename/cleanup exercised only on the rare failure path. Skip-if-exists rules still have to handle re-runs anyway, so two mechanisms doing similar work.

**C. Rollback: track each write; delete on exception.**

- **Pros:** Restores directory on failure.
- **Cons:** Rollback can itself fail; bare `except: pass` to silence it hides bugs. Adds complexity for a scenario the natural recovery already handles.

**D. Pre-flight checks** before any writes.

- **Pros:** Catches structural failures (read-only fs, invalid name) before partial state.
- **Cons:** Doesn't address mid-write failures. TOCTOU race. Not a substitute, only a complement.

#### Recommendation

**A — Best-effort with idempotent re-run.**

- The skip-if-exists pattern locked in the spec IS the atomicity guarantee at the verb level.
- Re-running init recovers from any partial state by design.
- B/C add real code on the rare path for limited benefit — complexity sits on the edge (failure handling), not at the core.
- Pre-flight name validation (Decision 5) happens before any writes — small dose of D's benefit without TOCTOU concerns (name validation is non-IO).

#### What the implementer writes

```python
def init_project(target: Path, dev: bool = False) -> InitResult:
    _validate_target_name(target.name)   # Decision 5: fails before any writes
    
    files: dict[str, Literal["created", "skipped"]] = {}
    messages: list[str] = []
    
    _stamp_feather_yaml(target, files, messages)
    _stamp_pyproject(target, files, messages, dev=dev)
    _stamp_env(target, files, messages)
    _stamp_gitignore(target, files, messages)
    
    return InitResult(target=target, files=files, messages=messages)
```

If any `_stamp_*` raises, the exception propagates. cli.py prints the traceback (Typer default) and exits non-zero. Operator re-runs to finish.

---

### Decision 8 — `pyproject.toml`: default mode + `--dev` flag

#### The question

`feather init` produces a `pyproject.toml` declaring `feather-etl` as a dependency. Two audiences: the feather-etl core team developing against bleeding-edge local source; everyone else (beta customers, future PyPI users, anyone sharing a client project) wanting a portable file that works on any machine. **One mode, two modes, or a flag?**

#### Alternatives

**A. Always portable (PyPI pin); operator runs `uv add --editable` separately for local source.**

- **Pros:** Always portable. No flags. Simplest contract.
- **Cons:** Core team types `uv add --editable <path>` after every init — friction every time, forever.

**B. Always auto-detect and write** (local-checkout → editable; PyPI install → version pin).

- **Pros:** One-command setup for everyone.
- **Cons:** Bakes machine-specific absolute paths into the default `pyproject.toml` — non-portable. If anyone shares the file (teammate, beta customer, CI), the path is wrong.

**C. Always editable + auto-detect.**

- **Pros:** Always works for core team.
- **Cons:** Non-portable; breaks for beta customers and PyPI users.

**D. Default = portable PyPI pin; `--dev` flag opts into auto-detected editable.** `--dev` exits non-zero if no local checkout discoverable.

- **Pros:** Default produces portable artifacts (right for the 90%+ case post-v1, the beta customer case now). `--dev` makes the machine-specific behavior explicit and opt-in. `--dev` failure surfaces contradictions (e.g., operator asked for `--dev` but is on a PyPI install).
- **Cons:** Core team types `--dev` on every init. One typed exception class (the `--dev` failure case). Adds a flag.

#### Recommendation

**D — Default + `--dev` flag.**

- Portability of committed artifacts wins over one-command-friction for the core team.
- `--dev` makes machine-specific behavior explicit and opt-in.
- Default mode produces files anyone can share or commit.
- Pre-v1 friction is +5 characters per init invocation for the core team — acceptable.

#### Detection logic — fixed depth, no walking

`Path(feather_etl.__file__).resolve().parent.parent.parent` plus `(candidate / "pyproject.toml").exists()`. No directory walking, no `[project].name` parsing. Editable installs put the package at `<repo>/src/feather_etl/__init__.py` — three `parent`s land at the repo root. PyPI installs put the package in `site-packages/feather_etl/__init__.py` — three `parent`s land somewhere in the Python install tree without a `pyproject.toml`. Detection check distinguishes them with one stat call.

#### Why `--dev` fails hard instead of falling back

If the operator passed `--dev` but feather-etl was installed from PyPI, their request is contradictory. Silently falling back to default-mode behavior would hide the contradiction — the operator might not realize they didn't get what they asked for. Explicit non-zero exit with a clear message (`--dev requires a local feather-etl checkout. Checked: <path>. No pyproject.toml found there.`) tells them what's wrong.

The failure is raised by core as `DevModeUnavailableError(checked_path: Path)`. cli.py catches it, prints the message defined in spec.md Req 3, and exits non-zero. The exception carries the checked path so the message can name it.

#### Templates

```python
# core.py
PYPROJECT_DEFAULT_TEMPLATE = """\
[project]
name = "{name}"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = ["feather-etl>={version}"]
"""

PYPROJECT_DEV_TEMPLATE = """\
[project]
name = "{name}"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = ["feather-etl"]

[tool.uv.sources]
feather-etl = {{ path = "{source_path}", editable = true }}
"""
```

The `{version}` placeholder in the default template is filled at init time from `feather_etl.__version__` — pins the floor at the currently-installed version, tracks PyPI publication automatically.

---

## Risks / Trade-offs

Accepted friction points and bounded risks the design deliberately does NOT mitigate further. For human reviewers and future-self — agents executing commits don't need these. Re-litigate only if real-world usage proves an item costs more than the friction it earns.

- **Operator must `rm` a file to reset it.** No `--force` shortcut. Acceptable — the friction is the feature; destructive operations earn an explicit gesture.
- **Core team types `--dev` on every init.** ~5 characters of friction per invocation, every project, until PyPI usage dominates. Acceptable — preserves portable defaults for everyone else.
- **`--dev` failure is loud, not graceful.** If the operator misjudged their install (passed `--dev` while on PyPI), init exits with an error instead of doing something useful. Acceptable — silent fallback hides a real contradiction.
- **Auto-detected source path may be wrong in edge cases.** If `feather_etl.__file__.parent.parent.parent` doesn't point at the operator's actual checkout (rare layouts, symlinked installs), `--dev` writes the wrong path. The operator notices via the confirmation line ("editable from `<wrong-path>`") and edits manually. Bounded; the wrong path is visible, not hidden.
- **Accidental re-run is quiet (exit 0).** An operator who didn't realize the project was already initialized won't get a loud error. Mitigation: per-file output lists what was skipped and why. If this confuses people in practice, a `--strict` flag is one line away.
- **Partial-init state on rare mid-write failures.** A crash mid-stamping leaves a half-init directory. Recovery: re-run init; skip-if-exists fills in missing files. Acceptable — re-run pattern handles the rare case by design.
- **No agent-skill installation.** Agents in client projects don't get `feather-etl`-specific skills automatically. Acceptable — skills are out of PRD scope; a `feather skills install` verb is the future home if we want it.
