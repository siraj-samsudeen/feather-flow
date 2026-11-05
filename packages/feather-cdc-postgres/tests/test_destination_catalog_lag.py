"""_cdc_runtime metadata READS retry MotherDuck's catalog lag (#3079 hardening item 1).

docs/agents/gotchas/motherduck-catalog-lags-a-write.md: a table created on one connection
can be invisible to a NEW connection for ~25s. Only duckdb.CatalogException naming
`_cdc_runtime` / "does not exist" is retried; any other exception propagates immediately.
"""
from __future__ import annotations

import duckdb
import pytest

from feather_cdc_postgres.destination import Destination, DestinationSpec


def _dest(duckdb_path):
    dest = Destination(DestinationSpec(kind="duckdb", database="t", path=duckdb_path))
    dest.ensure_runtime_tables()
    dest.write_checkpoint("r", "s", 42)
    return dest


def test_read_checkpoint_retries_past_a_transient_catalog_exception(duckdb_path, monkeypatch):
    dest = _dest(duckdb_path)
    real_connect = Destination.connect
    calls = {"n": 0}

    def flaky_connect(self):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise duckdb.CatalogException("Table with name checkpoint does not exist")
        return real_connect(self)

    monkeypatch.setattr(Destination, "connect", flaky_connect)

    def _read():
        with dest.connect() as con:
            return con.execute(
                'select last_commit_lsn, load_ids from "_cdc_runtime".checkpoint '
                "where registration = ?", ["r"]).fetchone()

    row = dest._read_metadata_with_retry(_read, max_wait_s=5.0, backoff_s=0.01)
    assert int(row[0]) == 42
    assert calls["n"] == 3


def test_read_checkpoint_end_to_end_survives_two_transient_failures(duckdb_path, monkeypatch):
    dest = _dest(duckdb_path)
    real_connect = Destination.connect
    calls = {"n": 0}

    def flaky_connect(self):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise duckdb.CatalogException("Table with name checkpoint does not exist")
        return real_connect(self)

    monkeypatch.setattr(Destination, "connect", flaky_connect)
    # ensure_runtime_tables() already ran and set self._ensured, so read_checkpoint goes
    # straight to the retry-wrapped connect() — never retries the (skipped) write path.
    lsn, _ = dest.read_checkpoint("r")
    assert lsn == 42
    assert calls["n"] == 3


def test_a_different_catalog_exception_is_not_retried(duckdb_path, monkeypatch):
    dest = _dest(duckdb_path)

    def broken_connect(self):
        raise duckdb.BinderException("Binder Error: some other problem")

    monkeypatch.setattr(Destination, "connect", broken_connect)
    with pytest.raises(duckdb.BinderException):
        dest.read_checkpoint("r")


def test_retry_gives_up_after_the_deadline_and_raises(duckdb_path, monkeypatch):
    dest = _dest(duckdb_path)

    def always_missing(self):
        raise duckdb.CatalogException("Table with name checkpoint does not exist")

    monkeypatch.setattr(Destination, "connect", always_missing)
    with pytest.raises(duckdb.CatalogException):
        dest._read_metadata_with_retry(
            lambda: dest.connect(), max_wait_s=0.05, backoff_s=0.01)
