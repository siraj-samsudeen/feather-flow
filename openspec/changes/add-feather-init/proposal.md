## Why

`feather init` is verb 1 of the explore phase (PRD §6.1). Every later verb depends on a project that has been stamped — a `feather.yaml` to read sources from, a `pyproject.toml` so `uv run feather <verb>` can resolve the dependency, a `.env` for secrets, a `.gitignore` so transient artefacts don't leak into git. Until init exists, the rest of the CLI has nothing to stand on.

This is also the first command in the rewrite. It sets the convention for every command that follows: thin Typer wrapper, domain logic in a `core` module, no required interactive prompts, no flags that don't earn their keep.

## What

A single command that scaffolds a feather project in a target directory.

```
feather init [DIR] [--dev]
```

- `feather init` — scaffold the current directory, default mode (`pyproject.toml` pins `feather-etl` from PyPI).
- `feather init rama_dw` — create `./rama_dw/` if absent, then scaffold it (default mode).
- `feather init [--dev]` — `--dev` flips to editable mode: auto-detect the local feather-etl checkout and write `[tool.uv.sources]` editable block. Errors out if no local checkout is discoverable. The right mode for the feather-etl core team only.

Stamps exactly four files into the target:

- `feather.yaml` — minimal, with a commented `sources:` block and one active default.
- `pyproject.toml` — a `[project]` block (`name` = target dir basename, `version`, `requires-python`) plus `feather-etl` declared as a dependency. Init auto-detects whether it's running from a local feather-etl checkout or from a PyPI install and writes the matching dependency form: an editable `[tool.uv.sources]` block pointing at the local checkout (common during dev), or a PyPI version pin (`feather-etl>=0.1.0`). One confirmation line tells the operator which form landed so they can sanity-check at a glance.
- `.env` — empty file, ready for `feather source add` to write password references.
- `.gitignore` — one line: `.env`. Other entries (`explore/`, `*.duckdb`, `.feather/`) are added by the verbs that create those files. Each verb owns its own gitignore entries.

Per-file rules — each file is independent; init never overwrites and never merges. Re-running `feather init` is always safe and always exits 0. Non-zero exits are reserved for genuine I/O failures (permission denied, disk full, read-only filesystem, etc.); these surface as the underlying Python exception's exit code rather than as init-specific error classes.

| File | Absent | Present |
|---|---|---|
| `feather.yaml` | create | skip + print "delete this file and re-run to reset" |
| `pyproject.toml` | create | skip + print "delete this file and re-run to reset" |
| `.env` | touch | skip silently |
| `.gitignore` | create with `.env` as the only entry | skip + print "ensure `.env` is listed" |

Sibling files in the target dir are not looked at and not touched. (Rationale for the absent `--force`, backup, and merge mechanics lives in the "Does NOT do" section below.)

## What this change does NOT do

- No `--force` flag. The operator who wants a reset does `rm <file> && feather init`. Putting the destructive op in the operator's hands beats any flag.
- No `--json` flag. Init's output is small and structured-but-textual; humans and agents both read text fluently. `--json` earns its keep on data-emitting verbs (`feather sample`, `feather compare`, `feather tally --list`), not here.
- No `.gitignore` merge logic. **For any behavior that is not on the feature's load-bearing path, choose the simplest implementation that doesn't lose data — even when a more sophisticated approach is technically possible. Complexity belongs at the core, not at the edges.** Applied here: if the operator already has a `.gitignore`, init prints the one entry that should be present (`.env`) and lets them handle it.
- No gitignore entries for files this verb doesn't create. `explore/`, `*.duckdb`, `.feather/` are added by the verbs that create those files. Same principle that defers `.feather/state.yaml` creation to `feather use`: each verb owns its full footprint, including its gitignore entries.
- No `feather.example.yaml`. `feather source add` (later) teaches the schema interactively; the example file goes stale and splits the source of truth.
- No `CLAUDE.md` template, no skill installation into `.claude/skills/`. Skill internals are out of scope for the explore PRD.
- No pre-creation of `explore/`, `tally/`, `tables/`. Each verb mkdirs its own tree on write — the nested paths (`explore/<date>/…`, `tally/<event>/…`) need that logic anyway, so init pre-creating the roots would only do half the job.
- No `.feather/state.yaml` and no `.feather/` line in the gitignore. `feather use` owns its full footprint end-to-end.
- No `--name` flag. Project name is the target directory's basename when something needs a label, stored only inside `pyproject.toml`'s `name` field.
- No directory walking for source detection. Detection is fixed-depth: `Path(feather_etl.__file__).resolve().parent.parent.parent` + one `.exists()` check on the candidate's `pyproject.toml`. Walking edge cases (symlinks, wrong-parent-pyproject hijacking) avoided. Used only in `--dev` mode.
- No silent fallback when `--dev` detection fails. If the operator passes `--dev` but feather-etl was installed from PyPI, init exits with a clear error instead of silently writing the PyPI form. The operator's explicit request is honored or surfaced as conflicting — never quietly contradicted.

Each of these was either in `main`, in code-reorg's design, or in the PRD's earlier reading; they were considered and dropped. See design.md for the reasoning.

## Impact

- New package: `src/feather_etl/commands/init/` (`cli.py` + `core.py` + colocated tests).
- New entry in `src/feather_etl/cli.py` registering the verb.
- `pyproject.toml` (this repo's) gains the Typer dependency if not already present.
