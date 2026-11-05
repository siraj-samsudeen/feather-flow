"""A `staging` registration is bounded the same way a `dev` one is.

Staging is where business users validate master data before production. Its mirror may select
what dev selects; nothing else may differ from production, and nothing may let staging data
reach a production or dev database, slot or alarm. No database needed.
"""
from __future__ import annotations

import pathlib

import pytest

from feather_cdc_postgres import manifest as mf, runner
from test_dev_registration import _without_selection_and_identity

REGISTRATIONS = pathlib.Path(__file__).parent / "fixtures" / "registrations"
PROD = mf.load(str(REGISTRATIONS / "demo_app.yml"))
DEV = mf.load(str(REGISTRATIONS / "demo_app_dev.yml"))
STAGING = mf.load(str(REGISTRATIONS / "demo_app_staging.yml"))


def test_staging_validates_and_is_marked_staging():
    assert mf.errors(mf.validate_static(STAGING)) == []
    assert STAGING.environment == "staging"


def test_staging_writes_only_its_own_database(monkeypatch):
    monkeypatch.delenv("PG_MD_CDC_DESTINATION", raising=False)
    assert runner._destination(STAGING).spec.database == "bronze_demo_app_staging"
    assert STAGING.destination.database not in {PROD.destination.database, DEV.destination.database}


def test_staging_denies_at_least_everything_production_denies():
    assert set(PROD.source.deny.tables) <= set(STAGING.source.deny.tables)
    assert set(PROD.source.deny.schemas) <= set(STAGING.source.deny.schemas)
    assert set(PROD.source.exclude.tables) <= set(STAGING.source.exclude.tables)
    for rel, cols in PROD.source.deny_columns.items():
        assert set(cols) <= set(STAGING.source.deny_columns.get(rel, [])), rel


def test_staging_matches_production_outside_its_selection():
    assert _without_selection_and_identity(STAGING) == _without_selection_and_identity(PROD)


def test_staging_reads_the_same_env_names_as_production():
    assert (STAGING.source.dsn_env, STAGING.destination.token_env) == ("DEMO_APP_PG_DSN", "MOTHERDUCK_TOKEN")


@pytest.mark.parametrize("railway_env,refused", [
    ("staging", False), ("dev", True), ("production", True), (None, False),
])
def test_staging_runs_only_in_the_staging_environment(railway_env, refused):
    env = {} if railway_env is None else {"RAILWAY_ENVIRONMENT_NAME": railway_env}
    assert (mf.environment_mismatch(STAGING, env) is not None) is refused


def test_production_and_dev_refuse_the_staging_environment():
    env = {"RAILWAY_ENVIRONMENT_NAME": "staging"}
    assert mf.environment_mismatch(PROD, env) is not None
    assert mf.environment_mismatch(DEV, env) is not None
