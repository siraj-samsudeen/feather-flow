"""The dev Featherbase registration is a bounded copy of production's (#4340).

Dev leads production — a Featherbase release reaches dev first — so the two files may differ in
their selection. Nothing else may differ, and nothing may let dev data reach a production
database, slot or alarm. No database needed.
"""
from __future__ import annotations

import dataclasses
import pathlib

import pytest

from pg_md_cdc import manifest as mf, runner

REGISTRATIONS = pathlib.Path(__file__).resolve().parents[1] / "registrations"
PROD = mf.load(str(REGISTRATIONS / "featherbase.yml"))
DEV = mf.load(str(REGISTRATIONS / "featherbase_dev.yml"))
ALL = [mf.load(str(p)) for p in sorted(REGISTRATIONS.glob("*.yml"))]


def test_dev_validates_and_is_marked_dev():
    assert mf.errors(mf.validate_static(DEV)) == []
    assert DEV.environment == "dev"
    # Staging (#4617) is the other non-production mirror; its own file checks it the same way.
    assert {m.environment for m in ALL
            if m.source_id not in ("featherbase_dev", "featherbase_staging")} == {"production"}


@pytest.mark.parametrize("identity", [
    lambda m: m.source_id,
    lambda m: m.destination.database,
    lambda m: m.replication.slot,
    lambda m: m.replication.publication,
    lambda m: m.control.extractor,
], ids=["source_id", "database", "slot", "publication", "extractor"])
def test_no_two_registrations_share_an_identity(identity):
    """Dev data can never land in bronze_featherbase, drain production's slot, or log under
    production's extractor — not even through a future third registration."""
    values = [identity(m) for m in ALL]
    assert len(values) == len(set(values)), values


def test_dev_never_names_a_production_database():
    assert DEV.destination.database == "bronze_featherbase_dev"
    assert DEV.destination.database not in {m.destination.database for m in ALL if m.source_id != DEV.source_id}


def test_dev_denies_at_least_everything_production_denies():
    """A credential/PII table production refuses must never reach the dev mirror either."""
    assert set(PROD.source.deny.tables) <= set(DEV.source.deny.tables)
    assert set(PROD.source.deny.schemas) <= set(DEV.source.deny.schemas)
    assert set(PROD.source.exclude.tables) <= set(DEV.source.exclude.tables)


def test_dev_withholds_every_column_production_withholds():
    """#4346: a secret column production never mirrors (person.password_hash) never reaches the
    dev mirror either."""
    for rel, cols in PROD.source.deny_columns.items():
        assert set(cols) <= set(DEV.source.deny_columns.get(rel, [])), rel


def _without_selection_and_identity(m: mf.Manifest) -> dict:
    """Everything that must match production: the whole manifest minus identity, cadence and the
    selection that dev is allowed to change first."""
    d = dataclasses.asdict(m)
    for k in ("source_id", "environment", "path"):
        d.pop(k)
    d["destination"].pop("database")
    d["destination"].pop("table_schemas")
    for k in ("slot", "publication", "cadence_minutes"):
        d["replication"].pop(k)
    d["control"].pop("extractor")
    d["source"].pop("deny")              # checked above as a superset
    d["source"]["discovery"]["options"].pop("system_tables")
    return d


def test_dev_matches_production_outside_its_selection():
    assert _without_selection_and_identity(DEV) == _without_selection_and_identity(PROD)


def test_dev_reads_the_same_env_names_as_production():
    """The Railway instance supplies the DEV values under these names (runbook §m)."""
    assert (DEV.source.dsn_env, DEV.destination.token_env) == ("FEATHERBASE_PG_DSN", "MOTHERDUCK_TOKEN")


# --------------------------------------------------------------------------- environment guard


