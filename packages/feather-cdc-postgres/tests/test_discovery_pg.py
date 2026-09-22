"""Catalog discovery + compilation against a real Postgres (#3079 D2/D4/D6/D7)."""
from __future__ import annotations

import psycopg
import pytest

from pg_md_cdc import discovery, schema_registry as sr, selectors as sel
from pg_md_cdc.manifest import parse


def _ddl(dsn, *statements):
    with psycopg.connect(dsn, autocommit=True) as c:
        for s in statements:
            c.execute(s)


def _m(**over):
    raw = {
        "api_version": "pg-md-cdc/v1",
        "source_id": "demo",
        "source": {"dsn_env": "DEMO_PG_DSN", "include": {"schemas": ["*"]}},
        "destination": {"token_env": "MOTHERDUCK_TOKEN", "database": "bronze_demo"},
        "replication": {"slot": "demo_slot", "publication": "demo_pub"},
        "control": {"extractor": "demo"},
    }
    for k, v in over.items():
        raw[k] = {**raw[k], **v} if isinstance(v, dict) and isinstance(raw.get(k), dict) else v
    return parse(raw)


def test_two_schemas_with_the_same_table_name_stay_separate(fresh_db):
    _ddl(
        fresh_db,
        "create schema a", "create schema b",
        "create table a.t (id int primary key, v text)",
        "create table b.t (id int primary key, v text)",
    )
    rels = discovery.discover(fresh_db)
    assert [(r.schema, r.table) for r in rels] == [("a", "t"), ("b", "t")]
    plan = sel.compile(_m(), rels)
    assert [r.target for r in plan.relations] == ["a.t", "b.t"]
    assert len({r.target for r in plan.relations}) == 2


def test_composite_primary_key_and_replica_identity_are_read(fresh_db):
    _ddl(
        fresh_db,
        "create table public.t (a int not null, b int not null, v text, primary key (a, b))",
        "alter table public.t replica identity full",
    )
    (rel,) = discovery.discover(fresh_db)
    assert rel.primary_key == ("a", "b")
    assert rel.replica_identity == "f"
    plan = sel.compile(_m(replication={"replica_identity": "full"}), [rel])
    assert plan.relations[0].key == ("a", "b")
    assert plan.replica_identity_alters == []  # already FULL — no ALTER proposed


def test_a_table_without_a_key_is_rejected_by_default_and_kept_when_append_only(fresh_db):
    _ddl(fresh_db, "create table public.events (v text, at timestamptz)")
    rels = discovery.discover(fresh_db)
    rejected = sel.compile(_m(), rels)
    assert rejected.relations == []
    assert "no_key_rejected" in {p.code for p in rejected.problems if p.severity == "error"}
    allowed = sel.compile(_m(replication={"tables_without_key": "append_only"}), rels)
    assert [(r.table, r.key_policy) for r in allowed.relations] == [("events", sel.APPEND_ONLY)]


def test_a_not_null_unique_index_becomes_the_merge_key(fresh_db):
    _ddl(
        fresh_db,
        "create table public.t (code text not null, v text)",
        "create unique index uq_code on public.t (code)",
        "alter table public.t replica identity full",
    )
    (rel,) = discovery.discover(fresh_db)
    assert [i.name for i in rel.unique_indexes] == ["uq_code"]
    plan = sel.compile(_m(replication={"replica_identity": "full"}), [rel])
    assert (plan.relations[0].key, plan.relations[0].key_policy, plan.relations[0].key_index) == (
        ("code",), sel.UNIQUE_INDEX, "uq_code",
    )


def test_a_partial_unique_index_is_not_offered_as_a_key(fresh_db):
    """A partial unique index guarantees uniqueness only over its predicate, so merging on it
    would silently overwrite rows outside it."""
    _ddl(
        fresh_db,
        "create table public.t (code text not null, live bool not null, v text)",
        "create unique index uq_live on public.t (code) where live",
    )
    (rel,) = discovery.discover(fresh_db)
    assert rel.unique_indexes == ()
    assert sel.compile(_m(), [rel]).relations == []


