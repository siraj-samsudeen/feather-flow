"""A new registration's first run creates its MotherDuck database (#4426), as its declared owner (#4427).

MotherDuck will not open `md:<db>` for a database that does not exist, so `run`/`resnapshot`
create it first, under the run's own token (write = ownership). Only after every refusal, never
from `validate`/`plan`, and never with the token in a connection string or an error. No database
needed: duckdb.connect is faked.
"""
from __future__ import annotations

import contextlib
import pathlib
import types

import pytest

from feather_cdc_postgres import cli, destination as dest_mod, manifest as mf, runner
from feather_cdc_postgres.destination import Destination, DestinationSpec

TOKEN = "eyJhbGciOiJI-not-a-real-token-4426"
REGISTRATIONS = pathlib.Path(__file__).resolve().parents[1] / "registrations"
PROD = mf.load(str(REGISTRATIONS / "featherbase.yml"))
DEV = mf.load(str(REGISTRATIONS / "featherbase_dev.yml"))


class _FakeCon:
    def __init__(self, log):
        self.log = log

    def execute(self, sql, *args):
        self.log.append(("sql", sql))
        return self

    def fetchone(self):
        return (self.log.user,)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass


class _Log(list):
    user = "featherbase_cdc_service_account"   # what `select current_user()` answers


@pytest.fixture
def connects(monkeypatch):
    """Every duckdb.connect the destination makes: ("connect", dsn, config) then its SQL."""
    log = _Log()

    def fake_connect(dsn=":memory:", *args, config=None, **kwargs):
        log.append(("connect", dsn, dict(config or {})))
        return _FakeCon(log)

    monkeypatch.setattr(dest_mod.duckdb, "connect", fake_connect)
    return log


def _md(monkeypatch, database="bronze_x", owner="featherbase_cdc_service_account"):
    monkeypatch.setenv("PG_MD_CDC_TEST_TOKEN", TOKEN)
    return Destination(DestinationSpec(kind="motherduck", database=database,
                                       token_env="PG_MD_CDC_TEST_TOKEN", owner=owner))


def _creates(log) -> list:
    return [e for e in log if e[0] == "sql" and "create database" in e[1].lower()]


# --------------------------------------------------------------------------- Destination


def test_a_motherduck_destination_creates_its_database_on_bare_md(monkeypatch, connects):
    _md(monkeypatch).ensure_database()
    assert connects[0] == ("connect", "md:", {"motherduck_token": TOKEN})
    assert connects[1] == ("sql", 'create database if not exists "bronze_x"')
    assert all(TOKEN not in str(e[1]) for e in connects)


def test_a_duckdb_destination_creates_nothing(connects, tmp_path):
    Destination(DestinationSpec(kind="duckdb", database="t",
                                path=str(tmp_path / "x.duckdb"))).ensure_database()
    assert connects == []


@pytest.mark.parametrize("database", ["bronze_x?session_hint=y", 'bronze"x', "Bronze_X",
                                      "md:bronze_x", "bronze_x\n", ""])
def test_a_name_that_is_not_a_bare_identifier_never_reaches_create(database, monkeypatch, connects):
    with pytest.raises(ValueError, match="bare identifier"):
        _md(monkeypatch, database).ensure_database()
    assert connects == []


