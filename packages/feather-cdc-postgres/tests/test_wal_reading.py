"""`conftest.wal_runs` orders runs by when they finished, never by filename (#89).

A WAL-only `ControlPlane` writes one `<run_id>.jsonl` per run, and `new_run_id` is
`<UTC second>-<extractor>-<6 random hex>`. Two runs inside one test land in the same second
almost every time, so sorting the filenames sorts on the random suffix — a fair coin. Three
tests asked "what did the last run finish as?" that way and read the FIRST run's outcome half
the time: `assert 'ok' == 'failed'` on a test that had arranged a failure.

The fixtures below are the shape the coin flip actually produced on 08-Nov-2026 — filename
order exactly reversed against finish order — so this fails deterministically on a reader that
sorts names, with no looping.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from conftest import finish_record, wal_records, wal_runs

EXTRACTOR = "demo_cdc"


def write_run(wal_dir, run_id, *, status, finished_at, n_err=0):
    """One run's WAL file, in the shape `RunContext` writes it."""
    directory = pathlib.Path(wal_dir, EXTRACTOR)
    directory.mkdir(parents=True, exist_ok=True)
    lines = [
        {"kind": "run_start", "run_id": run_id, "extractor": EXTRACTOR, "mode": "cdc",
         "started_at": finished_at, "status": "running"},
        {"kind": "event", "run_id": run_id, "extractor": EXTRACTOR, "entity": "public.t",
         "unit": "cdc", "status": "ok" if n_err == 0 else "error"},
        {"kind": "run_finish", "run_id": run_id, "extractor": EXTRACTOR, "status": status,
         "stop_reason": "clean" if n_err == 0 else "error", "n_err": n_err,
         "started_at": finished_at, "finished_at": finished_at},
    ]
    (directory / f"{run_id}.jsonl").write_text(
        "".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")


@pytest.fixture
def two_runs_whose_names_sort_backwards(tmp_path):
    """Same UTC second; the second run's random suffix sorts BEFORE the first run's."""
    wal_dir = str(tmp_path / "wal")
    write_run(wal_dir, "20261108T142020Z-demo_cdc-c71d32",
              status="ok", finished_at="2026-11-08T14:20:20.311544+00:00")
    write_run(wal_dir, "20261108T142020Z-demo_cdc-03092d",
              status="failed", finished_at="2026-11-08T14:20:20.884120+00:00", n_err=1)
    return wal_dir


def test_the_last_run_is_the_one_that_finished_last_not_the_one_whose_name_sorts_last(
        two_runs_whose_names_sort_backwards):
    record = finish_record(two_runs_whose_names_sort_backwards, extractor=EXTRACTOR)
    assert (record["status"], record["n_err"]) == ("failed", 1)
    assert record["run_id"].endswith("03092d")


def test_runs_come_back_oldest_first_so_a_test_can_walk_them_in_order(
        two_runs_whose_names_sort_backwards):
    runs = wal_runs(two_runs_whose_names_sort_backwards, EXTRACTOR)
    outcomes = [[r["status"] for r in records if r["kind"] == "run_finish"][0]
                for _, records in runs]
    assert outcomes == ["ok", "failed"]
    assert [run_id[-6:] for run_id, _ in runs] == ["c71d32", "03092d"]


def test_every_record_of_a_run_stays_with_that_run_rather_than_interleaving(
        two_runs_whose_names_sort_backwards):
    """Events are keyed by run_id, so a reader that merged all files into one time-sorted list
    would still be right — but the one test that reads events wants them grouped."""
    records = wal_records(two_runs_whose_names_sort_backwards, EXTRACTOR)
    assert [r["kind"] for r in records] == [
        "run_start", "event", "run_finish", "run_start", "event", "run_finish"]
    assert records[1]["status"] == "ok" and records[4]["status"] == "error"


def test_two_runs_that_recorded_the_same_instant_raise_rather_than_guess(tmp_path):
    """Microsecond ties cannot happen between two real runs, but if the recorded times ever
    stop discriminating, say so — do not quietly go back to picking one at random."""
    wal_dir = str(tmp_path / "wal")
    same = "2026-11-08T14:20:20.311544+00:00"
    write_run(wal_dir, "20261108T142020Z-demo_cdc-aaaaaa", status="ok", finished_at=same)
    write_run(wal_dir, "20261108T142020Z-demo_cdc-bbbbbb", status="failed", finished_at=same,
              n_err=1)
    with pytest.raises(AssertionError, match="two runs share a timestamp"):
        finish_record(wal_dir, extractor=EXTRACTOR)


def test_a_wal_directory_with_no_finished_run_says_so(tmp_path):
    with pytest.raises(AssertionError, match="no run finished"):
        finish_record(str(tmp_path / "wal"), extractor=EXTRACTOR)
