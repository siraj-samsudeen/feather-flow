"""Publication + replica-identity reconciliation, schema-qualified (#3079 D4/D6)."""
from __future__ import annotations

import psycopg
import pytest

from pg_md_cdc import publication


@pytest.fixture
def two_schemas(fresh_db):
    """`a.t` and `b.t` — the same table NAME in two schemas, which must never collapse."""
    with psycopg.connect(fresh_db, autocommit=True) as c:
        for schema in ("a", "b"):
            c.execute(f'create schema "{schema}"')
            c.execute(f'create table "{schema}"."t" (id int primary key, v text)')
        c.execute('create table public."keep" (id int primary key)')
    return fresh_db


def test_creates_the_publication_and_adds_both_schemas(two_schemas, names):
    pub = names["publication"]
    added, dropped = publication.reconcile(two_schemas, pub, [("a", "t"), ("b", "t")])
    assert added == [("a", "t"), ("b", "t")] and dropped == []
    assert publication.published_relations(two_schemas, pub) == {("a", "t"), ("b", "t")}


def test_reconcile_is_idempotent(two_schemas, names):
    pub = names["publication"]
    publication.reconcile(two_schemas, pub, [("a", "t")])
    added, dropped = publication.reconcile(two_schemas, pub, [("a", "t")])
    assert (added, dropped) == ([], [])


def test_narrowing_drops_only_the_relation_that_left(two_schemas, names):
    pub = names["publication"]
    publication.reconcile(two_schemas, pub, [("a", "t"), ("b", "t"), ("public", "keep")])
    added, dropped = publication.reconcile(two_schemas, pub, [("b", "t"), ("public", "keep")])
    assert added == [] and dropped == [("a", "t")]
    assert publication.published_relations(two_schemas, pub) == {("b", "t"), ("public", "keep")}


def test_plan_only_mutates_nothing(two_schemas, names):
    pub = names["publication"]
    added, dropped = publication.reconcile(two_schemas, pub, [("a", "t")], plan_only=True)
    assert added == [("a", "t")]
    assert not publication.publication_exists(two_schemas, pub)


def test_replica_identity_is_diff_based_and_plannable(two_schemas, names):
    rels = [("a", "t"), ("b", "t")]
    planned = publication.ensure_replica_identity(two_schemas, rels, "full", plan_only=True)
    assert planned == [("a", "t"), ("b", "t")]
    with psycopg.connect(two_schemas, autocommit=True) as c:
        assert [r[0] for r in c.execute(
            "select relreplident from pg_class c join pg_namespace n on n.oid=c.relnamespace "
            "where n.nspname in ('a','b') and c.relname='t'").fetchall()] == ["d", "d"]
    altered = publication.ensure_replica_identity(two_schemas, rels, "full")
    assert altered == [("a", "t"), ("b", "t")]
    # second call is a read-only no-op — the ALTER takes an exclusive lock
    assert publication.ensure_replica_identity(two_schemas, rels, "full") == []


def test_a_quoted_identifier_survives_reconciliation(fresh_db, names):
    pub = names["publication"]
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute('create schema "Odd Schema"')
        c.execute('create table "Odd Schema"."select" (id int primary key)')
    added, _ = publication.reconcile(fresh_db, pub, [("Odd Schema", "select")])
    assert added == [("Odd Schema", "select")]
    assert publication.published_relations(fresh_db, pub) == {("Odd Schema", "select")}


def test_drop_publication_is_idempotent(fresh_db, names):
    publication.drop_publication(fresh_db, names["publication"])
    publication.reconcile(fresh_db, names["publication"], [])
    publication.drop_publication(fresh_db, names["publication"])
    assert not publication.publication_exists(fresh_db, names["publication"])


def test_replica_identity_default_leaves_a_source_table_alone(two_schemas, names):
    """`replica_identity: default` is the manifest saying "do not touch my source tables".
    Applying FULL anyway would take an ACCESS EXCLUSIVE lock on a live table and multiply its
    UPDATE/DELETE WAL volume — the two costs `plan` exists to show before a run."""
    rels = [("a", "t"), ("b", "t")]
    assert publication.ensure_replica_identity(two_schemas, rels, "default") == []
    publication.ensure_replica_identity(two_schemas, rels, "full")
    # and back: `default` on a FULL table is the one ALTER this mode does propose
    assert publication.ensure_replica_identity(two_schemas, rels, "default") == rels
    with psycopg.connect(two_schemas, autocommit=True) as c:
        assert [r[0] for r in c.execute(
            "select relreplident from pg_class c join pg_namespace n on n.oid=c.relnamespace "
            "where n.nspname in ('a','b') and c.relname='t'").fetchall()] == ["d", "d"]


@pytest.fixture
def generated_column(fresh_db):
    """A STORED generated column, as Featherbase's `has_role.person` (person-rename 0101)."""
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute("create table public.g (id int primary key, kind text, v text, "
                  "person text generated always as (case when kind = 'P' then v end) stored)")
        c.execute("insert into public.g values (1, 'P', 'a')")
    return fresh_db


def _server_version(dsn: str) -> int:
    with psycopg.connect(dsn) as c:
        return c.info.server_version


def test_full_identity_table_with_generated_column_stays_updatable(generated_column, names):
    # PG 18 refuses an UPDATE on a published FULL-identity table whose generated columns are
    # not published ("Replica identity must not contain unpublished generated columns").
    pub = names["publication"]
    publication.reconcile(generated_column, pub, [("public", "g")])
    publication.ensure_replica_identity(generated_column, [("public", "g")], "full")
    with psycopg.connect(generated_column, autocommit=True) as c:
        c.execute("update public.g set v = 'b' where id = 1")
        assert c.execute("select person from public.g").fetchone()[0] == "b"


def test_an_existing_publication_is_altered_to_publish_generated_columns(generated_column, names):
    if _server_version(generated_column) < 180000:
        pytest.skip("publish_generated_columns is PostgreSQL 18+")
    pub = names["publication"]
    with psycopg.connect(generated_column, autocommit=True) as c:
        c.execute(f'create publication "{pub}" with (publish = \'insert, update, delete, truncate\')')
    publication.reconcile(generated_column, pub, [("public", "g")])
    with psycopg.connect(generated_column) as c:
        assert c.execute("select pubgencols from pg_publication where pubname = %s",
                         (pub,)).fetchone()[0] == "s"


def test_helper_reinit_upgrades_an_existing_publication(generated_column, names):
    """`init_replication(reset=False)` reaches create_publication on a publication made before
    the fix; it must converge it too, not just skip on 'already exists'."""
    if _server_version(generated_column) < 180000:
        pytest.skip("publish_generated_columns is PostgreSQL 18+")
    import psycopg2
    from pg_replication.helpers import create_publication

    pub = names["publication"]
    with psycopg.connect(generated_column, autocommit=True) as c:
        c.execute(f'create publication "{pub}" for table public.g')
        c.execute("alter table public.g replica identity full")
    conn = psycopg2.connect(generated_column)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            create_publication(pub, cur)
    finally:
        conn.close()
    with psycopg.connect(generated_column, autocommit=True) as c:
        c.execute("update public.g set v = 'b' where id = 1")
        assert c.execute("select pubgencols from pg_publication where pubname = %s",
                         (pub,)).fetchone()[0] == "s"
