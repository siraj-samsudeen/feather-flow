"""Scenario tests for feather init."""

from typer.testing import CliRunner

from feather_etl.cli import app


# Requirement 1 - Scenario 1a: Init with no arg stamps files into CWD, if empty
def test_init_with_no_arg_stamps_files_into_cwd_if_empty(monkeypatch, tmp_path) -> None:
    """init.1a — Init with no arg stamps files into CWD, if empty."""
    # Requirement 1 scenario: no argument -> current directory.
    # Verifies that omitting the directory argument defaults target resolution to CWD.
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "feather.yaml").is_file()


# Requirement 2 - Scenario 2a: Stamped feather.yaml content matches template
def test_stamped_feather_yaml_content_matches_template(monkeypatch, tmp_path) -> None:
    """init.2a — Stamped feather.yaml content matches template."""
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0, result.output
    content = (tmp_path / "feather.yaml").read_text()
    assert "defaults:\n  sample_threshold: 100_000" in content
    assert "# sources:" in content


# Requirement 2 - Scenario 2b: feather.yaml present is preserved
def test_feather_yaml_present_is_preserved(monkeypatch, tmp_path) -> None:
    """init.2b — feather.yaml present is preserved."""
    monkeypatch.chdir(tmp_path)
    sentinel = "# operator-edited feather.yaml — must not be clobbered\n"
    (tmp_path / "feather.yaml").write_text(sentinel)
    runner = CliRunner()

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "feather.yaml").read_text() == sentinel
    assert (
        "feather.yaml: present (delete this file and re-run to reset)" in result.stderr
    )


# Requirement 1 - Scenario 1b: Init with new dir name creates the dir and stamps files inside
def test_init_with_new_dir_name_creates_the_dir_and_stamps_files_inside(
    monkeypatch, tmp_path
) -> None:
    """init.1b — Init with new dir name creates the dir and stamps files inside."""
    # Requirement 1 scenario: new directory -> create automatically.
    # Verifies that providing a new directory path resolves it as target and creates it automatically.
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    result = runner.invoke(app, ["init", "demo"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "demo").is_dir()
    assert (tmp_path / "demo" / "feather.yaml").is_file()


# Requirement 1 - Scenario 1c: Init reuses an existing sub-directory, ignoring sibling files
def test_init_reuses_an_existing_sub_directory_ignoring_sibling_files(
    monkeypatch, tmp_path
) -> None:
    """init.1c — Init reuses an existing sub-directory, ignoring sibling files."""
    # Requirement 1 scenario: existing directory -> reuse safely.
    # Verifies that providing an existing directory target reuses the directory without affecting other files.
    monkeypatch.chdir(tmp_path)

    subdir = tmp_path / "demo"
    subdir.mkdir()

    readme = subdir / "README.md"
    readme.write_text("keep me")

    runner = CliRunner()

    result = runner.invoke(app, ["init", "demo"])

    assert result.exit_code == 0, result.output
    assert (subdir / "feather.yaml").is_file()
    assert readme.read_text() == "keep me"


# Requirement 3 - Scenario 3a: Default mode writes pyproject.toml with PyPI version pin
def test_default_mode_writes_pyproject_toml_with_pypi_version_pin(
    monkeypatch, tmp_path
) -> None:
    """init.3a — Default mode writes pyproject.toml with PyPI version pin."""
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    result = runner.invoke(app, ["init", "demo"])

    assert result.exit_code == 0, result.output
    pyproject_path = tmp_path / "demo" / "pyproject.toml"
    assert pyproject_path.is_file()
    content = pyproject_path.read_text()
    assert '[project]\nname = "demo"' in content
    assert 'version = "0.1.0"' in content
    assert 'dependencies = ["feather-etl>=0.1.0"]' in content
    assert "tool.uv.sources" not in content


# Requirement 3 - Scenario 3d: pyproject.toml present is preserved
def test_pyproject_toml_present_is_preserved(monkeypatch, tmp_path) -> None:
    """init.3d — pyproject.toml present is preserved."""
    monkeypatch.chdir(tmp_path)
    # pre-create target directory demo
    demo_dir = tmp_path / "demo"
    demo_dir.mkdir()
    sentinel = "# operator-edited pyproject.toml\n"
    pyproject_path = demo_dir / "pyproject.toml"
    pyproject_path.write_text(sentinel)

    runner = CliRunner()
    result = runner.invoke(app, ["init", "demo"])

    assert result.exit_code == 0, result.output
    assert pyproject_path.read_text() == sentinel
    assert (
        "pyproject.toml: present (delete this file and re-run to reset)"
        in result.stderr
    )


# Requirement 6 - Scenario 6a: Default mode confirms PyPI as the source
def test_default_mode_confirms_pypi_as_the_source(monkeypatch, tmp_path) -> None:
    """init.6a — Default mode confirms PyPI as the source."""
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    result = runner.invoke(app, ["init", "demo"])

    assert result.exit_code == 0, result.output
    assert "feather-etl: pinned to PyPI (>=0.1.0)" in result.stderr


# Requirement 1 - Scenario Name Validation: Validate target project name naming rules
def test_init_invalid_project_name_raises_value_error(tmp_path) -> None:
    """Validate project name against alphanumeric, hyphen, and underscore characters."""
    invalid_dir = tmp_path / "invalid name @ 123"
    from feather_etl.commands.init.core import init_project
    import pytest

    with pytest.raises(ValueError, match="Invalid project name"):
        init_project(invalid_dir)


# Requirement 1 - Scenario CLI Failure: Invalid project name exits CLI with error
def test_init_with_invalid_directory_name_fails(monkeypatch, tmp_path) -> None:
    """Invalid directory name raises error and exits non-zero."""
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["init", "invalid name"])
    assert result.exit_code != 0


