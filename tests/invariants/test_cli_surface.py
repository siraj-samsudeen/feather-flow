import subprocess
import sys


def test_cli_init_help() -> None:
    """Verify that 'python -m feather_etl.cli init --help' exits successfully."""
    result = subprocess.run(
        [sys.executable, "-m", "feather_etl.cli", "init", "--help"],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": "src"},
    )
    assert result.returncode == 0, f"Command failed with code {result.returncode}. Stderr: {result.stderr}"
    assert "Usage:" in result.stdout