def test_an_undeclared_type_surfaces_a_named_warning(fresh_db):
    _ddl(fresh_db, "create table public.t (id int primary key, addr inet, where_at point, tags text[])")
    (rel,) = discovery.discover(fresh_db)
    cols, problems = sr.compile_relation(rel, ("id",))
    text = " ".join(p.message for p in problems)
    assert "public.t.addr" in text and "public.t.where_at" in text and "public.t.tags" in text
    assert {c.name for c in cols if c.compiled.mapping == sr.TEXT_FALLBACK} == {"addr", "where_at", "tags"}
    assert {c.name: c.compiled.dest_type for c in cols}["id"] == "bigint"


def test_arrays_are_detected_from_the_catalog(fresh_db):
    _ddl(fresh_db, "create table public.t (id int primary key, tags text[])")
    (rel,) = discovery.discover(fresh_db)
    assert rel.column("tags").is_array is True and rel.column("id").is_array is False


def test_deny_matches_a_real_relation(fresh_db):
    _ddl(fresh_db, "create table public.access_token (id int primary key)", "create table public.ok (id int primary key)")
    m = _m(source={"dsn_env": "DEMO_PG_DSN", "include": {"schemas": ["*"]}, "deny": {"tables": ["public.access_token"]}})
    plan = sel.compile(m, discovery.discover(fresh_db))
    assert plan.denied_hits == ["public.access_token"]
    assert [r.table for r in plan.relations] == ["ok"]


def test_system_schemas_are_never_discovered(fresh_db):
    assert discovery.discover(fresh_db) == []


def test_publication_and_slot_state(fresh_db, names):
    _ddl(
        fresh_db,
        "create table public.t (id int primary key)",
        f'create publication "{names["publication"]}" for table public.t',
    )
    assert discovery.publication_relations(fresh_db, names["publication"]) == {("public", "t")}
    assert discovery.publication_relations(fresh_db, "no_such_pub") is None

    absent = discovery.slot_state(fresh_db, names["slot"])
    assert absent["exists"] is False
    assert absent["max_slot_wal_keep_size_bytes"] is None or isinstance(absent["max_slot_wal_keep_size_bytes"], int)

    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("select pg_create_logical_replication_slot(%s, 'pgoutput')", (names["slot"],))
    live = discovery.slot_state(fresh_db, names["slot"])
    assert live["exists"] is True and live["wal_status"] == "reserved" and live["lag_bytes"] >= 0


def test_featherbase_adapter_reproduces_the_pilot_catalog_rules(fresh_db):
    """Every filter the #2851 drainer applies, exercised at once: physical name derivation,
    kind filter, data_source filter, module allow-list, the always-on catalog tables, and the
    declared-but-not-materialised skip. A regression here silently changes what gets replicated."""
    from pg_md_cdc.adapters.featherbase import select

    _ddl(
        fresh_db,
        'create table public.table_def (id serial primary key, "name" text, module text, '
        "data_source text, kind text, description text)",
        "create table public.column_def (id int primary key)",
        "create table public.access_log (id int primary key)",
        "create table public.comment (id int primary key)",
        "create table public.reflected (id int primary key)",
        "create table public.settings_eav (id int primary key)",
        "create table public.other_module (id int primary key)",
        """insert into public.table_def ("name", module, data_source, kind, description) values
             ('Access Log', 'Core', null, 'table', 'Audit trail.'),
             ('Comment', 'Core', null, 'sub_table', null),
             ('Reflected', 'Core', 'sap', 'table', null),
             ('Settings Eav', 'Core', null, 'settings', null),
             ('Other Module', 'Billing', null, 'table', null),
             ('Not Yet Built', 'Core', null, 'table', null),
             ('Table', null, null, 'table', null),
             ('Column', null, null, 'table', null)""",
    )
    every = select(fresh_db, {})
    assert [r.table for r in every] == [
        "access_log", "column_def", "comment", "other_module", "table_def",
    ]
    assert {r.table: r.description for r in every}["access_log"] == "Audit trail."
    assert {r.table: r.module for r in every}["other_module"] == "Billing"

    core_only = select(fresh_db, {"modules": ["Core"]})
    # the catalog itself rides along whatever the allow-list says
    assert [r.table for r in core_only] == ["access_log", "column_def", "comment", "table_def"]