# Requirement 5 - Scenario 5a: Create .gitignore when absent
def test_gitignore_created_when_absent(monkeypatch, tmp_path) -> None:
    """init.5a — Create .gitignore when absent."""
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / ".gitignore").read_text() == ".env\n"


# Requirement 5 - Scenario 5b: Existing .gitignore is preserved
def test_gitignore_present_is_preserved(monkeypatch, tmp_path) -> None:
    """init.5b — Existing .gitignore is preserved."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".gitignore").write_text("node_modules/\n")
    runner = CliRunner()

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / ".gitignore").read_text() == "node_modules/\n"
    assert ".gitignore: present (ensure .env is listed)" in result.stderr


# Requirement 3 - Scenario 3b: --dev with local checkout writes pyproject.toml with editable block
def test_dev_mode_writes_pyproject_toml_with_editable_block(
    monkeypatch, tmp_path
) -> None:
    """init.3b — --dev with local checkout writes pyproject.toml with editable block."""
    monkeypatch.chdir(tmp_path)
    fake_checkout = tmp_path / "fake_checkout"
    fake_checkout.mkdir()
    (fake_checkout / "pyproject.toml").write_text("[project]\n")

    monkeypatch.setattr(
        "feather_etl.commands.init.core.feather_etl_source_path",
        lambda: fake_checkout,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["init", "demo", "--dev"])

    assert result.exit_code == 0, result.output
    pyproject_path = tmp_path / "demo" / "pyproject.toml"
    assert pyproject_path.is_file()
    content = pyproject_path.read_text()
    assert 'dependencies = ["feather-etl"]' in content
    assert "[tool.uv.sources]" in content
    assert f'path = "{fake_checkout}"' in content
    assert "editable = true" in content


# Requirement 3 - Scenario 3c: --dev without local checkout exits non-zero
def test_dev_mode_without_local_checkout_exits_non_zero(monkeypatch, tmp_path) -> None:
    """init.3c — --dev without local checkout exits non-zero."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "feather_etl.commands.init.core.feather_etl_source_path",
        lambda: None,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["init", "demo", "--dev"])

    assert result.exit_code != 0
    assert "--dev requires a local feather-etl checkout" in result.stderr
    assert "Either install feather-etl from a local source" in result.stderr


