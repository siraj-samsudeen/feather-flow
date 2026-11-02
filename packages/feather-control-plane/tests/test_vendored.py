"""The vendored `control_plane` copies (in each Railway service's src/) must be byte-identical to
the canonical `control_plane/src/control_plane`. If this fails, run `control_plane/vendor-sync.sh`.

This is the drift guard for the temporary vendoring (#204, Path 1) — copies exist only until the
library is published to the public feather-etl package, at which point the copies + this test go.

The copies are DISCOVERED, never listed by hand (#4245): a hand list here went stale when #4258
added `petpooja_extract` as an 8th copy, and that copy missed a vocabulary change. Every
`<service>/src/control_plane` in the repo must be a vendor-sync target and must match, so a 9th
service cannot drift silently either. Runs on the PR gate (python_tests.yaml), not only nightly.
"""
from __future__ import annotations

import filecmp
import pathlib
import re
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[2]
CANONICAL = REPO / "control_plane/src/control_plane"
VENDOR_SYNC = REPO / "control_plane/vendor-sync.sh"


def _discovered() -> set[pathlib.Path]:
    """Every vendored copy the repo CARRIES: `<top-level dir>/src/control_plane`, canonical excluded.

    Discovery is `git ls-files`, not a disk walk (#4576). A retired service leaves its vendored copy
    behind — `featherbase_extract/` was deleted in #3156 and its copy sat on disk for two months,
    failing this guard on every working machine while CI, which clones fresh, stayed green. A guard
    that is red where it cannot help and green where it cannot catch anything teaches people to
    ignore it.
    """
    try:
        out = subprocess.run(["git", "-C", str(REPO), "ls-files", "*/src/control_plane/*"],
                             capture_output=True, text=True, encoding="utf-8", check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return {p for p in REPO.glob("*/src/control_plane") if p.is_dir() and p != CANONICAL}
    # ls-files lists files; the copy is their `.../src/control_plane` parent directory.
    found = {REPO / line.split("/src/control_plane/")[0] / "src" / "control_plane"
             for line in out.splitlines() if "/src/control_plane/" in line}
    return {p for p in found if p != CANONICAL}


def _sync_targets() -> set[pathlib.Path]:
    """The `TARGETS=( ... )` entries of vendor-sync.sh, as `<target>/control_plane` paths."""
    text = VENDOR_SYNC.read_text(encoding="utf-8")
    block = re.search(r"^TARGETS=\((.*?)^\)", text, re.S | re.M)
    assert block, "vendor-sync.sh has no TARGETS=( ... ) block"
    entries = re.findall(r'^\s*"([^"]+)"', block.group(1), re.M)
    return {REPO / e / "control_plane" for e in entries}


def test_every_vendored_copy_is_a_vendor_sync_target():
    found, targets = _discovered(), _sync_targets()
    assert found, "no vendored copies discovered — has the layout changed?"
    assert found == targets, (
        f"not synced by vendor-sync.sh: {sorted(str(p.relative_to(REPO)) for p in found - targets)}; "
        f"listed but missing: {sorted(str(p.relative_to(REPO)) for p in targets - found)}"
    )


def test_vendored_copies_match_canonical():
    assert CANONICAL.is_dir(), f"canonical package missing: {CANONICAL}"
    for v in sorted(_discovered() | _sync_targets()):
        assert v.is_dir(), f"missing vendored copy {v} — run control_plane/vendor-sync.sh"
        cmp = filecmp.dircmp(CANONICAL, v, ignore=["__pycache__"])
        drift = cmp.left_only + cmp.right_only + cmp.diff_files
        assert not drift, (
            f"vendored copy {v} drifted from canonical {CANONICAL}: {drift} "
            "— run control_plane/vendor-sync.sh"
        )
