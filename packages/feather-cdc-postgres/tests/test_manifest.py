"""Static manifest validation (#3079 D1/D12)."""
from __future__ import annotations

import pathlib

import pytest

from pg_md_cdc import manifest as mf

REGISTRATIONS = pathlib.Path(__file__).resolve().parents[1] / "registrations"

BASE = {
    "api_version": "pg-md-cdc/v1",
    "source_id": "demo",
    "source": {"dsn_env": "DEMO_PG_DSN", "include": {"schemas": ["public"]}},
    "destination": {"token_env": "MOTHERDUCK_TOKEN", "database": "bronze_demo"},
    "replication": {"slot": "demo_slot", "publication": "demo_pub"},
    "control": {"extractor": "demo"},
}


def _codes(problems, severity=None):
    return {p.code for p in problems if severity is None or p.severity == severity}


def _m(**overrides):
    raw = {k: (dict(v) if isinstance(v, dict) else v) for k, v in BASE.items()}
    for k, v in overrides.items():
        if isinstance(v, dict) and isinstance(raw.get(k), dict):
            raw[k] = {**raw[k], **v}
        else:
            raw[k] = v
    return mf.parse(raw)


def test_baseline_manifest_has_no_errors():
    assert _codes(mf.validate_static(_m()), "error") == set()


def test_wrong_api_version_is_rejected():
    assert "api_version" in _codes(mf.validate_static(_m(api_version="pg-md-cdc/v2")), "error")


def test_source_id_must_be_a_lowercase_identifier():
    assert "source_id" in _codes(mf.validate_static(_m(source_id="Demo-1")), "error")


def test_a_dsn_value_in_place_of_an_env_name_is_rejected_and_never_echoed():
    """Catches the breakage where someone pastes a live DSN into the manifest: the check must
    fire AND the value must not appear in the problem text (a validator that echoes the input
    turns a config mistake into a credential leak)."""
    dsn = "postgresql://u:sup3rsecret@host:5432/db"
    problems = mf.validate_static(_m(source={"dsn_env": dsn}))
    assert "env_ref_is_a_value" in _codes(problems, "error")
    joined = " ".join(p.message for p in problems)
    assert "sup3rsecret" not in joined and dsn not in joined


def test_redact_removes_env_values_verbatim():
    env = {"TOKEN": "correcthorsebattery", "SHORT": "ab"}
    out = mf.redact("token=correcthorsebattery host=postgresql://u:p@h/db", env=env)
    assert "correcthorsebattery" not in out
    assert "u:p@h" not in out


def test_bad_pg_identifiers_are_rejected():
    problems = mf.validate_static(_m(replication={"slot": "demo slot", "publication": "1pub"}))
    assert _codes(problems, "error") >= {"pg_identifier"}


def test_schema_mapping_collision_is_an_error():
    problems = mf.validate_static(_m(destination={"schema_mapping": {"a": "x", "b": "x"}}))
    assert "schema_mapping_collision" in _codes(problems, "error")


def test_unimplemented_availability_policy_is_an_error():
    problems = mf.validate_static(_m(control={"availability_policy": "run_through_control_outage"}))
    assert "availability_policy" in _codes(problems, "error")


def test_persistent_local_wal_needs_a_mounted_dir(tmp_path):
    missing = mf.validate_static(_m(control={"event_durability": "persistent_local_wal"}))
    assert "wal_dir_missing" in _codes(missing, "error")
    ephemeral = mf.validate_static(
        _m(control={"event_durability": "persistent_local_wal", "wal_dir": str(tmp_path / "wal")})
    )
    assert "wal_dir_not_persistent" in _codes(ephemeral, "error")


def test_mounted_wal_dir_is_accepted(tmp_path, monkeypatch):
    """The root filesystem is deliberately NOT enough: a Railway container's / IS a mount, so
    accepting it would wave through exactly the ephemeral case D9 rejects."""
    volume = tmp_path / "volume"
    monkeypatch.setattr(mf.os.path, "ismount", lambda p: str(p) == str(volume))
    ok = mf.validate_static(_m(control={"event_durability": "persistent_local_wal", "wal_dir": str(volume / "wal")}))
    assert "wal_dir_not_persistent" not in _codes(ok, "error")
    at_root = mf.validate_static(_m(control={"event_durability": "persistent_local_wal", "wal_dir": "/"}))
    assert "wal_dir_not_persistent" in _codes(at_root, "error")


def test_unknown_key_is_rejected_rather_than_ignored():
    with pytest.raises(mf.ManifestError):
        mf.parse({**BASE, "replicaton": {}})


def test_key_override_must_be_schema_qualified_and_a_known_policy():
    problems = mf.validate_static(_m(replication={"key_overrides": {"bare": "append_only", "a.b": "nope"}}))
    assert {"key_override_name", "enum"} <= _codes(problems, "error")


def test_pipeline_name_defaults_per_schema_and_honours_the_override():
    assert mf.pipeline_name(_m(), "public") == "demo_cdc__public"
    assert mf.pipeline_name(_m(destination={"pipeline_name": "featherbase_cdc"}), "public") == "featherbase_cdc"