@pytest.mark.parametrize("m,railway_env,refused", [
    (DEV, "production", True),
    (PROD, "dev", True),
    (DEV, "dev", False),
    (PROD, "production", False),
    (PROD, None, False),          # a laptop or a test: no Railway variable, no check
])
def test_environment_mismatch(m, railway_env, refused):
    env = {} if railway_env is None else {"RAILWAY_ENVIRONMENT_NAME": railway_env}
    assert (mf.environment_mismatch(m, env) is not None) is refused


def test_an_unknown_environment_is_a_static_error():
    m = dataclasses.replace(DEV, environment="qa")
    assert "enum" in {p.code for p in mf.errors(mf.validate_static(m))}


@pytest.mark.parametrize("entry", ["run", "resnapshot"])
def test_a_misplaced_worker_refuses_before_any_connection(entry, monkeypatch):
    """Production's registration started in Railway's dev environment (a copied instance) must
    stop before it reads a DSN or writes a run row — the DSNs are deliberately unset here, so
    any later failure would carry a different message."""
    for var in ("FEATHERBASE_PG_DSN", "CDC_LOG_PG_DSN", "CONTROL_PG_DSN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "dev")
    call = (lambda: runner.run(PROD)) if entry == "run" else (lambda: runner.resnapshot(PROD, yes=True))
    with pytest.raises(SystemExit, match="environment 'production'.*Railway environment 'dev'"):
        call()


# --------------------------------------------------------------------------- shared-owner guards
# The dev worker uses featherbase_cdc_service_account, the owner of bronze_featherbase (Siraj,
# 23-Oct-2026): the token CAN write production bronze, so the code must make sure it never does.


def test_the_dev_registration_resolves_to_the_dev_database_only(monkeypatch):
    monkeypatch.delenv("PG_MD_CDC_DESTINATION", raising=False)
    assert runner._destination(DEV).spec.database == "bronze_featherbase_dev"
    assert runner._destination(PROD).spec.database == "bronze_featherbase"


def test_a_non_production_registration_must_write_a_database_named_for_its_environment():
    bad = dataclasses.replace(DEV, destination=dataclasses.replace(DEV.destination,
                                                                   database="bronze_featherbase"))
    assert "environment_database" in {p.code for p in mf.errors(mf.validate_static(bad))}
    assert "environment_database" not in {p.code for p in mf.errors(mf.validate_static(DEV))}
    assert "environment_database" not in {p.code for p in mf.errors(mf.validate_static(PROD))}


def _dev_pointing_at(tmp_path, database: str) -> str:
    """A copy of the registrations directory whose dev file names `database`."""
    for p in REGISTRATIONS.glob("*.yml"):
        (tmp_path / p.name).write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    dev = tmp_path / "featherbase_dev.yml"
    dev.write_text(dev.read_text(encoding="utf-8").replace(
        "database: bronze_featherbase_dev", f"database: {database}"), encoding="utf-8")
    return str(dev)


def test_the_runtime_refuses_a_dev_registration_aimed_at_a_production_database(tmp_path, monkeypatch):
    """Even past validation (a name that ends in _dev), a dev registration whose database is a
    production registration's database is refused before any connection is opened."""
    for var in ("FEATHERBASE_PG_DSN", "CDC_LOG_PG_DSN", "CONTROL_PG_DSN", "RAILWAY_ENVIRONMENT_NAME"):
        monkeypatch.delenv(var, raising=False)
    prod_copy = tmp_path / "featherbase.yml"
    path = _dev_pointing_at(tmp_path, "bronze_featherbase_dev")
    prod_copy.write_text(prod_copy.read_text(encoding="utf-8").replace(
        "database: bronze_featherbase\n", "database: bronze_featherbase_dev\n"), encoding="utf-8")
    m = mf.load(path)
    for call in (lambda: runner.run(m), lambda: runner.resnapshot(m, yes=True)):
        with pytest.raises(SystemExit, match="production registration"):
            call()


def test_the_runtime_refuses_the_prod_database_even_without_a_railway_environment(tmp_path, monkeypatch):
    for var in ("FEATHERBASE_PG_DSN", "CDC_LOG_PG_DSN", "CONTROL_PG_DSN", "RAILWAY_ENVIRONMENT_NAME"):
        monkeypatch.delenv(var, raising=False)
    m = mf.load(_dev_pointing_at(tmp_path, "bronze_featherbase"))
    with pytest.raises(SystemExit, match="bronze_featherbase"):
        runner.run(m)


# A database name is a bare identifier. `md:<database>` accepts connection parameters, so
# `bronze_featherbase?session_hint=x_dev` would pass a suffix check yet connect to
# bronze_featherbase (Codex adversarial review, 23-Oct-2026).
URI_BYPASSES = ["bronze_featherbase?session_hint=cdc_dev", "bronze_featherbase#_dev",
                "md:bronze_featherbase_dev", "bronze_featherbase_dev?x=1", "Bronze_Featherbase_dev",
                "bronze featherbase_dev", "bronze_featherbase/_dev", "bronze_featherbase_dev\n"]


@pytest.mark.parametrize("database", URI_BYPASSES)
def test_validate_refuses_a_database_that_is_not_a_bare_identifier(database):
    bad = dataclasses.replace(DEV, destination=dataclasses.replace(DEV.destination, database=database))
    assert "destination_database" in {p.code for p in mf.errors(mf.validate_static(bad))}


@pytest.mark.parametrize("database", URI_BYPASSES)
@pytest.mark.parametrize("entry", ["run", "resnapshot"])
def test_the_runtime_refuses_a_non_identifier_database_before_any_connection(
        database, entry, tmp_path, monkeypatch):
    for var in ("FEATHERBASE_PG_DSN", "CDC_LOG_PG_DSN", "CONTROL_PG_DSN", "RAILWAY_ENVIRONMENT_NAME",
                "MOTHERDUCK_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    m = mf.load(_dev_pointing_at(tmp_path, f'"{database}"'))
    call = (lambda: runner.run(m)) if entry == "run" else (lambda: runner.resnapshot(m, yes=True))
    with pytest.raises(SystemExit, match="bare identifier"):
        call()


def test_every_shipped_registration_names_a_bare_identifier_database():
    for m in ALL:
        assert mf.errors([p for p in mf.validate_static(m) if p.code == "destination_database"]) == []


# The proof-only `PG_MD_CDC_DESTINATION=duckdb:<path>` override is a LOCAL FILE path. DuckDB opens
# `md:`/`motherduck:` paths as MotherDuck, so an unchecked override would let a valid dev manifest
# write a production database with the shared token (Codex adversarial review, 23-Oct-2026).
OVERRIDE_BYPASSES = ["duckdb:md:bronze_featherbase", "duckdb:md:bronze_featherbase?motherduck_token=x",
                     "duckdb:motherduck:bronze_featherbase", "duckdb:MD:bronze_featherbase",
                     "duckdb:s3://bucket/x.duckdb", "duckdb:/tmp/x.duckdb?access_mode=read_write"]


@pytest.mark.parametrize("override", OVERRIDE_BYPASSES)
@pytest.mark.parametrize("entry", ["run", "resnapshot"])
def test_a_destination_override_that_is_not_a_local_file_is_refused_before_any_connection(
        override, entry, monkeypatch):
    for var in ("FEATHERBASE_PG_DSN", "CDC_LOG_PG_DSN", "CONTROL_PG_DSN", "RAILWAY_ENVIRONMENT_NAME"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("PG_MD_CDC_DESTINATION", override)
    call = (lambda: runner.run(DEV)) if entry == "run" else (lambda: runner.resnapshot(DEV, yes=True))
    with pytest.raises(SystemExit, match="local file path"):
        call()
    with pytest.raises(SystemExit, match="local file path"):
        runner._destination(DEV)


def test_a_local_file_override_is_still_accepted(tmp_path, monkeypatch):
    monkeypatch.setenv("PG_MD_CDC_DESTINATION", f"duckdb:{tmp_path / 'dest.duckdb'}")
    assert runner._destination(DEV).spec.kind == "duckdb"
