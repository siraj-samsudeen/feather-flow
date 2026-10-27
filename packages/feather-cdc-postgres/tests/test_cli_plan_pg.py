"""`validate` / `plan` against a real Postgres: read-only, and they say so truthfully (#3079 D1)."""
from __future__ import annotations

import json
import pathlib

import psycopg
import pytest

from pg_md_cdc import cli

MANIFEST = """\
api_version: pg-md-cdc/v1
source_id: demo
source:
  dsn_env: DEMO_PG_DSN
  include:
    schemas: ["*"]
{extra}destination:
  token_env: DEMO_MD_TOKEN
  owner: demo_cdc_service_account
  database: bronze_demo
replication:
  slot: {slot}
  publication: {publication}
  replica_identity: full
control:
  extractor: demo
"""


def _manifest(tmp_path: pathlib.Path, names, extra: str = "") -> str:
    p = tmp_path / "demo.yml"
    p.write_text(MANIFEST.format(slot=names["slot"], publication=names["publication"], extra=extra), encoding="utf-8")
    return str(p)


def _ddl(dsn, *statements):
    with psycopg.connect(dsn, autocommit=True) as c:
        for s in statements:
            c.execute(s)


def _catalog_fingerprint(dsn) -> tuple:
    with psycopg.connect(dsn, autocommit=True) as c:
        pubs = c.execute("select pubname from pg_publication order by 1").fetchall()
        pubrels = c.execute("select schemaname, tablename from pg_publication_tables order by 1, 2").fetchall()
        ident = c.execute(
            "select n.nspname, c.relname, c.relreplident from pg_class c join pg_namespace n "
            "on n.oid = c.relnamespace where c.relkind = 'r' and n.nspname not in "
            "('pg_catalog', 'information_schema', 'pg_toast') order by 1, 2"
        ).fetchall()
        slots = c.execute("select slot_name from pg_replication_slots order by 1").fetchall()
    return (pubs, pubrels, ident, slots)


@pytest.fixture
def env_dsn(monkeypatch, fresh_db):
    monkeypatch.setenv("DEMO_PG_DSN", fresh_db)
    monkeypatch.setenv("DEMO_MD_TOKEN", "not-a-real-token-value")
    return fresh_db


def test_plan_mutates_nothing(tmp_path, env_dsn, names, capsys):
    """The whole point of `plan`: it is quoted to an operator as safe to run against production.
    A single ALTER PUBLICATION or ALTER ... REPLICA IDENTITY slipping into the read path would
    make that claim false, and this fingerprint is what catches it."""
    _ddl(
        env_dsn,
        "create table public.a (id int primary key, v text)",
        "create table public.b (id int primary key)",
        f'create publication "{names["publication"]}" for table public.b',
    )
    before = _catalog_fingerprint(env_dsn)
    rc = cli.main(["plan", _manifest(tmp_path, names)])
    after = _catalog_fingerprint(env_dsn)
    assert before == after
    assert rc == 0
    out = capsys.readouterr().out
    assert "public.a" in out and "add (1)" in out
    assert "drop (0)" in out
    assert "replica identity ALTERs (2)" in out  # both are 'd' today; plan lists, never applies


def test_plan_reports_a_missing_publication_and_slot_without_creating_them(tmp_path, env_dsn, names, capsys):
    _ddl(env_dsn, "create table public.a (id int primary key)")
    cli.main(["plan", _manifest(tmp_path, names)])
    out = capsys.readouterr().out
    assert "MISSING — would be created" in out and "absent — would be created" in out
    with psycopg.connect(env_dsn, autocommit=True) as c:
        assert c.execute("select count(*) from pg_publication").fetchone()[0] == 0
        assert c.execute("select count(*) from pg_replication_slots").fetchone()[0] == 0


def test_validate_exits_nonzero_on_an_unkeyed_table_and_names_it(tmp_path, env_dsn, names, capsys):
    _ddl(env_dsn, "create table public.events (v text)")
    rc = cli.main(["validate", _manifest(tmp_path, names)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "no_key_rejected" in out and "public.events" in out


def test_validate_never_prints_an_env_value(tmp_path, env_dsn, names, capsys):
    _ddl(env_dsn, "create table public.a (id int primary key)")
    cli.main(["validate", _manifest(tmp_path, names)])
    out = capsys.readouterr().out
    assert env_dsn not in out and "not-a-real-token-value" not in out
    assert "$DEMO_PG_DSN" in out  # the NAME is echoed, so an operator can see what it read


def test_missing_env_var_is_an_error_naming_the_variable(tmp_path, monkeypatch, names, capsys):
    monkeypatch.delenv("DEMO_PG_DSN", raising=False)
    rc = cli.main(["validate", _manifest(tmp_path, names)])
    out = capsys.readouterr().out
    assert rc == 1 and "env_not_set" in out and "DEMO_PG_DSN" in out


def test_json_output_is_machine_readable(tmp_path, env_dsn, names, capsys):
    _ddl(env_dsn, "create table public.a (id int primary key)")
    cli.main(["--json", "plan", _manifest(tmp_path, names)])
    payload = json.loads(capsys.readouterr().out)
    assert payload["source_id"] == "demo"
    assert payload["relations"][0]["table"] == "a"
    assert len(payload["selection_hash"]) == 64
    assert payload["publication_add"] == [["public", "a"]]


def test_generate_dbt_writes_the_requested_path(tmp_path, env_dsn, names):
    _ddl(env_dsn, "create table public.a (id int primary key)")
    out = tmp_path / "sources.yml"
    rc = cli.main(["generate-dbt", _manifest(tmp_path, names), "--out", str(out)])
    assert rc == 0
    text = out.read_text(encoding="utf-8")
    assert "table_class: application" in text and "- name: _dlt_loads" in text


def test_a_deny_hit_fails_validate(tmp_path, env_dsn, names, capsys):
    _ddl(env_dsn, "create table public.access_token (id int primary key)")
    extra = "  deny:\n    tables: [\"public.access_token\"]\n"
    rc = cli.main(["validate", _manifest(tmp_path, names, extra=extra)])
    assert rc == 1
    assert "deny_hit" in capsys.readouterr().out


def test_an_unreachable_source_exits_1_with_a_redacted_message_and_no_traceback(
        tmp_path, names, monkeypatch, capsys):
    """Only ManifestError used to be caught, so every OTHER failure — an unreachable source
    being the common one on Railway — printed a raw traceback whose psycopg message carries the
    DSN. Exit 1, one `[error] <type>: <message>` line, the password gone, and `--json` still
    emits parseable JSON on stdout so a scripted caller does not choke on an empty document."""
    monkeypatch.setenv("DEMO_PG_DSN",
                       "postgresql://cdc:hunter2secret@127.0.0.1:1/nope?connect_timeout=1")
    manifest = _manifest(tmp_path, names)

    assert cli.main(["validate", manifest]) == 1
    captured = capsys.readouterr()
    assert captured.err.startswith("[error] OperationalError:")
    assert "Traceback" not in captured.err
    assert "hunter2secret" not in captured.err + captured.out

    # positive control for the redaction: an exception that really does carry the DSN (dlt's
    # config errors echo the value they resolved), through the --json path.
    from pg_md_cdc import discovery

    def _leak(dsn, **kw):
        raise RuntimeError(f"could not read the catalog of {dsn}")

    monkeypatch.setattr(discovery, "discover", _leak)
    assert cli.main(["--json", "plan", manifest]) == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["error"] == "RuntimeError"
    assert "hunter2secret" not in json.dumps(payload) + captured.err
    assert "redacted" in payload["message"]
