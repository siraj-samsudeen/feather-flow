"""The vendored `control_plane` copies (in each Railway service's src/) must be byte-identical to
the canonical `control_plane/src/control_plane`. If this fails, run `control_plane/vendor-sync.sh`.

This is the drift guard for the temporary vendoring (#204, Path 1) — copies exist only until the
library is published to the public feather-etl package, at which point the copies + this test go.
"""
from __future__ import annotations

import filecmp
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]
CANONICAL = REPO / "control_plane/src/control_plane"

# Services that vendor a copy (keep in sync with vendor-sync.sh TARGETS).
VENDORED = [
    REPO / "gofrugal_extract/src/control_plane",
    REPO / "zakya_extract/src/control_plane",
    REPO / "sap_bronze/src/control_plane",
]


def test_vendored_copies_match_canonical():
    assert CANONICAL.is_dir(), f"canonical package missing: {CANONICAL}"
    for v in VENDORED:
        assert v.is_dir(), f"missing vendored copy {v} — run control_plane/vendor-sync.sh"
        cmp = filecmp.dircmp(CANONICAL, v, ignore=["__pycache__"])
        drift = cmp.left_only + cmp.right_only + cmp.diff_files
        assert not drift, (
            f"vendored copy {v} drifted from canonical {CANONICAL}: {drift} "
            "— run control_plane/vendor-sync.sh"
        )
