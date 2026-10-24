from typing import (
    Optional,
    Dict,
    Mapping,
    Iterator,
    Union,
    List,
    Sequence,
    Any,
    Tuple,
    FrozenSet,
    NamedTuple,
)
from dataclasses import dataclass, field

import psycopg2
from psycopg2.extensions import cursor, connection as ConnectionExt
from psycopg2.extras import (
    LogicalReplicationConnection,
    ReplicationCursor,
    ReplicationMessage,
    StopReplication,
)

import dlt
from dlt.common import logger
from dlt.common.typing import TDataItem
from dlt.common.pendulum import pendulum
from dlt.common.normalizers.naming.snake_case import NamingConvention
from dlt.common.schema.typing import (
    TTableSchema,
    TTableSchemaColumns,
    TColumnNames,
    TWriteDisposition,
    DLT_NAME_PREFIX,
)
from dlt.common.schema.utils import merge_column
from dlt.common.configuration.specs.config_section_context import ConfigSectionContext
from dlt.common.data_writers.escape import escape_postgres_identifier

from dlt.extract.items import DataItemWithMeta
from dlt.extract import DltResource

from dlt.sources.config import with_config
from dlt.sources.credentials import ConnectionStringCredentials
from dlt.sources.sql_database import (
    sql_table as core_sql_table,
    sql_database as core_sql_database,
)

from .schema_types import _to_dlt_column_schema, _to_dlt_val
from .exceptions import IncompatiblePostgresVersionException
from .decoders import (
    Begin,
    Relation,
    Insert,
    Update,
    Delete,
    Truncate,
    ColumnData,
)

__source_name__ = "pg_replication"


@with_config(
    sections=("sources", "pg_replication"),
    sections_merge_style=ConfigSectionContext.resource_merge_style,
    section_arg_name="slot_name",
)
def replication_connection(
    slot_name: str, credentials: ConnectionStringCredentials = dlt.secrets.value
) -> LogicalReplicationConnection:
    """Returns a psycopg2 LogicalReplicationConnection to interact with postgres replication functionality.

    Requires `slot_name` to be passed in order to generate compatible configuraiton section.

    Raises error if the user does not have the REPLICATION attribute assigned.
    """
    return _get_rep_conn(credentials)


@with_config(
    sections=("sources", "pg_replication"),
    sections_merge_style=ConfigSectionContext.resource_merge_style,
    section_arg_name="slot_name",
)
def init_replication(
    slot_name: str = dlt.config.value,
    pub_name: str = dlt.config.value,
    schema_name: str = dlt.config.value,
    table_names: Optional[Union[str, Sequence[str]]] = dlt.config.value,
    credentials: ConnectionStringCredentials = dlt.secrets.value,
    publish: str = "insert, update, delete",
    persist_snapshots: bool = False,
    include_columns: Optional[Mapping[str, Sequence[str]]] = None,
    columns: Optional[Mapping[str, TTableSchemaColumns]] = None,
    reset: bool = False,
) -> Optional[Union[DltResource, List[DltResource]]]:
    """Initializes replication for one, several, or all tables within a schema.

    Can be called repeatedly with the same `slot_name` and `pub_name`:
    - creates a replication slot and publication with provided names if they do not exist yet
    - skips creation of slot and publication if they already exist (unless`reset` is set to `False`)
    - supports addition of new tables by extending `table_names`
    - removing tables is not supported, i.e. exluding a table from `table_names`
      will not remove it from the publication
    - switching from a table selection to an entire schema is possible by omitting
      the `table_names` argument
    - changing `publish` has no effect (altering the published DML operations is not supported)
    - table snapshots can only be persisted on the first call (because the snapshot
      is exported when the slot is created)

    Args:
        slot_name (str): Name of the replication slot to create if it does not exist yet.
        pub_name (str): Name of the publication to create if it does not exist yet.
        schema_name (str): Name of the schema to replicate tables from.
        table_names (Optional[Union[str, Sequence[str]]]):  Name(s) of the table(s)
          to include in the publication. If not provided, the whole schema with `schema_name` will be replicated
          (also tables added to the schema after the publication was created). You need superuser privileges
          for the whole schema replication. When specifying individual table names, the database role must
          own the tables if it's not a superuser.
        credentials (ConnectionStringCredentials): Postgres database credentials.
        publish (str): Comma-separated string of DML operations. Can be used to
          control which changes are included in the publication. Allowed operations
          are `insert`, `update`, and `delete`. `truncate` is currently not
          supported—messages of that type are ignored.
          E.g. `publish="insert"` will create a publication that only publishes insert operations.
        persist_snapshots (bool): Whether the table states in the snapshot exported
          during replication slot creation are persisted to tables. If true, a
          snapshot table is created in Postgres for all included tables, and corresponding
          resources (`DltResource` objects) for these tables are created and returned.
          The resources can be used to perform an initial load of all data present
          in the tables at the moment the replication slot got created.
        include_columns (Optional[Dict[str, Sequence[str]]]): Maps table name(s) to
          sequence of names of columns to include in the snapshot table(s).
          Any column not in the sequence is excluded. If not provided, all columns
          are included. For example:
          ```
          include_columns={
              "table_x": ["col_a", "col_c"],
              "table_y": ["col_x", "col_y", "col_z"],
          }
          ```
          Argument is only used if `persist_snapshots` is `True`.
        columns (Optional[Dict[str, TTableSchemaColumns]]): Maps
          table name(s) to column hints to apply on the snapshot table resource(s).
          For example:
          ```
          columns={
              "table_x": {"col_a": {"data_type": "json"}},
              "table_y": {"col_y": {"precision": 32}},
          }
          ```
          Argument is only used if `persist_snapshots` is `True`.
        reset (bool): If set to True, the existing slot and publication are dropped
          and recreated. Has no effect if a slot and publication with the provided
          names do not yet exist.

    Returns:
        - None if `persist_snapshots` is `False`
        - a `DltResource` object or a list of `DltResource` objects for the snapshot
          table(s) if `persist_snapshots` is `True` and the replication slot did not yet exist
    """
    if isinstance(table_names, str):
        table_names = [table_names]

    rep_conn = _get_rep_conn(credentials)
    try:
        with rep_conn.cursor() as cur:
            if reset:
                drop_replication_slot(slot_name, cur)
                drop_publication(pub_name, cur)
            create_publication(pub_name, cur, publish)
            if table_names is None:
                add_schema_to_publication(schema_name, pub_name, cur)
            else:
                add_tables_to_publication(table_names, schema_name, pub_name, cur)

            slot = create_replication_slot(slot_name, cur)
            if persist_snapshots:
                if slot is None:
                    raise RuntimeError(
                        f"Cannot create snapshots because slot {slot_name} is already created."
                    )

                # get list of tables via sql_database if not provided
                if table_names is None:
                    # do not include dlt tables
                    table_names = [
                        table_name
                        for table_name in core_sql_database(
                            credentials, schema=schema_name, reflection_level="minimal"
                        ).resources.keys()
                        if not table_name.lower().startswith(DLT_NAME_PREFIX)
                    ]

                # need separate session to read the snapshot: https://stackoverflow.com/q/75852587
                with _get_conn(credentials) as conn:
                    with conn.cursor() as cur_snap:
                        # set the snapshot to the snaphost of the newly created replication slot
                        cur_snap.execute(
                            f"""
                            START TRANSACTION ISOLATION LEVEL REPEATABLE READ;
                            SET TRANSACTION SNAPSHOT '{slot["snapshot_name"]}';
                        """
                        )
                        snapshot_tables = [
                            (
                                table_name,
                                persist_snapshot_table(
                                    snapshot_name=slot["snapshot_name"],
                                    table_name=table_name,
                                    schema_name=schema_name,
                                    cur=cur_snap,
                                    include_columns=(
                                        None
                                        if include_columns is None
                                        else include_columns.get(table_name)
                                    ),
                                ),
                                _get_pk(cur_snap, table_name, schema_name),
                            )
                            for table_name in table_names
                        ]
                    # commit all tables before creating resources that will reflect them
                    conn.commit()
                snapshot_table_resources = [
                    snapshot_table_resource(
                        snapshot_table_name=snapshot_table_name,
                        schema_name=schema_name,
                        table_name=table_name,
                        write_disposition="append" if publish == "insert" else "merge",
                        columns=None if columns is None else columns.get(table_name),
                        primary_key=primary_key,
                        credentials=credentials,
                    )
                    for table_name, snapshot_table_name, primary_key in snapshot_tables
                ]
                if len(snapshot_table_resources) == 1:
                    return snapshot_table_resources[0]
                return snapshot_table_resources
    except Exception:
        rep_conn.rollback()
        raise
    else:
        rep_conn.commit()
    finally:
        rep_conn.close()
    return None


