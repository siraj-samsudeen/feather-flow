# add-feather-init — tasks

## 1. feather.yaml in CWD


**Before:** `feather init` errors (command doesn't exist).
**After:** `feather init` in an empty CWD creates `feather.yaml` with the template; exits 0.

Lands the **verb-template skeleton** (core/cli split, register-pattern, accumulator dataclass) every later verb (`source add`, `curate`, `extract`) inherits. The skeleton is the load-bearing contract; file contents are minimal.

### Tasks
- [ ] 1.1 Write test for init.1a — Init with no arg stamps files into CWD, if empty.
- [ ] 1.2 Write test for init.2a — Stamped feather.yaml content matches template.
- [ ] 1.3 Implement verb skeleton + `_stamp_feather_yaml` so 1.1 + 1.2 pass.

### Design decisions/hints

- Root app uses `typer.Typer(name="feather")` with a no-op `@app.callback()` to force multi-command parsing. (Decision 1)
- Each verb exports `register(app)`; root `cli.py` calls `register_init(app)`. (Decision 2)
- `init_project(target: Path) -> InitResult` — no `dev`, no `dir` parameter yet. (Decision 3)
- `InitResult` carries TWO fields this commit (`target`, `files`); `messages` and `feather_etl_source_path` land in the commits that demand them. (Decision 4)
- `FEATHER_YAML_TEMPLATE` as a module-level constant, verbatim from spec Req 2. (Decision 5)


## 2. feather.yaml idempotency


**Before:** re-running `feather init` clobbers an existing `feather.yaml`.
**After:** existing `feather.yaml` survives byte-identical; one message line names the file and points at the reset-by-delete path.

Introduces the **skip-if-exists pattern** (reused by `pyproject.toml` §4 and `.gitignore` §7) and the **`messages` channel** on `InitResult` + cli's per-message echo loop — first commit that needs either.

### Tasks
- [ ] 2.1 Write test for init.2b — feather.yaml present is preserved.
- [ ] 2.2 Add skip-if-exists branch to `_stamp_feather_yaml` + loud-skip message so 2.1 passes.

### Design decisions/hints

- Loud-skip = one entry appended to `InitResult.messages` naming the file and pointing at the reset-by-delete path (exact wording per spec Req 2). The `messages` field debuts on `InitResult` here — first commit that needs it. (Decision 4)
- cli prints messages verbatim: `for m in result.messages: typer.echo(m, err=True)` — first commit that needs the echo loop. (Decision 4 — "the split")
- Idempotent re-run IS the atomicity guarantee — skip-if-exists preserves byte-identical existing content. (Decision 7)

## 3. Target-dir resolution


**Before:** `feather init` only works in CWD (no `<dir>` arg).
**After:** `feather init <dir>` works whether `./<dir>/` exists or not; unrelated sibling files in an existing target are neither inspected nor modified.

Adds the **`dir` positional argument** + target resolution — first commit that exercises a non-CWD target.

### Tasks
- [ ] 3.1 Write test for init.1b — Init with new dir name creates the dir and stamps files inside.
- [ ] 3.2 Write test for init.1c — Init reuses an existing sub-directory, ignoring sibling files.
- [ ] 3.3 Add `dir` positional arg to cli + target-dir resolution in `init_project` so 3.1 + 3.2 pass.

### Design decisions/hints

- `cli.py` resolves the target from the positional `dir` arg; `mkdir(parents=True, exist_ok=True)` when target does not exist. (spec Req 1)
- Stamping continues to operate on `target / <filename>` — sibling files in an existing target are neither inspected nor modified. (spec Req 1, last sentence)
- 1c test must include unrelated sibling files (e.g., `README.md`, `data/`, `notes.txt`) and assert they are byte-identical after the run.


## 4. pyproject.toml — default mode


**Before:** `feather init` doesn't stamp a `pyproject.toml`.
**After:** `feather init` (no flag) stamps a portable `pyproject.toml` (PyPI version pin, no `[tool.uv.sources]`), preserves an existing one with a loud-skip, and prints a default-mode source-wiring confirmation on stderr.

Lands the **default-mode `pyproject.toml` arm** + introduces **pre-flight name validation** + the **source-wiring confirmation helper** (PyPI arm only; `--dev` arm follows in §5).

### Tasks
- [ ] 4.1 Write test for init.3a — Default mode writes pyproject.toml with PyPI version pin.
- [ ] 4.2 Write test for init.3d — pyproject.toml present is preserved.
- [ ] 4.3 Write test for init.6a — Default mode confirms PyPI as the source.
- [ ] 4.4 Implement `__version__` literal + `_validate_target_name` + `_stamp_pyproject` (default arm) + source-wiring confirmation (PyPI arm) so 4.1 + 4.2 + 4.3 pass.

### Design decisions/hints

- `__version__ = "0.0.0"` lands in `src/feather_etl/__init__.py` — first consumer of the literal via the `{version}` substitution in `PYPROJECT_DEFAULT_TEMPLATE`. (Decision 8 — "Templates")
- `_validate_target_name` runs at the top of `init_project` BEFORE any `_stamp_*` helper — first commit that needs `target.name` as a TOML string. cli catches the resulting `ValueError` and exits non-zero with the message. (Decision 5 — "Lightweight validation")
- Source-wiring confirmation goes to stderr (default arm reads `InitResult.feather_etl_source_path is None` to pick the PyPI wording). (Decision 6, Decision 4 — "the split")
- Default template body + skip-message wording verbatim from spec Req 3 (default-mode); confirmation wording verbatim from spec Req 6.
- pyproject loud-skip reuses the pattern introduced in §2 (Decision 4 — loud-skip).


## 5. pyproject.toml — --dev mode


**Before:** `feather init --dev` flag doesn't exist — only the default arm works.
**After:** `feather init --dev` writes an editable `[tool.uv.sources]` block from the auto-detected local checkout, prints a `--dev` source-wiring confirmation, and exits non-zero with a clear error when no checkout is discoverable.

Adds the **`--dev` flag** + the **fixed-depth source detection** + the **fail-hard `DevModeUnavailableError`** path. Final touch on `pyproject.toml`.

### Tasks
- [ ] 5.1 Write test for init.3b — --dev with local checkout writes pyproject.toml with editable source block.
- [ ] 5.2 Write test for init.3c — --dev without local checkout exits non-zero.
- [ ] 5.3 Write test for init.6b — --dev mode confirms the editable path.
- [ ] 5.4 Implement `feather_etl_source_path()` helper + `DevModeUnavailableError` + `--dev` arm of `_stamp_pyproject` + cli error path so 5.1 + 5.2 + 5.3 pass.

### Design decisions/hints

- Detection is **fixed-depth**: `parent.parent.parent` of `feather_etl.__file__`, no upward walking. (Decision 8 — "Detection logic")
- `--dev` template body uses `[tool.uv.sources]` with the auto-detected editable path. (Decision 8 — "Templates")
- On detection failure, raise `DevModeUnavailableError(checked_path: Path)`; cli catches it, prints spec Req 3's wording, exits non-zero. Fail-hard rather than silent fallback. (Decision 8 — "Why --dev fails hard", Decision 7)
- `InitResult.feather_etl_source_path` is populated once during `init_project` and read by cli to compose the confirmation — single-detection pattern, no double-calls or TOCTOU race. (Decision 4 — "the split", single-detection pattern)
- 5.1 monkeypatches `feather_etl.__file__` to a synthetic checkout layout containing a `pyproject.toml`; 5.2 monkeypatches it to a path whose `.parent.parent.parent` has no `pyproject.toml`.


## 6. .env touching


**Before:** `feather init` doesn't create a `.env`.
**After:** Fresh projects get an empty `.env`; existing `.env` files are preserved byte-identical with no per-file output (silent-skip).

Lands the **silent-skip variant** — `.env`'s skip path appends NOTHING to `InitResult.messages` (contrast with the loud-skip used by other files).

### Tasks
- [ ] 6.1 Write test for init.4a — Create empty .env when absent.
- [ ] 6.2 Write test for init.4b — Preserve existing .env silently.
- [ ] 6.3 Add `_stamp_env` helper in `commands/init/core.py` (touch on absent, return silently on present) so 6.1 + 6.2 pass.

### Design decisions/hints

- `_stamp_env(target, files, messages)` follows the per-file accumulator pattern — touch on absent, return silently on present. (Decision 7)
- Silent-skip means NO entry in `InitResult.messages` for `.env` — distinct from the loud-skip path used by other files. (Decision 4 — "the split")
- 6.2 test must assert ABSENCE of any `.env` line in BOTH stdout AND stderr (not just one stream).


## 7. .gitignore creation


**Before:** `feather init` doesn't create a `.gitignore`.
**After:** Fresh projects get a one-line `.gitignore` containing `.env`; existing `.gitignore` files are preserved with a loud-skip informational message.

Lands the final per-file stamper. Content scope is locked: only the single line `.env` — other entries belong to the verbs that create those files.

### Tasks
- [ ] 7.1 Write test for init.5a — Create .gitignore when absent.
- [ ] 7.2 Write test for init.5b — Existing .gitignore is preserved.
- [ ] 7.3 Add `_stamp_gitignore` helper in `commands/init/core.py` so 7.1 + 7.2 pass.

### Design decisions/hints

- `.gitignore` content this commit is exactly one line: `.env` (verbatim per spec Req 5).
- Loud-skip = one entry appended to `InitResult.messages` naming the file and pointing at the reset-by-delete path. Reuses the §2 pattern. (Decision 4 — "the split")
- `_stamp_gitignore` follows the per-file accumulator pattern. (Decision 7)
- Forward-pointer: `explore/`, `*.duckdb`, `.feather/` etc. are NOT added here — they're owned by the verbs that create those files.


## 8. CLI smoke test


**Before:** Tests use `CliRunner` (in-process); packaging / entry-point regressions go undetected.
**After:** `tests/invariants/test_cli_surface.py` subprocess-runs `feather init --help` and asserts exit 0 — catches regressions `CliRunner` cannot detect.

Cross-verb infrastructure that init is the first to land. Later verbs add ONE line each to the same `test_cli_surface.py`.

### Tasks
- [ ] 8.1 Bootstrap `tests/invariants/` (PEP 420 namespace, no `__init__.py`) + add `tests/invariants/test_cli_surface.py` subprocess-running `feather init --help` with assert exit 0.
- [ ] 8.2 Final coverage gate — 100% line + branch across `feather_etl.commands.init`.

### Design decisions/hints

- Test env is `uv run pytest`, which puts the `feather` console script on `PATH` via the editable install — no extra invocation magic needed.
- Once-per-surface, not per-verb. Later verbs add ONE line each to the same `test_cli_surface.py`, not their own smoke-test file. (`docs/testing.md §"Smoke test"`)
- Final coverage gate is the last commit of the verb — earlier commits each maintain 100% line+branch on the files they touch, but §8 is the cross-verb sweep. (`docs/testing.md §"Coverage"`)
- No new spec scenarios; the exit-code 0 contract from proposal.md is verified implicitly by every `CliRunner.invoke(...).exit_code == 0` assertion in §§1–7.