# Requirement 6 - Scenario 6b: --dev mode confirms the editable path
def test_dev_mode_confirms_editable_path(monkeypatch, tmp_path) -> None:
    """init.6b — --dev mode confirms the editable path."""
    monkeypatch.chdir(tmp_path)
    fake_checkout = tmp_path / "fake_checkout"
    fake_checkout.mkdir()
    (fake_checkout / "pyproject.toml").write_text("[project]\n")

    monkeypatch.setattr(
        "feather_etl.commands.init.core.feather_etl_source_path",
        lambda: fake_checkout,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["init", "demo", "--dev"])

    assert result.exit_code == 0, result.output
    assert f"feather-etl: editable from {fake_checkout}" in result.stderr


# Requirement 4 - Scenario 4a: Create empty .env when absent
def test_env_stamping_creates_empty_env_file(monkeypatch, tmp_path) -> None:
    """init.4a — Verify that running init creates an empty .env file."""
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    result = runner.invoke(app, ["init", "demo"])

    assert result.exit_code == 0, result.output
    env_path = tmp_path / "demo" / ".env"
    assert env_path.is_file()
    assert env_path.read_text() == ""


# Requirement 4 - Scenario 4b: Preserve existing .env silently
def test_env_stamping_preserves_existing_env_file(monkeypatch, tmp_path) -> None:
    """init.4b — Verify that if .env already exists, it is preserved and skipped."""
    monkeypatch.chdir(tmp_path)
    demo_dir = tmp_path / "demo"
    demo_dir.mkdir()
    env_path = demo_dir / ".env"
    env_path.write_text("API_KEY=secret_value\n")

    runner = CliRunner()
    result = runner.invoke(app, ["init", "demo"])

    assert result.exit_code == 0, result.output
    assert env_path.read_text() == "API_KEY=secret_value\n"
    assert ".env:" not in result.stderr


# Requirement 3 - Scenario Source Path Failure: Returns None if local pyproject is missing
def test_feather_etl_source_path_returns_none_if_no_pyproject(monkeypatch, tmp_path) -> None:
    """Test that feather_etl_source_path() returns None if pyproject.toml is not found."""
    fake_file = tmp_path / "a" / "b" / "c" / "d" / "e" / "core.py"
    fake_file.parent.mkdir(parents=True, exist_ok=True)
    fake_file.touch()

    import feather_etl.commands.init.core as core
    monkeypatch.setattr(core, "__file__", str(fake_file))

    assert core.feather_etl_source_path() is None


# Requirement 3 - Scenario Dev Mode Direct Failure: init_project raises when source path missing
def test_init_project_dev_mode_raises_when_source_path_missing(monkeypatch, tmp_path) -> None:
    """DevModeUnavailableError is raised by init_project when feather_etl_source_path returns None."""
    monkeypatch.setattr(
        "feather_etl.commands.init.core.feather_etl_source_path",
        lambda: None,
    )
    from feather_etl.commands.init.core import init_project, DevModeUnavailableError
    import pytest
    with pytest.raises(DevModeUnavailableError):
        init_project(tmp_path / "demo", dev=True)


# Requirement 3 - Scenario Source Path Success: Returns candidate path if pyproject exists
def test_feather_etl_source_path_returns_candidate_if_pyproject_exists(monkeypatch, tmp_path) -> None:
    """Test that feather_etl_source_path() returns the candidate path if pyproject.toml is found."""
    fake_file = tmp_path / "a" / "b" / "c" / "d" / "e" / "core.py"
    fake_file.parent.mkdir(parents=True, exist_ok=True)
    fake_file.touch()

    (tmp_path / "a" / "pyproject.toml").touch()

    import feather_etl.commands.init.core as core
    monkeypatch.setattr(core, "__file__", str(fake_file))

    assert core.feather_etl_source_path() == tmp_path / "a"