@dlt.sources.config.with_config(sections=("sources", "pg_replication"))
def get_pg_version(
    cur: cursor = None,
    credentials: ConnectionStringCredentials = dlt.secrets.value,
) -> int:
    """Returns Postgres server version as int."""
    if cur is not None:
        return cur.connection.server_version
    with _get_conn(credentials) as conn:
        return conn.server_version


def create_publication(
    name: str,
    cur: cursor,
    publish: str = "insert, update, delete",
) -> None:
    """Creates a publication for logical replication if it doesn't exist yet.

    An existing publication is left as is, except that on PG 18+ it is made to publish
    stored generated columns.
    Raises error if the user does not have the CREATE privilege for the database.
    """
    esc_name = escape_postgres_identifier(name)
    # PG 18: unpublished stored generated columns make FULL-identity tables un-updatable.
    pg18 = cur.connection.server_version >= 180000
    gencols = ", publish_generated_columns = stored" if pg18 else ""
    try:
        cur.execute(f"CREATE PUBLICATION {esc_name} WITH (publish = '{publish}'{gencols});")
        logger.info(
            f"Successfully created publication {esc_name} with publish = '{publish}'."
        )
    except psycopg2.errors.DuplicateObject:  # the publication already exists
        logger.info(f'Publication "{name}" already exists.')
        if pg18:
            cur.execute("SELECT pubgencols FROM pg_publication WHERE pubname = %s", (name,))
            if cur.fetchone()[0] != "s":
                cur.execute(f"ALTER PUBLICATION {esc_name} SET (publish_generated_columns = stored);")


def add_table_to_publication(
    table_name: str,
    schema_name: str,
    pub_name: str,
    cur: cursor,
) -> None:
    """Adds a table to a publication for logical replication.

    Does nothing if the table is already a member of the publication.
    Raises error if the user is not owner of the table.
    """
    qual_name = _make_qualified_table_name(table_name, schema_name)
    esc_pub_name = escape_postgres_identifier(pub_name)
    try:
        cur.execute(f"ALTER PUBLICATION {esc_pub_name} ADD TABLE {qual_name};")
        logger.info(
            f"Successfully added table {qual_name} to publication {esc_pub_name}."
        )
    except psycopg2.errors.DuplicateObject:
        logger.info(
            f"Table {qual_name} is already a member of publication {esc_pub_name}."
        )


def add_tables_to_publication(
    table_names: Union[str, Sequence[str]],
    schema_name: str,
    pub_name: str,
    cur: cursor,
) -> None:
    """Adds one or multiple tables to a publication for logical replication.

    Calls `add_table_to_publication` for each table in `table_names`.
    """
    if isinstance(table_names, str):
        table_names = table_names
    for table_name in table_names:
        add_table_to_publication(table_name, schema_name, pub_name, cur)


def add_schema_to_publication(
    schema_name: str,
    pub_name: str,
    cur: cursor,
) -> None:
    """Adds a schema to a publication for logical replication if the schema is not a member yet.

    Raises error if the user is not a superuser.
    """
    if (version := get_pg_version(cur)) < 150000:
        raise IncompatiblePostgresVersionException(
            f"Cannot add schema to publication because the Postgres server version {version} is too low."
            " Adding schemas to a publication is only supported for Postgres version 15 or higher."
            " Upgrade your Postgres server version or set the `table_names` argument to explicitly specify table names."
        )
    esc_schema_name = escape_postgres_identifier(schema_name)
    esc_pub_name = escape_postgres_identifier(pub_name)
    try:
        cur.execute(
            f"ALTER PUBLICATION {esc_pub_name} ADD TABLES IN SCHEMA {esc_schema_name};"
        )
        logger.info(
            f"Successfully added schema {esc_schema_name} to publication {esc_pub_name}."
        )
    except psycopg2.errors.DuplicateObject:
        logger.info(
            f"Schema {esc_schema_name} is already a member of publication {esc_pub_name}."
        )


def create_replication_slot(  # type: ignore[return]
    name: str, cur: ReplicationCursor, output_plugin: str = "pgoutput"
) -> Optional[Dict[str, str]]:
    """Creates a replication slot if it doesn't exist yet."""
    try:
        cur.create_replication_slot(name, output_plugin=output_plugin)
        logger.info(f'Successfully created replication slot "{name}".')
        result = cur.fetchone()
        return {
            "slot_name": result[0],
            "consistent_point": result[1],
            "snapshot_name": result[2],
            "output_plugin": result[3],
        }
    except psycopg2.errors.DuplicateObject:  # the replication slot already exists
        logger.info(
            f'Replication slot "{name}" cannot be created because it already exists.'
        )


def drop_replication_slot(name: str, cur: ReplicationCursor) -> None:
    """Drops a replication slot if it exists."""
    try:
        cur.drop_replication_slot(name)
        logger.info(f'Successfully dropped replication slot "{name}".')
    except psycopg2.errors.UndefinedObject:  # the replication slot does not exist
        logger.info(
            f'Replication slot "{name}" cannot be dropped because it does not exist.'
        )


def drop_publication(name: str, cur: ReplicationCursor) -> None:
    """Drops a publication if it exists."""
    esc_name = escape_postgres_identifier(name)
    try:
        cur.execute(f"DROP PUBLICATION {esc_name};")
        logger.info(f"Successfully dropped publication {esc_name}.")
    except psycopg2.errors.UndefinedObject:  # the publication does not exist
        logger.info(
            f"Publication {esc_name} cannot be dropped because it does not exist."
        )


def persist_snapshot_table(
    snapshot_name: str,
    table_name: str,
    schema_name: str,
    cur: cursor,
    include_columns: Optional[Sequence[str]] = None,
) -> str:
    """Persists exported snapshot table state.

    Reads snapshot table content and copies it into new table.
    """
    col_str = "*"
    if include_columns is not None:
        col_str = ", ".join(map(escape_postgres_identifier, include_columns))
    # make sure to shorten identifier
    naming = NamingConvention(63)
    # name must start with _dlt so we skip this table when replicating
    snapshot_table_name = naming.normalize_table_identifier(
        f"_dlt_{table_name}_s_{snapshot_name}"
    )
    snapshot_qual_name = _make_qualified_table_name(snapshot_table_name, schema_name)
    qual_name = _make_qualified_table_name(table_name, schema_name)
    cur.execute(
        f"""
        CREATE TABLE {snapshot_qual_name} AS SELECT {col_str} FROM {qual_name};
        """
    )
    logger.info(f"Successfully persisted snapshot table state in {snapshot_qual_name}.")
    return snapshot_table_name


