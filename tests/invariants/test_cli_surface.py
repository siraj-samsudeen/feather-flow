import pathlib
import subprocess
import sys


def test_cli_init_help() -> None:
    """Verify that 'feather init --help' exits successfully."""
    feather_bin = pathlib.Path(sys.executable).parent / "feather"
    result = subprocess.run(
        [str(feather_bin), "init", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Command failed with code {result.returncode}. Stderr: {result.stderr}"
    assert "Usage:" in result.stdout
