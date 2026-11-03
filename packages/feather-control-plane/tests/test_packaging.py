"""Guards on how `control_plane` is IMPORTED, not on what it does.

Both failures below were live in this library's previous home and neither produced a red build:
one was hidden behind nine hand-vendored copies, the other behind a hand-copied `business_day.py`.
A packaging defect that no test can see is a defect that ships.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

import control_plane


def test_control_plane_is_a_real_package_not_a_namespace_shadow():
    """`control_plane` must resolve to an installed package, never to a same-named directory.

    This library is src-layout: the importable package is `src/control_plane`, and the project
    folder around it used to ALSO be called `control_plane`. Put that project folder on sys.path
    — which is what `PYTHONPATH=<repo root>` does when there is no install — and Python binds it
    as a PEP 420 namespace package: `import control_plane` SUCCEEDS, `__file__` is None, and
    every `from control_plane import X` fails with `cannot import name 'X' ... (unknown
    location)`. That is precisely how this suite was red in CI for months while the production
    code, which ran from vendored copies, was fine.

    The move to `packages/feather-control-plane/` makes the collision impossible by naming, and
    this asserts the property rather than trusting the name to stay that way.

    An installed regular package beats a namespace portion no matter where each sits on sys.path,
    so this only fires when the package is NOT installed and the suite is running on a bare
    PYTHONPATH — which is the exact condition that produced the CI failure, and the exact
    condition a fresh consumer's first `pytest` lands in.
    """
    assert control_plane.__file__ is not None, (
        "control_plane resolved to a namespace package at "
        f"{list(control_plane.__path__)} — the real package is not installed and a same-named "
        "directory on sys.path is shadowing it"
    )
    assert pathlib.Path(control_plane.__file__).name == "__init__.py"
    assert len(list(control_plane.__path__)) == 1


def test_the_documented_public_api_is_importable():
    """Every name in `__all__` resolves. The lazy `__getattr__` must not shrink the surface.

    `from control_plane import ControlPlane` is the exact import that failed in CI, so it is the
    exact import worth asserting.
    """
    from control_plane import ControlPlane, ControlPlaneError, RunContext, status

    assert control_plane.__all__ == ["ControlPlane", "ControlPlaneError", "RunContext", "status"]
    assert ControlPlane.__name__ == "ControlPlane"
    assert issubclass(ControlPlaneError, RuntimeError)
    assert RunContext.__name__ == "RunContext"
    assert status.EV_OK == "ok"


def test_unknown_attributes_still_raise_attribute_error():
    """A module-level `__getattr__` that answers everything breaks `hasattr` and `from … import`."""
    try:
        control_plane.not_a_real_name
    except AttributeError as exc:
        assert "not_a_real_name" in str(exc)
    else:
        raise AssertionError("expected AttributeError")


def test_the_stdlib_half_imports_without_loading_psycopg():
    """`control_plane.business_day` must not drag a database driver in behind it.

    psycopg IS installed in this test environment, so "does it crash?" proves nothing — the
    question is whether the module gets LOADED. A fresh interpreter answers it: if psycopg is
    absent from `sys.modules` after the import, then an environment without psycopg installed
    would have been fine too.

    Not a style point. `__init__.py` eagerly importing `.run` is why consumers that only wanted
    `ist_today()` copied `business_day.py` into their own trees by hand rather than depend on
    this package.
    """
    probe = (
        "import sys; "
        "from control_plane.business_day import ist_today; "
        "from control_plane import status; "
        "ist_today(); "
        "print('psycopg' in sys.modules)"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "False", (
        "importing the stdlib half loaded psycopg — something in the import chain is eager again"
    )
