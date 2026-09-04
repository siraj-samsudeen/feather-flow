"""Generated dbt sources: classification metadata and parity with the file it supersedes (#3079 D3)."""
from __future__ import annotations

import pathlib

import yaml

from pg_md_cdc import generate_dbt, selectors as sel
from pg_md_cdc.adapters import AdapterRelation
from pg_md_cdc.discovery import SourceColumn, SourceRelation
from pg_md_cdc.manifest import load, parse

REPO = pathlib.Path(__file__).resolve().parents[2]
REGISTRATIONS = pathlib.Path(__file__).resolve().parents[1] / "registrations"

# The ten tables the live dbt file carries today (dbt_runner/dbt/models/featherbase/
# _featherbase_sources.yml). The generated file must remain a superset of them.
LIVE_TABLES = [
    "background_job", "column_def", "comment", "has_role", "home_page", "home_page_link",
    "job_execution", "role", "table_def", "user",
]


def _rel(schema, table):
    return SourceRelation(
        schema=schema, table=table, oid=abs(hash(table)) % 99999, primary_key=("id",),
        replica_identity="f",
        columns=[SourceColumn("id", 1, 23, -1, True, "int4")],
    )


def _featherbase_plan():
    m = load(str(REGISTRATIONS / "featherbase.yml"))
    discovered = [_rel("public", t) for t in LIVE_TABLES]
    adapter = [
        AdapterRelation("public", t, None if t.endswith("_def") else "Core", None) for t in LIVE_TABLES
    ]
    return m, sel.compile(m, discovered, adapter)


def test_generated_featherbase_file_is_a_superset_of_todays_tables():
    m, plan = _featherbase_plan()
    doc = yaml.safe_load(generate_dbt.render(m, plan))
    (source,) = doc["sources"]
    assert source["name"] == "bronze_featherbase"  # existing source() refs must keep resolving
    assert source["database"] == "bronze_featherbase" and source["schema"] == "public"
    names = [t["name"] for t in source["tables"]]
    assert set(LIVE_TABLES) <= set(names)
    assert "_dlt_loads" in names


def test_meta_blocks_carry_class_module_and_source_identity():
    m, plan = _featherbase_plan()
    doc = yaml.safe_load(generate_dbt.render(m, plan))
    by = {t["name"]: t for t in doc["sources"][0]["tables"]}
    assert by["background_job"]["meta"]["table_class"] == "source_operational"
    assert by["job_execution"]["meta"]["table_class"] == "source_operational"
    assert by["table_def"]["meta"]["table_class"] == "source_system"
    assert by["comment"]["meta"]["table_class"] == "application"
    assert by["comment"]["meta"]["source_module"] == "Core"
    assert by["comment"]["meta"]["source_schema"] == "public"
    assert by["comment"]["meta"]["source_table"] == "comment"
    assert by["_dlt_loads"]["meta"]["table_class"] == "loader_internal"


def test_dlt_loads_keeps_the_24h_warn_only_freshness_backstop():
    m, plan = _featherbase_plan()
    loads = {t["name"]: t for t in yaml.safe_load(generate_dbt.render(m, plan))["sources"][0]["tables"]}["_dlt_loads"]
    assert loads["loaded_at_field"] == "inserted_at"
    assert loads["freshness"] == {"warn_after": {"count": 24, "period": "hour"}}
    assert "error_after" not in loads


def test_adapter_description_wins_over_the_default():
    m = load(str(REGISTRATIONS / "featherbase.yml"))
    plan = sel.compile(m, [_rel("public", "comment")], [AdapterRelation("public", "comment", "Core", "Threaded notes.")])
    doc = yaml.safe_load(generate_dbt.render(m, plan))
    assert doc["sources"][0]["tables"][0]["description"] == "Threaded notes."


def test_default_description_names_the_module():
    m = load(str(REGISTRATIONS / "featherbase.yml"))
    plan = sel.compile(m, [_rel("public", "comment")], [AdapterRelation("public", "comment", "Core", None)])
    doc = yaml.safe_load(generate_dbt.render(m, plan))
    assert doc["sources"][0]["tables"][0]["description"] == "Featherbase native table (module: Core)."


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
            "destination": {"token_env": "MOTHERDUCK_TOKEN", "database": "bronze_featherbase"},
            "replication": {"slot": "s", "publication": "p"},
            "control": {"extractor": "featherbase"},
        }
    )
    plan2 = sel.compile(m2, [_rel("a", "t"), _rel("b", "t")], None)
    doc2 = yaml.safe_load(generate_dbt.render(m2, plan2))
    names = [s["name"] for s in doc2["sources"]]
    assert names == ["bronze_featherbase__a", "bronze_featherbase__b"] and len(set(names)) == 2


def test_control_plane_tables_classify_as_warehouse_control():
    m = load(str(REGISTRATIONS / "control_plane.yml"))
    plan = sel.compile(m, [_rel("control", "run"), _rel("control", "event")])
    doc = yaml.safe_load(generate_dbt.render(m, plan))
    by = {t["name"]: t for t in doc["sources"][0]["tables"]}
    assert by["run"]["meta"]["table_class"] == "warehouse_control"
    assert by["event"]["meta"]["table_class"] == "warehouse_control"


def test_output_is_valid_yaml_even_with_quotes_in_a_description():
    m = load(str(REGISTRATIONS / "featherbase.yml"))
    plan = sel.compile(m, [_rel("public", "comment")], [AdapterRelation("public", "comment", "Core", 'a "quoted" note')])
    doc = yaml.safe_load(generate_dbt.render(m, plan))
    assert doc["sources"][0]["tables"][0]["description"] == "a 'quoted' note"


def test_default_out_path():
    m = load(str(REGISTRATIONS / "featherbase.yml"))
    assert generate_dbt.default_out_path(m, REPO) == REPO / "dbt_runner/dbt/models/featherbase/_featherbase_sources.yml"
