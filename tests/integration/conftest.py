"""Integration-layer fixtures.

Re-exports the shared ``project`` / ``ProjectFixture`` harness from
``tests/e2e/conftest.py`` so integration tests can use the same
directory-scaffolding primitives (``copy_fixture``, ``write_config``,
``write_curation``, ``query``) that e2e tests use.

Integration tests must NOT use the ``cli`` fixture (Rule I1): they call
``feather_flow.*`` Python APIs directly. Local per-file fixtures belong
inline in the test file, not here (Rule I7).
"""

from __future__ import annotations

from tests.e2e.conftest import ProjectFixture, project

__all__ = ["ProjectFixture", "project"]