def test_a_covering_unique_index_keys_on_its_KEY_columns_only(fresh_db):
    """`unique (a) include (payload)` stores both attnums in pg_index.indkey; only the first
    `indnkeyatts` are the key. Taking all of them would merge on (a, payload), and every UPDATE
    of `payload` would then INSERT a duplicate instead of replacing the row (#3079 review)."""
    _ddl(
        fresh_db,
        "create table public.t (a int not null, payload text not null, v text)",
        "create unique index t_a_uq on public.t (a) include (payload)",
    )
    (rel,) = discovery.discover(fresh_db)
    assert [idx.columns for idx in rel.unique_indexes] == [("a",)]
    plan = sel.compile(_m(replication={"replica_identity": "full"}), [rel])
    assert plan.relations[0].key == ("a",)
    assert plan.relations[0].key_policy == sel.UNIQUE_INDEX


def test_a_partitioned_parent_is_reported_and_its_leaves_are_selected(fresh_db):
    """`pg_publication_tables` expands a partitioned parent to its leaves and pgoutput emits
    leaf identity, so a parent in the desired set is re-added every tick and the parent's
    destination copy would stop receiving rows. Leaves only; the parent is a named warning."""
    _ddl(
        fresh_db,
        "create table public.events (id int, day date, v text, primary key (id, day)) "
        "partition by range (day)",
        "create table public.events_2026 partition of public.events "
        "for values from ('2026-01-01') to ('2027-01-01')",
        "create table public.events_2027 partition of public.events "
        "for values from ('2027-01-01') to ('2028-01-01')",
        "create table public.plain (id int primary key)",
    )
    rels = discovery.discover(fresh_db)
    kinds = {r.table: (r.kind, r.parent) for r in rels}
    assert kinds == {"events": ("p", None), "events_2026": ("r", "public.events"),
                     "events_2027": ("r", "public.events"), "plain": ("r", None)}
    plan = sel.compile(_m(), rels)
    assert [r.table for r in plan.relations] == ["events_2026", "events_2027", "plain"]
    (warning,) = [p for p in plan.problems if p.code == "partitioned_parent_skipped"]
    assert warning.severity == "warning"
    assert "public.events_2026" in warning.message and "public.events_2027" in warning.message
    assert plan.relations[0].key == ("id", "day")


def test_featherbase_adapter_two_way_rule_selects_user_tables_and_only_named_system_tables(fresh_db):
    """D-13 (Siraj, 05-Sep-2026): `table_def."system" = false` (user-created) always flows,
    `"system" = true` (Featherbase's own) only when named in `options.system_tables` — the
    catalog tables included. Without the option, today's behaviour (everything) is unchanged."""
    from pg_md_cdc.adapters import NAMED_SYSTEM, USER_CREATED
    from pg_md_cdc.adapters.featherbase import select

    _ddl(
        fresh_db,
        'create table public.table_def (id serial primary key, "name" text, module text, '
        'data_source text, kind text, description text, "system" boolean not null default false)',
        "create table public.column_def (id int primary key)",
        "create table public.role (id int primary key)",
        "create table public.background_job (id int primary key)",
        "create table public.share (id int primary key)",
        "create table public.test (id int primary key)",
        "create table public.invoice (id int primary key)",
        """insert into public.table_def ("name", module, data_source, kind, description, "system") values
             ('Role', 'Core', null, 'table', null, true),
             ('Background Job', 'Core', null, 'table', null, true),
             ('Share', 'Core', null, 'table', null, true),
             ('Test', 'Custom', null, 'table', null, false),
             ('Invoice', 'Custom', null, 'table', null, false),
             ('Not Yet Built', 'Custom', null, 'table', null, false),
             ('Table', 'Core', null, 'table', null, true),
             ('Column', 'Core', null, 'sub_table', null, true)""",
    )
    two_way = select(fresh_db, {"system_tables": ["table_def", "column_def", "role"]})
    assert {r.table: r.origin for r in two_way} == {
        "column_def": NAMED_SYSTEM, "invoice": USER_CREATED, "role": NAMED_SYSTEM,
        "table_def": NAMED_SYSTEM, "test": USER_CREATED}
    # `share` and `background_job` are system tables not named → out; the catalog is no longer
    # unconditional: drop it from the list and it goes too.
    without_catalog = select(fresh_db, {"system_tables": ["role"]})
    assert [r.table for r in without_catalog] == ["invoice", "role", "test"]
    # a name in the list that is user-created anyway, or not in the catalog, changes nothing
    assert [r.table for r in select(fresh_db, {"system_tables": ["role", "test", "no_such"]})] == [
        "invoice", "role", "test"]
    # user-created tables still honour the module allow-list; named system tables ride along
    assert [r.table for r in select(fresh_db, {"system_tables": ["role"], "modules": ["Core"]})] == ["role"]

    legacy = select(fresh_db, {})
    assert [r.table for r in legacy] == ["background_job", "column_def", "invoice", "role", "share",
                                         "table_def", "test"]
    assert {r.origin for r in legacy} == {None}