def test_a_failed_create_never_carries_the_token(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError(f"Catalog Error: auth failed for motherduck_token={TOKEN}")

    monkeypatch.setattr(dest_mod.duckdb, "connect", boom)
    with pytest.raises(RuntimeError) as info:
        _md(monkeypatch).ensure_database()
    text = str(info.value)
    assert TOKEN not in text and "bronze_x" in text
    assert info.value.__cause__ is None and info.value.__suppress_context__


# --------------------------------------------------------------------------- run / resnapshot


class _FakeRun:
    def __init__(self, log):
        self.log = log

    def event(self, **kw):
        self.log.append(("event", kw.get("unit")))

    def finish(self, *a, **kw):
        self.log.append(("finish", a[0] if a else None))


class _FakeCP:
    def __init__(self, log):
        self.log = log

    @contextlib.contextmanager
    def run(self, kind):
        yield _FakeRun(self.log)


def _wire_runner(monkeypatch, log):
    """Fake everything around the create: source discovery, the run log, the owner lock."""
    rel = types.SimpleNamespace(qualified="featherbase.person", schema="featherbase",
                                table="person")
    plan = types.SimpleNamespace(relations=[rel])
    monkeypatch.delenv("PG_MD_CDC_DESTINATION", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    monkeypatch.setenv("MOTHERDUCK_TOKEN", TOKEN)
    monkeypatch.setattr(runner, "_compile", lambda m: ("postgresql://fake", plan))
    monkeypatch.setattr(runner, "_control_plane", lambda m, dev: _FakeCP(log))

    def fake_execute(cfg, relations, *, cp):
        log.append(("execute",))
        return 0

    monkeypatch.setattr(runner, "execute", fake_execute)

    class _Owner:
        def __init__(self, *a):
            pass

        def acquire(self):
            return True

        def release(self):
            pass

    monkeypatch.setattr(runner, "OwnerSession", _Owner)

    def stop_after_create(self):
        log.append(("ensure_runtime_tables",))
        raise RuntimeError("stop here")

    monkeypatch.setattr(Destination, "ensure_runtime_tables", stop_after_create)


def test_run_creates_the_database_before_it_executes(monkeypatch, connects):
    _wire_runner(monkeypatch, connects)
    assert runner.run(DEV) == 0
    create = connects.index(("sql", 'create database if not exists "bronze_featherbase_dev"'))
    assert connects[create - 1] == ("connect", "md:", {"motherduck_token": TOKEN})
    assert create < connects.index(("execute",))


def test_resnapshot_creates_the_database_before_its_runtime_tables(monkeypatch, connects):
    _wire_runner(monkeypatch, connects)
    with pytest.raises(RuntimeError, match="stop here"):
        runner.resnapshot(DEV, yes=True)
    create = connects.index(("sql", 'create database if not exists "bronze_featherbase_dev"'))
    assert create < connects.index(("ensure_runtime_tables",))


def test_a_failed_create_in_run_is_recorded_as_a_failed_tick(monkeypatch, connects):
    _wire_runner(monkeypatch, connects)

    class _CreateFails(_FakeCon):
        def execute(self, sql, *args):
            if "create database" in sql:
                raise RuntimeError("network down")
            return super().execute(sql, *args)

    monkeypatch.setattr(dest_mod.duckdb, "connect",
                        lambda dsn, *a, config=None, **k: _CreateFails(connects))
    with pytest.raises(RuntimeError, match="network down"):
        runner.run(DEV)
    assert ("event", "create_database") in connects
    assert ("finish", runner.cp_status.RUN_FAILED) in connects
    assert ("execute",) not in connects


def _dev_pointing_at(tmp_path, database: str) -> str:
    for p in REGISTRATIONS.glob("*.yml"):
        (tmp_path / p.name).write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    dev = tmp_path / "featherbase_dev.yml"
    dev.write_text(dev.read_text(encoding="utf-8").replace(
        "database: bronze_featherbase_dev", f"database: {database}"), encoding="utf-8")
    return str(dev)


def _prod_db_in_dev(tmp_path):
    """A dev registration whose (validly _dev-suffixed) database is also a production one's."""
    path = _dev_pointing_at(tmp_path, "bronze_featherbase_dev")
    prod = tmp_path / "featherbase.yml"
    prod.write_text(prod.read_text(encoding="utf-8").replace(
        "database: bronze_featherbase\n", "database: bronze_featherbase_dev\n"), encoding="utf-8")
    return mf.load(path)


@pytest.mark.parametrize("entry", ["run", "resnapshot"])
@pytest.mark.parametrize("case", ["wrong_environment", "production_database",
                                  "not_an_identifier"])
def test_a_refused_registration_creates_nothing(entry, case, tmp_path, monkeypatch, connects):
    _wire_runner(monkeypatch, connects)
    if case == "wrong_environment":
        monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "dev")
        m = PROD
    elif case == "production_database":
        m = _prod_db_in_dev(tmp_path)
    else:
        m = mf.load(_dev_pointing_at(tmp_path, '"bronze_featherbase?x=1_dev"'))
    call = (lambda: runner.run(m)) if entry == "run" else (lambda: runner.resnapshot(m, yes=True))
    with pytest.raises(SystemExit):
        call()
    assert connects == []


# --------------------------------------------------------------------------- read-only commands


@pytest.mark.parametrize("command", ["validate", "plan"])
def test_validate_and_plan_never_create(command, monkeypatch, connects):
    """The read-only commands never construct a destination connection at all. The source DSN is
    unset here; the PG-backed proof that `plan` mutates nothing is test_cli_plan_pg.py."""
    monkeypatch.delenv("FEATHERBASE_PG_DSN", raising=False)
    monkeypatch.setenv("MOTHERDUCK_TOKEN", TOKEN)
    called = []
    monkeypatch.setattr(Destination, "ensure_database", lambda self: called.append(1))
    cli.main([command, str(REGISTRATIONS / "featherbase_dev.yml")])
    assert called == [] and connects == []


# --------------------------------------------------------------------------- owner (#4427)


def test_the_owner_check_reads_current_user_on_bare_md(monkeypatch, connects):
    _md(monkeypatch).verify_owner()
    assert connects == [("connect", "md:", {"motherduck_token": TOKEN}),
                        ("sql", "select current_user()")]


def test_a_mismatched_owner_names_both_accounts_and_never_the_token(monkeypatch, connects):
    connects.user = "dbt_dev_service_account"
    with pytest.raises(dest_mod.WrongOwner) as info:
        _md(monkeypatch).verify_owner()
    text = str(info.value)
    assert "featherbase_cdc_service_account" in text and "dbt_dev_service_account" in text
    assert TOKEN not in text


def test_a_failed_owner_check_never_carries_the_token(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError(f"auth failed for {TOKEN}")

    monkeypatch.setattr(dest_mod.duckdb, "connect", boom)
    with pytest.raises(RuntimeError) as info:
        _md(monkeypatch).verify_owner()
    assert TOKEN not in str(info.value) and info.value.__suppress_context__


def test_a_motherduck_destination_without_an_owner_refuses(monkeypatch, connects):
    with pytest.raises(ValueError, match="owner"):
        _md(monkeypatch, owner=None).verify_owner()
    assert connects == []


def test_a_duckdb_destination_has_no_owner_to_check(connects, tmp_path):
    Destination(DestinationSpec(kind="duckdb", database="t",
                                path=str(tmp_path / "x.duckdb"))).verify_owner()
    assert connects == []


def test_run_with_the_declared_owner_proceeds_to_create_and_execute(monkeypatch, connects):
    _wire_runner(monkeypatch, connects)
    assert runner.run(DEV) == 0
    check = connects.index(("sql", "select current_user()"))
    create = connects.index(("sql", 'create database if not exists "bronze_featherbase_dev"'))
    assert check < create < connects.index(("execute",))


@pytest.mark.parametrize("entry", ["run", "resnapshot"])
def test_a_token_for_another_account_refuses_before_any_create_or_write(entry, monkeypatch, connects):
    _wire_runner(monkeypatch, connects)
    connects.user = "dbt_dev_service_account"
    call = (lambda: runner.run(DEV)) if entry == "run" else (lambda: runner.resnapshot(DEV, yes=True))
    with pytest.raises(SystemExit) as info:
        call()
    text = str(info.value)
    assert "featherbase_cdc_service_account" in text and "dbt_dev_service_account" in text
    assert TOKEN not in text
    assert _creates(connects) == []
    assert not any(e[0] in ("execute", "ensure_runtime_tables", "event", "finish") for e in connects)


def test_an_unreachable_owner_check_is_recorded_and_creates_nothing(monkeypatch, connects):
    _wire_runner(monkeypatch, connects)

    def boom(*a, **k):
        raise RuntimeError(f"network down {TOKEN}")

    monkeypatch.setattr(dest_mod.duckdb, "connect", boom)
    with pytest.raises(RuntimeError, match="network down") as info:
        runner.run(DEV)
    assert TOKEN not in str(info.value)
    assert ("event", "owner_check") in connects and ("execute",) not in connects


@pytest.mark.parametrize("owner", [None, "", "featherbase cdc", "x;drop"])
def test_validation_requires_a_well_formed_owner(owner, tmp_path):
    raw = (REGISTRATIONS / "featherbase_dev.yml").read_text(encoding="utf-8")
    line = "  owner: featherbase_cdc_service_account\n"
    assert line in raw
    new = "" if owner is None else f"  owner: {owner!r}\n"
    path = tmp_path / "featherbase_dev.yml"
    path.write_text(raw.replace(line, new), encoding="utf-8")
    codes = {p.code for p in mf.errors(mf.validate_static(mf.load(str(path))))}
    assert "destination_owner" in codes


def test_every_shipped_registration_declares_its_registry_owner():
    """Owners as recorded in seeds layer_access_registry.csv (the MotherDuck username)."""
    owners = {mf.load(str(p)).source_id: mf.load(str(p)).destination.owner
              for p in sorted(REGISTRATIONS.glob("*.yml"))}
    assert owners == {"control_plane": "control_plane_cdc_service_account",
                      "featherbase": "featherbase_cdc_service_account",
                      "featherbase_dev": "featherbase_cdc_service_account",
                      "featherbase_staging": "featherbase_cdc_service_account",
                      "petpooja_webhook": "petpooja_extract"}