def snapshot_table_resource(
    snapshot_table_name: str,
    schema_name: str,
    table_name: str,
    primary_key: TColumnNames,
    write_disposition: TWriteDisposition,
    columns: TTableSchemaColumns = None,
    credentials: ConnectionStringCredentials = dlt.secrets.value,
) -> DltResource:
    """Returns a resource for a persisted snapshot table.

    Can be used to perform an initial load of the table, so all data that
    existed in the table prior to initializing replication is also captured.
    """
    resource: DltResource = core_sql_table(
        credentials=credentials,
        table=snapshot_table_name,
        schema=schema_name,
        reflection_level="full_with_precision",
    )
    resource.apply_hints(
        table_name=table_name,
        write_disposition=write_disposition,
        columns=columns,
        primary_key=primary_key,
    )
    return resource


def get_max_lsn(
    slot_name: str,
    options: Dict[str, str],
    credentials: ConnectionStringCredentials,
) -> Optional[int]:
    """Returns maximum Log Sequence Number (LSN) in replication slot.

    Returns None if the replication slot is empty.
    Does not consume the slot, i.e. messages are not flushed.
    Raises error if the replication slot or publication does not exist.
    """
    # comma-separated value string
    options_str = ", ".join(
        f"'{x}'" for xs in list(map(list, options.items())) for x in xs
    )
    with _get_conn(credentials) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT MAX(lsn) - '0/0' AS max_lsn "  # subtract '0/0' to convert pg_lsn type to int (https://stackoverflow.com/a/73738472)
                f"FROM pg_logical_slot_peek_binary_changes('{slot_name}', NULL, NULL, {options_str});"
            )
            lsn: int = cur.fetchone()[0]
        return lsn


def get_pub_ops(
    pub_name: str,
    cur: cursor,
) -> Dict[str, bool]:
    """Returns dictionary of DML operations and their publish status."""

    cur.execute(
        f"""
        SELECT pubinsert, pubupdate, pubdelete, pubtruncate
        FROM pg_publication WHERE pubname = '{pub_name}'
    """
    )
    result = cur.fetchone()
    # credentials.connection.commit()
    if result is None:
        raise ValueError(f'Publication "{pub_name}" does not exist.')
    return {
        "insert": result[0],
        "update": result[1],
        "delete": result[2],
        "truncate": result[3],
    }


def apply_true_primary_key(
    columns: TTableSchemaColumns, pk: TColumnNames
) -> None:
    """Overrides per-column primary_key hints with the table's actual primary key (local
    patch, #2851 — see process_relation: replica-identity flags are not the PK under
    REPLICA IDENTITY FULL, and a whole-row merge key turns updates into duplicates)."""
    pk_set = {pk} if isinstance(pk, str) else set(pk)
    for name, schema in columns.items():
        schema["primary_key"] = name in pk_set


def resolve_unchanged_toast(
    new: List[ColumnData],
    old: Optional[List[ColumnData]],
    table_name: str,
) -> List[ColumnData]:
    """Substitutes unchanged-TOAST columns in an UPDATE's new tuple (local patch, #2851 review).

    pgoutput omits the value of a TOASTed column the UPDATE did not touch, marking it
    category 'u' instead — upstream's `_to_dlt_val` then raises and the drain dies without
    advancing the slot. When the table has REPLICA IDENTITY FULL the old tuple carries the
    real value (unchanged, by definition), so splice it in. Without an old tuple there is
    no way to recover the value in a merge-shaped load — fail loudly with the remedy
    rather than silently NULLing operator data.
    """
    if not any(c.col_data_category == "u" for c in new):
        return new
    if old is None or len(old) != len(new):
        raise ValueError(
            f"table {table_name!r}: UPDATE carries unchanged-TOAST column(s) but no full old"
            " tuple to recover them from. Set REPLICA IDENTITY FULL on the table"
            f' (ALTER TABLE "{table_name}" REPLICA IDENTITY FULL) — this drainer enforces this'
            " each tick; re-run it or apply the ALTER by hand."
        )
    return [o if n.col_data_category == "u" else n for n, o in zip(new, old)]


def lsn_int_to_hex(lsn: int) -> str:
    """Convert integer LSN to postgres' hexadecimal representation."""
    # https://stackoverflow.com/questions/66797767/lsn-external-representation.
    return f"{lsn >> 32 & 4294967295:X}/{lsn & 4294967295:08X}"


def advance_slot(
    upto_lsn: int,
    slot_name: str,
    credentials: ConnectionStringCredentials,
) -> None:
    """Advances position in the replication slot.

    Flushes all messages upto (and including) the message with LSN = `upto_lsn`.
    This function is used as alternative to psycopg2's `send_feedback` method, because
    the behavior of that method seems odd when used outside of `consume_stream`.
    """
    if upto_lsn != 0:
        with _get_conn(credentials) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT * FROM pg_replication_slot_advance('{slot_name}', '{lsn_int_to_hex(upto_lsn)}');"
                )


def _get_conn(
    credentials: ConnectionStringCredentials,
    connection_factory: Optional[Any] = None,
) -> ConnectionExt:
    """Returns a psycopg2 connection to interact with postgres."""
    conn = psycopg2.connect(
        dsn=credentials.to_native_representation(),
        connection_factory=connection_factory,
    )
    if connection_factory is not LogicalReplicationConnection:
        conn.autocommit = False
    return conn  # type: ignore[no-any-return]


def _get_rep_conn(
    credentials: ConnectionStringCredentials,
) -> LogicalReplicationConnection:
    """Returns a psycopg2 LogicalReplicationConnection to interact with postgres replication functionality.

    Raises error if the user does not have the REPLICATION attribute assigned.
    """
    return _get_conn(credentials, LogicalReplicationConnection)  # type: ignore[return-value]


def _make_qualified_table_name(table_name: str, schema_name: str) -> str:
    """Escapes and combines a schema and table name."""
    return (
        escape_postgres_identifier(schema_name)
        + "."
        + escape_postgres_identifier(table_name)
    )


def _get_pk(
    cur: cursor,
    table_name: str,
    schema_name: str,
) -> Optional[TColumnNames]:
    """Returns primary key column(s) for postgres table.

    Returns None if no primary key columns exist.
    """
    qual_name = _make_qualified_table_name(table_name, schema_name)
    # https://wiki.postgresql.org/wiki/Retrieve_primary_key_columns
    cur.execute(
        f"""
        SELECT a.attname
        FROM   pg_index i
        JOIN   pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
        WHERE  i.indrelid = '{qual_name}'::regclass
        AND    i.indisprimary;
    """
    )
    result = [tup[0] for tup in cur.fetchall()]
    if len(result) == 0:
        return None
    elif len(result) == 1:
        return result[0]  # type: ignore[no-any-return]
    return result


