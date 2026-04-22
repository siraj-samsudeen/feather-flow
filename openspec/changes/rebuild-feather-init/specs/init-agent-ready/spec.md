## ADDED Requirements

### Requirement: Install skills from package resources
`feather init` SHALL copy skill definitions from `src/feather_etl/resources/skills/` into the client project's `.claude/skills/` directory. The `add-source` skill SHALL be installed by default.

#### Scenario: First run installs add-source skill
- **WHEN** init runs in a directory without `.claude/skills/`
- **THEN** `.claude/skills/add-source/SKILL.md` is created

#### Scenario: Skill SKILL.md contains source-adding instructions
- **WHEN** an agent reads `.claude/skills/add-source/SKILL.md`
- **THEN** the skill instructs the agent to read `feather.example.yaml`, find the matching source type, and write the entry into `feather.yaml`

### Requirement: Always overwrite installed skills
On re-run, `feather init` SHALL overwrite all files under `.claude/skills/` with the current package versions.

#### Scenario: Re-run overwrites skill with updated version
- **WHEN** init re-runs and `.claude/skills/add-source/SKILL.md` exists
- **THEN** the file is overwritten with the current package version

### Requirement: Stamp CLAUDE.md pointing to skills
`feather init` SHALL create a `CLAUDE.md` that points agents to the installed skills. `CLAUDE.md` is a data file — skip if exists on re-run.

#### Scenario: CLAUDE.md points to add-source skill
- **WHEN** init creates `CLAUDE.md`
- **THEN** it contains instructions directing agents to `.claude/skills/add-source/` for adding data sources

#### Scenario: Re-run skips existing CLAUDE.md
- **WHEN** `CLAUDE.md` already exists
- **THEN** init does not modify it
