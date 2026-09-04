"""Schema registry + drift policy against a real Postgres and a real dlt load (#3079 D7).

Logical replication carries rows, not DDL, so every claim here has to be proved by what
actually lands in duckdb after the DDL — not by what the classifier says in isolation
(that is `test_schema_registry.py`). The policy under test: additive drift evolves silently,
a breaking non-key drift is reported and KEEPS loading, a key change removes the table from
the publication while the rest of the publication drains.
"""
from __future__ import annotations

import duckdb
from pg_md_cdc import publication
from pg_md_cdc.lifecycle import DRIFT_REVIEW, RESNAPSHOT_REQUIRED, STREAMING

from test_e2e_pg import make, rel, sql, tick


def cols(duckdb_path, schema, table) -> dict:
    con = duckdb.connect(duckdb_path)
    try:
        return {r[0]: r[1] for r in con.execute(
            "select column_name, data_type from information_schema.columns "
            "where table_schema = ? and table_name = ?", [schema, table]).fetchall()}
    finally:
        con.close()


def registry(duckdb_path, registration) -> dict:
    con = duckdb.connect(duckdb_path)
    try:
        rows = con.execute(
            'select "table", "column", dest_type, mapping, key_role from '
            '"_cdc_runtime".schema_registry where registration = ?', [registration]).fetchall()
    finally:
        con.close()
    return {(r[0], r[1]): {"dest_type": r[2], "mapping": r[3], "key_role": r[4]} for r in rows}


def drifts(run, entity=None):
    return [e for e in run.units("schema_drift") if entity is None or e["entity"] == entity]


def kinds(run, entity=None):
    return {d["kind"] for e in drifts(run, entity) for d in e["extra"]["drifts"]}


# --------------------------------------------------------------------------- f · the registry


def test_the_first_run_registers_one_row_per_column_and_flags_the_unmapped_type(
        fresh_db, names, duckdb_path, tmp_path):
    """Every OID in use on the two live sources compiles to a declared destination type; an
    `inet` column has no declared mapping, so it lands as text AND says so in an event."""
    sql(fresh_db, "create table public.wide ("
                  "id int primary key, label text, seen timestamptz, payload jsonb, "
                  "flag bool, host inet)",
        "insert into public.wide values (1, 'a', now(), '{\"k\": 1}', true, '10.0.0.1')")
    relations = [rel("public", "wide", "id")]
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path)
    run, _ = tick(cfg, relations)

    reg = registry(duckdb_path, cfg.registration)
    assert {c for (t, c) in reg} == {"id", "label", "seen", "payload", "flag", "host"}
    assert reg[("wide", "id")] == {"dest_type": "bigint", "mapping": "widened",
                                   "key_role": "key"}
    assert reg[("wide", "label")]["dest_type"] == "text"
    assert reg[("wide", "seen")]["dest_type"] == "timestamp"
    assert reg[("wide", "payload")]["dest_type"] == "json"
    assert reg[("wide", "flag")]["dest_type"] == "bool"
    assert reg[("wide", "host")] == {"dest_type": "text", "mapping": "text_fallback",
                                     "key_role": "none"}

    (event,) = run.units("schema_registry")
    assert event["entity"] == "public.wide" and event["status"] == "ok"
    assert event["extra"]["text_fallback_columns"] == ["host"]
    # a first run has nothing to diff against
    assert run.units("schema_drift") == []


def test_a_signature_is_stored_per_relation_and_re_read_as_the_same_shape(
        fresh_db, names, duckdb_path, tmp_path):
    sql(fresh_db, "create table public.t (id int primary key, v text)",
        "insert into public.t values (1, 'a')")
    relations = [rel("public", "t", "id")]
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path)
    tick(cfg, relations)
    stored = dest.read_signatures(cfg.registration)
    sig = stored[("public", "t")]
    assert sig["key"] == ["id"] and set(sig["columns"]) == {"id", "v"}
    assert sig["columns"]["id"]["dest_type"] == "bigint"


# --------------------------------------------------------------------------- a · additive


def test_a_nullable_column_added_mid_stream_evolves_and_earlier_rows_stay_null(
        fresh_db, names, duckdb_path, tmp_path):
    sql(fresh_db, "create table public.t (id int primary key, v text)",
        "insert into public.t values (1, 'first')")
    relations = [rel("public", "t", "id")]
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path)
    tick(cfg, relations)

    sql(fresh_db, "alter table public.t add column note text",
        "insert into public.t values (2, 'second', 'has a note')")
    run, _ = tick(cfg, relations)

    assert kinds(run, "public.t") == {"add_nullable_column"}
    (event,) = drifts(run, "public.t")
    assert event["status"] == "ok" and event["extra"]["action"] == "auto_evolved"
    assert dest.read_lifecycle(cfg.registration)[("public", "t")] == STREAMING

    con = duckdb.connect(duckdb_path)
    assert con.execute("select id, v, note from public.t order by id").fetchall() == [
        (1, "first", None), (2, "second", "has a note")]
    con.close()


def test_a_widened_type_is_additive_and_does_not_stop_streaming(
        fresh_db, names, duckdb_path, tmp_path):
    sql(fresh_db, "create table public.t (id int primary key, n smallint)",
        "insert into public.t values (1, 3)")
    relations = [rel("public", "t", "id")]
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path)
    tick(cfg, relations)

    sql(fresh_db, "alter table public.t alter column n type bigint",
        "insert into public.t values (2, 9223372036854775807)")
    run, _ = tick(cfg, relations)

    assert kinds(run, "public.t") == {"type_widen"}
    assert drifts(run, "public.t")[0]["status"] == "ok"
    assert dest.read_lifecycle(cfg.registration)[("public", "t")] == STREAMING
    con = duckdb.connect(duckdb_path)
    assert con.execute("select n from public.t where id = 2").fetchone() == (
        9223372036854775807,)
    con.close()


