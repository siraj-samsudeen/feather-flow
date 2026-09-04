"""The MotherDuck token must never reach a log (#3079 review).

duckdb quotes the connection string it was opened with in its exception text, and Railway
captures stdout — so the token goes in a typed credentials object / a `config=` dict, never
in `md:<db>?motherduck_token=<token>`.
"""
from __future__ import annotations

import pytest

from pg_md_cdc.destination import Destination, DestinationSpec, q

TOKEN = "eyJhbGciOiJI-not-a-real-token-3079"


def _md(monkeypatch):
    monkeypatch.setenv("PG_MD_CDC_TEST_TOKEN", TOKEN)
    return Destination(DestinationSpec(kind="motherduck", database="bronze_x",
                                       token_env="PG_MD_CDC_TEST_TOKEN"))


def test_the_token_appears_in_neither_str_nor_repr_of_the_credentials(monkeypatch):
    creds = _md(monkeypatch).credentials()
    assert creds.database == "bronze_x" and creds.password == TOKEN
    assert TOKEN not in str(creds) and TOKEN not in repr(creds)


def test_the_token_appears_in_neither_str_nor_repr_of_the_dlt_destination(monkeypatch):
    dest = _md(monkeypatch)._dlt_destination()
    assert TOKEN not in str(dest) and TOKEN not in repr(dest)


def test_a_missing_token_env_names_the_variable_and_not_a_value(monkeypatch):
    monkeypatch.delenv("PG_MD_CDC_TEST_TOKEN", raising=False)
    dest = Destination(DestinationSpec(kind="motherduck", database="bronze_x",
                                       token_env="PG_MD_CDC_TEST_TOKEN"))
    with pytest.raises(ValueError, match="PG_MD_CDC_TEST_TOKEN"):
        dest.credentials()


def test_the_duckdb_kind_still_connects_by_path(duckdb_path):
    dest = Destination(DestinationSpec(kind="duckdb", database="t", path=duckdb_path))
    assert dest.credentials() == duckdb_path
    with dest.connect() as con:
        assert con.execute("select 1").fetchone() == (1,)
    dest.ensure_runtime_tables()
    dest.write_checkpoint("r", "s", 42)
    assert dest.read_checkpoint("r")[0] == 42


def test_an_embedded_double_quote_is_doubled_not_dropped():
    assert q('we"ird') == '"we""ird"'
    assert q('t"; drop table x; --') == '"t""; drop table x; --"'
