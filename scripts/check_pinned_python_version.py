"""Fail when `.python-version` is an interpreter the project does not claim.

The repo pins one interpreter for every contributor and for CI in
`.python-version`; `pyproject.toml` separately declares what the package
supports. Nothing connected the two, so on 03-Nov-2026 `.python-version` sat at
`3.14`, `mysql-connector-python` could not import (3.14 removed
`ssl.PROTOCOL_TLSv1`), and pytest aborted during *collection* — zero tests ran,
with no failure count to notice (issue #83).

Declared support is read from BOTH halves of `[project]`, because either alone
lets the 3.14 pin through:

  • `requires-python = ">=3.10"` — open-ended upward, so 3.14 satisfies it.
  • `Programming Language :: Python :: X.Y` classifiers — stop at 3.13, which
    is where the real upper bound lives.

That matches how #83 states it: "requires-python is >=3.10 with classifiers
through 3.13 — 3.14 was outside its declared support."

Stdlib-only on purpose: a check that runs before the dependencies are
trustworthy must not need any.

CONTENTS
  == Public Interface ==
    • main — CLI entry point; prints a verdict, returns a process exit code
    • check_pinned_version — the whole check as data, for tests
    • read_pinned_version — `.python-version` → "3.13"
    • read_declared_support — `pyproject.toml` → DeclaredSupport
    • is_version_allowed — does a version satisfy a specifier set?

  == Data Types ==
    • DeclaredSupport — requires-python specifier + classified X.Y versions
    • CheckResult — ok / message pair

  == Private Helpers ==
    • _parse_specifier_set — ">=3.10,<3.14" → [(">=", (3, 10), False), ...]
    • _parse_version — "3.13.1" → (3, 13, 1)
    • _satisfies_one — one version against one specifier
    • _compare — zero-padded tuple comparison
    • _project_table_without_tomllib — 3.10 fallback reader

CALL ORDER
  1. read_pinned_version     (.python-version)
  2. read_declared_support   (pyproject.toml: requires-python + classifiers)
  3. check_pinned_version    (the verdict: both halves must agree)

SEE ALSO
  .github/workflows/ci.yaml — the `interpreter-guard` job that runs this
  tests/unit/test_check_pinned_python_version.py — its unit tests
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# PEP 440 operators, longest first so ">=" never matches as ">".
_OPERATORS = (">=", "<=", "==", "!=", "~=", ">", "<")

_CLASSIFIER_PATTERN = re.compile(
    r"^Programming Language :: Python :: (?P<version>\d+\.\d+)$"
)


# == Public Interface ==


def main(repo_root: Path | None = None) -> int:
    """Print the verdict and return a process exit code (0 ok, 1 violated)."""
    result = check_pinned_version(repo_root or REPO_ROOT)
    print(result.message, file=sys.stdout if result.ok else sys.stderr)
    return 0 if result.ok else 1


def check_pinned_version(repo_root: Path) -> CheckResult:
    """Check `.python-version` against what `pyproject.toml` claims to support."""
    pinned = read_pinned_version(repo_root / ".python-version")
    support = read_declared_support(repo_root / "pyproject.toml")

    violations = _violations(pinned, support)
    if violations:
        return CheckResult(
            ok=False,
            message="\n".join(
                [f".python-version is {pinned}, which this project does not claim:"]
                + [f"  - {violation}" for violation in violations]
                + [
                    "Pin an interpreter inside the declared support, or widen the",
                    "declaration deliberately (requires-python AND the classifiers).",
                    "See issue #83: a 3.14 pin broke mysql-connector-python's import",
                    "and pytest could not even collect.",
                ]
            ),
        )

    return CheckResult(
        ok=True,
        message=(
            f".python-version {pinned} is within the declared support "
            f"(requires-python {support.requires_python!r}, classifiers "
            f"{_format_versions(support.classified_versions) or 'none'})."
        ),
    )


def read_pinned_version(path: Path) -> str:
    """Return the first interpreter named in a `.python-version` file.

    Tolerates what pyenv and uv both allow: comments, blank lines, several
    versions (the first wins), an implementation prefix (`cpython-3.13.1`),
    and the free-threaded suffix (`3.13t`).
    """
    for raw_line in path.read_text().splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if "-" in line:
            line = line.split("-", 1)[1]
        return line.rstrip("t")
    raise ValueError(f"{path} names no Python version")


def read_declared_support(path: Path) -> DeclaredSupport:
    """Read `requires-python` and the Python classifiers from `[project]`."""
    project = _read_project_table(path)
    classified = sorted(
        {
            _parse_version(match.group("version"))
            for classifier in project.get("classifiers", [])
            if (match := _CLASSIFIER_PATTERN.match(str(classifier)))
        }
    )
    return DeclaredSupport(
        requires_python=project.get("requires-python"),
        classified_versions=classified,
    )


def is_version_allowed(version: str, requires_python: str) -> bool:
    """Does `version` satisfy every clause of a PEP 440 specifier set?

    Parsed as a specifier set rather than string-matched, because a substring
    test for ">=3.10" accepts 3.14 — one half of the bug this module guards.
    """
    candidate = _parse_version(version)
    return all(
        _satisfies_one(candidate, operator, bound, wildcard)
        for operator, bound, wildcard in _parse_specifier_set(requires_python)
    )


# == Data Types ==


@dataclass(frozen=True)
class DeclaredSupport:
    """What `pyproject.toml` says the package runs on."""

    requires_python: str | None
    classified_versions: list[tuple[int, ...]]


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    message: str


# == Private Helpers ==


def _violations(pinned: str, support: DeclaredSupport) -> list[str]:
    """Name every half of the declaration the pinned version contradicts.

    A missing half is not a violation — a project with no classifiers is still
    checked against requires-python, and vice versa.
    """
    problems = []

    if support.requires_python and not is_version_allowed(
        pinned, support.requires_python
    ):
        problems.append(
            f"outside requires-python {support.requires_python!r} in pyproject.toml"
        )

    series = _parse_version(pinned)[:2]
    if support.classified_versions and series not in support.classified_versions:
        problems.append(
            f"no 'Programming Language :: Python :: "
            f"{series[0]}.{series[1]}' classifier "
            f"(declared: {_format_versions(support.classified_versions)})"
        )

    return problems


def _read_project_table(path: Path) -> dict:
    try:
        import tomllib
    except ModuleNotFoundError:
        # tomllib is stdlib only from 3.11, and this project supports 3.10.
        return _project_table_without_tomllib(path)

    return tomllib.loads(path.read_text()).get("project", {})


def _format_versions(versions: list[tuple[int, ...]]) -> str:
    return ", ".join(".".join(str(part) for part in version) for version in versions)


def _parse_specifier_set(text: str) -> list[tuple[str, tuple[int, ...], bool]]:
    """Split ">=3.10, <3.14" into (operator, version, is_wildcard) clauses."""
    clauses = []
    for part in text.split(","):
        clause = part.strip()
        if not clause:
            continue
        for operator in _OPERATORS:
            if clause.startswith(operator):
                literal = clause[len(operator) :].strip()
                wildcard = literal.endswith(".*")
                if wildcard:
                    literal = literal[: -len(".*")]
                clauses.append((operator, _parse_version(literal), wildcard))
                break
        else:
            raise ValueError(f"Unrecognised version specifier: {clause!r}")
    return clauses


def _parse_version(text: str) -> tuple[int, ...]:
    """Turn "3.13.1" into (3, 13, 1), dropping any pre/post/dev suffix."""
    release = re.match(r"\s*v?(\d+(?:\.\d+)*)", text)
    if not release:
        raise ValueError(f"Unrecognised version: {text!r}")
    return tuple(int(part) for part in release.group(1).split("."))


def _satisfies_one(
    candidate: tuple[int, ...],
    operator: str,
    bound: tuple[int, ...],
    wildcard: bool,
) -> bool:
    if wildcard:
        matches_prefix = _compare(candidate[: len(bound)], bound) == 0
        if operator == "==":
            return matches_prefix
        if operator == "!=":
            return not matches_prefix
        raise ValueError(f"Operator {operator!r} does not accept a .* wildcard")

    if operator == "~=":
        if len(bound) < 2:
            raise ValueError("~= needs at least two version components")
        floor_ok = _compare(candidate, bound) >= 0
        series = len(bound) - 1
        same_series = _compare(candidate[:series], bound[:series]) == 0
        return floor_ok and same_series

    order = _compare(candidate, bound)
    return {
        ">=": order >= 0,
        "<=": order <= 0,
        ">": order > 0,
        "<": order < 0,
        "==": order == 0,
        "!=": order != 0,
    }[operator]


def _compare(left: tuple[int, ...], right: tuple[int, ...]) -> int:
    """Compare release tuples, zero-padding so (3, 13) == (3, 13, 0)."""
    width = max(len(left), len(right))
    padded_left = left + (0,) * (width - len(left))
    padded_right = right + (0,) * (width - len(right))
    return (padded_left > padded_right) - (padded_left < padded_right)


def _project_table_without_tomllib(path: Path) -> dict:
    """Scan `[project]` for requires-python and classifiers, for Python 3.10.

    Covers only the two keys this module reads, in the shapes this repo's
    pyproject uses: a quoted scalar and a multi-line array of quoted strings.
    """
    project: dict = {}
    in_project_table = False
    in_classifiers = False

    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()

        if in_classifiers:
            project["classifiers"].extend(re.findall(r"""["']([^"']*)["']""", line))
            if "]" in line:
                in_classifiers = False
            continue

        if line.startswith("["):
            in_project_table = line == "[project]"
            continue
        if not in_project_table:
            continue

        requires = re.match(r"""requires-python\s*=\s*["']([^"']*)["']""", line)
        if requires:
            project["requires-python"] = requires.group(1)
        elif re.match(r"classifiers\s*=\s*\[", line):
            project["classifiers"] = re.findall(
                r"""["']([^"']*)["']""", line.split("[", 1)[1]
            )
            in_classifiers = "]" not in line.split("[", 1)[1]

    return project


if __name__ == "__main__":
    raise SystemExit(main())
