## 1. Slice: Basic init

- [ ] 1.1 Create `src/feather_etl/resources/templates/` with static templates: `feather.yaml`, `pyproject.toml`, `.env.example`, `.gitignore`, `feather.example.yaml`
- [ ] 1.2 Ensure `pyproject.toml` build config includes `resources/` as package data
- [ ] 1.3 Create `src/feather_etl/commands/__init__.py` and `src/feather_etl/commands/init/__init__.py`
- [ ] 1.4 Create `src/feather_etl/commands/init/core.py` with `init_project(path, name) → InitResult` that stamps all five files
- [ ] 1.5 Create `src/feather_etl/commands/init/cli.py` with Typer wrapper and register in `src/feather_etl/cli.py`
- [ ] 1.6 Add `core_test.py` — first run creates all files, feather.yaml has `sources: []`, pyproject.toml has correct name, feather.example.yaml has all source types, .env.example has sqlserver+postgres, .gitignore has all entries
- [ ] 1.7 Add `cli_test.py` — `feather init my-proj` creates directory and files
- [ ] 1.8 Verify 100% branch coverage on `commands/init/` modules

## 2. Slice: Idempotent init

- [ ] 2.1 Add skip-if-exists logic to `core.py` for data files (feather.yaml, pyproject.toml, .env.example, .gitignore)
- [ ] 2.2 Add overwrite-from-package logic for feather.example.yaml
- [ ] 2.3 Update `InitResult` to distinguish created vs skipped
- [ ] 2.4 Add `core_test.py` tests: re-run skips existing data files, overwrites example yaml, init in existing project creates only missing files
- [ ] 2.5 Add `cli_test.py` test: re-run output shows created and skipped files

## 3. Slice: Agent-ready init

- [ ] 3.1 Create `src/feather_etl/resources/skills/add-source/SKILL.md` with agent instructions for adding sources
- [ ] 3.2 Add `CLAUDE.md` template to `resources/templates/` pointing to `.claude/skills/add-source/`
- [ ] 3.3 Implement `install_skills(target_dir)` in `core.py` — copy from package resources, always overwrite
- [ ] 3.4 Update stamping logic: CLAUDE.md is skip-if-exists, `.claude/skills/**` is always overwrite
- [ ] 3.5 Add tests: skills installed on first run, overwritten on re-run, CLAUDE.md skipped on re-run, SKILL.md content is valid
- [ ] 3.6 Add `test_cli_surface.py` entry for `feather init`
- [ ] 3.7 Verify `test_file_style.py` invariants pass on all new modules
