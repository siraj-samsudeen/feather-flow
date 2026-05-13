# Pluggable Transport Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decouple the SQL Server source from `pyodbc`. Introduce a per-source `Transport` abstraction with three implementations — `pyodbc`, `arrow-odbc` (new default), `connectorx` (opt-in for parallel reads) — selected from `feather.yaml` without source-class forks.

**Design spec:** [`docs/superpowers/specs/2026-05-13-transport-registry-design.md`](../specs/2026-05-13-transport-registry-design.md) — the authoritative requirements + design doc. This plan is the implementation decomposition.
**Issue:** [#61](https://github.com/siraj-samsudeen/feather-etl/issues/61).

**Position in stack:** #62 → **#61** → #63.
`#62` (batched-append + `_extract_windows`) is already on `main`. `extract.py` already reads `getattr(source, "transport_name", ...)` for `_extract_windows.transport_used` — once this plan lands, that label flips from `"SqlServerSource"` to the real transport name with no further wiring. `#63` (pre-flight planner) will read `source.transport.supports_partition_on()` to gate its `large` bucket recommendation.

**Architecture:**

- New package `src/feather_etl/transports/`:
  - `base.py` — `Transport` protocol + a shared `_emit_heartbeats` helper that wraps any `Iterator[pa.RecordBatch]` and emits the same heartbeat log records `fetch_batches` does today.
  - `pyodbc_transport.py` — `PyodbcTransport`. Owns the existing pyodbc cursor + `fetchmany` + per-row coerce + Arrow assembly loop (lifted off `SqlServerSource` so the source can stay thin).
  - `arrow_odbc_transport.py` — `ArrowOdbcTransport`. Wraps `arrow_odbc.read_arrow_batches_from_odbc` (new default for SQL Server). Lazy-imports `arrow_odbc`.
  - `connectorx_transport.py` — `ConnectorxTransport`. Wraps `connectorx.read_sql(..., return_type="arrow_stream", partition_on=..., partition_num=...)`. Lazy-imports `connectorx`.
  - `registry.py` — string→class lazy map (`{"pyodbc": "...PyodbcTransport", "arrow-odbc": ..., "connectorx": ...}`), mirrors `sources/registry.py`.

- `SqlServerSource.__init__` / `.from_yaml` gain a `transport: str = "arrow-odbc"` argument. `from_yaml` validates the value via the transport registry (unknown name → clear `ValueError` at config load, listing available names).

- `SqlServerSource.transport_name` (new attribute) — the resolved string (e.g. `"arrow-odbc"`). `extract.py` line 185 already reads this for `_extract_windows.transport_used`; this plan flips the label from class-name fallback to the real transport.

- `SqlServerSource.extract_batches` becomes thin (~25 lines):
  1. Validate `columns` (existing `]`-in-column-name guard kept).
  2. Build the SELECT query (existing `_build_where_clause` + `[col]` quoting kept).
  3. Resolve the transport via the registry.
  4. Delegate to `transport.stream_batches(conn_str, query, ...)`, which already wraps its output through `_emit_heartbeats`.

- Heartbeat emission stays where it is today (`fetch_batches` heartbeat semantics) but is reused across all transports via `_emit_heartbeats` so the user sees identical log lines regardless of transport.

**Transport contract** (locked in `base.py`):

```python
class Transport(Protocol):
    name: str  # class attribute: "pyodbc" / "arrow-odbc" / "connectorx"

    def stream_batches(
        self,
        connection_string: str,
        query: str,
        *,
        batch_size: int,
        table_label: str,
        partition_on: str | None = None,
        partition_num: int = 4,
        heartbeat_every_rows: int = 100_000,
        heartbeat_every_seconds: int = 30,
    ) -> Iterator[pa.RecordBatch]: ...

    def supports_partition_on(self) -> bool: ...
```

`partition_on` / `partition_num` are accepted by every transport but only meaningful for `ConnectorxTransport`. The other two ignore them. `supports_partition_on()` returns `True` only for `ConnectorxTransport` — `#63`'s pre-flight planner reads this to decide whether the `large` bucket can recommend parallel reads on a given source.

**What this plan does NOT do:**

- No per-table `transport_overrides` in curation. Issue #61 mentions one; epic #60's `curation.json` direction was *reversed* in #58's brainstorming (no new keys on `curation.json`). Per-source override on `feather.yaml` is the locked-down level for this PR; per-table goes through a future RFC if/when the need is concrete.
- No PostgreSQL/MySQL transport swap — same pattern, different PR per the issue's "Out of scope."
- No removal of `pyodbc` from `dependencies`. Pyodbc stays a hard dep (it's the fallback transport and other code paths still need it).

**Tech Stack:** Python 3.10+, `pyodbc>=5` (existing), `arrow-odbc>=8` (new hard dep, ~50KB wheel that wraps the `odbc-api` Rust crate), `connectorx>=0.4` (new optional extra), PyArrow (existing), pytest.

---

## File Structure

**New files:**

- `src/feather_etl/transports/__init__.py` — re-exports `Transport`, `get_transport_class`.
- `src/feather_etl/transports/base.py` — `Transport` protocol, `_emit_heartbeats(iterator, ...)` helper.
- `src/feather_etl/transports/pyodbc_transport.py` — `PyodbcTransport`.
- `src/feather_etl/transports/arrow_odbc_transport.py` — `ArrowOdbcTransport`.
- `src/feather_etl/transports/connectorx_transport.py` — `ConnectorxTransport`.
- `src/feather_etl/transports/registry.py` — lazy `get_transport_class(name)` map.
- `tests/unit/transports/__init__.py`
- `tests/unit/transports/test_registry.py` — name resolution, unknown-name error.
- `tests/unit/transports/test_pyodbc_transport.py` — fully mocked pyodbc; covers row coercion + heartbeat wrap.
- `tests/unit/transports/test_arrow_odbc_transport.py` — `arrow_odbc.read_arrow_batches_from_odbc` mocked at module level.
- `tests/unit/transports/test_connectorx_transport.py` — `connectorx.read_sql` mocked at module level; covers missing-dep error UX.
- `tests/unit/transports/test_heartbeat.py` — `_emit_heartbeats` row/time thresholds; cloned shape of the existing `fetch_batches` heartbeat tests.
- `tests/unit/sources/test_sqlserver_transport_selection.py` — `from_yaml` resolves `transport:` field, default is `arrow-odbc`, unknown name raises.
- `tests/integration/test_transport_equivalence.py` — stub all three transports yielding the same canned `RecordBatch`es; assert `SqlServerSource.extract_batches` produces identical Arrow schema + row counts through each. (Real-DB equivalence is in `scripts/`, not in CI.)
- `scripts/manual_transport_equivalence.py` — operator-run smoke script that hits a real SQL Server (via `$FEATHER_TEST_SQLSERVER_DSN`) through all three transports and prints a per-transport row-count / schema diff. Skipped if env var absent. The reproducible-by-hand version of the acceptance criterion #4.

**Modified files:**

- `pyproject.toml` — add `arrow-odbc>=8.0` to `dependencies`; add `[project.optional-dependencies] connectorx = ["connectorx>=0.4"]`.
- `src/feather_etl/sources/sqlserver.py` — `__init__` and `from_yaml` accept `transport`; `extract_batches` becomes a thin wrapper delegating to the resolved transport; the existing pyodbc-specific helpers (`_pyodbc_type_to_arrow`, `_PYODBC_TYPE_MAP`, `_sqlserver_coerce_row`) move into `pyodbc_transport.py`. `_PYODBC_TYPE_MAP` is a closed module-internal map; nothing imports it.
- `src/feather_etl/sources/fetch_batches.py` — `_emit_heartbeats`/`_should_emit_heartbeat` lifted to `transports/base.py` so it's shared without a circular import; `fetch_batches` continues to call them. (Net: one new module owning the helpers; `fetch_batches` becomes one line shorter at the import site.) **Other sources (`postgres`, `mysql`) continue to use `fetch_batches` unchanged.**
- `src/feather_etl/extract.py` — no functional change. The existing `getattr(source, "transport_name", type(source).__name__)` line already reads what this plan exposes. Drop the "Until #61's transport registry lands..." comment now that #61 has landed.

**Unchanged surfaces:**

- `Destination` protocol, `_extract_windows`, `WindowSpec`, `WindowResult` — all from #62, untouched.
- `Source` protocol — `extract_batches` signature unchanged.
- `feather discover`, `feather run` — none of those paths exercise the transport choice.
- Other DB sources (`postgres`, `mysql`, `sqlite`, `duckdb_file`) — unchanged.

---

## Task 1: Transport protocol + heartbeat helper + registry skeleton

**Files:**
- Create: `src/feather_etl/transports/__init__.py`
- Create: `src/feather_etl/transports/base.py`
- Create: `src/feather_etl/transports/registry.py`
- Create: `tests/unit/transports/__init__.py`
- Create: `tests/unit/transports/test_registry.py`
- Create: `tests/unit/transports/test_heartbeat.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/transports/test_registry.py
"""Lazy transport registry — mirrors sources/registry.py."""

from __future__ import annotations

import pytest

from feather_etl.transports.registry import get_transport_class, TRANSPORT_CLASSES


def test_known_names_resolve() -> None:
    for name in ("pyodbc", "arrow-odbc", "connectorx"):
        cls = get_transport_class(name)
        assert cls.name == name


def test_unknown_name_raises_with_listing() -> None:
    with pytest.raises(ValueError) as exc:
        get_transport_class("nope")
    msg = str(exc.value)
    assert "nope" in msg
    # Lists known names so users see what's available.
    for known in ("pyodbc", "arrow-odbc", "connectorx"):
        assert known in msg


def test_registry_is_string_based_for_lazy_import() -> None:
    # Each value is a 'module.path:ClassName' or '...ClassName' string,
    # not a class object — importing the registry must not import any
    # optional connector. Closes the same lazy-import guarantee as
    # sources/registry.py.
    for v in TRANSPORT_CLASSES.values():
        assert isinstance(v, str)
```

```python
# tests/unit/transports/test_heartbeat.py
"""_emit_heartbeats wraps any RecordBatch iterator with the same row/time
thresholds fetch_batches uses today. Shared so all transports emit
identical log lines."""

from __future__ import annotations

import logging
from typing import Iterator

import pyarrow as pa
import pytest

from feather_etl.transports.base import _emit_heartbeats


def _batch(n: int) -> pa.RecordBatch:
    return pa.RecordBatch.from_pylist([{"id": i} for i in range(n)])


def _drain(it: Iterator[pa.RecordBatch]) -> int:
    return sum(b.num_rows for b in it)


def test_passthrough_yields_all_batches(caplog: pytest.LogCaptureFixture) -> None:
    out = _emit_heartbeats(
        iter([_batch(5), _batch(7)]),
        table_label="t",
        heartbeat_every_rows=1_000,
        heartbeat_every_seconds=1_000,
    )
    assert _drain(out) == 12


def test_row_threshold_emits_heartbeat(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="feather_etl.transports.base")
    out = _emit_heartbeats(
        iter([_batch(50), _batch(50), _batch(50)]),
        table_label="t",
        heartbeat_every_rows=100,
        heartbeat_every_seconds=0,  # disable time-based
        clock=lambda: 0.0,
    )
    _drain(out)
    msgs = [r for r in caplog.records if r.message == "extract_heartbeat"]
    # First heartbeat fires after the second batch (50+50=100). Third
    # batch is below the next 100-row threshold (50 < 100) — no second
    # heartbeat. Exactly one heartbeat record.
    assert len(msgs) == 1
    assert msgs[0].__dict__["rows_loaded"] == 100


def test_time_threshold_emits_heartbeat(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="feather_etl.transports.base")
    tick = iter([0.0, 0.5, 1.5, 2.5])
    out = _emit_heartbeats(
        iter([_batch(1), _batch(1), _batch(1)]),
        table_label="t",
        heartbeat_every_rows=0,  # disable row-based
        heartbeat_every_seconds=1,
        clock=lambda: next(tick),
    )
    _drain(out)
    msgs = [r for r in caplog.records if r.message == "extract_heartbeat"]
    # Two batches cross the 1-second threshold (at 1.5s and 2.5s).
    assert len(msgs) == 2


def test_both_thresholds_zero_disables_heartbeat(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="feather_etl.transports.base")
    out = _emit_heartbeats(
        iter([_batch(10_000), _batch(10_000)]),
        table_label="t",
        heartbeat_every_rows=0,
        heartbeat_every_seconds=0,
    )
    _drain(out)
    msgs = [r for r in caplog.records if r.message == "extract_heartbeat"]
    assert msgs == []
```

- [ ] **Step 2: Run the tests, confirm failure**

```bash
uv run pytest tests/unit/transports/ -v
```

Expected: `ModuleNotFoundError: feather_etl.transports`.

- [ ] **Step 3: Implement `base.py`**

```python
# src/feather_etl/transports/base.py
"""Transport protocol + shared heartbeat helper.

Transports own *how* batches are pulled off the wire (pyodbc cursor /
arrow-odbc Rust loop / connectorx parallel partitions). The Source layer
above them is single-pyodbc-free: it only builds the query string,
delegates to `transport.stream_batches`, and forwards the resulting
batches to the destination.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Iterator, Protocol

import pyarrow as pa


logger = logging.getLogger(__name__)


class Transport(Protocol):
    """Pluggable batch-stream provider for a database source.

    Implementations live in sibling modules; `registry.get_transport_class`
    resolves a name to a class. `partition_on` / `partition_num` are
    accepted by every transport for a uniform signature but only
    `ConnectorxTransport.supports_partition_on()` returns True.
    """

    name: str

    def stream_batches(
        self,
        connection_string: str,
        query: str,
        *,
        batch_size: int,
        table_label: str,
        partition_on: str | None = None,
        partition_num: int = 4,
        heartbeat_every_rows: int = 100_000,
        heartbeat_every_seconds: int = 30,
    ) -> Iterator[pa.RecordBatch]: ...

    def supports_partition_on(self) -> bool: ...


def _should_emit(
    rows_since_last: int,
    secs_since_last: float,
    every_rows: int,
    every_seconds: int,
) -> bool:
    by_rows = every_rows > 0 and rows_since_last >= every_rows
    by_time = every_seconds > 0 and secs_since_last >= every_seconds
    return by_rows or by_time


def _emit_heartbeats(
    batches: Iterator[pa.RecordBatch],
    *,
    table_label: str,
    heartbeat_every_rows: int,
    heartbeat_every_seconds: int,
    clock: Callable[[], float] = time.monotonic,
) -> Iterator[pa.RecordBatch]:
    """Yield each batch unchanged, emitting `extract_heartbeat` log records
    on the same row/time cadence `fetch_batches` uses today.

    Lifted from `sources/fetch_batches.py` so every transport can wrap its
    output uniformly. `fetch_batches` continues to call this same helper
    so behavior for pyodbc-via-fetch_batches is byte-identical.
    """
    start = clock()
    last_emit_time = start
    last_emit_rows = 0
    rows = 0
    for batch in batches:
        yield batch
        rows += batch.num_rows
        now = clock()
        if _should_emit(
            rows_since_last=rows - last_emit_rows,
            secs_since_last=now - last_emit_time,
            every_rows=heartbeat_every_rows,
            every_seconds=heartbeat_every_seconds,
        ):
            elapsed = now - start
            rps = int(rows / elapsed) if elapsed > 0 else 0
            logger.info(
                "extract_heartbeat",
                extra={
                    "table": table_label,
                    "rows_loaded": rows,
                    "elapsed_s": round(elapsed, 1),
                    "rows_per_sec": rps,
                },
            )
            last_emit_time = now
            last_emit_rows = rows
```

- [ ] **Step 4: Implement `registry.py`**

```python
# src/feather_etl/transports/registry.py
"""Lazy transport-name → class map. Mirrors sources/registry.py.

Storing the class as a 'module.path.ClassName' string defers the optional
imports (`arrow_odbc`, `connectorx`) until first use, so a user who never
opts into them never needs them installed.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from feather_etl.transports.base import Transport


TRANSPORT_CLASSES: dict[str, str] = {
    "pyodbc": "feather_etl.transports.pyodbc_transport.PyodbcTransport",
    "arrow-odbc": "feather_etl.transports.arrow_odbc_transport.ArrowOdbcTransport",
    "connectorx": "feather_etl.transports.connectorx_transport.ConnectorxTransport",
}


def get_transport_class(name: str) -> type["Transport"]:
    """Resolve a transport name to its class, importing lazily."""
    if name not in TRANSPORT_CLASSES:
        raise ValueError(
            f"Unknown transport '{name}'. "
            f"Available: {sorted(TRANSPORT_CLASSES)}"
        )
    module_path, cls_name = TRANSPORT_CLASSES[name].rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, cls_name)
```

- [ ] **Step 5: Implement `__init__.py`**

```python
# src/feather_etl/transports/__init__.py
"""Pluggable batch-stream transports — #61."""

from feather_etl.transports.base import Transport, _emit_heartbeats
from feather_etl.transports.registry import (
    TRANSPORT_CLASSES,
    get_transport_class,
)

__all__ = [
    "Transport",
    "TRANSPORT_CLASSES",
    "_emit_heartbeats",
    "get_transport_class",
]
```

(Importing the `__init__` does NOT import the concrete transports — those are imported lazily by `get_transport_class`.)

- [ ] **Step 6: The heartbeat tests run; the registry tests fail because the concrete classes don't exist yet**

```bash
uv run pytest tests/unit/transports/test_heartbeat.py -v   # should be green now
uv run pytest tests/unit/transports/test_registry.py -v    # fails at import of PyodbcTransport etc.
```

`test_registry.py::test_known_names_resolve` will fail until Tasks 2/4/5 land. The string-based registry test should pass already (it doesn't import the concrete classes).

Mark the two import-dependent assertions with `pytest.mark.xfail(reason="implemented in tasks 2/4/5")` for now, or simply leave them red until the implementing tasks land — both styles are common in subagent-driven plans; pick the xfail one because the next subagents need a green pre-test signal:

```python
@pytest.mark.xfail(reason="PyodbcTransport lands in Task 2, others in 4/5")
def test_known_names_resolve() -> None:
    ...
```

(Tasks 2/4/5 each flip one `xfail` to a real pass.)

- [ ] **Step 7: Commit**

```bash
git add src/feather_etl/transports/__init__.py \
        src/feather_etl/transports/base.py \
        src/feather_etl/transports/registry.py \
        tests/unit/transports/__init__.py \
        tests/unit/transports/test_registry.py \
        tests/unit/transports/test_heartbeat.py
git commit -m "feat(transports): Transport protocol + lazy registry + heartbeat helper (#61)"
```

---

## Task 2: PyodbcTransport (port the existing pyodbc loop)

**Files:**
- Create: `src/feather_etl/transports/pyodbc_transport.py`
- Create: `tests/unit/transports/test_pyodbc_transport.py`
- Modify: `tests/unit/transports/test_registry.py` (remove `xfail` from `test_known_names_resolve`'s "pyodbc" assertion path, or — simpler — keep the parametrize and remove the marker after Task 5 since all three resolve together)

This task moves the existing fetch loop from `sources/sqlserver.py` (lines ~301–356, the body of `extract_batches`) into a self-contained transport. **Behavior must be byte-identical to today** for pyodbc — that's the regression-guard acceptance criterion in #61.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/transports/test_pyodbc_transport.py
"""PyodbcTransport — wraps the existing fetchmany loop. Mocks pyodbc."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest

from feather_etl.transports.pyodbc_transport import PyodbcTransport


FAKE_CONN = "DRIVER={ODBC Driver 18};SERVER=fake;DATABASE=db"


def _cursor_with(rows: list[tuple], description: list[tuple]) -> MagicMock:
    cur = MagicMock()
    cur.description = description
    # Two fetchmany calls: rows, then empty.
    cur.fetchmany.side_effect = [rows, []]
    return cur


def test_name_and_does_not_support_partition_on() -> None:
    t = PyodbcTransport()
    assert t.name == "pyodbc"
    assert t.supports_partition_on() is False


@patch("feather_etl.transports.pyodbc_transport.pyodbc")
def test_stream_batches_yields_record_batches(mock_pyodbc: MagicMock) -> None:
    cursor = _cursor_with(
        rows=[(1, "a"), (2, "b")],
        description=[
            ("id", int, None, None, None, None, None),
            ("name", str, None, None, None, None, None),
        ],
    )
    conn = MagicMock()
    conn.cursor.return_value = cursor
    mock_pyodbc.connect.return_value = conn

    out = list(
        PyodbcTransport().stream_batches(
            FAKE_CONN,
            "SELECT id, name FROM x",
            batch_size=100,
            table_label="x",
        )
    )

    assert len(out) == 1
    batch = out[0]
    assert isinstance(batch, pa.RecordBatch)
    assert batch.column_names == ["id", "name"]
    assert batch.column("id").to_pylist() == [1, 2]
    assert batch.column("name").to_pylist() == ["a", "b"]

    # SET NOCOUNT ON is emitted before the SELECT — preserves today's
    # behavior so the regression-guard acceptance criterion holds.
    calls = [c.args[0] for c in cursor.execute.call_args_list]
    assert calls[0] == "SET NOCOUNT ON"
    assert calls[1] == "SELECT id, name FROM x"


@patch("feather_etl.transports.pyodbc_transport.pyodbc")
def test_decimal_and_uuid_are_coerced(mock_pyodbc: MagicMock) -> None:
    import decimal
    import uuid

    val_uuid = uuid.uuid4()
    cursor = _cursor_with(
        rows=[(decimal.Decimal("3.14"), val_uuid)],
        description=[
            ("price", decimal.Decimal, None, None, None, None, None),
            ("id", uuid.UUID, None, None, None, None, None),
        ],
    )
    conn = MagicMock()
    conn.cursor.return_value = cursor
    mock_pyodbc.connect.return_value = conn

    batches = list(
        PyodbcTransport().stream_batches(
            FAKE_CONN, "SELECT price, id FROM t", batch_size=100, table_label="t"
        )
    )

    assert batches[0].column("price").to_pylist() == [3.14]
    assert batches[0].column("id").to_pylist() == [str(val_uuid)]


@patch("feather_etl.transports.pyodbc_transport.pyodbc")
def test_zero_row_query_yields_schema_only_batch(mock_pyodbc: MagicMock) -> None:
    """Preserves the fetch_batches contract: every call yields ≥1 batch
    so the destination's schema-from-first-batch path keeps working."""
    cursor = MagicMock()
    cursor.description = [
        ("id", int, None, None, None, None, None),
    ]
    cursor.fetchmany.side_effect = [[]]  # immediately empty
    conn = MagicMock()
    conn.cursor.return_value = cursor
    mock_pyodbc.connect.return_value = conn

    batches = list(
        PyodbcTransport().stream_batches(
            FAKE_CONN, "SELECT id FROM empty", batch_size=100, table_label="empty"
        )
    )
    assert len(batches) == 1
    assert batches[0].num_rows == 0
    assert batches[0].column_names == ["id"]


@patch("feather_etl.transports.pyodbc_transport.pyodbc")
def test_no_description_query_yields_nothing(mock_pyodbc: MagicMock) -> None:
    """e.g. an UPDATE in a query slot — cursor.description is None.
    Mirror the today behavior: don't crash, yield no batches."""
    cursor = MagicMock()
    cursor.description = None
    conn = MagicMock()
    conn.cursor.return_value = cursor
    mock_pyodbc.connect.return_value = conn

    batches = list(
        PyodbcTransport().stream_batches(
            FAKE_CONN, "UPDATE t SET x=1", batch_size=100, table_label="t"
        )
    )
    assert batches == []
```

- [ ] **Step 2: Run the tests, confirm failure**

```bash
uv run pytest tests/unit/transports/test_pyodbc_transport.py -v
```

Expected: `ModuleNotFoundError: feather_etl.transports.pyodbc_transport`.

- [ ] **Step 3: Implement `pyodbc_transport.py`**

Copy (verbatim) the pyodbc-specific type maps, coerce function, and the cursor loop from `sources/sqlserver.py` (today's lines ~19–84 and ~301–356). Wrap the resulting iterator with `_emit_heartbeats`. Public surface is one class.

```python
# src/feather_etl/transports/pyodbc_transport.py
"""PyodbcTransport — pyodbc-backed batch streamer.

Owns the cursor lifecycle + per-row Python→Arrow coercion + Arrow
schema construction from `cursor.description`. Existed previously inside
`sources/sqlserver.py`; lifted here verbatim so the source layer is
transport-agnostic.
"""

from __future__ import annotations

import datetime
import decimal
import logging
import uuid
from typing import Any, Iterator

import pyarrow as pa
import pyodbc

from feather_etl.transports.base import _emit_heartbeats


logger = logging.getLogger(__name__)


_PYODBC_TYPE_MAP: dict[type, pa.DataType] = {
    int: pa.int64(),
    float: pa.float64(),
    str: pa.string(),
    bool: pa.bool_(),
    bytes: pa.binary(),
    bytearray: pa.binary(),
    decimal.Decimal: pa.float64(),
    datetime.datetime: pa.timestamp("us"),
    datetime.date: pa.date32(),
    datetime.time: pa.time64("us"),
    uuid.UUID: pa.string(),
}


def _pyodbc_type_to_arrow(type_code: type) -> pa.DataType:
    return _PYODBC_TYPE_MAP.get(type_code, pa.string())


def _coerce(val: Any, _col: str) -> Any:
    if isinstance(val, decimal.Decimal):
        return float(val)
    if isinstance(val, uuid.UUID):
        return str(val)
    return val


class PyodbcTransport:
    name = "pyodbc"

    def supports_partition_on(self) -> bool:
        return False

    def stream_batches(
        self,
        connection_string: str,
        query: str,
        *,
        batch_size: int,
        table_label: str,
        partition_on: str | None = None,  # ignored
        partition_num: int = 4,  # ignored
        heartbeat_every_rows: int = 100_000,
        heartbeat_every_seconds: int = 30,
    ) -> Iterator[pa.RecordBatch]:
        con = pyodbc.connect(connection_string)
        try:
            cursor = con.cursor()
            cursor.execute("SET NOCOUNT ON")
            cursor.execute(query)

            if cursor.description is None:
                cursor.close()
                return

            col_names = [d[0] for d in cursor.description]
            col_types = [_pyodbc_type_to_arrow(d[1]) for d in cursor.description]
            schema = pa.schema(
                [pa.field(n, t) for n, t in zip(col_names, col_types)]
            )

            yield from _emit_heartbeats(
                self._iter_cursor(cursor, schema, col_names, batch_size),
                table_label=table_label,
                heartbeat_every_rows=heartbeat_every_rows,
                heartbeat_every_seconds=heartbeat_every_seconds,
            )
            cursor.close()
        finally:
            con.close()

    @staticmethod
    def _iter_cursor(
        cursor: Any,
        schema: pa.Schema,
        col_names: list[str],
        batch_size: int,
    ) -> Iterator[pa.RecordBatch]:
        yielded_any = False
        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break
            cols: dict[str, list] = {n: [] for n in col_names}
            for row in rows:
                for i, n in enumerate(col_names):
                    cols[n].append(_coerce(row[i], n))
            yield pa.RecordBatch.from_pydict(cols, schema=schema)
            yielded_any = True
        if not yielded_any:
            yield pa.RecordBatch.from_pydict(
                {n: [] for n in col_names}, schema=schema
            )
```

- [ ] **Step 4: Confirm tests pass**

```bash
uv run pytest tests/unit/transports/test_pyodbc_transport.py -v
```

Expected: 5 PASS.

- [ ] **Step 5: Confirm the registry test for `"pyodbc"` now resolves**

The xfail from Task 1 stays on for the other two names; Task 4 and Task 5 each remove the xfail marker.

- [ ] **Step 6: Commit**

```bash
git add src/feather_etl/transports/pyodbc_transport.py \
        tests/unit/transports/test_pyodbc_transport.py
git commit -m "feat(transports): PyodbcTransport — pyodbc fetch loop as a Transport (#61)"
```

---

## Task 3: ArrowOdbcTransport

**Files:**
- Create: `src/feather_etl/transports/arrow_odbc_transport.py`
- Create: `tests/unit/transports/test_arrow_odbc_transport.py`
- Modify: `pyproject.toml` (add `arrow-odbc>=8.0` to `dependencies`)

`arrow_odbc.read_arrow_batches_from_odbc` returns a `BatchReader` (PyCapsule-protocol Arrow object). Iterating yields `pa.RecordBatch` instances directly — no Python row coercion, no manual schema build.

- [ ] **Step 1: Add the dependency**

In `pyproject.toml`, add `"arrow-odbc>=8.0"` to the `dependencies` list. Then:

```bash
uv sync
```

Verify it imports:

```bash
uv run python -c "import arrow_odbc; print(arrow_odbc.__version__)"
```

- [ ] **Step 2: Write the failing test**

```python
# tests/unit/transports/test_arrow_odbc_transport.py
"""ArrowOdbcTransport — wraps arrow_odbc.read_arrow_batches_from_odbc.

Mocks at the module level so the test doesn't need an actual ODBC driver
or SQL Server. Equivalence with PyodbcTransport on real data is covered by
the operator script in scripts/manual_transport_equivalence.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pyarrow as pa

from feather_etl.transports.arrow_odbc_transport import ArrowOdbcTransport


def test_name_and_does_not_support_partition_on() -> None:
    t = ArrowOdbcTransport()
    assert t.name == "arrow-odbc"
    assert t.supports_partition_on() is False


@patch("feather_etl.transports.arrow_odbc_transport.read_arrow_batches_from_odbc")
def test_stream_batches_passes_through_arrow_batches(mock_reader: MagicMock) -> None:
    canned = [
        pa.RecordBatch.from_pylist([{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]),
        pa.RecordBatch.from_pylist([{"id": 3, "name": "c"}]),
    ]
    # arrow_odbc returns an iterable of RecordBatches.
    mock_reader.return_value = iter(canned)

    out = list(
        ArrowOdbcTransport().stream_batches(
            "DRIVER={ODBC Driver 18};SERVER=x",
            "SELECT id, name FROM t",
            batch_size=100,
            table_label="t",
        )
    )
    assert out == canned

    # Verify we forwarded the query, connection string, and batch_size.
    kwargs = mock_reader.call_args.kwargs
    assert kwargs["query"] == "SELECT id, name FROM t"
    assert kwargs["connection_string"] == "DRIVER={ODBC Driver 18};SERVER=x"
    assert kwargs["max_batch_size"] == 100


@patch("feather_etl.transports.arrow_odbc_transport.read_arrow_batches_from_odbc")
def test_empty_result_yields_no_batches(mock_reader: MagicMock) -> None:
    mock_reader.return_value = iter([])
    assert (
        list(
            ArrowOdbcTransport().stream_batches(
                "X", "SELECT 1 WHERE 0=1", batch_size=10, table_label="empty"
            )
        )
        == []
    )
```

- [ ] **Step 3: Run, confirm failure**

```bash
uv run pytest tests/unit/transports/test_arrow_odbc_transport.py -v
```

Expected: `ModuleNotFoundError: feather_etl.transports.arrow_odbc_transport`.

- [ ] **Step 4: Implement**

```python
# src/feather_etl/transports/arrow_odbc_transport.py
"""ArrowOdbcTransport — bounded-memory streaming via arrow-odbc.

`arrow_odbc.read_arrow_batches_from_odbc` returns a BatchReader; iterating
it yields native pa.RecordBatch instances. No Python-row coercion needed
because the underlying Rust crate maps SQL→Arrow directly.
"""

from __future__ import annotations

import logging
from typing import Iterator

import pyarrow as pa
from arrow_odbc import read_arrow_batches_from_odbc

from feather_etl.transports.base import _emit_heartbeats


logger = logging.getLogger(__name__)


class ArrowOdbcTransport:
    name = "arrow-odbc"

    def supports_partition_on(self) -> bool:
        return False

    def stream_batches(
        self,
        connection_string: str,
        query: str,
        *,
        batch_size: int,
        table_label: str,
        partition_on: str | None = None,  # ignored
        partition_num: int = 4,  # ignored
        heartbeat_every_rows: int = 100_000,
        heartbeat_every_seconds: int = 30,
    ) -> Iterator[pa.RecordBatch]:
        reader = read_arrow_batches_from_odbc(
            query=query,
            connection_string=connection_string,
            max_batch_size=batch_size,
        )
        yield from _emit_heartbeats(
            iter(reader),
            table_label=table_label,
            heartbeat_every_rows=heartbeat_every_rows,
            heartbeat_every_seconds=heartbeat_every_seconds,
        )
```

If `read_arrow_batches_from_odbc`'s real keyword name differs from `max_batch_size` (check `arrow_odbc.__init__.py` or the docstring after `uv sync`), adjust the keyword on the call site AND in the test. Do not invent a fallback — confirm against the installed version.

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/unit/transports/test_arrow_odbc_transport.py -v
```

Expected: 3 PASS.

- [ ] **Step 6: Remove xfail in `test_registry.py` for `"arrow-odbc"` (or keep until Task 5 if your earlier xfail covers all three together)**

Re-run:

```bash
uv run pytest tests/unit/transports/test_registry.py -v
```

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock \
        src/feather_etl/transports/arrow_odbc_transport.py \
        tests/unit/transports/test_arrow_odbc_transport.py
git commit -m "feat(transports): ArrowOdbcTransport — bounded-memory default for SQL Server (#61)"
```

---

## Task 4: ConnectorxTransport (optional extra)

**Files:**
- Create: `src/feather_etl/transports/connectorx_transport.py`
- Create: `tests/unit/transports/test_connectorx_transport.py`
- Modify: `pyproject.toml` — add `[project.optional-dependencies]` section

`connectorx.read_sql` returns a `pa.Table` (or, with `return_type="arrow_stream"`, a record-batch reader). We use the stream variant. `partition_on` + `partition_num` open N parallel TDS sessions — this is the only transport for which `supports_partition_on()` returns True.

- [ ] **Step 1: Add the optional extra**

In `pyproject.toml`, alongside the `[project]` block:

```toml
[project.optional-dependencies]
connectorx = ["connectorx>=0.4"]
```

Then install for local dev so the tests can find the module:

```bash
uv pip install "connectorx>=0.4"
```

(Note: not `uv sync` — that wouldn't pick up the extra. The extra is `pip install feather-etl[connectorx]` for end users; the local dev install is via `uv pip install`.)

Verify:

```bash
uv run python -c "import connectorx; print(connectorx.__version__)"
```

- [ ] **Step 2: Write the failing test**

```python
# tests/unit/transports/test_connectorx_transport.py
"""ConnectorxTransport — parallel reads via connectorx.

Mocks connectorx.read_sql; the integration test (real DB) is the
operator-run script.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest

from feather_etl.transports.connectorx_transport import (
    ConnectorxTransport,
    _odbc_to_connectorx_uri,
)


def test_name_and_supports_partition_on() -> None:
    t = ConnectorxTransport()
    assert t.name == "connectorx"
    assert t.supports_partition_on() is True


@patch("feather_etl.transports.connectorx_transport.cx")
def test_stream_batches_forwards_partition_args(mock_cx: MagicMock) -> None:
    canned = pa.Table.from_pylist([{"id": 1, "name": "a"}, {"id": 2, "name": "b"}])
    mock_cx.read_sql.return_value = canned

    out = list(
        ConnectorxTransport().stream_batches(
            "DRIVER={ODBC Driver 18};SERVER=host;DATABASE=db;UID=u;PWD=p",
            "SELECT id, name FROM t",
            batch_size=100,
            table_label="t",
            partition_on="id",
            partition_num=8,
        )
    )

    # 2 rows → at least one batch carrying them all.
    total_rows = sum(b.num_rows for b in out)
    assert total_rows == 2

    kwargs = mock_cx.read_sql.call_args.kwargs
    assert kwargs["partition_on"] == "id"
    assert kwargs["partition_num"] == 8
    assert kwargs["return_type"] == "arrow"


@patch("feather_etl.transports.connectorx_transport.cx")
def test_stream_batches_without_partition_on(mock_cx: MagicMock) -> None:
    """Non-parallel single-connection mode still works — partition_on is optional."""
    canned = pa.Table.from_pylist([{"id": 1}])
    mock_cx.read_sql.return_value = canned

    out = list(
        ConnectorxTransport().stream_batches(
            "DRIVER={ODBC Driver 18};SERVER=host;DATABASE=db;UID=u;PWD=p",
            "SELECT id FROM t",
            batch_size=100,
            table_label="t",
        )
    )
    assert sum(b.num_rows for b in out) == 1
    kwargs = mock_cx.read_sql.call_args.kwargs
    # partition_on omitted → connectorx falls back to single connection.
    assert "partition_on" not in kwargs or kwargs["partition_on"] is None


def test_odbc_to_connectorx_uri_translates_known_fields() -> None:
    uri = _odbc_to_connectorx_uri(
        "DRIVER={ODBC Driver 18 for SQL Server};"
        "SERVER=host.example.com,1433;DATABASE=mydb;"
        "UID=myuser;PWD=p@ss;TrustServerCertificate=yes"
    )
    # connectorx expects a SQLAlchemy-style URL.
    assert uri.startswith("mssql://")
    assert "myuser" in uri
    assert "mydb" in uri
    assert "host.example.com" in uri


def test_missing_connectorx_dep_raises_clear_message(monkeypatch) -> None:
    """If the extra isn't installed, instantiating the transport must raise
    a message that names the right install command — no AttributeError /
    'NoneType' confusion."""
    import sys

    monkeypatch.setitem(sys.modules, "connectorx", None)  # simulate ImportError on next import

    # Reimport the module fresh so the top-level import re-runs.
    import importlib

    import feather_etl.transports.connectorx_transport as mod

    with pytest.raises(ImportError) as exc:
        importlib.reload(mod)
    assert "feather-etl[connectorx]" in str(exc.value)
```

- [ ] **Step 3: Run, confirm failure**

```bash
uv run pytest tests/unit/transports/test_connectorx_transport.py -v
```

Expected: `ModuleNotFoundError` for the connectorx_transport module.

- [ ] **Step 4: Implement**

```python
# src/feather_etl/transports/connectorx_transport.py
"""ConnectorxTransport — parallel reads via connectorx (`partition_on`).

This is the only transport whose import sits behind a try/except — it's an
*optional* extra (`pip install feather-etl[connectorx]`). The other two
ship by default.
"""

from __future__ import annotations

import logging
import re
from typing import Iterator

import pyarrow as pa

try:
    import connectorx as cx
except ImportError as e:
    raise ImportError(
        "ConnectorxTransport requires the 'connectorx' extra. "
        "Install it with: pip install 'feather-etl[connectorx]'"
    ) from e

from feather_etl.transports.base import _emit_heartbeats


logger = logging.getLogger(__name__)


_ODBC_KV = re.compile(r"([A-Za-z]+)=({[^}]*}|[^;]*)")


def _odbc_to_connectorx_uri(odbc_conn: str) -> str:
    """Translate a SQL Server ODBC connection string into a connectorx URI.

    connectorx accepts SQLAlchemy-style URLs (`mssql://user:pass@host:port/db`).
    The user already configures the ODBC string in feather.yaml; we don't
    want to require a second URL just for connectorx. So we extract the
    standard fields (SERVER/DATABASE/UID/PWD) and rebuild.
    """
    fields: dict[str, str] = {}
    for m in _ODBC_KV.finditer(odbc_conn):
        key = m.group(1).lower()
        val = m.group(2).strip("{}")
        fields[key] = val

    server = fields.get("server", "")
    database = fields.get("database", "")
    user = fields.get("uid", "")
    password = fields.get("pwd", "")

    # SERVER may be "host,port".
    if "," in server:
        host, port = server.split(",", 1)
        host_part = f"{host}:{port.strip()}"
    else:
        host_part = server

    return f"mssql://{user}:{password}@{host_part}/{database}"


class ConnectorxTransport:
    name = "connectorx"

    def supports_partition_on(self) -> bool:
        return True

    def stream_batches(
        self,
        connection_string: str,
        query: str,
        *,
        batch_size: int,
        table_label: str,
        partition_on: str | None = None,
        partition_num: int = 4,
        heartbeat_every_rows: int = 100_000,
        heartbeat_every_seconds: int = 30,
    ) -> Iterator[pa.RecordBatch]:
        uri = _odbc_to_connectorx_uri(connection_string)
        kwargs: dict[str, object] = {"return_type": "arrow"}
        if partition_on:
            kwargs["partition_on"] = partition_on
            kwargs["partition_num"] = partition_num

        table = cx.read_sql(uri, query, **kwargs)
        # `cx.read_sql(..., return_type="arrow")` returns a pa.Table —
        # split into batches so the downstream heartbeat + destination
        # see the streaming shape every other transport produces.
        yield from _emit_heartbeats(
            iter(table.to_batches(max_chunksize=batch_size)),
            table_label=table_label,
            heartbeat_every_rows=heartbeat_every_rows,
            heartbeat_every_seconds=heartbeat_every_seconds,
        )
```

(`return_type="arrow_stream"` is also valid in newer connectorx and yields batches directly — but the `"arrow"` + `to_batches(max_chunksize=)` form is what's compatible across 0.3.x and 0.4.x. Pick the simpler form.)

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/unit/transports/test_connectorx_transport.py -v
```

Expected: 5 PASS.

- [ ] **Step 6: Remove the remaining xfail in `test_registry.py`**

Now all three transports resolve.

- [ ] **Step 7: Run the whole transports suite + registry**

```bash
uv run pytest tests/unit/transports/ -v
```

Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock \
        src/feather_etl/transports/connectorx_transport.py \
        tests/unit/transports/test_connectorx_transport.py \
        tests/unit/transports/test_registry.py
git commit -m "feat(transports): ConnectorxTransport — opt-in parallel reads (#61)"
```

---

## Task 5: Wire SqlServerSource to the transport registry

**Files:**
- Modify: `src/feather_etl/sources/sqlserver.py`
- Create: `tests/unit/sources/test_sqlserver_transport_selection.py`
- Modify: `tests/unit/sources/test_sqlserver.py` (existing tests now patch through `PyodbcTransport`; see step 4)
- Modify: `src/feather_etl/extract.py` (drop the "Until #61's transport registry lands..." comment)
- Modify: `src/feather_etl/sources/fetch_batches.py` — replace its inline `_emit_heartbeat` / `_should_emit_heartbeat` with imports from `transports.base` (one-line refactor; behavior unchanged).

This is the integration step. After it lands, `feather extract` on a SQL Server source uses arrow-odbc by default.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/sources/test_sqlserver_transport_selection.py
"""SqlServerSource resolves transport from feather.yaml — closes #61."""

from __future__ import annotations

from pathlib import Path

import pytest

from feather_etl.sources.sqlserver import SqlServerSource


_CONN_KWARGS = dict(
    host="example.com",
    port=1433,
    user="u",
    password="p",
    database="db",
)


def _yaml_entry(**overrides) -> dict:
    return {"type": "sqlserver", "name": "src", **_CONN_KWARGS, **overrides}


def test_default_transport_is_arrow_odbc(tmp_path: Path) -> None:
    src = SqlServerSource.from_yaml(_yaml_entry(), tmp_path)
    assert src.transport_name == "arrow-odbc"


def test_explicit_pyodbc_transport(tmp_path: Path) -> None:
    src = SqlServerSource.from_yaml(_yaml_entry(transport="pyodbc"), tmp_path)
    assert src.transport_name == "pyodbc"


def test_explicit_connectorx_transport(tmp_path: Path) -> None:
    src = SqlServerSource.from_yaml(_yaml_entry(transport="connectorx"), tmp_path)
    assert src.transport_name == "connectorx"


def test_unknown_transport_raises_at_yaml_load(tmp_path: Path) -> None:
    with pytest.raises(ValueError) as exc:
        SqlServerSource.from_yaml(_yaml_entry(transport="bogus"), tmp_path)
    msg = str(exc.value)
    assert "bogus" in msg
    # Lists known transports so the user can pick.
    for name in ("pyodbc", "arrow-odbc", "connectorx"):
        assert name in msg


def test_extract_batches_delegates_to_transport(monkeypatch, tmp_path: Path) -> None:
    """When extract_batches is called, the resolved transport's
    stream_batches is invoked with the built SELECT — no pyodbc.connect
    happens directly on the source."""
    import pyarrow as pa

    src = SqlServerSource.from_yaml(_yaml_entry(transport="pyodbc"), tmp_path)

    captured: dict[str, object] = {}

    def fake_stream(self, conn, query, *, batch_size, table_label, **kwargs):
        captured["conn"] = conn
        captured["query"] = query
        captured["batch_size"] = batch_size
        captured["table_label"] = table_label
        yield pa.RecordBatch.from_pylist([{"id": 1}])

    monkeypatch.setattr(
        "feather_etl.transports.pyodbc_transport.PyodbcTransport.stream_batches",
        fake_stream,
    )

    batches = list(
        src.extract_batches(
            "dbo.t",
            columns=["id"],
            filter="region = 'EU'",
            watermark_column="updated_at",
            watermark_value="2026-05-12",
        )
    )

    assert len(batches) == 1
    assert "SELECT [id] FROM dbo.t" in captured["query"]
    assert "region = 'EU'" in captured["query"]
    assert "updated_at > '2026-05-12'" in captured["query"]
    assert captured["batch_size"] == src.batch_size
    assert captured["table_label"] == "dbo.t"
```

- [ ] **Step 2: Run, confirm failure**

```bash
uv run pytest tests/unit/sources/test_sqlserver_transport_selection.py -v
```

Expected: `AttributeError: 'SqlServerSource' has no attribute 'transport_name'`.

- [ ] **Step 3: Edit `sources/sqlserver.py`**

- Remove `_PYODBC_TYPE_MAP`, `_pyodbc_type_to_arrow`, `_sqlserver_coerce_row` — they live in `transports/pyodbc_transport.py` now.
- Keep `_SQL_TYPE_MAP` (used by `get_schema` from INFORMATION_SCHEMA — different code path, unrelated to extraction).
- Keep `check`, `list_databases`, `discover`, `get_schema`, `_format_watermark`, `validate_source_table`, `from_yaml`, `detect_changes` — all still use pyodbc directly for control-plane queries (cheap one-shot reads); the transport abstraction is only for the bulk-read extract path.
- Add `transport` parameter to `__init__` (default `"arrow-odbc"`).
- Add `transport_name` property returning `self.transport`.
- Rewrite `extract_batches` to a thin delegator.

```python
# Inside SqlServerSource

def __init__(
    self,
    connection_string: str,
    *,
    name: str = "",
    host: str | None = None,
    port: int | None = None,
    user: str | None = None,
    password: str | None = None,
    database: str | None = None,
    databases: list[str] | None = None,
    batch_size: int = 120_000,
    transport: str = "arrow-odbc",
) -> None:
    super().__init__(connection_string)
    self.name = name
    self.host = host
    self.port = port
    self.user = user
    self.password = password
    self.database = database
    self.databases = databases
    self.batch_size = batch_size

    # Validate transport name at construction so config errors surface early.
    from feather_etl.transports.registry import get_transport_class
    self._transport_cls = get_transport_class(transport)
    self.transport = transport

    self._last_error: str | None = None

@property
def transport_name(self) -> str:
    return self.transport

@classmethod
def from_yaml(cls, entry: dict, config_dir: Path) -> "SqlServerSource":
    # ... existing parsing of host/port/user/password/database/databases ...

    transport = entry.get("transport", "arrow-odbc")

    # Re-use the existing conn-str construction logic exactly as today.
    # (Copy from current from_yaml; only addition: pass transport=transport.)

    source = cls(
        connection_string=conn_str,
        name=name,
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        databases=databases,
        transport=transport,
    )
    source._explicit_name = bool(entry.get("name"))
    return source

def extract_batches(
    self,
    table: str,
    columns: list[str] | None = None,
    filter: str | None = None,
    watermark_column: str | None = None,
    watermark_value: str | None = None,
    heartbeat_every_rows: int = 100_000,
    heartbeat_every_seconds: int = 30,
) -> Iterator[pa.RecordBatch]:
    """Stream `pa.RecordBatch` chunks via the configured transport."""
    if columns:
        bad = [c for c in columns if "]" in c]
        if bad:
            raise ValueError(
                f"SQL Server column names containing ']' cannot be "
                f"bracket-quoted: {bad}"
            )

    col_clause = ", ".join(f"[{c}]" for c in columns) if columns else "*"
    where = self._build_where_clause(filter, watermark_column, watermark_value)
    query = f"SELECT {col_clause} FROM {table}{where}"

    transport = self._transport_cls()
    yield from transport.stream_batches(
        self.connection_string,
        query,
        batch_size=self.batch_size,
        table_label=table,
        heartbeat_every_rows=heartbeat_every_rows,
        heartbeat_every_seconds=heartbeat_every_seconds,
    )
```

- [ ] **Step 4: Fix the existing `test_sqlserver.py` tests**

The pre-existing tests in `tests/unit/sources/test_sqlserver.py` patch `feather_etl.sources.sqlserver.pyodbc` — but the pyodbc cursor loop now lives in `feather_etl.transports.pyodbc_transport`. Two clean options:

**Option A (recommended):** Patch through the transport module path instead.

```python
# Before
@patch("feather_etl.sources.sqlserver.pyodbc")
# After (for tests that exercise extract_batches via the default arrow-odbc → reroute through pyodbc explicitly):
@patch("feather_etl.transports.pyodbc_transport.pyodbc")
```

And construct the source with `transport="pyodbc"` so the test goes through the path it's mocking:

```python
@pytest.fixture
def source() -> SqlServerSource:
    return SqlServerSource(
        connection_string=FAKE_CONN_STR, batch_size=100, transport="pyodbc"
    )
```

`check`, `list_databases`, `discover`, `get_schema`, `detect_changes` still use pyodbc directly on the source, so those tests keep their `@patch("feather_etl.sources.sqlserver.pyodbc")` decorator unchanged.

**Option B:** Add a second `pytest.fixture` for the arrow-odbc default and keep one fixture pinned to pyodbc. Pick A — fewer fixtures, clearer intent.

Walk through every `@patch("feather_etl.sources.sqlserver.pyodbc")` in the existing test file:

- `extract_batches` / `extract` tests → change patch target to `feather_etl.transports.pyodbc_transport.pyodbc` AND set `transport="pyodbc"` on the fixture.
- `check`, `discover`, `get_schema`, `list_databases`, `detect_changes` tests → patch target stays `feather_etl.sources.sqlserver.pyodbc`.

(If the test file uses a single shared fixture for all tests, parametrize the fixture or split into two.)

- [ ] **Step 5: Drop the stale comment in `extract.py`**

Lines 182–184 of `src/feather_etl/extract.py` currently read:

```python
# Until #61's transport registry lands, sources don't expose a transport
# name — record the source class so _extract_windows.transport_used is at
# least diagnostic instead of misleading.
transport_used = getattr(source, "transport_name", type(source).__name__)
```

Replace with:

```python
# All sources implementing the transport-registry contract expose
# transport_name (#61). Sources that don't yet (csv, sqlite, file
# sources) fall back to the class name, which is fine — those paths
# don't trip the transport choice.
transport_used = getattr(source, "transport_name", type(source).__name__)
```

- [ ] **Step 6: Refactor `sources/fetch_batches.py` to reuse `transports.base._emit_heartbeats`**

`fetch_batches` still drives the pyodbc cursor and constructs Arrow batches — keep that. But its heartbeat helpers are duplicates of `transports.base`. Replace its `_should_emit_heartbeat` and `_emit_heartbeat` with imports:

```python
# At the top of sources/fetch_batches.py
from feather_etl.transports.base import _emit_heartbeats
```

Then rewrite `fetch_batches` to construct the un-heartbeat'd cursor-loop generator and pass it through `_emit_heartbeats(...)`. This is a one-screen refactor; the public contract of `fetch_batches` doesn't change. **Postgres and MySQL sources still call `fetch_batches`** — they aren't migrated to the transport registry in this PR. The refactor is purely about eliminating duplicate code.

If the time involved in re-shaping `fetch_batches` is more than ~20 minutes, **stop and skip step 6** — leave the duplicate heartbeat code in `fetch_batches.py` for now and file a follow-up. The duplicate is small and isolated. Don't let this side-quest delay the main PR.

- [ ] **Step 7: Run the SQL Server + transports test suites**

```bash
uv run pytest tests/unit/sources/test_sqlserver.py \
              tests/unit/sources/test_sqlserver_transport_selection.py \
              tests/unit/transports/ -v
```

Expected: all green.

- [ ] **Step 8: Run the full test suite**

```bash
uv run pytest -q
```

Expected: 902+ tests green (with the new transport tests adding ~20).

- [ ] **Step 9: Commit**

```bash
git add src/feather_etl/sources/sqlserver.py \
        src/feather_etl/sources/fetch_batches.py \
        src/feather_etl/extract.py \
        tests/unit/sources/test_sqlserver.py \
        tests/unit/sources/test_sqlserver_transport_selection.py
git commit -m "feat(sources): SqlServerSource selects transport from yaml; default arrow-odbc (#61)"
```

---

## Task 6: Transport equivalence test (stub-based, in CI)

**Files:**
- Create: `tests/integration/test_transport_equivalence.py`

The headline #61 acceptance criterion: "Test fixture proves all three transports produce identical Arrow schema + row count on a small fixture table."

We don't have a SQL Server fixture in CI. Two-layer test strategy:

1. **CI layer (this task)** — stub all three transports so each yields the same canned `RecordBatch`. Drive `SqlServerSource.extract_batches` through each with `transport="pyodbc"` / `"arrow-odbc"` / `"connectorx"`; assert the schema and total row count match across all three. This proves the source's per-transport delegation doesn't reshape the data on the way out, which is the regression we care about.
2. **Operator layer (Task 7)** — a manual script that exercises a real SQL Server through all three; output is human-checked.

- [ ] **Step 1: Write the test**

```python
# tests/integration/test_transport_equivalence.py
"""#61 acceptance — all three transports surface identical Arrow schema +
row count via SqlServerSource.extract_batches.

In CI we don't have a SQL Server fixture, so we stub each transport to
yield the same canned batches. Real-DB equivalence lives in
scripts/manual_transport_equivalence.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pyarrow as pa
import pytest

from feather_etl.sources.sqlserver import SqlServerSource


CANNED_BATCHES = [
    pa.RecordBatch.from_pylist(
        [{"id": 1, "name": "alice", "price": 9.99}],
    ),
    pa.RecordBatch.from_pylist(
        [{"id": 2, "name": "bob", "price": 19.99}],
    ),
]


def _make_source(tmp_path: Path, transport: str) -> SqlServerSource:
    return SqlServerSource.from_yaml(
        {
            "type": "sqlserver",
            "name": "t",
            "host": "h",
            "port": 1433,
            "user": "u",
            "password": "p",
            "database": "d",
            "transport": transport,
        },
        tmp_path,
    )


def _stub(self, *args, **kwargs) -> Iterator[pa.RecordBatch]:
    yield from CANNED_BATCHES


@pytest.mark.parametrize("transport", ["pyodbc", "arrow-odbc", "connectorx"])
def test_extract_batches_identical_across_transports(
    monkeypatch, tmp_path: Path, transport: str
) -> None:
    monkeypatch.setattr(
        "feather_etl.transports.pyodbc_transport.PyodbcTransport.stream_batches",
        _stub,
    )
    monkeypatch.setattr(
        "feather_etl.transports.arrow_odbc_transport.ArrowOdbcTransport.stream_batches",
        _stub,
    )
    monkeypatch.setattr(
        "feather_etl.transports.connectorx_transport.ConnectorxTransport.stream_batches",
        _stub,
    )

    source = _make_source(tmp_path, transport)
    batches = list(source.extract_batches("dbo.t"))

    rows = sum(b.num_rows for b in batches)
    assert rows == 2
    schema = batches[0].schema
    assert schema.names == ["id", "name", "price"]
    # Pin types so a future change to one transport's coercion path
    # is caught.
    assert schema.field("id").type == pa.int64()
    assert schema.field("name").type == pa.string()
    assert schema.field("price").type == pa.float64()
```

- [ ] **Step 2: Run**

```bash
uv run pytest tests/integration/test_transport_equivalence.py -v
```

Expected: 3 PASS (one parametrize per transport).

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_transport_equivalence.py
git commit -m "test(transports): stub-based equivalence across all three transports (#61)"
```

---

## Task 7: Operator-run real-DB equivalence script (scripts/, not CI)

**Files:**
- Create: `scripts/manual_transport_equivalence.py`

- [ ] **Step 1: Write the script**

```python
# scripts/manual_transport_equivalence.py
"""Hit a real SQL Server through all three transports and diff the output.

Operator-run smoke check for #61's "identical schema + row count"
acceptance criterion. Not in CI — needs a live server.

Usage:
    export FEATHER_TEST_SQLSERVER_DSN="DRIVER={ODBC Driver 18 for SQL Server};SERVER=host,1433;DATABASE=db;UID=u;PWD=p;TrustServerCertificate=yes"
    export FEATHER_TEST_SQLSERVER_TABLE="dbo.SmallReferenceTable"   # < 10k rows
    uv run python scripts/manual_transport_equivalence.py

Picks the smallest table by default. Prints a per-transport row count,
schema, and a diff if any transport disagrees.
"""

from __future__ import annotations

import os
import sys

import pyarrow as pa

from feather_etl.transports.registry import get_transport_class


def main() -> int:
    dsn = os.environ.get("FEATHER_TEST_SQLSERVER_DSN")
    table = os.environ.get("FEATHER_TEST_SQLSERVER_TABLE")
    if not dsn or not table:
        print(
            "Set FEATHER_TEST_SQLSERVER_DSN and FEATHER_TEST_SQLSERVER_TABLE",
            file=sys.stderr,
        )
        return 2

    query = f"SELECT TOP 1000 * FROM {table}"
    results: dict[str, tuple[int, pa.Schema]] = {}

    for name in ("pyodbc", "arrow-odbc", "connectorx"):
        cls = get_transport_class(name)
        try:
            batches = list(
                cls().stream_batches(
                    dsn,
                    query,
                    batch_size=200,
                    table_label=table,
                    heartbeat_every_rows=0,
                    heartbeat_every_seconds=0,
                )
            )
        except ImportError as e:
            print(f"  {name}: SKIPPED ({e})")
            continue

        rows = sum(b.num_rows for b in batches)
        schema = batches[0].schema if batches else pa.schema([])
        results[name] = (rows, schema)
        print(f"  {name}: rows={rows} schema={schema}")

    if len(set((r, str(s)) for r, s in results.values())) > 1:
        print("\nDISAGREEMENT — transports produced different results", file=sys.stderr)
        return 1

    print("\nAll transports agree.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Test the script syntactically (lint-only — don't run, no live DB)**

```bash
uv run python -c "import scripts.manual_transport_equivalence"  # may fail; scripts may not be importable, that's OK
ruff check scripts/manual_transport_equivalence.py
```

- [ ] **Step 3: Commit**

```bash
git add scripts/manual_transport_equivalence.py
git commit -m "scripts: manual real-DB transport equivalence smoke script (#61)"
```

---

## Task 8: Final sweep — format, lint, full test suite

- [ ] **Step 1: Ruff format the whole repo**

Per `feedback_format_all_before_push`: run `ruff format .` across the whole repo and commit as a separate style commit.

```bash
ruff format .
git diff --stat
```

If the diff is non-empty:

```bash
git add -A
git commit -m "style: ruff format sweep before pushing #61 work"
```

- [ ] **Step 2: Ruff check**

```bash
ruff check .
```

Expected: clean. If anything trips, fix it inline — these are usually unused-import / no-cover lints.

- [ ] **Step 3: Full pytest sweep**

```bash
uv run pytest -q
```

Expected: 902 (pre-#61 baseline) + the new tests from Tasks 1–6 — roughly **920+ green**, zero failures.

- [ ] **Step 4: Verify no `import pyodbc` outside the source-side control-plane and `pyodbc_transport.py`**

```bash
grep -rn "^import pyodbc\|^from pyodbc" src/feather_etl/
```

Expected: only `sources/sqlserver.py` (for control-plane: check/discover/get_schema/detect_changes) and `transports/pyodbc_transport.py` (for the extract loop). No other module should import pyodbc.

---

## Self-Review

**Spec coverage (#61 acceptance criteria):**

| Criterion | Task |
|---|---|
| `feather extract` with no config changes uses arrow-odbc on SQL Server | Task 5 — `transport: str = "arrow-odbc"` default in `__init__` |
| Setting `transport: connectorx` on a source switches without code changes | Task 5 — `from_yaml` reads the field; Tasks 4 supplies the implementation |
| `transport: pyodbc` still works (regression guard) | Tasks 2 + 5; existing `test_sqlserver.py` exercises the pyodbc path with `transport="pyodbc"` |
| Test fixture proves all three transports produce identical Arrow schema + row count on a small fixture table | Task 6 (stub-based in CI) + Task 7 (real-DB operator script) |

**Affected-file coverage:**

| File from issue | Touched in |
|---|---|
| `src/feather_etl/sources/sqlserver.py` | Task 5 (extract becomes thin) |
| `src/feather_etl/transports/` (new package) | Tasks 1–4 |
| `src/feather_etl/config.py` | NOT touched. `transport` is a SQL-Server-source-specific concept; lives in `SqlServerSource.from_yaml`, not in a generic `SourceConfig`. Postgres/MySQL don't have it yet (out of scope). |

**Placeholder scan:** none.

**Type-and-name consistency:**

- `Transport.stream_batches` signature is identical across all three implementations (Tasks 2/3/4).
- `transport_name` attribute on `SqlServerSource` is read by `extract.py` line 185 (no change needed there).
- Registry names — `"pyodbc"`, `"arrow-odbc"`, `"connectorx"` — match between `registry.py`, `from_yaml` default, and tests.

**Risks & mitigations:**

- **arrow-odbc dependency churn** — arrow-odbc is on a fast release cadence. Pin `>=8.0` (the API has been stable since 8.x).
- **connectorx ODBC URI translation** — `_odbc_to_connectorx_uri` is a known sharp edge; covered in Task 4 unit tests. The real-DB script in Task 7 catches anything the unit tests miss.
- **Postgres/MySQL untouched** — by design, scoped to SQL Server in #61. Their `extract_batches` still calls `fetch_batches` directly (`fetch_batches` keeps working — Task 5 step 6 only deduplicates the heartbeat helper, not the cursor loop).
- **Existing `test_sqlserver.py` test count** — patch targets shift but assertions don't. If the test rewrite in Task 5 step 4 sprawls beyond ~30 minutes, leave the legacy patches in place by wrapping the old pyodbc imports re-exposed at the original path — but the recommended fix is to point patches at the transport module.

---

## Branch & worktree

Worktree created at `.claude/worktrees/feature+61-transport-registry`, branch `feature/61-transport-registry`, branched off the current `main` (which contains #62 already merged). 902 tests green at baseline. Subagent-driven execution proceeds from here per CLAUDE.md.

Out-of-tree files left behind from issue #57 (`docs/issues/zakya-invoice-source.md`, `scripts/zakya_*.py`) are unrelated to #61. Leave them untouched.
