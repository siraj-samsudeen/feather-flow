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
