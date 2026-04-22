## ADDED Requirements

### Requirement: Skip existing data files
For data files (`feather.yaml`, `pyproject.toml`, `.env.example`, `.gitignore`), `feather init` SHALL skip any file that already exists. No warning, no error, no overwrite.

#### Scenario: Re-run skips existing feather.yaml
- **WHEN** `feather.yaml` already exists in the target directory
- **THEN** init does not modify it

#### Scenario: Re-run skips existing pyproject.toml
- **WHEN** `pyproject.toml` already exists
- **THEN** init does not modify it

#### Scenario: Init in existing project
- **WHEN** user runs `feather init .` in a directory that already has some of the data files
- **THEN** init creates only the missing data files and skips the rest

### Requirement: Overwrite package-owned files
`feather.example.yaml` SHALL always be overwritten from the package on every run, even if it already exists.

#### Scenario: Re-run overwrites feather.example.yaml
- **WHEN** `feather.example.yaml` already exists
- **THEN** init replaces it with the current package version

### Requirement: Report what happened
`feather init` SHALL report which files were created and which were skipped.

#### Scenario: Text output on first run
- **WHEN** init creates all files in a fresh directory
- **THEN** output lists all created files

#### Scenario: Text output on re-run
- **WHEN** some files already exist on re-run
- **THEN** output distinguishes created and skipped files