@pytest.mark.parametrize("name", ["featherbase", "control_plane"])
def test_shipped_registrations_validate_with_zero_errors(name):
    m = mf.load(str(REGISTRATIONS / f"{name}.yml"))
    problems = mf.validate_static(m)
    assert [p for p in problems if p.severity == "error"] == []
    # env references only — no value ever lands in a registration file
    assert m.source.dsn_env.isupper() and m.destination.token_env.isupper()


def test_reconcile_exclude_entries_must_be_schema_qualified():
    ok = mf.validate_static(_m(control={"reconcile_exclude": ["control.run", "control.event*"]}))
    assert "reconcile_exclude_pattern" not in _codes(ok, "error")
    bad = mf.validate_static(_m(control={"reconcile_exclude": ["run"]}))
    assert "reconcile_exclude_pattern" in _codes(bad, "error")


def test_the_control_plane_registration_excludes_the_tables_it_writes_while_running():
    m = mf.load(str(REGISTRATIONS / "control_plane.yml"))
    assert set(m.control.reconcile_exclude) == {"control.run", "control.event",
                                                "control.budget_usage"}


def test_key_override_full_row_is_a_static_error():
    problems = mf.validate_static(_m(replication={"key_overrides": {"public.t": "full_row"}}))
    assert "full_row_unsupported" in _codes(problems, "error")
    assert _codes(mf.validate_static(
        _m(replication={"key_overrides": {"public.t": "append_only"}})), "error") == set()


# --------------------------------------------------------------------------- D-13 · table_schemas


def test_table_schemas_first_match_wins_then_schema_mapping():
    m = _m(destination={"schema_mapping": {"public": "application"},
                        "table_schemas": [{"match": "public.table_def", "schema": "catalog"},
                                          {"match": "public.*", "schema": "everything_else"}]})
    assert _codes(mf.validate_static(m), "error") == set()
    assert m.destination.target_schema("public", "table_def") == "catalog"
    assert m.destination.target_schema("public", "comment") == "everything_else"
    assert m.destination.target_schema("other", "comment") == "other"
    assert m.destination.target_schema("public") == "application"   # no table: mapping only
    assert mf.pipeline_name(m, "catalog") == "demo_cdc__catalog"


def test_table_schemas_targets_must_be_identifiers_and_never_the_runtime_schema():
    bad = mf.validate_static(_m(destination={"table_schemas": [
        {"match": "public.a", "schema": "_cdc_runtime"},
        {"match": "public.b", "schema": "public_staging"},
        {"match": "public.c", "schema": "Bad Name"},
        {"match": "bare", "schema": "ok"},
    ]}))
    assert {"target_schema_reserved", "target_schema_identifier", "table_schema_match"} <= _codes(bad, "error")
    also_bad = mf.validate_static(_m(destination={"schema_mapping": {"public": "_cdc_runtime"}}))
    assert "target_schema_reserved" in _codes(also_bad, "error")


def test_table_schemas_entries_must_be_match_plus_schema():
    with pytest.raises(mf.ManifestError):
        mf.parse({**BASE, "destination": {**BASE["destination"], "table_schemas": [{"match": "a.b", "target": "x"}]}})
    with pytest.raises(mf.ManifestError):
        mf.parse({**BASE, "destination": {**BASE["destination"], "table_schemas": ["a.b"]}})


def test_a_pinned_pipeline_name_cannot_serve_several_target_schemas():
    problems = mf.validate_static(_m(destination={
        "pipeline_name": "one", "table_schemas": [{"match": "public.a", "schema": "x"},
                                                  {"match": "public.b", "schema": "y"}]}))
    assert "pipeline_name_ambiguous" in _codes(problems, "error")


def test_the_featherbase_registration_is_a_clean_run_with_per_table_targets():
    """The pilot's `pipeline_name: featherbase_cdc` pin is gone (the cutover is a clean run and
    there are several datasets now); the three named groups route to their own schemas and
    everything else — every user-created table — falls back to `application`."""
    m = mf.load(str(REGISTRATIONS / "featherbase.yml"))
    assert m.destination.pipeline_name is None
    assert m.destination.schema_mapping == "preserve_source"
    assert m.source.discovery.options["schema"] == "featherbase"
    assert m.source.discovery.options["system_tables"] == [
        "table_def", "column_def", "person", "identifier_system", "role", "has_role",
        "permission", "data_scope", "share", "activity_log", "user_event", "comment"]
    routed = {
        r.match.split(".", 1)[1]: r.schema
        for r in m.destination.table_schemas
        if r.match.startswith("featherbase.")
    }
    assert routed == {"table_def": "catalog", "column_def": "catalog", "user": "access",
                      "person": "access", "identifier_system": "access", "role": "access", "has_role": "access", "permission": "access",
                      "data_scope": "access", "share": "access", "activity_log": "activity",
                      "user_event": "activity", "comment": "activity"}
    # every named table is routed; `user` keeps its rule only as the RETIRED name (#4338), so
    # WAL written before featherbase 0101 renamed it still lands in the frozen access.user
    assert set(routed) == set(m.source.discovery.options["system_tables"]) | {"user"}
    assert "user" not in m.source.discovery.options["system_tables"]
    assert m.destination.target_schema("tasker", "anything_the_app_made") == "application"
