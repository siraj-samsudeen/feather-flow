## Why

Setting up a feather-etl project requires config files, gitignore entries, and environment variable templates. `feather init` automates this — from zero to a project ready for `feather validate` in one command. It also installs agent skills so that AI assistants can configure sources without human intervention.

## What

`feather init` scaffolds a feather-etl client project in a target directory. It works in empty directories and existing projects alike.

| File | Purpose | Re-run behavior |
|------|---------|-----------------|
| `feather.yaml` | Config — empty sources list | Skip if exists |
| `feather.example.yaml` | All source-type schemas | Overwrite from package |
| `pyproject.toml` | Package dep + commented local pointer | Skip if exists |
| `.env.example` | DB env vars (sqlserver + postgres) | Skip if exists |
| `.gitignore` | Feather entries | Skip if exists |
| `CLAUDE.md` | Agent instructions pointing to skills | Skip if exists |
| `.claude/skills/add-source/SKILL.md` | Agent skill for adding sources | Overwrite from package |

## Vertical Slices

### Slice 1: Basic init

Stamps all files in a fresh directory. User can run `feather init my-proj` and get a working skeleton.

Capability: `init-basic`

### Slice 2: Idempotent init

Safe to re-run — skip existing data files, overwrite skills and example yaml. User can add feather to an existing project.

Capability: `init-idempotent`

### Slice 3: Agent-ready init

Skills installed, CLAUDE.md points to them. Agents can add sources without human help.

Capability: `init-agent-ready`

## Impact

- New package directory: `src/feather_etl/resources/skills/` containing skill definitions shipped with the package
- New package directory: `src/feather_etl/resources/templates/` containing stamped templates
- New command package: `src/feather_etl/commands/init/` with `cli.py` and `core.py`
- `pyproject.toml` build config must include `resources/` as package data
- CLI entrypoint `cli.py` gains the `init` command registration