class ReviewedLayout(NamedTuple):
    """Local patch (#4346, Codex round 17): a relation's published columns exactly as a
    pgoutput Relation message describes them, plus what the message cannot carry.

    `columns`: ((attnum, name, type oid, typmod, replica-identity key flag), ...) in attnum
    order — the message lists the same columns in the same order, minus the attnum.
    `hidden`: attnums of live columns pgoutput does not publish (generated columns before
    PG 18, virtual ones on 18). The message omits them, so a hidden column that becomes
    published (`ALTER COLUMN ... DROP EXPRESSION`) is caught by the catalogue, not the message."""
    columns: Tuple[Tuple[int, str, int, int, int], ...]
    hidden: FrozenSet[int] = frozenset()

    def described(self) -> List[Tuple[str, int, int, int]]:
        """What a Relation message of this layout must say, column by column."""
        return [(n, t, m, k) for _a, n, t, m, k in self.columns]

    def to_json(self) -> Dict[str, Any]:
        return {"columns": [list(c) for c in self.columns], "hidden": sorted(self.hidden)}

    @classmethod
    def from_json(cls, raw: Any) -> Optional["ReviewedLayout"]:
        """None for anything but the full description (an older [[attnum, name], ...] row
        cannot be compared exactly, so the relation has no reviewed layout: quarantined)."""
        if not isinstance(raw, dict) or "columns" not in raw:
            return None
        return cls(tuple((int(a), str(n), int(t), int(m), int(k)) for a, n, t, m, k in raw["columns"]),
                   frozenset(int(a) for a in raw.get("hidden", ())))


def published_shape(cur: Any, relid: int) -> Tuple[ReviewedLayout, bool]:
    """Local patch (#4346): this relation's `ReviewedLayout` in whatever snapshot `cur` reads,
    and whether the table has EVER dropped a column (`attisdropped` rows stay for good).
    Stored generated columns are published on PG 18 (publication.py), virtual ones never.
    The key flag is pgoutput's: every column under REPLICA IDENTITY FULL, the identity index's
    columns under DEFAULT (the primary key) or USING INDEX, none otherwise.
    Works on a psycopg2 or a psycopg 3 cursor."""
    cur.execute("select current_setting('server_version_num')::int >= 180000")
    pg18 = bool(cur.fetchone()[0])
    published = "a.attgenerated in ('', 's')" if pg18 else "a.attgenerated = ''"
    cur.execute(
        f"""select a.attnum, a.attname, a.atttypid::bigint, a.atttypmod, a.attisdropped,
                   ({published}) as published,
                   case when c.relreplident = 'f' then 1
                        when exists (select 1 from pg_index i
                                     where i.indrelid = c.oid and a.attnum = any(i.indkey)
                                       and ((c.relreplident = 'd' and i.indisprimary)
                                            or (c.relreplident = 'i' and i.indisreplident)))
                        then 1 else 0 end
            from pg_attribute a join pg_class c on c.oid = a.attrelid
            where a.attrelid = %s and a.attnum > 0 order by a.attnum""", (relid,))
    rows = cur.fetchall()
    columns = tuple((int(a), n, int(t), int(m), int(k))
                    for a, n, t, m, dropped, pub, k in rows if not dropped and pub)
    hidden = frozenset(int(a) for a, _n, _t, _m, dropped, pub, _k in rows
                       if not dropped and not pub)
    return ReviewedLayout(columns, hidden), any(r[4] for r in rows)


@dataclass
class ItemGenerator:
    credentials: ConnectionStringCredentials
    slot_name: str
    options: Dict[str, str]
    upto_lsn: int
    start_lsn: int = 0
    target_batch_size: int = 1000
    include_columns: Optional[Dict[str, Sequence[str]]] = (None,)  # type: ignore[assignment]
    columns: Optional[Dict[str, TTableSchemaColumns]] = (None,)  # type: ignore[assignment]
    # (schema, table) -> primary key columns (#3079): saves one catalog round-trip per
    # Relation message and keeps relation identity schema-qualified.
    keys_by_relation: Optional[Dict[Any, Sequence[str]]] = None
    # (schema, table) -> key policy; "append_only" turns the CDC merge key into `lsn`.
    policies_by_relation: Optional[Dict[Any, str]] = None
    # (schema, table) -> boundary LSN of that relation's current baseline (#3079 review): a
    # transaction with commit LSN below it is already in the baseline and is dropped here.
    baseline_lsn_by_relation: Optional[Dict[Any, int]] = None
    # Local patch (#4346): (schema, table) -> columns that never leave the CDC process.
    exclude_columns: Optional[Dict[Any, Sequence[str]]] = None
    # Local patch (#4346): relation OID -> denied columns. SHARED and MUTATED (the caller passes
    # one dict to every batch of a drain): a deny follows the table's OID through a rename.
    exclude_columns_by_relid: Optional[Dict[int, Any]] = None
    # Local patch (#4346, Sonnet F1): (schema, table) -> the columns its last reviewed signature
    # names. On a relation that carries a deny, any other column is withheld until reviewed.
    approved_columns: Optional[Dict[Any, Any]] = None
    # relation OID -> reviewed columns (the signature's `oid`), and relation OID -> denied
    # attnums. Both SHARED across the drain's batches and follow a table rename.
    approved_by_relid: Optional[Dict[int, Any]] = None
    exclude_attnums_by_relid: Optional[Dict[int, Any]] = None
    # relation OID -> `ReviewedLayout` (#4346), recorded by the relation's baseline under its
    # locks. A description of a deny-carrying relation must match it exactly. Read-only.
    reviewed_layout_by_relid: Optional[Dict[int, Any]] = None
    # OIDs whose deny was bound at a trusted moment (run start, or a baseline under its locks).
    # None = not tracked (library use). SHARED, not mutated.
    bound_relids: Optional[Any] = None
    # OIDs whose changes are skipped (quarantined) — seeded with the persisted marks, SHARED and
    # MUTATED, so a quarantine holds across every batch of the drain.
    quarantined_relids: Optional[set] = None
    last_commit_lsn: Optional[int] = field(default=None, init=False)
    generated_all: bool = False
    # TRUNCATEs seen in this batch, in WAL order: {namespace, table, lsn, commit_ts}.
    truncates: List[Dict[str, Any]] = field(default_factory=list, init=False)

    def collect(self) -> Dict[str, List[Union[TDataItem, DataItemWithMeta]]]:
        """Consumes one batch and returns it grouped by SOURCE SCHEMA (#3079): two schemas
        may hold the same table name, and each source schema loads through its own pipeline."""
        consumer = self._consume()
        grouped: Dict[str, List[Union[TDataItem, DataItemWithMeta]]] = {}
        # One group per relation DESCRIPTION, in WAL order (#4345): a relation renamed or
        # altered mid-batch has a group per description, each with its own name and hints.
        for _rel_id, schema, data_items in consumer.groups:
            table_name = schema["name"]
            namespace = schema.get("namespace") or ""
            if table_name.lower().startswith(DLT_NAME_PREFIX):
                continue  # dlt internal table on the source
            if len(data_items) < 2:
                # Local patch (#4338): no rows, only the hints item — every change of this
                # relation in the batch was below its baseline boundary (or truncated away).
                # Loading the hints alone records `lsn`/`deleted_ts` in the dlt schema without
                # creating them, and the relation's next real merge then fails on `lsn`.
                continue
            bucket = grouped.setdefault(namespace, [])
            bucket.append(data_items[0])  # meta item with column hints only, no data
            bucket.append(dlt.mark.with_table_name(data_items[1:], table_name))
        return grouped

    def _consume(self) -> "MessageConsumer":
        """Yields replication messages from MessageConsumer.

        Starts replication of messages published by the publication from the replication slot.
        Maintains LSN of last consumed Commit message in object state.
        Does not advance the slot.
        """
        # Local patch (#3079 review): `cur` must exist before the try, or a failure to OPEN the
        # replication connection — the slot already having a consumer, or an unreachable source —
        # dies in `finally` with `UnboundLocalError: cur` and hides the real error from the log.
        cur = None
        try:
            cur = _get_rep_conn(self.credentials).cursor()
            pub_ops = get_pub_ops(self.options["publication_names"], cur)
            cur.start_replication(
                slot_name=self.slot_name,
                start_lsn=self.start_lsn,
                decode=False,
                options=self.options,
            )
            consumer = MessageConsumer(
                upto_lsn=self.upto_lsn,
                pub_ops=pub_ops,
                target_batch_size=self.target_batch_size,
                include_columns=self.include_columns,
                columns=self.columns,
                credentials=self.credentials,  # for true-PK resolution (#2851 patch)
                keys_by_relation=self.keys_by_relation,
                policies_by_relation=self.policies_by_relation,
                baseline_lsn_by_relation=self.baseline_lsn_by_relation,
                exclude_columns=self.exclude_columns,  # Local patch (#4346)
                exclude_columns_by_relid=self.exclude_columns_by_relid,
                approved_columns=self.approved_columns,
                approved_by_relid=self.approved_by_relid,
                exclude_attnums_by_relid=self.exclude_attnums_by_relid,
                reviewed_layout_by_relid=self.reviewed_layout_by_relid,
                bound_relids=self.bound_relids,
                quarantined_relids=self.quarantined_relids,
            )
            cur.consume_stream(consumer)
        except StopReplication:  # completed batch or reached `upto_lsn`
            pass
        finally:
            if cur is not None:
                cur.connection.close()
        self.last_commit_lsn = consumer.last_commit_lsn
        self.generated_all = consumer.consumed_all
        self.truncates = consumer.truncates
        self.key_problems = consumer.key_problems
        self.denied_seen = getattr(consumer, "denied_seen", set())  # Local patch (#4346)
        self.unreviewed = getattr(consumer, "unreviewed", {})
        self.quarantined = getattr(consumer, "quarantine_report", {})
        return consumer

    def __iter__(self) -> Iterator[Union[TDataItem, DataItemWithMeta]]:
        """Flat item stream for the single-schema `replication_resource` (upstream shape)."""
        for items in self.collect().values():
            yield from items


