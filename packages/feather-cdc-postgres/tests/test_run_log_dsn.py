"""#4078: the CDC logs to its own CDC log DB, named by CDC_LOG_PG_DSN.

CONTROL_PG_DSN is accepted for one release — and must lose when both are set, because for the
control-plane CDC it is ALSO the source: a log that followed it would land in the very database
being mirrored.
"""
import pytest

from feather_cdc_postgres import runner


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("CDC_LOG_PG_DSN", raising=False)
    monkeypatch.delenv("CONTROL_PG_DSN", raising=False)


def test_the_cdc_log_db_wins_over_the_control_plane(monkeypatch):
    monkeypatch.setenv("CDC_LOG_PG_DSN", "postgresql://cdc-log/db")
    monkeypatch.setenv("CONTROL_PG_DSN", "postgresql://control-plane/db")
    assert runner._run_log_dsn() == "postgresql://cdc-log/db"


def test_control_pg_dsn_is_still_accepted_for_one_release(monkeypatch):
    monkeypatch.setenv("CONTROL_PG_DSN", "postgresql://control-plane/db")
    assert runner._run_log_dsn() == "postgresql://control-plane/db"


def test_neither_set_is_fatal_and_names_both(monkeypatch):
    class _Control:
        extractor, wal_dir = "featherbase", None

    class _Manifest:
        control = _Control()

    with pytest.raises(SystemExit) as e:
        runner._control_plane(_Manifest(), dev=False)
    assert "CDC_LOG_PG_DSN" in str(e.value) and "CONTROL_PG_DSN" in str(e.value)
