## Scenario → commit mapping

- **Commit 1** — 1a + 2a — basic CWD init + correct `feather.yaml` content. Observability dependency: 1a's "files are stamped" assertion is meaningless without 2a defining what gets stamped.
- **Commit 2** — 2b — `feather.yaml` idempotency. Stands alone — establishes the skip-if-exists pattern that later file commits replay.
- **Commit 3** — 1b + 1c — `feather init <dir>` target resolution for both branches. Same code path: `if not target.exists(): mkdir(...)`. Scenario 1c additionally exercises the skip-if-exists pattern from commit 2 through the subdir path.
- **Commit 4** — 3a + 3b — `pyproject.toml` create + preserve. Same code path: two branches of `if exists:` for the second marker file.
- **Commit 5** — 6a — next-step hint.
- **Commit 6** — 4a + 4b — `.env` touch + preserve. Same code path: two branches of `if exists:` for the secrets file (with silent skip on present).
- **Commit 7** — 5a + 5b — `.gitignore` create + preserve. Same code path: two branches of `if exists:` for the gitignore file.
- **Commit 8** — no spec scenarios. Cross-verb infrastructure (CLI smoke test).

## Commit 1 — `feather init` creates `feather.yaml` in CWD

After this commit, `feather init` is a real verb that creates a valid `feather.yaml` in the current working directory. Spec scope: scenarios 1a + 2a.

- [ ] 1.1 Write tests for:
      - 1a — init in CWD stamps files into the target
      - 2a — `feather.yaml` content matches the template

      Run them — confirm RED with informative failure messages.
- [ ] 1.2 Scaffold the `feather_etl.commands.init` package, wire its Typer entry into `feather_etl.cli`, and implement `core.init_project` to make tests GREEN. Follow design decisions 1 and 3.
- [ ] 1.3 Run coverage: 100% line + branch on `feather_etl.commands.init`.

## Commit 2 — `feather.yaml` idempotency

After this commit, re-running `feather init` does not destroy an existing `feather.yaml`. Introduces the skip-if-exists pattern that later file commits replay. Spec scope: scenario 2b.

- [ ] 2.1 Write test for:
      - 2b — existing `feather.yaml` is preserved with reset-hint message

      Confirm RED.
- [ ] 2.2 Extend `core.init_project` with skip-if-exists handling for `feather.yaml`, including the per-file message specified in Req 2.
- [ ] 2.3 Run coverage.

## Commit 3 — `feather init <dir>` target resolution (new and existing dirs)

After this commit, `feather init rama_dw` works whether `./rama_dw/` exists or not. New directory: created (with parents if needed). Existing directory: walked into, exercising the skip-if-exists pattern from commit 2 for any feather.yaml already present. Sibling files unrelated to init (`README.md`, `data/`, `notes.txt`) are neither inspected nor modified. Spec scope: scenarios 1b + 1c (two branches of the same target-resolution `if`).

- [ ] 3.1 Write tests for:
      - 1b — `feather init <dir>` creates a missing target directory
      - 1c — `feather init <dir>` reuses an existing target dir without touching unrelated sibling files

      The 1c test should include unrelated sibling files and assert they are untouched. Confirm RED.
- [ ] 3.2 Extend target resolution in `core.init_project` to handle the positional-arg case (`if not target.exists(): target.mkdir(parents=True)`; existing-directory case needs no extra logic — file stamping operates on `target / filename` regardless).
- [ ] 3.3 Run coverage.

## Commit 4 — `pyproject.toml` stamping

After this commit, init also stamps a minimal `[project]` skeleton for `pyproject.toml`. Spec scope: scenarios 3a + 3b — two branches of the same `if exists:` code path.

- [ ] 4.1 Write tests for:
      - 3a — `pyproject.toml` name derived from target directory basename
      - 3b — existing `pyproject.toml` is preserved with reset-hint message

      Confirm RED.
- [ ] 4.2 Extend `core.init_project` to stamp `pyproject.toml` following the same template + skip-if-exists shape as `feather.yaml`.
- [ ] 4.3 Run coverage.

## Commit 5 — Next-step hint

After this commit, init prints the `uv add` command pair (editable form with auto-detected path + PyPI form) so the operator knows how to wire `feather-etl`. Spec scope: scenario 6a.

- [ ] 5.1 Write test for:
      - 6a — hint shows both editable (auto-detected path) and PyPI `uv add` commands

      Monkeypatch `feather_etl.__file__` to verify the path substitution. Confirm RED.
- [ ] 5.2 Add the source-path helper in `core.py` (per design decision 3) and have `cli.py` print the hint after the per-file output lines.
- [ ] 5.3 Run coverage.

## Commit 6 — `.env` touching

After this commit, fresh projects get an empty `.env` ready for `feather source add` to write into; existing `.env` files are preserved byte-identical with no output for the file (silent skip distinguishes it from marker files). Spec scope: scenarios 4a + 4b — two branches of the same `if exists:` code path.

- [ ] 6.1 Write tests for:
      - 4a — empty `.env` created when absent
      - 4b — existing `.env` preserved silently (no per-file output line)

      The 4b test must assert that no `.env` line appears in init's stdout/stderr (the silent-skip contract). Confirm RED.
- [ ] 6.2 Extend `core.init_project` to handle `.env` (touch on absent, skip silently on present — no message appended).
- [ ] 6.3 Run coverage.

## Commit 7 — `.gitignore` creation

After this commit, fresh projects get a one-line `.gitignore` containing `.env`; existing `.gitignore` files are preserved with an informational message. Spec scope: scenarios 5a + 5b — two branches of the same `if exists:` code path.

- [ ] 7.1 Write tests for:
      - 5a — `.gitignore` created with single `.env` line when absent
      - 5b — existing `.gitignore` preserved with "`.env` should be listed" message

      Confirm RED.
- [ ] 7.2 Extend `core.init_project` to handle `.gitignore` (create with one line on absent, skip with message on present).
- [ ] 7.3 Run coverage.

## Commit 8 — CLI smoke test

After this commit, packaging and entry-point integrity for `feather init` is verifiable end-to-end via a real subprocess invocation — catching regressions that in-process `CliRunner` cannot detect (e.g., the verb registered but the script entry point broken). Cross-verb infrastructure that init is the first to land. Spec scope: none new. The exit-code contract from proposal.md ("init exits 0 on every successful completion; non-zero only on I/O failure") is verified implicitly by every `CliRunner.invoke(...).exit_code == 0` assertion in commits 1-7.

- [ ] 8.1 Add a `test_cli_surface.py` (or extend if it exists) that subprocess-runs `feather init --help` and asserts exit 0.
- [ ] 8.2 Final coverage check across `feather_etl.commands.init`: 100% line + branch.