class MessageConsumer:
    """Consumes messages from a ReplicationCursor sequentially.

    Generates data item for each `insert`, `update`, and `delete` message.
    Processes in batches to limit memory usage.
    Maintains message data needed by subsequent messages in internal state.
    """

    def __init__(
        self,
        upto_lsn: int,
        pub_ops: Dict[str, bool],
        target_batch_size: int = 1000,
        include_columns: Optional[Dict[str, Sequence[str]]] = None,
        columns: Optional[Dict[str, TTableSchemaColumns]] = None,
        credentials: Optional[ConnectionStringCredentials] = None,
        keys_by_relation: Optional[Dict[Any, Sequence[str]]] = None,
        policies_by_relation: Optional[Dict[Any, str]] = None,
        baseline_lsn_by_relation: Optional[Dict[Any, int]] = None,
        exclude_columns: Optional[Dict[Any, Sequence[str]]] = None,
        exclude_columns_by_relid: Optional[Dict[int, Any]] = None,
        approved_columns: Optional[Dict[Any, Any]] = None,
        approved_by_relid: Optional[Dict[int, Any]] = None,
        exclude_attnums_by_relid: Optional[Dict[int, Any]] = None,
        reviewed_layout_by_relid: Optional[Dict[int, Any]] = None,
        bound_relids: Optional[Any] = None,
        quarantined_relids: Optional[set] = None,
    ) -> None:
        self.keys_by_relation = keys_by_relation or {}
        # Local patch (#4346): (schema, table) -> denied columns, dropped BY NAME from the
        # hints and every data item. Exclusion, not an allow-list: a column added at the source
        # mid-run still flows; `last_table_schema` keeps every column (positional zip).
        self.exclude_columns = exclude_columns or {}
        # relation OID (pgoutput relation_id) -> denied columns, kept by reference: once a
        # relation was seen under a denied name its deny sticks to the OID, so a rename (the
        # new name has no entry) cannot release the column — in this batch or a later one.
        self.exclude_by_relid = (
            exclude_columns_by_relid if exclude_columns_by_relid is not None else {})
        # Local patch (#4346, Sonnet F1): reviewed columns per (schema, table), and the
        # (schema, table) -> columns withheld because no review has seen them yet.
        self.approved_columns = approved_columns or {}
        self.approved_by_relid = approved_by_relid if approved_by_relid is not None else {}
        self.exclude_attnums = (
            exclude_attnums_by_relid if exclude_attnums_by_relid is not None else {})
        self.unreviewed: Dict[Any, List[str]] = {}
        self.reviewed_layout = (
            reviewed_layout_by_relid if reviewed_layout_by_relid is not None else {})
        self.bound_relids = None if bound_relids is None else frozenset(bound_relids)
        # Relation ids whose changes are skipped — sticky: once a row of a relation was met
        # under an unprovable description, nothing more of it loads until a resnapshot.
        self.quarantined_relids: set = (
            quarantined_relids if quarantined_relids is not None else set())
        # relation id -> why its CURRENT description is unprovable. It becomes a quarantine
        # only when a change past the relation's baseline arrives under it (a description
        # older than a fresh baseline carries no rows to load, and must not re-flag it).
        self._unproven: Dict[int, str] = {}
        # relation id -> (schema, table, why), for the runner to report and persist. Keyed by
        # the OID (Codex round 17): two relations met under one name are two quarantines.
        self.quarantine_report: Dict[int, Tuple[str, str, str]] = {}
        # (schema, table) -> compiled key policy (#3079 D-8). "append_only" merges on `lsn`.
        self.policies_by_relation = policies_by_relation or {}
        self.baseline_lsn_by_relation = baseline_lsn_by_relation or {}
        # Commit LSN of the transaction being decoded (pgoutput Begin.final_lsn): the value the
        # baseline boundary is compared against — never the per-record LSN, which is below the
        # boundary for a transaction that was still in flight when the baseline was taken.
        self._txn_commit_lsn: int = 0
        self.truncates: List[Dict[str, Any]] = []
        # Local patch (#4338): (schema, table) -> why a merge key could not be honoured for a
        # record of this batch. The runner turns each into an error event — never silent.
        self.key_problems: Dict[Any, str] = {}
        self.upto_lsn = upto_lsn
        self.pub_ops = pub_ops
        self.target_batch_size = target_batch_size
        self.include_columns = include_columns
        self.columns = columns
        self.credentials = credentials  # true-PK resolution (#2851 patch)

        self.consumed_all: bool = False
        # data_items attribute maintains all data items
        self.data_items: Dict[
            int, List[Union[TDataItem, DataItemWithMeta]]
        ] = dict()  # maps relation_id to list of data items
        # other attributes only maintain last-seen values
        self.last_table_schema: Dict[
            int, TTableSchema
        ] = dict()  # maps relation_id to table schema
        # Local patch (#4345): (relation_id, table schema, items) per relation DESCRIPTION, in
        # the order the descriptions arrived. `data_items[relation_id]` is the CURRENT group's
        # list; a Relation message mid-batch (a rename, a column change — possibly mid-
        # transaction) closes it here and opens a new one instead of stopping the stream.
        self.groups: List[Any] = []
        self.last_commit_ts: pendulum.DateTime
        self.last_commit_lsn = None

    def __call__(self, msg: ReplicationMessage) -> None:
        """Processes message received from stream."""
        self.process_msg(msg)

    def process_msg(self, msg: ReplicationMessage) -> None:
        """Processes encoded replication message.

        Identifies message type and decodes accordingly.
        Message treatment is different for various message types.
        Breaks out of stream with StopReplication exception — only ever at a Commit — when
        - `upto_lsn` is reached
        - `target_batch_size` is reached
        A table's schema change no longer stops the stream (#4345): it arrives mid-transaction
        whenever rows were changed before the DDL, and a stop there left a partial
        transaction and no commit LSN to restart from.
        """
        op = msg.payload[0]
        if op == 73:  # ASCII for 'I'
            self.process_change(Insert(msg.payload), msg.data_start)
        elif op == 85:  # ASCII for 'U'
            self.process_change(Update(msg.payload), msg.data_start)
        elif op == 68:  # ASCII for 'D'
            self.process_change(Delete(msg.payload), msg.data_start)
        elif op == 66:  # ASCII for 'B'
            begin = Begin(msg.payload)
            self.last_commit_ts = begin.commit_ts  # type: ignore[assignment]
            self._txn_commit_lsn = begin.lsn
        elif op == 67:  # ASCII for 'C'
            self.process_commit(msg)
        elif op == 82:  # ASCII for 'R'
            self.process_relation(Relation(msg.payload))
        elif op == 84:  # ASCII for 'T'
            self.process_truncate(Truncate(msg.payload), msg.data_start)
        else:
            raise ValueError(f"Unknown replication op {op}")

    def process_commit(self, msg: ReplicationMessage) -> None:
        """Updates object state when Commit message is observed.

        Raises StopReplication when `upto_lsn` or `target_batch_size` is reached.
        """
        self.last_commit_lsn = msg.data_start
        if msg.data_start >= self.upto_lsn:
            self.consumed_all = True
        # Every retained group, not just each relation's current one (#4345 review): a
        # relation whose description changed keeps its earlier rows in `groups`, and counting
        # only `data_items` would let a batch outgrow `target_batch_size` without limit.
        n_items = sum(len(items) for _rel_id, _schema, items in self.groups)
        if self.consumed_all or n_items >= self.target_batch_size:
            raise StopReplication

    def process_relation(self, decoded_msg: Relation) -> None:
        """Processes a replication message of type Relation.

        Stores table schema in object state.
        Creates meta item to emit column hints while yielding data.

        Local patch (#4345): a relation that already has items in this batch had its
        description changed (renamed, columns altered). Upstream raised StopReplication here,
        which is NOT a commit boundary — pgoutput sends this message mid-transaction when rows
        were changed before the DDL — so the batch held a partial transaction and, with no
        commit yet, no LSN to restart at: the worker wedged on `None >> 32`. Instead the
        current group is closed with the rows it has (they keep the name and hints they were
        written under) and decoding carries on to the commit under the new description.
        """
        current = self.data_items.get(decoded_msg.relation_id)
        if current is not None and len(current) < 2:
            # Only the hints item, no rows: nothing was written under the old description.
            self.groups = [g for g in self.groups if g[2] is not current]
        # get table schema information from source and store in object state
        table_name = decoded_msg.relation_name
        columns: TTableSchemaColumns = {
            c.name: _to_dlt_column_schema(c) for c in decoded_msg.columns
        }
        # Local patch (#2851 review follow-up): the Relation message's per-column key flag
        # mirrors the REPLICA IDENTITY, not the primary key. With REPLICA IDENTITY FULL —
        # which we set to make unchanged-TOAST updates decodable — EVERY column carries the
        # flag, so the merge key silently became the whole row and every UPDATE landed as a
        # duplicate instead of replacing. Resolve the true primary key from the catalog and
        # override; keep the flags only if the lookup fails (never silently keep a
        # whole-row key when a real PK exists).
        # Declared key first (#3079): the compiled plan already resolved it, schema-qualified.
        pk = self.keys_by_relation.get((decoded_msg.namespace, table_name))
        if pk is not None:
            pk = list(pk) if not isinstance(pk, str) else pk
        key_problem = "merge key unresolved: records merge on replica-identity flags"
        if pk is None and self.credentials is not None:
            try:
                conn = _get_conn(self.credentials)
                try:
                    with conn.cursor() as cur:
                        # The catalog is read NOW, the record was written THEN (#4345 review):
                        # after a rename the name may belong to another table (a swap). Use its
                        # key only if it is still this relation.
                        cur.execute("SELECT to_regclass(%s)::oid", (
                            _make_qualified_table_name(table_name, decoded_msg.namespace),))
                        row = cur.fetchone()
                        oid = row[0] if row else None
                        if oid is not None and int(oid) == decoded_msg.relation_id:
                            pk = _get_pk(cur, table_name, decoded_msg.namespace)
                        elif oid is not None:
                            key_problem = (
                                "merge key unresolved: the name now belongs to another table at "
                                "the source (renamed since the record was written); records "
                                "merge on replica-identity flags")
                finally:
                    conn.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    f"could not resolve primary key of {decoded_msg.namespace}.{table_name}"
                    f" ({exc}); falling back to replica-identity flags as the merge key"
                )
        if pk is not None:
            apply_true_primary_key(columns, pk)
        else:
            # Neither the compiled plan nor the source catalog knows this relation's key (a
            # relation renamed or dropped since the record was written, with no recorded key):
            # its records merge on replica-identity flags — report it (#4338).
            self.key_problems[(decoded_msg.namespace, table_name)] = key_problem

        self.last_table_schema[decoded_msg.relation_id] = {
            "name": table_name,
            "namespace": decoded_msg.namespace,  # #3079: relation identity is (schema, table)
            "columns": columns,
        }

        # apply user input
        # 1) exclude columns
        # Local patch (#4346): looked up by (namespace, table) — the bare name let a projection
        # on a.t reach b.t — and the deny-list is applied on top.
        self._withhold_unreviewed(decoded_msg, table_name)
        include_columns, exclude_columns = self._projection(
            decoded_msg.relation_id, decoded_msg.namespace, table_name)
        if include_columns is not None:
            columns = {k: v for k, v in columns.items() if k in include_columns}
        columns = {k: v for k, v in columns.items() if k not in exclude_columns}
        # 2) override source hints
        column_hints: TTableSchemaColumns = (
            dict() if self.columns is None else self.columns.get(table_name, dict())
        )
        for column_name, column_val in column_hints.items():
            columns[column_name] = merge_column(columns[column_name], column_val)

        # add hints for replication columns
        append_only = (
            self.policies_by_relation.get((decoded_msg.namespace, table_name)) == "append_only"
        )
        columns["lsn"] = {"data_type": "bigint", "nullable": True}
        if self.pub_ops["update"] or self.pub_ops["delete"]:
            columns["lsn"]["dedup_sort"] = "desc"
        if self.pub_ops["delete"]:
            columns["deleted_ts"] = {
                # An append_only relation records arrivals; a DELETE record must not remove
                # the row version it refers to (#3079 D-8).
                "hard_delete": not append_only,
                "data_type": "timestamp",
                "nullable": True,
            }

        # determine write disposition
        write_disposition: TWriteDisposition = "append"
        if self.pub_ops["update"] or self.pub_ops["delete"]:
            write_disposition = "merge"
        if append_only:
            # A keyless relation gets primary_key=[], so a "merge" with no key silently
            # DEGRADES TO APPEND and a batch replayed after a crash lands twice. `lsn` is
            # unique per WAL record, so merging on it makes the replay exact; an UPDATE
            # still lands as a new row version, which is what append_only promises.
            columns["lsn"]["primary_key"] = True
            write_disposition = "merge"

        # include meta item to emit hints while yielding data
        meta_item = dlt.mark.with_hints(
            [],
            dlt.mark.make_hints(
                table_name=table_name,
                write_disposition=write_disposition,
                columns=columns,
            ),
            create_table_variant=True,
        )
        items: List[Union[TDataItem, DataItemWithMeta]] = [meta_item]
        self.data_items[decoded_msg.relation_id] = items
        self.groups.append(
            (decoded_msg.relation_id, self.last_table_schema[decoded_msg.relation_id], items))

    def _current_shape(self, relation_id: int):
        """Local patch (#4346): `published_shape` of this relation in the CURRENT catalogue, or
        None when it cannot be read (then nothing can be proven)."""
        if self.credentials is None:
            return None
        try:
            conn = _get_conn(self.credentials)
            try:
                with conn.cursor() as cur:
                    return published_shape(cur, relation_id)
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 — unknown identity is handled as "quarantine"
            return None

    def _prove_layout(self, relid: int, key, decoded_msg: Relation, exclude, denied_attnums):
        """Local patch (#4346, Codex round 17): (why, layout). `why` says why this description
        cannot be trusted; else `layout` is the relation's reviewed layout, which the
        description matches EXACTLY, so each position is the reviewed attnum at that position.

        The rule, with no inference: the description's columns — count, names in order, type
        OIDs, typmods and replica-identity key flags — must equal the layout the relation's
        baseline recorded under its locks, and the catalogue must still hold every reviewed
        attnum and publish no column the review saw hidden. Anything else, of any kind (an
        added column included), is unproven and quarantines the relation until a resnapshot.

        Why the catalogue half is needed. The message carries no attnums. A dropped attnum is
        never reused and a new column always gets a higher one, so while every reviewed attnum
        is live and nothing hidden surfaced, a message of exactly the reviewed length IS the
        reviewed attnums in order. Drop `email`, rename `password_hash` to `email`, add a new
        `password_hash`: the message reads exactly like the review, and only the dropped
        attnum gives it away. A generated column whose expression is dropped joins the
        published set below a reviewed attnum (Codex round 17) — the count catches it, the
        hidden set too."""
        if self.bound_relids is not None and relid not in self.bound_relids:
            # A table renamed onto a denied name mid-run: which of its columns is the denied
            # one was never bound at a trusted moment (Codex round 16, P1).
            return ("met under a denied name mid-run; its deny was not bound at run start or "
                    "by a baseline, so the denied column's identity is unknown", None)
        layout = self.reviewed_layout.get(relid)
        if layout is None:
            return ("no reviewed layout: the table was not baselined with its deny, so its "
                    "column identities are unknown until a resnapshot", None)
        described = [(c.name, int(c.type_id), int(c.atttypmod), 1 if c.part_of_pkey else 0)
                     for c in decoded_msg.columns]
        if described != layout.described():
            return ("the column description differs from the reviewed layout (a column added, "
                    "dropped, renamed, reordered or retyped, or the replica identity changed): "
                    "any DDL on a table with denied columns needs a resnapshot", None)
        shape = self._current_shape(relid)
        if shape is None:
            return "column identity unreadable (catalogue lookup failed)", None
        current, _ever_dropped = shape
        live = {c[0] for c in current.columns} | set(current.hidden)
        if any(c[0] not in live for c in layout.columns):
            return ("a reviewed column was dropped since the review: identity unprovable",
                    None)
        if layout.hidden & {c[0] for c in current.columns}:
            return ("a column that was hidden at the review (generated) is published now: "
                    "identity unprovable", None)
        attnum_of = {c[1]: c[0] for c in layout.columns}
        denied = set(denied_attnums) | {attnum_of[c] for c in exclude if c in attnum_of}
        for k in self.keys_by_relation.get(key) or ():
            if k in attnum_of and attnum_of[k] in denied:
                return f"merge-key column {k!r} is a denied column", None
        return None, layout

    def _withhold_unreviewed(self, decoded_msg: Relation, table_name: str) -> None:
        """Local patch (#4346): judge a description of a relation that carries a deny. Proven
        (`_prove_layout`): withhold each column whose attnum is denied and each reviewed column
        the stored signature does not approve. Unproven: mark it; a change under it quarantines
        the relation (`process_change`) — withholding columns instead would merge NULLs over
        good bronze values. Merge-key columns are never withheld (a key cannot be denied)."""
        relid = decoded_msg.relation_id
        key = (decoded_msg.namespace or "", table_name)
        self._unproven.pop(relid, None)   # a new description is judged afresh
        _include, exclude = self._projection(relid, decoded_msg.namespace, table_name)
        denied_attnums = set(self.exclude_attnums.get(relid, ()))
        if not exclude and not denied_attnums:
            return
        why, layout = self._prove_layout(relid, key, decoded_msg, exclude, denied_attnums)
        if why:
            self._unproven[relid] = why
            return
        attnum_of = {c[1]: c[0] for c in layout.columns}
        # Bound by POSITION, which is proven — the identity the runner persists (#4346).
        learned = {c: attnum_of[c] for c in exclude if c in attnum_of}
        denied_attnums |= set(learned.values())
        self.exclude_attnums[relid] = frozenset(denied_attnums)
        if not hasattr(self, "denied_seen"):
            self.denied_seen = set()
        self.denied_seen.update((relid, key[0], key[1], c, a) for c, a in learned.items())
        approved = self.approved_by_relid.get(relid)
        if approved is None:
            approved = self.approved_columns.get(key)
            if approved is not None:
                self.approved_by_relid[relid] = approved
        keys = set(self.keys_by_relation.get(key) or ())
        withheld, unreviewed = set(), set()
        for c in decoded_msg.columns:
            n = c.name
            if n in exclude or n in keys:
                continue
            if attnum_of[n] in denied_attnums:
                withheld.add(n)            # the denied column, by its attnum
            elif approved is None or n not in approved:
                withheld.add(n)
                unreviewed.add(n)          # not in the reviewed signature
        if withheld:
            self.exclude_by_relid[relid] = exclude | frozenset(withheld)
        if unreviewed:
            self.unreviewed[key] = sorted(set(self.unreviewed.get(key, [])) | unreviewed)

    def _projection(self, relation_id: int, namespace: str, table_name: str):
        """Local patch (#4346): (include, exclude) for one relation description. Include is
        keyed by (schema, table); exclude is the name's deny UNION the OID's, and the union is
        written back to the OID so it survives a rename."""
        key = (namespace or "", table_name)
        include = None
        include_map = getattr(self, "include_columns", None)
        if include_map is not None:
            # (schema, table) first; the bare name is upstream's documented contract for the
            # single-schema `replication_resource` wrapper, kept so its callers do not widen.
            include = include_map.get(key, include_map.get(table_name))
        by_relid = getattr(self, "exclude_by_relid", None)
        if by_relid is None:
            by_relid = self.exclude_by_relid = {}
        by_name = frozenset(getattr(self, "exclude_columns", {}).get(key, ()))
        exclude = by_name | frozenset(by_relid.get(relation_id, ()))
        if exclude:
            by_relid[relation_id] = exclude
        # (The (oid, schema, table, column, attnum) the runner persists is recorded only by
        # `_withhold_unreviewed`, from a PROVEN position — never by a name lookup that a rename
        # can race, Codex round 16.)
        return include, exclude

    def _relation_key(self, relation_id: int):
        schema = self.last_table_schema[relation_id]
        return schema.get("namespace") or "", schema["name"]

    def _in_baseline(self, relation_id: int) -> bool:
        """True when the current transaction is already in this relation's baseline: its commit
        LSN is below the consistent point the baseline was taken at (strict `<`, the decoder's
        own skip predicate). Loading it again would duplicate an append_only row."""
        boundary = self.baseline_lsn_by_relation.get(self._relation_key(relation_id))
        return boundary is not None and self._txn_commit_lsn < boundary

    def process_truncate(self, decoded_msg: Truncate, msg_start_lsn: int) -> None:
        """Local patch (#3079 review): a source TRUNCATE is data, not a warning. Per relation:
        a keyed mirror must empty — the rows collected so far in this batch are discarded (the
        truncate supersedes them) and the runner deletes the destination rows BEFORE loading
        what follows, so batch order holds; an append_only history keeps every arrival and only
        records the marker. Either way the runner gets a `truncates` entry to act on."""
        for relation_id in decoded_msg.relation_ids:
            if relation_id not in self.last_table_schema:
                continue  # not a published relation of ours (never seen a Relation message)
            if self._in_baseline(relation_id):
                continue
            if relation_id in getattr(self, "quarantined_relids", ()):
                continue   # Local patch (#4346): nothing of a quarantined relation applies
            namespace, table_name = self._relation_key(relation_id)
            keyed = self.policies_by_relation.get((namespace, table_name)) != "append_only"
            if keyed:
                # Every earlier group bound for the truncated DESTINATION name — whichever
                # relation wrote it (#4345): a column change splits a table's rows over two
                # groups, and a rename swap (or drop and re-create) puts another relation's rows
                # under this name; any left would load AFTER the runner's delete and resurrect
                # truncated rows. A group under another name targets another table: kept.
                for _rel_id, schema, items in self.groups:
                    if (schema.get("namespace") or "", schema["name"]) == (namespace, table_name):
                        del items[1:]  # keep the meta item
            self.truncates.append({
                "namespace": namespace, "table": table_name, "lsn": msg_start_lsn,
                "commit_lsn": self._txn_commit_lsn, "commit_ts": self.last_commit_ts,
                "applied": "delete_all" if keyed else "recorded_only",
            })

    def process_change(
        self, decoded_msg: Union[Insert, Update, Delete], msg_start_lsn: int
    ) -> None:
        """Processes replication message of type Insert, Update, or Delete.

        Adds data item for inserted/updated/deleted record to instance attribute.
        """
        if self._in_baseline(decoded_msg.relation_id):
            return
        # Local patch (#4346): a change under an unproven description quarantines the relation,
        # and a quarantine is sticky — nothing more of it loads (this batch, later batches of
        # the drain, later runs via the persisted mark) until a resnapshot re-reviews it.
        relid = decoded_msg.relation_id
        why = getattr(self, "_unproven", {}).get(relid)
        if why is not None and relid not in self.quarantined_relids:
            self.quarantined_relids.add(relid)
            self.quarantine_report[relid] = (*self._relation_key(relid), why)
        if relid in getattr(self, "quarantined_relids", ()):
            return
        table_name = self.last_table_schema[decoded_msg.relation_id]["name"]
        # Local patch (#4346): schema-qualified projection, deny-list included.
        include_columns, exclude_columns = self._projection(
            decoded_msg.relation_id, *self._relation_key(decoded_msg.relation_id))
        if isinstance(decoded_msg, (Insert, Update)):
            column_data = decoded_msg.new_tuple.column_data
            if isinstance(decoded_msg, Update):
                column_data = resolve_unchanged_toast(
                    column_data,
                    decoded_msg.old_tuple.column_data
                    if decoded_msg.optional_tuple_identifier == "O"
                    else None,
                    table_name,
                )
        elif isinstance(decoded_msg, Delete):
            column_data = decoded_msg.old_tuple.column_data
        data_item = self.gen_data_item(
            data=column_data,
            column_schema=self.last_table_schema[decoded_msg.relation_id]["columns"],
            lsn=msg_start_lsn,
            commit_ts=self.last_commit_ts,
            for_delete=isinstance(decoded_msg, Delete),
            include_columns=include_columns,
            exclude_columns=exclude_columns,
        )
        if isinstance(decoded_msg, Update):
            old_key_delete = self._old_key_delete(decoded_msg, data_item, msg_start_lsn)
            if old_key_delete is not None:
                self.data_items[decoded_msg.relation_id].append(old_key_delete)
        self.data_items[decoded_msg.relation_id].append(data_item)

    @staticmethod
    def gen_data_item(
        data: List[ColumnData],
        column_schema: TTableSchemaColumns,
        lsn: int,
        commit_ts: pendulum.DateTime,
        for_delete: bool,
        include_columns: Optional[Sequence[str]] = None,
        exclude_columns: Sequence[str] = (),  # Local patch (#4346)
    ) -> TDataItem:
        """Generates data item from replication message data and corresponding metadata."""
        data_item = {
            schema["name"]: _to_dlt_val(
                val=data.col_data,
                data_type=schema["data_type"],
                byte1=data.col_data_category,
                for_delete=for_delete,
            )
            for (schema, data) in zip(column_schema.values(), data)
            if (True if include_columns is None else schema["name"] in include_columns)
            # Local patch (#4346): filtered by NAME after the positional zip pairs each column
            # with its value — dropping a column before the zip would shift every later value.
            and schema["name"] not in exclude_columns
        }
        data_item["lsn"] = lsn
        if for_delete:
            data_item["deleted_ts"] = commit_ts
        return data_item

    def _old_key_delete(self, decoded_msg: Update, new_item: TDataItem,
                        msg_start_lsn: int) -> Optional[TDataItem]:
        """Local patch (#4338): an UPDATE that changes the primary key. The merge keys on the
        NEW tuple, so without this the old key's row stays in the mirror for ever (a re-keyed
        id leaves a ghost). With REPLICA IDENTITY FULL or a key identity the old tuple carries
        the old key: emit a key-only delete for it at the same LSN — the keys differ, so lsn dedup keeps
        both, and a later re-use of the old key in the batch has a higher LSN and wins."""
        relation_id = decoded_msg.relation_id
        table = self.last_table_schema[relation_id]
        namespace, table_name = table.get("namespace") or "", table["name"]
        if self.policies_by_relation.get((namespace, table_name)) == "append_only":
            return None
        if decoded_msg.optional_tuple_identifier not in ("O", "K") or decoded_msg.old_tuple is None:
            return None
        columns = table["columns"]
        pk = [name for name, col in columns.items() if col.get("primary_key")]
        if not pk:
            return None
        # Local patch (#4346): a key that falls back to REPLICA IDENTITY FULL's flags names every
        # column, denied ones included — a tombstone keyed on them would carry the denied value.
        _include, exclude = self._projection(relation_id, namespace, table_name)
        if exclude & set(pk):
            self.key_problems[(namespace, table_name)] = (
                "a key-changing UPDATE's merge key includes a denied column; no tombstone was "
                "emitted — resnapshot this table")
            return None
        # A 'K' tuple carries only the REPLICA IDENTITY columns, which need not be the merge
        # key (an identity on a secondary unique index): a key field missing from the raw tuple
        # ('n' null, 'u' unchanged-TOAST, or absent) must never become a dummy value in a
        # tombstone — that could delete an unrelated row. Skip it and report instead.
        raw = decoded_msg.old_tuple.column_data
        position = {name: i for i, name in enumerate(columns)}
        if any(position[k] >= len(raw) or raw[position[k]].col_data_category in ("n", "u")
               for k in pk):
            self.key_problems[(namespace, table_name)] = (
                "a key-changing UPDATE's old tuple lacks the merge key; the old key's row "
                "may remain in bronze — resnapshot this table")
            return None
        old_item = self.gen_data_item(
            data=decoded_msg.old_tuple.column_data, column_schema=columns,
            lsn=msg_start_lsn, commit_ts=self.last_commit_ts, for_delete=True)
        if all(old_item.get(k) == new_item.get(k) for k in pk):
            return None
        # The tombstone carries the key and the delete metadata only — never a column the
        # projection (`include_columns`) leaves out, and nothing the delete does not need.
        return {**{k: old_item.get(k) for k in pk},
                "lsn": old_item["lsn"], "deleted_ts": old_item["deleted_ts"]}
