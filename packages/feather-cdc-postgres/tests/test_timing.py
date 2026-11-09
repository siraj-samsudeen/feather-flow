"""Stage timing is telemetry: it reports, and it never fails a run (#4708)."""
import duckdb

from feather_cdc_postgres.destination import Destination, DestinationSpec
from feather_cdc_postgres.timing import CONNECTIONS, ENTITY, UNIT, StageClock


class Run:
    def __init__(self):
        self.events = []

    def event(self, **kw):
        self.events.append(kw)


def test_a_stage_is_written_as_one_ok_event():
    run = Run()
    clock = StageClock(run)
    with clock.stage("probe_slot"):
        pass
    event = run.events[0]
    assert (event["entity"], event["unit"], event["status"]) == (ENTITY, UNIT, "ok")
    assert event["extra"]["stage"] == "probe_slot"
    assert event["extra"]["seconds"] >= 0


def test_stages_before_the_run_row_exists_are_written_once_it_does():
    clock = StageClock()
    with clock.stage("check_owner"):
        pass
    run = Run()
    clock.bind(run)
    assert [e["extra"]["stage"] for e in run.events] == ["check_owner"]


def test_a_failing_event_write_does_not_fail_the_stage():
    class Broken(Run):
        def event(self, **kw):
            raise RuntimeError("log database is down")

    with StageClock(Broken()).stage("drain"):
        pass


def test_a_stage_that_raises_is_still_recorded_and_the_error_propagates():
    run = Run()
    try:
        with StageClock(run).stage("drain"):
            raise ValueError("boom")
    except ValueError:
        pass
    assert run.events[0]["extra"]["stage"] == "drain"


def test_time_noted_inside_a_stage_is_added_up():
    run = Run()
    clock = StageClock(run)
    with clock.stage("drain"):
        clock.note("read_wal_s", 1.0)
        clock.note("read_wal_s", 2.0)
    assert run.events[0]["extra"]["read_wal_s"] == 3.0


def test_connections_are_counted_and_with_yields_a_real_connection(tmp_path):
    dest = Destination(DestinationSpec(kind="duckdb", database="d", path=str(tmp_path / "d.duckdb")))
    run = Run()
    clock = StageClock(run)
    with clock.stage("read"):
        with dest.connect() as con:
            assert isinstance(con, duckdb.DuckDBPyConnection)
            assert con.execute("select 1").fetchone() == (1,)
    extra = run.events[0]["extra"]
    assert extra["md_connections"] == 1
    assert extra["md_held_s"] >= extra["md_opening_s"] >= 0


def test_closing_twice_counts_the_hold_once(tmp_path):
    dest = Destination(DestinationSpec(kind="duckdb", database="d", path=str(tmp_path / "d.duckdb")))
    con = dest.connect()
    con.close()
    held = CONNECTIONS.held_s
    con.close()
    assert CONNECTIONS.held_s == held
