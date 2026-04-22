"""Repo-wide invariants for file style.

Enforces two of the three in-file rules from `docs/ORIENTATION.md`:
  1. Every module in src/feather_etl/ has a one-paragraph docstring.
  2. (Review-only) Public-first stepdown order — not testable here.
  3. No module exceeds 200 lines (hard cap; aim for 150).

These run as part of the normal pytest suite; any session that violates
them sees failures on the first test run. Empty `__init__.py` files
(zero non-whitespace content) are exempt from rule 1 — they are
package markers.
"""

from __future__ import annotations

from pathlib import Path

HARD_CAP_LINES = 200

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "feather_etl"


def _python_files() -> list[Path]:
    return sorted(SRC_ROOT.rglob("*.py"))


def test_every_module_has_docstring() -> None:
    import ast

    offenders: list[str] = []
    for path in _python_files():
        src = path.read_text()
        if not src.strip():
            continue
        tree = ast.parse(src, filename=str(path))
        if ast.get_docstring(tree) is None:
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, "Missing module docstrings:\n  " + "\n  ".join(offenders)


def test_no_module_exceeds_hard_cap() -> None:
    offenders: list[str] = []
    for path in _python_files():
        line_count = sum(1 for _ in path.open())
        if line_count > HARD_CAP_LINES:
            offenders.append(f"{path.relative_to(REPO_ROOT)} ({line_count} lines)")
    assert not offenders, (
        f"Files exceed {HARD_CAP_LINES}-line hard cap (aim for 150):\n  "
        + "\n  ".join(offenders)
    )