# --------------------------------------------------------------------------- b · drop column


def test_a_dropped_column_is_reported_but_the_table_keeps_loading(
        fresh_db, names, duckdb_path, tmp_path):
    """dlt never destroys a column, and one ordered slot cannot skip one relation — so the
    break is REPORTED (drift_review) rather than paused (plan Appendix C)."""
    sql(fresh_db, "create table public.t (id int primary key, v text, doomed text)",
        "insert into public.t values (1, 'a', 'still here')")
    relations = [rel("public", "t", "id")]
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path)
    tick(cfg, relations)

    sql(fresh_db, "alter table public.t drop column doomed",
        "insert into public.t values (2, 'b')")
    run, _ = tick(cfg, relations)

    (event,) = drifts(run, "public.t")
    assert event["status"] == "error" and event["extra"]["action"] == "kept_loading"
    assert kinds(run, "public.t") == {"drop_column"}
    assert dest.read_lifecycle(cfg.registration)[("public", "t")] == DRIFT_REVIEW
    assert publication.published_relations(fresh_db, cfg.publication) == {("public", "t")}

    con = duckdb.connect(duckdb_path)
    assert con.execute("select id, v, doomed from public.t order by id").fetchall() == [
        (1, "a", "still here"), (2, "b", None)]
    con.close()

    # the next quiet run does not re-report, and does not quietly clear the review either
    sql(fresh_db, "insert into public.t values (3, 'c')")
    run2, _ = tick(cfg, relations)
    assert drifts(run2, "public.t") == []
    assert dest.read_lifecycle(cfg.registration)[("public", "t")] == DRIFT_REVIEW
    assert len(_rows(duckdb_path)) == 3


def _rows(duckdb_path):
    con = duckdb.connect(duckdb_path)
    try:
        return con.execute("select id from public.t order by id").fetchall()
    finally:
        con.close()


# --------------------------------------------------------------------------- c · type break


def test_an_incompatible_type_change_reports_and_still_lands_the_rows(
        fresh_db, names, duckdb_path, tmp_path):
    """text → integer is not a proven-safe widening, so it is a breaking drift.

    OBSERVED (dlt 1.x → duckdb, 04-Sep-2026), not assumed: dlt creates NO variant column. The
    destination column stays VARCHAR and the incoming integers are COERCED to their text
    rendering ('20'), so nothing is lost and nothing is quarantined — which is exactly why the
    policy reports the break instead of pausing the table. Reversing it (int column, text
    arriving) is the direction that would fail, and the event is what warns about it.
    """
    sql(fresh_db, "create table public.t (id int primary key, v text)",
        "insert into public.t values (1, '10')")
    relations = [rel("public", "t", "id")]
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path)
    tick(cfg, relations)

    sql(fresh_db, "alter table public.t alter column v type integer using v::integer",
        "insert into public.t values (2, 20)")
    run, _ = tick(cfg, relations)

    (event,) = drifts(run, "public.t")
    assert event["status"] == "error"
    assert kinds(run, "public.t") == {"type_incompatible"}
    assert dest.read_lifecycle(cfg.registration)[("public", "t")] == DRIFT_REVIEW

    assert cols(duckdb_path, "public", "t")["v"] == "VARCHAR", "no variant column was created"
    con = duckdb.connect(duckdb_path)
    assert con.execute("select id, v from public.t order by id").fetchall() == [
        (1, "10"), (2, "20")], "a breaking type change must not stop rows landing"
    con.close()


# --------------------------------------------------------------------------- d · key change


def test_a_key_change_removes_only_that_table_from_the_publication(
        fresh_db, names, duckdb_path, tmp_path):
    sql(fresh_db,
        "create table public.t (id int primary key, alt int not null unique, v text)",
        "create table public.other (id int primary key, v text)",
        "insert into public.t values (1, 100, 'a')",
        "insert into public.other values (1, 'x')")
    before = [rel("public", "t", "id"), rel("public", "other", "id")]
    cfg, dest = make(fresh_db, names, duckdb_path, tmp_path)
    tick(cfg, before)

    sql(fresh_db, "alter table public.t drop constraint t_pkey",
        "alter table public.t add primary key (alt)",
        "insert into public.t values (2, 200, 'b')",
        "insert into public.other values (2, 'y')")
    after = [rel("public", "t", "alt"), rel("public", "other", "id")]
    run, result = tick(cfg, after)

    (event,) = drifts(run, "public.t")
    assert event["status"] == "error" and event["extra"]["action"] == "publication_removed"
    assert "resnapshot" in event["extra"]["remedy"] and "public.t" in event["extra"]["remedy"]
    assert kinds(run, "public.t") == {"key_change"}
    assert result["drift_blocked"] == [("public", "t")]
    assert publication.published_relations(fresh_db, cfg.publication) == {("public", "other")}
    assert dest.read_lifecycle(cfg.registration)[("public", "t")] == RESNAPSHOT_REQUIRED
    # the drain continued for everyone else in the same run
    assert dest.read_lifecycle(cfg.registration)[("public", "other")] == STREAMING
    con = duckdb.connect(duckdb_path)
    assert con.execute("select id, v from public.other order by id").fetchall() == [
        (1, "x"), (2, "y")]
    con.close()

    # the block survives the next run: the OLD signature still stands, so it re-detects
    run2, result2 = tick(cfg, after)
    assert result2["drift_blocked"] == [("public", "t")]
    assert publication.published_relations(fresh_db, cfg.publication) == {("public", "other")}
