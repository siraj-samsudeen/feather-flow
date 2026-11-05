"""Generated dbt sources: classification metadata and parity with the file it supersedes (#3079 D3)."""
from __future__ import annotations

import pathlib

import pytest
import yaml

from feather_cdc_postgres import cli, generate_dbt, selectors as sel
from feather_cdc_postgres.adapters import NAMED_SYSTEM, USER_CREATED, AdapterRelation
from feather_cdc_postgres.discovery import SourceColumn, SourceRelation
from feather_cdc_postgres.manifest import load, parse

REGISTRATIONS = pathlib.Path(__file__).parent / "fixtures" / "registrations"

PLATFORM_TABLES = ["column_def", "comment", "has_role", "role", "table_def", "user"]
APP_TABLES = ["task"]


def _rel(schema, table):
    return SourceRelation(
        schema=schema, table=table, oid=abs(hash(table)) % 99999, primary_key=("id",),
        replica_identity="f",
        columns=[SourceColumn("id", 1, 23, -1, True, "int4")],
    )


def _featherbase_plan():
    m = load(str(REGISTRATIONS / "demo_app.yml"))
    discovered = (
        [_rel("featherbase", t) for t in PLATFORM_TABLES]
        + [_rel("tasker", t) for t in APP_TABLES]
    )
    adapter = [
        AdapterRelation(
            "featherbase", t, None if t.endswith("_def") else "Core", None, NAMED_SYSTEM
        )
        for t in PLATFORM_TABLES
    ] + [
        AdapterRelation("tasker", t, "Tasker", None, USER_CREATED) for t in APP_TABLES
    ]
    return m, sel.compile(m, discovered, adapter)


def _tables_by_source(doc) -> dict:
    return {s["name"]: {t["name"]: t for t in s["tables"]} for s in doc["sources"]}


def test_generated_featherbase_file_has_one_source_per_target_schema():
    """D-13/#3894: the registration routes platform and app schemas into four bronze schemas, so the file carries
    four dbt sources — disambiguated `<database>__<schema>` — each with its own `_dlt_loads`.
    """
    m, plan = _featherbase_plan()
    doc = yaml.safe_load(generate_dbt.render(m, plan))
    assert [s["name"] for s in doc["sources"]] == [
        "bronze_demo_app__access", "bronze_demo_app__activity",
        "bronze_demo_app__application", "bronze_demo_app__catalog"]
    assert {s["schema"] for s in doc["sources"]} == {"access", "activity", "application", "catalog"}
    assert {s["database"] for s in doc["sources"]} == {"bronze_demo_app"}
    by = _tables_by_source(doc)
    assert set(by["bronze_demo_app__catalog"]) == {"table_def", "column_def", "_dlt_loads"}
    assert set(by["bronze_demo_app__access"]) == {"user", "role", "has_role", "_dlt_loads"}
    assert set(by["bronze_demo_app__activity"]) == {"comment", "_dlt_loads"}
    assert set(by["bronze_demo_app__application"]) == {"task", "_dlt_loads"}
    every = {t for tables in by.values() for t in tables}
    assert set(PLATFORM_TABLES + APP_TABLES) <= every


def test_meta_blocks_carry_class_module_and_source_identity():
    m, plan = _featherbase_plan()
    by = _tables_by_source(yaml.safe_load(generate_dbt.render(m, plan)))
    app, cat, act = (by["bronze_demo_app__application"], by["bronze_demo_app__catalog"],
                     by["bronze_demo_app__activity"])
    assert app["task"]["meta"]["table_class"] == "application"
    assert app["task"]["meta"]["source_schema"] == "tasker"
    assert cat["table_def"]["meta"]["table_class"] == "source_system"
    assert act["comment"]["meta"]["table_class"] == "application"
    assert act["comment"]["meta"]["source_module"] == "Core"
    assert act["comment"]["meta"]["source_schema"] == "featherbase"  # source, not target
    assert act["comment"]["meta"]["source_table"] == "comment"
    assert act["_dlt_loads"]["meta"]["table_class"] == "loader_internal"


