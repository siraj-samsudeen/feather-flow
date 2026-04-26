## 1. Slice: Basic init

- [ ] 1.1 Create `src/feather_etl/resources/templates/` with five empty files (`feather.yaml`, `pyproject.toml`, `.env.example`, `.gitignore`, `feather.example.yaml`); add `resources_test.py` asserting all five are reachable via `importlib.resources`
- [ ] 1.2 Fill `feather.yaml` template; test: content equals exactly `sources: []`
- [ ] 1.3 Fill `.gitignore` template; test: all six entries present (`*.duckdb`, `*.duckdb.wal`, `feather_validation.json`, `.env`, `__pycache__/`, `.venv/`)
- [ ] 1.4 Fill `.env.example` template; test: all eight var names present (`SQLSERVER_HOST/PORT/USER/PASSWORD`, `POSTGRES_HOST/PORT/USER/PASSWORD`)
- [ ] 1.5 Fill `pyproject.toml` template; test: `{name}` placeholder present, `feather-etl>=0.1.0` dependency present
- [ ] 1.6 Fill `feather.example.yaml` template; test: all eight source type keywords present; sqlserver block has `type`, `name`, `host`, `port`, `user`, `password`, `databases` fields
- [ ] 1.7 Create `commands/init/core.py` with `init_project(path, name) → InitResult`; test: creates directory if missing, writes all five files, `pyproject.toml` contains actual name (not `{name}` literal)
- [ ] 1.8 Create `commands/init/cli.py` and register in `cli.py`; test A: `feather init my-proj` creates directory and files; test B: `feather init .` inside a named CWD writes CWD basename as `name` in `pyproject.toml`

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
