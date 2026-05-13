# Batched-Append Destination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `feather extract`'s buffer-then-swap write path with a per-window transactional batched-append, durable across crashes via a new `_extract_windows` state table.

**Spec source:** GitHub issue [#62](https://github.com/siraj-samsudeen/feather-etl/issues/62). The issue body is the authoritative requirements doc; this plan is the implementation decomposition.

**Architecture:**

- Add `WindowSpec` (`window_key`, `window_predicate`, `window_start`, `window_end`) — the contract between (future) planner and destination.
- Add `DuckDBDestination.load_batched_append(table, window, batches, run_id) -> WindowResult` — one transaction per window: `CREATE TABLE IF NOT EXISTS` → `DELETE WHERE <predicate>` → `INSERT ... FROM _arrow_stream` → `INSERT OR REPLACE _extract_windows` → `COMMIT`.
- Add `_extract_windows` state table; `state.py` gets accessors for read/record/clear.
- `extract.run_extract` enumerates windows for each table (single-window for now — `key="all"`, predicate `1=1`) and skips ones already in `_extract_windows`.
- `--refresh <table>` truncates `_extract_windows` rows for the named tables first.
- New `--refresh-all` flag is the recovery path when destination DB is deleted out from under committed state.
- `streaming_full_load` is deleted (no remaining callers after `extract.py` migrates). `pipeline.py`'s `load_full`/`load_append`/`load_incremental` are out of scope — they're the legacy `feather run` paths and `#62` only touches the extract write path.

**Scope deferred to later stack issues:**

- `#61` — pluggable transport registry (arrow-odbc, connectorx). This plan keeps the existing per-source `extract_batches` interface.
- `#63` — pre-flight planner that enumerates `date`/`pk_range` windows from curation. This plan ships a stub single-window enumerator; #63 replaces it without changing the destination contract.

**Tech Stack:** Python 3.10+, DuckDB (state + destination), PyArrow (record batches), pytest.

---

## File Structure

**New files:**

- `src/feather_etl/extract_windows.py` — `WindowSpec` dataclass, `WindowResult` dataclass, single-window enumerator `plan_windows(table_config) -> list[WindowSpec]`. Will be replaced/extended by `planner.py` in #63 — keeping it as a separate module now means #63 can move it without churning the destination.
- `tests/unit/state/test_extract_windows.py` — state-table accessors.
- `tests/unit/destinations/test_batched_append.py` — replaces `test_streaming_full_load.py` (deleted in this plan).
- `tests/integration/test_extract_resume.py` — end-to-end crash/resume.

**Modified files:**

- `src/feather_etl/destinations/duckdb.py` — add `load_batched_append`; delete `streaming_full_load` + `_StreamingFullLoadSession`.
- `src/feather_etl/destinations/__init__.py` — Destination protocol update.
- `src/feather_etl/state.py` — `_extract_windows` table in `init_state`; new methods `record_committed_window`, `get_committed_windows`, `clear_windows_for_table`. **No schema-version bump** — adding a table is forward-compatible; the schema-version contract only triggers on column changes (per the existing v1→v2 pattern).
- `src/feather_etl/extract.py` — replace `streaming_full_load` block with the per-window enumerate-skip-load loop; add the "destination missing but state has windows" safety check.
- `src/feather_etl/commands/extract.py` — add `--refresh-all` Typer option.

**Deleted files:**

- `tests/unit/destinations/test_streaming_full_load.py` — coverage moves to `test_batched_append.py`.

---

## Task 1: `_extract_windows` state table + accessors

**Files:**
- Modify: `src/feather_etl/state.py`
- Create: `tests/unit/state/test_extract_windows.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/state/test_extract_windows.py
"""Unit tests for _extract_windows state table — durable per-window commit log."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from feather_etl.state import StateManager


def _make_state(tmp_path: Path) -> StateManager:
    state = StateManager(tmp_path / "feather_state.duckdb")
    state.init_state()
    return state


def test_init_creates_extract_windows_table(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    import duckdb

    con = duckdb.connect(str(state.path))
    try:
        tables = {
            r[0]
            for r in con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main'"
            ).fetchall()
        }
        assert "_extract_windows" in tables
    finally:
        con.close()


def test_get_committed_windows_empty_initially(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    assert state.get_committed_windows("sales") == set()


def test_record_and_get_committed_window(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    state.record_committed_window(
        table_name="sales",
        window_key="2026-05-12",
        window_start=datetime(2026, 5, 12, tzinfo=timezone.utc),
        window_end=datetime(2026, 5, 13, tzinfo=timezone.utc),
        rows_loaded=12345,
        transport_used="pyodbc",
        run_id="run_001",
    )
    assert state.get_committed_windows("sales") == {"2026-05-12"}


def test_record_committed_window_replaces_on_duplicate_key(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    state.record_committed_window(
        table_name="sales",
        window_key="2026-05-12",
        window_start=datetime(2026, 5, 12, tzinfo=timezone.utc),
        window_end=datetime(2026, 5, 13, tzinfo=timezone.utc),
        rows_loaded=100,
        transport_used="pyodbc",
        run_id="run_001",
    )
    state.record_committed_window(
        table_name="sales",
        window_key="2026-05-12",
        window_start=datetime(2026, 5, 12, tzinfo=timezone.utc),
        window_end=datetime(2026, 5, 13, tzinfo=timezone.utc),
        rows_loaded=200,  # new value
        transport_used="pyodbc",
        run_id="run_002",
    )
    import duckdb

    con = duckdb.connect(str(state.path))
    try:
        row = con.execute(
            "SELECT rows_loaded, run_id FROM _extract_windows "
            "WHERE table_name = 'sales' AND window_key = '2026-05-12'"
        ).fetchone()
        assert row == (200, "run_002")
    finally:
        con.close()


def test_clear_windows_for_table_only_affects_named_table(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    for tbl in ("sales", "customers"):
        state.record_committed_window(
            table_name=tbl,
            window_key="all",
            window_start=None,
            window_end=None,
            rows_loaded=1,
            transport_used="pyodbc",
            run_id="r",
        )
    state.clear_windows_for_table("sales")
    assert state.get_committed_windows("sales") == set()
    assert state.get_committed_windows("customers") == {"all"}


def test_window_start_end_nullable_for_single_window(tmp_path: Path) -> None:
    """`window_strategy: single` has no temporal bounds — start/end are NULL."""
    state = _make_state(tmp_path)
    state.record_committed_window(
        table_name="dim_branch",
        window_key="all",
        window_start=None,
        window_end=None,
        rows_loaded=16,
        transport_used="pyodbc",
        run_id="r",
    )
    assert state.get_committed_windows("dim_branch") == {"all"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/state/test_extract_windows.py -v`
Expected: collection errors / AttributeError on `state.get_committed_windows` etc.

- [ ] **Step 3: Add `_extract_windows` table to `init_state` and add accessor methods**

In `src/feather_etl/state.py`, inside `init_state` after the `_schema_snapshots` block (~line 142), add:

```python
con.execute("""
    CREATE TABLE IF NOT EXISTS _extract_windows (
        table_name VARCHAR,
        window_key VARCHAR,
        window_start TIMESTAMP,
        window_end TIMESTAMP,
        rows_loaded BIGINT,
        transport_used VARCHAR,
        committed_at TIMESTAMP,
        run_id VARCHAR,
        PRIMARY KEY (table_name, window_key)
    )
""")
```

At the bottom of the `StateManager` class, add:

```python
# --- Extract window commit log (#62) ---

def get_committed_windows(self, table_name: str) -> set[str]:
    """Return the set of window_key values already committed for a table."""
    con = self._connect()
    try:
        rows = con.execute(
            "SELECT window_key FROM _extract_windows WHERE table_name = ?",
            [table_name],
        ).fetchall()
        return {r[0] for r in rows}
    finally:
        con.close()

def record_committed_window(
    self,
    table_name: str,
    window_key: str,
    window_start: datetime | None,
    window_end: datetime | None,
    rows_loaded: int,
    transport_used: str,
    run_id: str,
) -> None:
    """Upsert a window into _extract_windows. Caller must already be inside
    the same transaction as the INSERT into bronze for atomicity — but for
    standalone replays/tests this helper opens its own connection."""
    con = self._connect()
    try:
        con.execute(
            "INSERT OR REPLACE INTO _extract_windows "
            "(table_name, window_key, window_start, window_end, rows_loaded, "
            " transport_used, committed_at, run_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                table_name,
                window_key,
                window_start,
                window_end,
                rows_loaded,
                transport_used,
                datetime.now(timezone.utc),
                run_id,
            ],
        )
    finally:
        con.close()

def clear_windows_for_table(self, table_name: str) -> None:
    """Delete all _extract_windows rows for a table. Used by --refresh."""
    con = self._connect()
    try:
        con.execute(
            "DELETE FROM _extract_windows WHERE table_name = ?", [table_name]
        )
    finally:
        con.close()

def clear_all_windows(self) -> None:
    """Truncate _extract_windows. Used by --refresh-all."""
    con = self._connect()
    try:
        con.execute("DELETE FROM _extract_windows")
    finally:
        con.close()
```

- [ ] **Step 4: Run tests, confirm pass**

Run: `uv run pytest tests/unit/state/test_extract_windows.py -v`
Expected: all 6 tests PASS.

- [ ] **Step 5: Run the rest of the state tests to confirm no regression**

Run: `uv run pytest tests/unit/state/ tests/unit/test_state_trigger.py -v`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/feather_etl/state.py tests/unit/state/test_extract_windows.py
git commit -m "feat(state): add _extract_windows commit log + accessors (#62)"
```

---

## Task 2: `WindowSpec`, `WindowResult`, and single-window enumerator

**Files:**
- Create: `src/feather_etl/extract_windows.py`
- Create: `tests/unit/test_extract_windows_planner.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_extract_windows_planner.py
"""Unit tests for the (currently single-window) extract planner.

Issue #63 will replace `plan_windows` with a multi-strategy planner; the
WindowSpec / WindowResult contract this module defines is what the
destination consumes and is locked here so #63 can extend without
churning destinations or state."""

from __future__ import annotations

from feather_etl.config import TableConfig
from feather_etl.extract_windows import WindowSpec, plan_windows


def _make_table(name: str = "sales") -> TableConfig:
    # Minimal — fields beyond name/database not used by single-window planner.
    return TableConfig(name=name, source_table=name, database="src", source_name="src")


def test_plan_windows_returns_one_window_with_full_table_predicate() -> None:
    plans = plan_windows(_make_table("sales"))
    assert plans == [
        WindowSpec(
            window_key="all",
            window_predicate="1=1",
            window_start=None,
            window_end=None,
        )
    ]


def test_window_spec_is_frozen_dataclass() -> None:
    spec = WindowSpec(
        window_key="2026-05-12",
        window_predicate="[Invoice Date] = '2026-05-12'",
        window_start=None,
        window_end=None,
    )
    # Frozen → hashable → can live in a set (used to skip committed windows).
    assert spec in {spec}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_extract_windows_planner.py -v`
Expected: ModuleNotFoundError on `feather_etl.extract_windows`.

- [ ] **Step 3: Create the module**

```python
# src/feather_etl/extract_windows.py
"""Window enumeration for batched-append extracts (#62).

Currently emits a single window covering the whole table — the degenerate
case. Issue #63 will add `date` and `pk_range` strategies driven by curation;
the WindowSpec contract here is locked so #63 needs no destination changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from feather_etl.config import TableConfig


@dataclass(frozen=True)
class WindowSpec:
    """One unit of resumable work for a table extract.

    `window_predicate` is interpolated directly into a DELETE statement on
    the bronze table — it MUST be safe SQL composed by the planner, never
    raw user input. The single-window case uses `1=1` (delete everything).
    """

    window_key: str
    window_predicate: str
    window_start: datetime | None
    window_end: datetime | None


@dataclass
class WindowResult:
    """Per-window outcome returned by destination.load_batched_append."""

    window_key: str
    rows_loaded: int
    committed: bool
    error: str | None = None


def plan_windows(table: TableConfig) -> list[WindowSpec]:
    """Enumerate the windows to extract for a table.

    Today: always returns one window covering the whole table. Issue #63
    extends this to read window_strategy/window_column from curation.
    """
    return [
        WindowSpec(
            window_key="all",
            window_predicate="1=1",
            window_start=None,
            window_end=None,
        )
    ]
```

- [ ] **Step 4: Run tests, confirm pass**

Run: `uv run pytest tests/unit/test_extract_windows_planner.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/feather_etl/extract_windows.py tests/unit/test_extract_windows_planner.py
git commit -m "feat(extract): WindowSpec contract + single-window planner stub (#62)"
```

---

## Task 3: `DuckDBDestination.load_batched_append`

**Files:**
- Modify: `src/feather_etl/destinations/duckdb.py`
- Modify: `src/feather_etl/destinations/__init__.py`
- Create: `tests/unit/destinations/test_batched_append.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/destinations/test_batched_append.py
"""Unit tests for DuckDBDestination.load_batched_append (#62).

Replaces the deleted test_streaming_full_load.py. Covers:
  - CREATE TABLE IF NOT EXISTS on first window
  - INSERT semantics for subsequent batches in a window
  - DELETE WHERE <predicate> before INSERT (idempotency within window)
  - _extract_windows row written atomically with bronze INSERT
  - Failure inside iterator → bronze unchanged, _extract_windows unchanged
  - Empty iterator → table created (if first window), zero rows, no commit row
  - run_id metadata propagated to _etl_run_id
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pyarrow as pa
import pytest

from feather_etl.destinations.duckdb import DuckDBDestination
from feather_etl.extract_windows import WindowSpec
from feather_etl.state import StateManager


def _batches(*tables: pa.Table):
    for t in tables:
        yield from t.to_batches()


def _table(rows: list[dict], schema: pa.Schema | None = None) -> pa.Table:
    return pa.Table.from_pylist(rows, schema=schema)


def _make_dest_and_state(tmp_path: Path) -> tuple[DuckDBDestination, StateManager]:
    dest = DuckDBDestination(path=tmp_path / "feather_data.duckdb")
    dest.setup_schemas()
    state = StateManager(tmp_path / "feather_state.duckdb")
    state.init_state()
    return dest, state


def _all_rows(dest: DuckDBDestination, table: str) -> list[tuple]:
    con = duckdb.connect(str(dest.path))
    try:
        return con.execute(
            f"SELECT id, name FROM {table} ORDER BY id"
        ).fetchall()
    finally:
        con.close()


def test_first_window_creates_table_and_inserts(tmp_path: Path) -> None:
    dest, state = _make_dest_and_state(tmp_path)
    spec = WindowSpec("all", "1=1", None, None)
    result = dest.load_batched_append(
        table="bronze.foo",
        window=spec,
        batches=_batches(_table([{"id": 1, "name": "a"}, {"id": 2, "name": "b"}])),
        run_id="r1",
        state=state,
        transport_used="pyodbc",
    )
    assert result.rows_loaded == 2
    assert result.committed is True
    assert _all_rows(dest, "bronze.foo") == [(1, "a"), (2, "b")]
    assert state.get_committed_windows("bronze.foo") == {"all"}


def test_window_delete_predicate_idempotent_rerun(tmp_path: Path) -> None:
    """Re-running the same window replaces, not duplicates."""
    dest, state = _make_dest_and_state(tmp_path)
    spec = WindowSpec("2026-05-12", "day = '2026-05-12'", None, None)
    tbl = _table([{"id": 1, "name": "a", "day": "2026-05-12"}])

    dest.load_batched_append("bronze.foo", spec, _batches(tbl), "r1", state, "pyodbc")
    dest.load_batched_append("bronze.foo", spec, _batches(tbl), "r2", state, "pyodbc")

    con = duckdb.connect(str(dest.path))
    try:
        n = con.execute("SELECT COUNT(*) FROM bronze.foo").fetchone()[0]
    finally:
        con.close()
    assert n == 1  # second call DELETEd before re-INSERTing


def test_two_windows_accumulate(tmp_path: Path) -> None:
    dest, state = _make_dest_and_state(tmp_path)
    w1 = WindowSpec("2026-05-12", "day = '2026-05-12'", None, None)
    w2 = WindowSpec("2026-05-13", "day = '2026-05-13'", None, None)

    dest.load_batched_append(
        "bronze.foo", w1,
        _batches(_table([{"id": 1, "name": "a", "day": "2026-05-12"}])),
        "r", state, "pyodbc",
    )
    dest.load_batched_append(
        "bronze.foo", w2,
        _batches(_table([{"id": 2, "name": "b", "day": "2026-05-13"}])),
        "r", state, "pyodbc",
    )
    assert state.get_committed_windows("bronze.foo") == {"2026-05-12", "2026-05-13"}
    con = duckdb.connect(str(dest.path))
    try:
        n = con.execute("SELECT COUNT(*) FROM bronze.foo").fetchone()[0]
    finally:
        con.close()
    assert n == 2


def test_failure_mid_window_leaves_state_clean(tmp_path: Path) -> None:
    """Iterator raises → no bronze rows for this window, no _extract_windows row."""
    dest, state = _make_dest_and_state(tmp_path)
    spec = WindowSpec("2026-05-12", "day = '2026-05-12'", None, None)

    def boom():
        yield from _table([{"id": 1, "name": "a", "day": "2026-05-12"}]).to_batches()
        raise RuntimeError("simulated transport failure")

    with pytest.raises(RuntimeError, match="simulated"):
        dest.load_batched_append(
            "bronze.foo", spec, boom(), "r1", state, "pyodbc",
        )

    assert state.get_committed_windows("bronze.foo") == set()
    # Bronze table may or may not exist depending on when failure hit;
    # if it exists it must have zero rows from this aborted window.
    con = duckdb.connect(str(dest.path))
    try:
        n = con.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema='bronze' AND table_name='foo'"
        ).fetchone()[0]
        if n == 1:
            assert con.execute("SELECT COUNT(*) FROM bronze.foo").fetchone()[0] == 0
    finally:
        con.close()


def test_empty_iterator_records_zero_row_window(tmp_path: Path) -> None:
    """A window legitimately with zero source rows still commits — it's done."""
    dest, state = _make_dest_and_state(tmp_path)
    spec = WindowSpec("empty-day", "day = '1999-01-01'", None, None)
    schema = pa.schema([("id", pa.int64()), ("name", pa.string()), ("day", pa.string())])

    def empty_iter():
        # One zero-row batch — establishes schema for CREATE TABLE.
        yield pa.RecordBatch.from_pylist([], schema=schema)

    result = dest.load_batched_append(
        "bronze.foo", spec, empty_iter(), "r1", state, "pyodbc",
    )
    assert result.rows_loaded == 0
    assert result.committed is True
    assert state.get_committed_windows("bronze.foo") == {"empty-day"}


def test_run_id_propagated_to_etl_metadata(tmp_path: Path) -> None:
    dest, state = _make_dest_and_state(tmp_path)
    spec = WindowSpec("all", "1=1", None, None)
    dest.load_batched_append(
        "bronze.foo", spec,
        _batches(_table([{"id": 1, "name": "a"}])),
        "run_42", state, "pyodbc",
    )
    con = duckdb.connect(str(dest.path))
    try:
        row = con.execute("SELECT _etl_run_id FROM bronze.foo").fetchone()
    finally:
        con.close()
    assert row[0] == "run_42"


def test_rejects_unqualified_table_name(tmp_path: Path) -> None:
    dest, state = _make_dest_and_state(tmp_path)
    spec = WindowSpec("all", "1=1", None, None)
    with pytest.raises(ValueError, match="expects schema.table"):
        dest.load_batched_append(
            "foo", spec, iter(()), "r1", state, "pyodbc",
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/destinations/test_batched_append.py -v`
Expected: AttributeError on `DuckDBDestination.load_batched_append`.

- [ ] **Step 3: Implement `load_batched_append`**

In `src/feather_etl/destinations/duckdb.py`, add the method to `DuckDBDestination` (place after `load_full`, before `load_append`):

```python
def load_batched_append(
    self,
    table: str,
    window: "WindowSpec",
    batches: "Iterator[pa.RecordBatch]",
    run_id: str,
    state: "StateManager",
    transport_used: str,
) -> "WindowResult":
    """Transactional per-window batched append (#62).

    Contract per window:
      BEGIN;
        CREATE TABLE IF NOT EXISTS <table> (schema from first batch + etl cols);
        DELETE FROM <table> WHERE <window.window_predicate>;
        for each batch: INSERT INTO <table> SELECT *, run_id, now() FROM _arrow_batch;
        INSERT OR REPLACE INTO _extract_windows (...);
      COMMIT;

    On any exception inside the loop the transaction is rolled back —
    bronze is unchanged and the window is NOT marked committed. A
    subsequent run sees the window absent from _extract_windows and
    re-attempts it.
    """
    from feather_etl.extract_windows import WindowResult

    schema_name, table_name = _parse_qualified_table(table, "load_batched_append")
    final = f"{schema_name}.{table_name}"

    con = self._connect()
    rows_loaded = 0
    schema_created = False
    try:
        con.execute("BEGIN TRANSACTION")
        try:
            for batch in batches:
                con.register("_arrow_batch", batch)
                try:
                    if not schema_created:
                        # First batch (even zero rows) defines the schema.
                        con.execute(
                            f"CREATE TABLE IF NOT EXISTS {final} AS "
                            f"SELECT *, CURRENT_TIMESTAMP AS _etl_loaded_at, "
                            f"CAST(NULL AS VARCHAR) AS _etl_run_id "
                            f"FROM _arrow_batch WHERE 1=0"
                        )
                        # Window-level DELETE happens exactly once, after
                        # the table exists, before any INSERT.
                        con.execute(
                            f"DELETE FROM {final} WHERE {window.window_predicate}"
                        )
                        schema_created = True
                    if batch.num_rows > 0:
                        con.execute(
                            f"INSERT INTO {final} "
                            f"SELECT *, CURRENT_TIMESTAMP AS _etl_loaded_at, "
                            f"'{run_id}' AS _etl_run_id FROM _arrow_batch"
                        )
                        rows_loaded += batch.num_rows
                finally:
                    con.unregister("_arrow_batch")

            # Edge case: iterator was completely empty (not even one
            # zero-row batch). We can't CREATE without a schema; treat
            # as "window has no source data, but the table must already
            # exist from a prior window" — if not, raise.
            if not schema_created:
                exists = con.execute(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema = ? AND table_name = ?",
                    [schema_name, table_name],
                ).fetchone()[0]
                if not exists:
                    raise RuntimeError(
                        f"load_batched_append: iterator yielded no batches and "
                        f"{final} does not exist — cannot infer schema."
                    )
                # Table exists from prior window — just DELETE for idempotency.
                con.execute(
                    f"DELETE FROM {final} WHERE {window.window_predicate}"
                )

            # _extract_windows write inside the same transaction as the
            # INSERTs so resume guarantees hold. Direct SQL — bypassing
            # state.record_committed_window's separate connection.
            con.execute(
                "INSERT OR REPLACE INTO _extract_windows "
                "(table_name, window_key, window_start, window_end, "
                " rows_loaded, transport_used, committed_at, run_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    final,  # bronze.foo — matches how callers reference the table
                    window.window_key,
                    window.window_start,
                    window.window_end,
                    rows_loaded,
                    transport_used,
                    datetime.now(timezone.utc),
                    run_id,
                ],
            )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    finally:
        con.close()

    return WindowResult(
        window_key=window.window_key,
        rows_loaded=rows_loaded,
        committed=True,
    )
```

Add the required imports at the top of the file:

```python
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    from feather_etl.extract_windows import WindowResult, WindowSpec
    from feather_etl.state import StateManager
```

**Cross-database state writes:** `_extract_windows` lives in the *state* DB but the INSERTs above run on the *destination* DB connection. DuckDB supports this via `ATTACH`. Add at the top of `load_batched_append`, just after opening `con`:

```python
con.execute(f"ATTACH '{state.path}' AS _state")
```

Then change the `INSERT OR REPLACE INTO _extract_windows` SQL to:

```python
con.execute(
    "INSERT OR REPLACE INTO _state._extract_windows "
    "(table_name, window_key, window_start, window_end, "
    " rows_loaded, transport_used, committed_at, run_id) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
    [...],
)
```

And ensure detach in `finally`:

```python
finally:
    try:
        con.execute("DETACH _state")
    except Exception:
        pass
    con.close()
```

The `ATTACH` puts both writes in the same transaction — that's the durability guarantee. Without it, bronze could commit while `_extract_windows` is lost on crash.

- [ ] **Step 4: Update the Destination protocol**

In `src/feather_etl/destinations/__init__.py`:

```python
from typing import Iterator, Protocol

import pyarrow as pa

from feather_etl.extract_windows import WindowResult, WindowSpec
from feather_etl.state import StateManager


class Destination(Protocol):
    def setup_schemas(self) -> None: ...
    def load_full(self, table: str, data: pa.Table, run_id: str) -> int: ...
    def load_incremental(
        self, table: str, data: pa.Table, run_id: str, timestamp_column: str
    ) -> int: ...
    def load_append(self, table: str, data: pa.Table, run_id: str) -> int: ...
    def load_batched_append(
        self,
        table: str,
        window: WindowSpec,
        batches: Iterator[pa.RecordBatch],
        run_id: str,
        state: StateManager,
        transport_used: str,
    ) -> WindowResult: ...
```

(`load_full` stays on the protocol because `pipeline.py:run_all` still uses it — the legacy `feather run` codepath, out of scope for #62.)

- [ ] **Step 5: Run tests, confirm pass**

Run: `uv run pytest tests/unit/destinations/test_batched_append.py -v`
Expected: 7 PASS.

- [ ] **Step 6: Run the full destination test suite to confirm no regression**

Run: `uv run pytest tests/unit/destinations/ tests/unit/test_append.py tests/unit/test_incremental.py -v`
Expected: green (streaming_full_load tests will still pass — deletion happens in Task 6).

- [ ] **Step 7: Commit**

```bash
git add src/feather_etl/destinations/duckdb.py src/feather_etl/destinations/__init__.py tests/unit/destinations/test_batched_append.py
git commit -m "feat(destinations): load_batched_append with per-window atomicity (#62)"
```

---

## Task 4: Wire `extract.run_extract` to enumerate-and-skip windows

**Files:**
- Modify: `src/feather_etl/extract.py`
- Modify (existing tests that hit `streaming_full_load` indirectly): `tests/integration/test_extract_*.py` (whatever exists — update assertions, not behavior).

- [ ] **Step 1: Write the failing test**

Add a new integration test exercising the windowed path end-to-end:

```python
# tests/integration/test_extract_windows_integration.py
"""run_extract now drives load_batched_append per window. Even with the
single-window stub, the _extract_windows commit log must be populated
and second runs must skip the same window."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from feather_etl.config import FeatherConfig
from feather_etl.extract import run_extract
from feather_etl.state import StateManager
from tests.helpers import make_csv_source_config  # existing helper, see note below


@pytest.fixture
def cfg_and_tables(tmp_path: Path) -> tuple[FeatherConfig, list, Path]:
    csv_dir = tmp_path / "src"
    csv_dir.mkdir()
    (csv_dir / "items.csv").write_text("id,name\n1,a\n2,b\n")
    cfg, tables = make_csv_source_config(tmp_path, csv_dir, table_name="items")
    return cfg, tables, tmp_path


def test_first_extract_records_committed_window(cfg_and_tables) -> None:
    cfg, tables, tmp_path = cfg_and_tables
    results = run_extract(cfg, tables, tmp_path)
    assert all(r.status == "success" for r in results)

    state = StateManager(tmp_path / "feather_state.duckdb")
    assert state.get_committed_windows("bronze.items") == {"all"}


def test_second_extract_with_unchanged_source_skips_via_change_detection(
    cfg_and_tables,
) -> None:
    """Existing change-detection layer still wins for unchanged source —
    we don't even hit the window-skip path. This pins that behavior so #63
    doesn't accidentally re-extract unchanged files."""
    cfg, tables, tmp_path = cfg_and_tables
    run_extract(cfg, tables, tmp_path)
    results = run_extract(cfg, tables, tmp_path)
    assert [r.status for r in results] == ["cached"]


def test_refresh_clears_window_and_reextracts(cfg_and_tables) -> None:
    cfg, tables, tmp_path = cfg_and_tables
    run_extract(cfg, tables, tmp_path)

    state = StateManager(tmp_path / "feather_state.duckdb")
    assert state.get_committed_windows("bronze.items") == {"all"}

    results = run_extract(cfg, tables, tmp_path, refresh=True)
    assert all(r.status == "success" for r in results)
    # Still exactly one committed window — refresh truncated then re-recorded.
    assert state.get_committed_windows("bronze.items") == {"all"}
```

If `tests/helpers.py` lacks `make_csv_source_config`, write the fixture inline using the same pattern as `tests/integration/test_change_detection.py` (which already exercises `run_extract`). Mirror an existing test fixture rather than inventing a new helper.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_extract_windows_integration.py -v`
Expected: AttributeError — `run_extract` hasn't been wired yet.

- [ ] **Step 3: Refactor `extract.run_extract`**

In `src/feather_etl/extract.py`, replace the `streaming_full_load` block (lines ~137-184) with:

```python
try:
    target = f"bronze.{table.name}"
    from feather_etl.extract_windows import plan_windows

    if refresh:
        state.clear_windows_for_table(target)

    windows = plan_windows(table)
    committed = state.get_committed_windows(target)
    todo = [w for w in windows if w.window_key not in committed]

    rows_total = 0
    transport_used = getattr(source, "transport_name", "pyodbc")
    for window in todo:
        from feather_etl.destinations.duckdb import DuckDBDestination  # local re-import for clarity
        _ = DuckDBDestination  # silence linter; dest already constructed above

        result = dest.load_batched_append(
            table=target,
            window=window,
            batches=_iter_source_batches(
                source,
                table.source_table,
                heartbeat_every_rows=extract_defaults.heartbeat_every_rows,
                heartbeat_every_seconds=extract_defaults.heartbeat_every_seconds,
            ),
            run_id=run_id,
            state=state,
            transport_used=transport_used,
        )
        rows_total += result.rows_loaded

    rows = rows_total
    logger.info(
        "extract_complete",
        extra={
            "table": table.source_table,
            "rows_loaded": rows,
            "status": "success",
        },
    )
    state.write_watermark(
        table_name=table.name,
        strategy=None,
        last_run_at=now,
        last_file_mtime=change.metadata.get("file_mtime"),
        last_file_hash=change.metadata.get("file_hash"),
        last_checksum=change.metadata.get("checksum"),
        last_row_count=change.metadata.get("row_count"),
        source_db=source_db,
    )
    # ...record_run + result append unchanged from original
```

(Keep the existing success-path `record_run` + `results.append` blocks intact; only the load mechanics change.)

Clean up the local-reimport noise — the cleaner version:

```python
try:
    target = f"bronze.{table.name}"
    from feather_etl.extract_windows import plan_windows

    if refresh:
        state.clear_windows_for_table(target)

    committed = state.get_committed_windows(target)
    windows = [w for w in plan_windows(table) if w.window_key not in committed]

    rows = 0
    transport_used = getattr(source, "transport_name", "pyodbc")
    for window in windows:
        result = dest.load_batched_append(
            table=target,
            window=window,
            batches=_iter_source_batches(
                source,
                table.source_table,
                heartbeat_every_rows=extract_defaults.heartbeat_every_rows,
                heartbeat_every_seconds=extract_defaults.heartbeat_every_seconds,
            ),
            run_id=run_id,
            state=state,
            transport_used=transport_used,
        )
        rows += result.rows_loaded
    # ...rest unchanged
```

- [ ] **Step 4: Run new tests, confirm pass**

Run: `uv run pytest tests/integration/test_extract_windows_integration.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Run existing extract suite to surface regressions**

Run: `uv run pytest tests/integration/test_extract_*.py tests/unit/test_extract*.py -v`
Expected: green. If existing tests asserted on `streaming_full_load` call counts or the `_new` staging table name, update those assertions to match the new code path (delete the assertion if it was implementation-coupled).

- [ ] **Step 6: Run the full test suite**

Run: `uv run pytest -q`
Expected: green (or only failures inside `test_streaming_full_load.py`, which gets removed in Task 6).

- [ ] **Step 7: Commit**

```bash
git add src/feather_etl/extract.py tests/integration/test_extract_windows_integration.py
git commit -m "feat(extract): drive load_batched_append per window with resume (#62)"
```

---

## Task 5: `--refresh-all` flag + missing-destination safety check

**Files:**
- Modify: `src/feather_etl/commands/extract.py`
- Modify: `src/feather_etl/extract.py`
- Create: `tests/unit/test_extract_refresh_safety.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_extract_refresh_safety.py
"""Acceptance criterion from #62:
'Deleting feather_data.duckdb without state cleanup raises a clear error
and requires --refresh-all to recover'."""

from __future__ import annotations

from pathlib import Path

import pytest

from feather_etl.extract import run_extract
from feather_etl.state import StateManager
from tests.helpers import make_csv_source_config


def _prime(tmp_path: Path):
    csv_dir = tmp_path / "src"
    csv_dir.mkdir()
    (csv_dir / "items.csv").write_text("id,name\n1,a\n")
    cfg, tables = make_csv_source_config(tmp_path, csv_dir, table_name="items")
    return cfg, tables


def test_missing_destination_with_committed_windows_raises(tmp_path: Path) -> None:
    cfg, tables = _prime(tmp_path)
    run_extract(cfg, tables, tmp_path)
    assert StateManager(tmp_path / "feather_state.duckdb").get_committed_windows(
        "bronze.items"
    ) == {"all"}

    # Simulate "I deleted the data file by hand."
    (cfg.destination.path).unlink()

    results = run_extract(cfg, tables, tmp_path)
    assert any(r.status == "failure" for r in results)
    err = next(r.error_message for r in results if r.status == "failure")
    assert "feather_data.duckdb" in err.lower() or "destination" in err.lower()
    assert "--refresh-all" in err


def test_refresh_all_clears_windows_and_reextracts(tmp_path: Path) -> None:
    cfg, tables = _prime(tmp_path)
    run_extract(cfg, tables, tmp_path)
    (cfg.destination.path).unlink()

    results = run_extract(cfg, tables, tmp_path, refresh_all=True)
    assert all(r.status == "success" for r in results)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_extract_refresh_safety.py -v`
Expected: signature mismatch on `refresh_all` kwarg or unexpected success.

- [ ] **Step 3: Implement**

In `src/feather_etl/extract.py`, add `refresh_all: bool = False` parameter to `run_extract`. At the top of `run_extract` (after `state.init_state()`):

```python
if refresh_all:
    state.clear_all_windows()

# Safety check: state claims committed windows exist but destination
# file is gone → silently re-extracting would dedupe incorrectly because
# the DELETE WHERE <predicate> only deletes from the destination, not
# from any stale notion of "what we last wrote." Force the operator to
# acknowledge.
if not config.destination.path.exists():
    orphan_tables = []
    con = state._connect()  # one read; cheap
    try:
        rows = con.execute(
            "SELECT DISTINCT table_name FROM _extract_windows"
        ).fetchall()
        orphan_tables = [r[0] for r in rows]
    finally:
        con.close()
    if orphan_tables and not refresh_all:
        msg = (
            f"feather_data.duckdb does not exist but _extract_windows has "
            f"{len(orphan_tables)} table(s) with committed windows "
            f"({', '.join(orphan_tables[:3])}{'...' if len(orphan_tables) > 3 else ''}). "
            f"State and destination are out of sync — rerun with --refresh-all "
            f"to clear the commit log and re-extract from scratch."
        )
        # Per-table failure result, no exception thrown — matches existing
        # error-handling convention in run_extract.
        return [
            ExtractResult(
                table_name=t.name,
                source_db=t.database or (t.source_name or ""),
                status="failure",
                error_message=msg,
            )
            for t in tables
        ]
```

In `src/feather_etl/commands/extract.py`, add the Typer option (after the existing `refresh` option, ~line 33):

```python
refresh_all: bool = typer.Option(
    False,
    "--refresh-all",
    help="Clear ALL extract commit-log entries before running. Use when "
    "feather_data.duckdb has been deleted out of band and state is stale.",
),
```

And update the `run_extract` call:

```python
results = run_extract(
    cfg, tables, cfg.config_dir, refresh=refresh, refresh_all=refresh_all
)
```

- [ ] **Step 4: Run tests, confirm pass**

Run: `uv run pytest tests/unit/test_extract_refresh_safety.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Full-suite sanity**

Run: `uv run pytest -q`
Expected: green except still-existing `test_streaming_full_load.py` (deleted in Task 6).

- [ ] **Step 6: Commit**

```bash
git add src/feather_etl/extract.py src/feather_etl/commands/extract.py tests/unit/test_extract_refresh_safety.py
git commit -m "feat(extract): --refresh-all flag + missing-destination safety check (#62)"
```

---

## Task 6: Delete `streaming_full_load` and its tests

**Files:**
- Modify: `src/feather_etl/destinations/duckdb.py` (remove `_StreamingFullLoadSession` + `streaming_full_load`)
- Delete: `tests/unit/destinations/test_streaming_full_load.py`
- Modify: `tests/unit/destinations/test_duckdb.py` (drop the `streaming_full_load` and `load_full` parse-error parametrize entries IF they reference the deleted method)

- [ ] **Step 1: Verify nothing else calls `streaming_full_load`**

Run: `grep -rn "streaming_full_load" src/ tests/`
Expected: only references inside `duckdb.py` (definition) and `test_streaming_full_load.py` (test file we're deleting).

If any other call site shows up, stop and refactor that caller in this task before proceeding.

- [ ] **Step 2: Delete the test file**

```bash
git rm tests/unit/destinations/test_streaming_full_load.py
```

- [ ] **Step 3: Remove `_StreamingFullLoadSession` and `streaming_full_load`**

In `src/feather_etl/destinations/duckdb.py`:
- Delete the `_StreamingFullLoadSession` class entirely (lines ~23-102 in the current file).
- Delete the `streaming_full_load` method on `DuckDBDestination` (lines ~191-207).
- Leave `load_full`, `load_append`, `load_incremental` untouched — they're used by `pipeline.run_all` (out of scope).

- [ ] **Step 4: Update `test_duckdb.py` parametrize**

Open `tests/unit/destinations/test_duckdb.py` line ~29. If the parametrize list still includes `streaming_full_load`, remove that entry. Leave `load_full`, `load_append`, `load_incremental` parametrize entries alone.

- [ ] **Step 5: Run full suite**

Run: `uv run pytest -q`
Expected: all green. No reference to `streaming_full_load` should remain.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor(destinations): remove streaming_full_load — superseded by load_batched_append (#62)"
```

---

## Task 7: Crash-resume acceptance test

The headline acceptance criterion from #62: "Crash mid-extract → next run resumes from the next uncommitted window with zero duplicates."

With the single-window planner, "next run resumes" degenerates to "second run does the one window if first crashed before committing." This task locks that behavior with a real-DuckDB integration test. #63 will extend it to multi-window scenarios.

**Files:**
- Create: `tests/integration/test_extract_resume.py`

- [ ] **Step 1: Write the test**

```python
# tests/integration/test_extract_resume.py
"""#62 acceptance — crash mid-extract leaves bronze + commit log consistent;
next run completes."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pyarrow as pa
import pytest

from feather_etl.destinations.duckdb import DuckDBDestination
from feather_etl.extract_windows import WindowSpec
from feather_etl.state import StateManager


def test_crash_mid_window_leaves_no_partial_commit(tmp_path: Path) -> None:
    dest = DuckDBDestination(path=tmp_path / "data.duckdb")
    dest.setup_schemas()
    state = StateManager(tmp_path / "state.duckdb")
    state.init_state()
    spec = WindowSpec("2026-05-12", "day = '2026-05-12'", None, None)

    def half_then_crash():
        yield pa.RecordBatch.from_pylist(
            [{"id": 1, "name": "a", "day": "2026-05-12"}]
        )
        yield pa.RecordBatch.from_pylist(
            [{"id": 2, "name": "b", "day": "2026-05-12"}]
        )
        raise RuntimeError("transport disconnected")

    with pytest.raises(RuntimeError, match="disconnected"):
        dest.load_batched_append(
            "bronze.foo", spec, half_then_crash(), "r1", state, "pyodbc"
        )

    # Commit log untouched.
    assert state.get_committed_windows("bronze.foo") == set()

    # Bronze either doesn't exist or has zero rows in this window's predicate.
    con = duckdb.connect(str(dest.path))
    try:
        existed = con.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = 'bronze' AND table_name = 'foo'"
        ).fetchone()[0]
        if existed:
            n = con.execute(
                "SELECT COUNT(*) FROM bronze.foo WHERE day = '2026-05-12'"
            ).fetchone()[0]
            assert n == 0
    finally:
        con.close()


def test_resume_completes_after_crash(tmp_path: Path) -> None:
    dest = DuckDBDestination(path=tmp_path / "data.duckdb")
    dest.setup_schemas()
    state = StateManager(tmp_path / "state.duckdb")
    state.init_state()
    spec = WindowSpec("2026-05-12", "day = '2026-05-12'", None, None)

    def crashing():
        yield pa.RecordBatch.from_pylist(
            [{"id": 1, "name": "a", "day": "2026-05-12"}]
        )
        raise RuntimeError("disconnected")

    with pytest.raises(RuntimeError):
        dest.load_batched_append(
            "bronze.foo", spec, crashing(), "r1", state, "pyodbc"
        )

    def clean():
        yield pa.RecordBatch.from_pylist(
            [
                {"id": 1, "name": "a", "day": "2026-05-12"},
                {"id": 2, "name": "b", "day": "2026-05-12"},
            ]
        )

    result = dest.load_batched_append(
        "bronze.foo", spec, clean(), "r2", state, "pyodbc"
    )

    assert result.rows_loaded == 2
    assert state.get_committed_windows("bronze.foo") == {"2026-05-12"}
    con = duckdb.connect(str(dest.path))
    try:
        rows = con.execute(
            "SELECT id, name FROM bronze.foo ORDER BY id"
        ).fetchall()
    finally:
        con.close()
    assert rows == [(1, "a"), (2, "b")]
```

- [ ] **Step 2: Run, confirm pass**

Run: `uv run pytest tests/integration/test_extract_resume.py -v`
Expected: 2 PASS.

- [ ] **Step 3: Full suite + lint + format**

Run:
```bash
uv run pytest -q
ruff check .
ruff format .
```

Expected: all green; format may touch files (commit those changes too if so).

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "test(extract): crash-resume acceptance tests for batched-append (#62)"
```

---

## Self-Review

**Spec coverage:**

| Acceptance criterion (issue #62) | Task |
|---|---|
| Crash mid-extract → next run resumes from next uncommitted window with zero duplicates | Task 3 (DELETE-before-INSERT + atomic commit) + Task 7 (test) |
| `--refresh <table>` re-extracts all windows for that table only | Task 4 (`clear_windows_for_table`) |
| `_extract_windows` row count == number of windows planned by pre-flight | Task 1 (commit log) + Task 4 (orchestrator drives one row per window). Multi-window planner deferred to #63. |
| Deleting `feather_data.duckdb` raises clear error, requires `--refresh-all` | Task 5 |
| `destinations/duckdb.py` — new method | Task 3 |
| `state.py` — new `_extract_windows` table + accessors | Task 1 |
| `commands/extract.py` — orchestrator | Task 5 (and Task 4 for plumbing) |
| Old `load_full` deleted | Partially. `streaming_full_load` (extract's actual current write path) deleted in Task 6. `load_full` itself kept for `pipeline.run_all` — #62's scope is the extract path, not the legacy `feather run` path. Documented in the plan header. |

**Placeholder scan:** none.

**Type consistency:** `WindowSpec`, `WindowResult`, `load_batched_append` signatures match across Tasks 2/3/4. State methods named consistently (`get_committed_windows`, `record_committed_window`, `clear_windows_for_table`, `clear_all_windows`). Transport string default `"pyodbc"` consistent across tasks (will be set by #61's registry later).

---

## Branch & worktree

The branch `feat/62-batched-append` is already created and checked out (set up before this plan was written). Per CLAUDE.md, plain in-place branching is fine when the user has set up the branch explicitly — no worktree needed here.

Out-of-tree files left behind from issue #57 (`docs/issues/zakya-invoice-source.md`, `docs/issues/README.md` modification, `scripts/zakya_*.py`) are unrelated to #62. Leave them untouched; the user will land them separately.
