"""Workflow stage 01: scaffold — feather init.

Scenarios here exercise `feather init` end-to-end: it creates the expected
files, the scaffolded config is syntactically valid YAML, and the
scaffolded `feather.yaml` parses through `load_config()` without
validation errors.
"""

from __future__ import annotations

from pathlib import Path


def test_scaffolds_project(cli, project):
    target = project.root / "test-project"
    result = cli("init", str(target), config=False)
    assert result.exit_code == 0
    assert (target / "feather.yaml").exists()
    assert (target / "pyproject.toml").exists()
    assert (target / ".gitignore").exists()
    assert (target / ".env.example").exists()


def test_init_nonempty_dir_fails(cli, project):
    target = project.root / "existing"
    target.mkdir()
    (target / "somefile.txt").write_text("exists")
    result = cli("init", str(target), config=False)
    assert result.exit_code != 0
    assert "already exists" in result.output


def test_init_allows_git_only_dir(cli, project):
    """UX-3: .git directory should not block feather init."""
    target = project.root / "git-project"
    target.mkdir()
    (target / ".git").mkdir()
    (target / ".gitignore").write_text("*.duckdb\n")
    result = cli("init", str(target), config=False)
    assert result.exit_code == 0
    assert (target / "feather.yaml").exists()


def test_init_dot_uses_cwd_name(cli, project, monkeypatch):
    """UX-2: feather init . should use directory name, not empty string."""
    target = project.root / "my-client"
    target.mkdir()
    monkeypatch.chdir(target)
    result = cli("init", ".", config=False)
    assert result.exit_code == 0
    toml = (target / "pyproject.toml").read_text()
    assert 'name = ""' not in toml
    assert "my-client" in toml


def test_init_prompts_for_project_name_when_omitted(project, monkeypatch):
    """When the user omits the positional project_name, feather init prompts
    for it via typer.prompt (which reads stdin)."""
    from typer.testing import CliRunner

    from feather_flow.cli import app

    monkeypatch.chdir(project.root)
    runner = CliRunner()
    result = runner.invoke(app, ["init"], input="prompted-proj\n")
    assert result.exit_code == 0
    assert (project.root / "prompted-proj" / "feather.yaml").exists()


def test_init_json_emits_project_and_files_created(cli, project):
    """`feather --json init <path>` writes an NDJSON line with the
    scaffolded project path + files_created count."""
    import json

    target = project.root / "json-project"
    result = cli("--json", "init", str(target), config=False)
    assert result.exit_code == 0
    # Everything on stdout is one JSON line (emit_line uses NDJSON).
    line = next(
        line for line in result.output.strip().splitlines() if line.startswith("{")
    )
    parsed = json.loads(line)
    assert parsed["project"].endswith("json-project")
    # files_created is a list of filenames created by scaffold_project
    assert isinstance(parsed["files_created"], list)
    assert "feather.yaml" in parsed["files_created"]


def test_scaffolded_yaml_uses_sources_list(tmp_path: Path):
    from feather_flow.init_wizard import scaffold_project

    scaffold_project(tmp_path / "proj")
    content = (tmp_path / "proj" / "feather.yaml").read_text()
    assert "sources:" in content
    assert "\nsource:" not in content  # must not emit singular form


def test_scaffolded_yaml_loads_clean(tmp_path: Path):
    from feather_flow.config import load_config
    from feather_flow.init_wizard import scaffold_project

    proj = tmp_path / "proj"
    scaffold_project(proj)
    # The default template references ./source.duckdb which doesn't exist;
    # validate=False skips source existence checks while still parsing.
    cfg = load_config(proj / "feather.yaml", validate=False)
    assert len(cfg.sources) == 1
