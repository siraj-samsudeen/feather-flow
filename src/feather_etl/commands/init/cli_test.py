"""Scenario tests for feather init."""

from typer.testing import CliRunner

from feather_etl.cli import app


def test_init_with_no_arg_stamps_files_into_cwd_if_empty(monkeypatch, tmp_path) -> None:
    """init.1a — Init with no arg stamps files into CWD, if empty."""
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "feather.yaml").is_file()


def test_stamped_feather_yaml_content_matches_template(monkeypatch, tmp_path) -> None:
    """init.2a — Stamped feather.yaml content matches template."""
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0, result.output
    content = (tmp_path / "feather.yaml").read_text()
    assert "defaults:\n  sample_threshold: 100_000" in content
    assert "# sources:" in content


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
