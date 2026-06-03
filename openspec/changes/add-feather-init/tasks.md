# add-feather-init — tasks

## 1. feather.yaml in CWD

- [x] 1.1 Write test for init.1a — Init with no arg stamps files into CWD, if empty.
- [x] 1.2 Write test for init.2a — Stamped feather.yaml content matches template.
- [x] 1.3 Implement verb skeleton + `_stamp_feather_yaml` so 1.1 + 1.2 pass.

## 2. feather.yaml idempotency

- [x] 2.1 Write test for init.2b — feather.yaml present is preserved.
- [x] 2.2 Add skip-if-exists branch to `_stamp_feather_yaml` + loud-skip message so 2.1 passes.

## 3. Target-dir resolution

- [x] 3.1 Write test for init.1b — Init with new dir name creates the dir and stamps files inside.
- [x] 3.2 Write test for init.1c — Init reuses an existing sub-directory, ignoring sibling files.
- [x] 3.3 Add `dir` positional arg to cli + target-dir resolution in `init_project` so 3.1 + 3.2 pass.

## 4. pyproject.toml — default mode

- [ ] 4.1 Write test for init.3a — Default mode writes pyproject.toml with PyPI version pin.
- [ ] 4.2 Write test for init.3d — pyproject.toml present is preserved.
- [ ] 4.3 Write test for init.6a — Default mode confirms PyPI as the source.
- [ ] 4.4 Implement `__version__` literal + `_validate_target_name` + `_stamp_pyproject` (default arm) + source-wiring confirmation (PyPI arm) so 4.1 + 4.2 + 4.3 pass.

## 5. pyproject.toml — --dev mode

- [ ] 5.1 Write test for init.3b — --dev with local checkout writes pyproject.toml with editable source block.
- [ ] 5.2 Write test for init.3c — --dev without local checkout exits non-zero.
- [ ] 5.3 Write test for init.6b — --dev mode confirms the editable path.
- [ ] 5.4 Implement `feather_etl_source_path()` helper + `DevModeUnavailableError` + `--dev` arm of `_stamp_pyproject` + cli error path so 5.1 + 5.2 + 5.3 pass.

## 6. .env touching

- [ ] 6.1 Write test for init.4a — Create empty .env when absent.
- [ ] 6.2 Write test for init.4b — Preserve existing .env silently.
- [ ] 6.3 Add `_stamp_env` helper in `commands/init/core.py` (touch on absent, return silently on present) so 6.1 + 6.2 pass.

## 7. .gitignore creation

- [ ] 7.1 Write test for init.5a — Create .gitignore when absent.
- [ ] 7.2 Write test for init.5b — Existing .gitignore is preserved.
- [ ] 7.3 Add `_stamp_gitignore` helper in `commands/init/core.py` so 7.1 + 7.2 pass.

## 8. CLI smoke test

- [ ] 8.1 Bootstrap `tests/invariants/` (PEP 420 namespace, no `__init__.py`) + add `tests/invariants/test_cli_surface.py` subprocess-running `feather init --help` with assert exit 0.
- [ ] 8.2 Final coverage gate — 100% line + branch across `feather_etl.commands.init`.