def test_dlt_loads_keeps_the_24h_warn_only_freshness_backstop():
    m, plan = _featherbase_plan()
    for source in yaml.safe_load(generate_dbt.render(m, plan))["sources"]:
        loads = {t["name"]: t for t in source["tables"]}["_dlt_loads"]
        assert loads["loaded_at_field"] == "inserted_at"
        assert loads["freshness"] == {"warn_after": {"count": 24, "period": "hour"}}
        assert "error_after" not in loads


def test_adapter_description_wins_over_the_default():
    m = load(str(REGISTRATIONS / "demo_app.yml"))
    plan = sel.compile(m, [_rel("public", "comment")], [AdapterRelation("public", "comment", "Core", "Threaded notes.")])
    doc = yaml.safe_load(generate_dbt.render(m, plan))
    assert doc["sources"][0]["tables"][0]["description"] == "Threaded notes."


def test_default_description_names_the_module():
    m = load(str(REGISTRATIONS / "demo_app.yml"))
    plan = sel.compile(m, [_rel("public", "comment")], [AdapterRelation("public", "comment", "Core", None)])
    doc = yaml.safe_load(generate_dbt.render(m, plan))
    assert doc["sources"][0]["tables"][0]["description"] == "Demo App native table (module: Core)."


def test_two_target_schemas_produce_two_disambiguated_sources():
    """Catches the collapse where a multi-schema source emits two dbt sources with the same
    name — dbt fails to parse, and the failure surfaces far from this generator."""
    m = load(str(REGISTRATIONS / "control_plane.yml"))
    plan = sel.compile(m, [_rel("control", "run")])
    doc = yaml.safe_load(generate_dbt.render(m, plan))
    assert [s["name"] for s in doc["sources"]] == ["bronze_control_plane"]

    m2 = parse(
        {
            "api_version": "pg-md-cdc/v1",
            "source_id": "featherbase",
            "source": {"dsn_env": "DEMO_PG_DSN", "include": {"schemas": ["*"]}},
            "destination": {"token_env": "MOTHERDUCK_TOKEN", "owner": "demo_cdc_service_account", "database": "bronze_demo_app"},
            "replication": {"slot": "s", "publication": "p"},
            "control": {"extractor": "featherbase"},
        }
    )
    plan2 = sel.compile(m2, [_rel("a", "t"), _rel("b", "t")], None)
    doc2 = yaml.safe_load(generate_dbt.render(m2, plan2))
    names = [s["name"] for s in doc2["sources"]]
    assert names == ["bronze_demo_app__a", "bronze_demo_app__b"] and len(set(names)) == 2


def test_control_plane_tables_classify_as_warehouse_control():
    m = load(str(REGISTRATIONS / "control_plane.yml"))
    plan = sel.compile(m, [_rel("control", "run"), _rel("control", "event")])
    doc = yaml.safe_load(generate_dbt.render(m, plan))
    by = {t["name"]: t for t in doc["sources"][0]["tables"]}
    assert by["run"]["meta"]["table_class"] == "warehouse_control"
    assert by["event"]["meta"]["table_class"] == "warehouse_control"


def test_output_is_valid_yaml_even_with_quotes_in_a_description():
    m = load(str(REGISTRATIONS / "demo_app.yml"))
    plan = sel.compile(m, [_rel("public", "comment")], [AdapterRelation("public", "comment", "Core", 'a "quoted" note')])
    doc = yaml.safe_load(generate_dbt.render(m, plan))
    assert doc["sources"][0]["tables"][0]["description"] == "a 'quoted' note"


def test_the_output_path_is_the_callers_and_has_no_default():
    """It used to default to `<four parents up>/dbt_runner/dbt/models/<source_id>/...` — one
    warehouse's directory layout, baked into a runtime that now installs from a wheel into
    site-packages. `--out` is required; there is nothing left to default to."""
    assert not hasattr(generate_dbt, "default_out_path")
    with pytest.raises(SystemExit):
        cli.main(["generate-dbt", str(REGISTRATIONS / "demo_app.yml")])