def test_featherbase_adapter_refuses_system_tables_without_the_system_column(fresh_db):
    """With no flag every row would read as user-created and the allow-list would admit every
    platform table — the opposite of what the option promises. Fail, never leak."""
    from pg_md_cdc.adapters.featherbase import select

    _ddl(fresh_db,
         'create table public.table_def (id serial primary key, "name" text, module text, '
         "data_source text, kind text)")
    with pytest.raises(RuntimeError, match='"system"'):
        select(fresh_db, {"system_tables": ["table_def"]})
    assert [r.table for r in select(fresh_db, {})] == ["table_def"]   # legacy path unaffected


def test_featherbase_adapter_uses_catalog_physical_relations_across_schemas(fresh_db):
    """#3894: platform Tables moved with the catalog to `featherbase`, while installed apps
    declare their own physical schema/relation. The catalog remains the authority for both."""
    from pg_md_cdc.adapters import NAMED_SYSTEM, USER_CREATED
    from pg_md_cdc.adapters.featherbase import select

    _ddl(
        fresh_db,
        "create schema featherbase",
        "create schema tasker",
        'create table featherbase.table_def ("name" text, module text, data_source text, '
        'kind text, description text, "system" boolean not null, physical_schema text, '
        "physical_relation text)",
        "create table featherbase.column_def (row_id text primary key)",
        "create table featherbase.role (row_id text primary key)",
        "create table featherbase.background_job (row_id text primary key)",
        "create table tasker.task (row_id text primary key)",
        "create table public.site (row_id text primary key)",
        """insert into featherbase.table_def
             ("name", module, data_source, kind, description, "system", physical_schema, physical_relation)
             values
             ('Role', 'Core', null, 'table', null, true, null, null),
             ('Background Job', 'Core', null, 'table', null, true, null, null),
             ('Table', 'Core', null, 'table', null, true, null, null),
             ('Column', 'Core', null, 'sub_table', null, true, null, null),
             ('tasker.task', 'Tasker', null, 'table', 'Shared work.', false, 'tasker', 'task')""",
    )

    selected = select(
        fresh_db,
        {"schema": "featherbase", "system_tables": ["table_def", "column_def", "role"]},
    )
    assert {(r.schema, r.table): r.origin for r in selected} == {
        ("featherbase", "column_def"): NAMED_SYSTEM,
        ("featherbase", "role"): NAMED_SYSTEM,
        ("featherbase", "table_def"): NAMED_SYSTEM,
        ("tasker", "task"): USER_CREATED,
    }
    assert {(r.schema, r.table): r.description for r in selected}[("tasker", "task")] == "Shared work."


@pytest.mark.parametrize(
    "physical_columns, values, message",
    [
        ("physical_schema text", "'tasker'", "both physical_schema and physical_relation"),
        (
            "physical_schema text, physical_relation text",
            "null, null",
            "has no physical_schema/physical_relation",
        ),
    ],
)
def test_featherbase_adapter_refuses_incomplete_current_physical_metadata(
    fresh_db, physical_columns, values, message
):
    """A current non-system catalog row cannot fall back to name derivation: that would look
    for `featherbase.tasker_task` and silently omit the real `tasker.task`."""
    from pg_md_cdc.adapters.featherbase import select

    _ddl(
        fresh_db,
        "create schema featherbase",
        'create table featherbase.table_def ("name" text, module text, data_source text, '
        f'kind text, "system" boolean not null, {physical_columns})',
        f"insert into featherbase.table_def values ('tasker.task', 'Tasker', null, 'table', false, {values})",
    )
    with pytest.raises(RuntimeError, match=message):
        select(fresh_db, {"schema": "featherbase", "system_tables": []})
