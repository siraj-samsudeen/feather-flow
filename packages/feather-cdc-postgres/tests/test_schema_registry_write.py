"""The schema registry is replaced in a few multi-row statements (#4708): one statement per
column cost MotherDuck a round trip each, 575 s of a 670 s Featherbase run."""
import duckdb
import pytest

from feather_cdc_postgres import destination as dest_mod
from feather_cdc_postgres.destination import Destination, DestinationSpec


def _row(table, column, registration="r", **over):
    return {"registration": registration, "schema": "s", "table": table, "column": column,
            "type_oid": 23, "typmod": -1, "not_null": True, "key_role": None,
            "dest_type": "bigint", "mapping": "direct", **over}


@pytest.fixture
def dest(tmp_path):
    d = Destination(DestinationSpec(kind="duckdb", database="d", path=str(tmp_path / "d.duckdb")))
    d.ensure_runtime_tables()
    return d


def _rows(dest):
    with dest.connect() as con:
        return con.execute('select registration, "table", "column" from _cdc_runtime.schema_registry '
                           "order by 1, 2, 3").fetchall()


def test_a_second_write_replaces_the_named_tables_and_leaves_the_rest(dest):
    dest.write_registry_rows([_row("a", "x"), _row("a", "old"), _row("b", "y")])
    dest.write_registry_rows([_row("a", "x"), _row("a", "new")])
    assert _rows(dest) == [("r", "a", "new"), ("r", "a", "x"), ("r", "b", "y")]


def test_another_registrations_rows_are_left_alone(dest):
    dest.write_registry_rows([_row("a", "x", registration="other")])
    dest.write_registry_rows([_row("a", "y")])
    assert _rows(dest) == [("other", "a", "x"), ("r", "a", "y")]


def test_every_value_is_stored(dest):
    dest.write_registry_rows([_row("a", "x", key_role="primary", not_null=None, type_oid=None)])
    with dest.connect() as con:
        row = con.execute("select type_oid, typmod, not_null, key_role, dest_type, mapping, seen_at "
                          "from _cdc_runtime.schema_registry").fetchone()
    assert row[:6] == (None, -1, None, "primary", "bigint", "direct")
    assert row[6] is not None


def test_more_rows_than_one_chunk_all_land(dest):
    rows = [_row(f"t{i % 90}", f"c{i}") for i in range(1203)]
    assert dest.write_registry_rows(rows) == 1203
    assert len(_rows(dest)) == 1203


def test_statements_do_not_grow_with_the_number_of_columns(dest, monkeypatch):
    seen = []

    class Spy:
        def __init__(self, con):
            self._con = con

        def execute(self, sql, *args):
            seen.append(sql)
            return self._con.execute(sql, *args)

        def executemany(self, *args):
            raise AssertionError("executemany costs MotherDuck a round trip per row")

        def close(self):
            self._con.close()

    real = dest_mod.duckdb.connect
    monkeypatch.setattr(dest_mod.duckdb, "connect", lambda *a, **k: Spy(real(*a, **k)))
    dest.write_registry_rows([_row(f"t{i % 90}", f"c{i}") for i in range(1203)])
    assert len(seen) == 6   # begin, 1 delete (90 tables), 3 inserts, commit


# A per-row `con.execute` loop would keep `executemany` out of the way and still pay MotherDuck
# a round trip per row, so the guard above cannot be the only one. This counts what the write
# actually costs: `executemany(sql, params)` is `len(params)` round trips, because that is what
# it was (575 s of a 670 s Featherbase run, #4708). The number is the assertion.


def _round_trips(dest, monkeypatch, rows) -> int:
    """Round trips `write_registry_rows` pays for `rows`, counted on the real code path."""
    trips = [0]

    class Counter:
        def __init__(self, con):
            self._con = con

        def execute(self, sql, *args):
            trips[0] += 1
            return self._con.execute(sql, *args)

        def executemany(self, sql, params, *args):
            trips[0] += len(params)
            return self._con.executemany(sql, params, *args)

        def close(self):
            self._con.close()

    real = dest_mod.duckdb.connect
    monkeypatch.setattr(dest_mod.duckdb, "connect", lambda *a, **k: Counter(real(*a, **k)))
    dest.write_registry_rows(rows)
    monkeypatch.undo()
    return trips[0]


def test_a_round_trip_is_not_paid_per_row_or_per_table(dest, monkeypatch):
    """1,203 columns over 90 tables — the order of production Featherbase's ~1,400-row registry.
    The pre-#4708 write paid 1,295 round trips for this (90 deletes + 1,203 rows + begin +
    commit); anything near that number is the regression, whichever call makes it."""
    rows = [_row(f"t{i % 90}", f"c{i}") for i in range(1203)]
    trips = _round_trips(dest, monkeypatch, rows)
    assert trips <= 10, (f"{trips} round trips for {len(rows)} columns over 90 tables — "
                         "the write is paying per row or per table again")
    assert len(_rows(dest)) == 1203   # and the rows still landed


def test_round_trips_barely_move_when_the_registry_grows_tenfold(dest, monkeypatch):
    """The real guard against per-row cost: 10x the columns must not be ~10x the round trips.
    Chunking at 500 means it grows by one statement per 500 columns, not one per column."""
    small = _round_trips(dest, monkeypatch, [_row(f"t{i % 9}", f"c{i}") for i in range(120)])
    big = _round_trips(dest, monkeypatch, [_row(f"t{i % 90}", f"c{i}") for i in range(1200)])
    assert (small, big) == (4, 6)   # begin + 1 delete + ceil(n/500) inserts + commit


def test_a_failed_write_rolls_back(dest):
    dest.write_registry_rows([_row("a", "x")])
    with pytest.raises(duckdb.Error):
        dest.write_registry_rows([_row("a", "y", type_oid="not a number")])
    assert _rows(dest) == [("r", "a", "x")]
