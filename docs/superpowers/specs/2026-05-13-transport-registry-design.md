# Pluggable transport registry for SQL Server extract

Created: 2026-05-13
Status: DRAFT
Issue: [#61](https://github.com/siraj-samsudeen/feather-etl/issues/61)
Epic: [#60](https://github.com/siraj-samsudeen/feather-etl/issues/60)

---

# Part I — Requirements

## 1. Problem

`feather extract` reads SQL Server through `pyodbc` and nothing else. The cursor + `fetchmany` loop is hard-coded inside `sources/sqlserver.py:extract_batches`. This wedges two real-world workloads into one transport that doesn't fit either of them.

Concretely, on rama-dw (2026-05-12):

- The first bootstrap of `ZAKYA.dbo.Sales` (≈50M rows × 97 cols) ran 3.5 hours through pyodbc before being killed. Python-level row coercion and the synchronous cursor walk pin a single CPU and grow RSS in step with the buffered batches. There is no streaming representation of Arrow-typed batches between the wire and PyArrow.
- A second attempt swapped to `connectorx` (parallel reads via `partition_on`) and got further — 90 minutes — before a laptop disconnect lost everything. But to use connectorx today you have to fork the source class. The mechanism for picking a transport doesn't exist.

The asymmetry that makes this hurt: the two trade-offs we need (bounded RSS, parallel reads) are not improvements to one transport — they are different transports with different operational profiles. `arrow-odbc` gives flat RSS via Arrow-native streaming over the Rust `odbc-api` crate but doesn't parallelise. `connectorx` gives parallel reads but requires per-source URI translation and an extra dependency we don't want to force on every user. `pyodbc` is the lowest-common-denominator fallback we already ship and the only one with rich Python-row error visibility.

Today there is no way to express "use transport X for this source." The transport is the source. That is the wedge we are removing.

This problem is the first sub-issue of epic #60 (adaptive transport + resumable batched extract). #62 (batched-append destination + per-window resume) has already landed on `main`; `extract.py:185` even reads `getattr(source, "transport_name", ...)` for `_extract_windows.transport_used`, anticipating this spec. #63 (pre-flight planner) will read `source.transport.supports_partition_on()` to decide whether the `large` bucket can recommend parallel reads on a given source.

## 2. Goal

An operator who needs a different SQL Server transport sets one field in `feather.yaml`. The next `feather extract` uses it. Absent that field, behaviour stays the same shape it has today — except the default flips to `arrow-odbc` so the typical wide-table extract gets bounded RSS for free.

```yaml
sources:
  - name: ZAKYA
    type: sqlserver
    transport: arrow-odbc        # default; "pyodbc" | "connectorx"
```

After this lands, the rama-dw `ZAKYA.dbo.Sales` extract runs with flat RSS on `arrow-odbc` by default. Switching to `connectorx` for a parallel bootstrap is a one-line YAML change, no Python diff. `pyodbc` is still available as the regression-guard fallback. `_extract_windows.transport_used` records the real transport name across runs.

The mechanism is per-source, lives on the existing YAML surface, and produces a loud failure at config-load time when the transport name is wrong — not at extract time three hours in.

## 3. Acceptance criteria

- `feather extract` against a SQL Server source with no `transport:` field uses `arrow-odbc`. RSS during a 1M-row extract stays flat (no row-count-shaped growth).
- Setting `transport: connectorx` on a source switches to connectorx without any code change. With a `partition_on` column in scope (delivered by #63), reads parallelise across N TDS sessions.
- Setting `transport: pyodbc` reproduces today's behaviour exactly — same Arrow schema, same row count, same heartbeat log lines. This is the regression guard for the existing test surface.
- An unknown transport name raises a clear `ValueError` at `feather.yaml` load time, listing the known names. No extract starts.
- All three transports produce identical Arrow schema and row count when reading the same SQL statement against the same source table. Verified by (a) a stub-based equivalence test in CI, and (b) a real-DB operator script not in CI.
- `_extract_windows.transport_used` records the resolved transport name string (`"pyodbc"` / `"arrow-odbc"` / `"connectorx"`), not the source class name.
- Installing `feather-etl` without the optional connectorx extra does not import `connectorx`. Attempting `transport: connectorx` without the extra raises an `ImportError` naming the install command.

### End-to-end verification

Run by hand once the implementation lands. Uses the rama-dw motivating case.

```bash
# Default: arrow-odbc, no YAML change.
cat feather.yaml
# sources:
#   - name: ZAKYA
#     type: sqlserver
#     host: ...

uv run feather extract zakya_sales --limit 100000
duckdb feather_state.duckdb \
  -c "SELECT transport_used FROM _extract_windows WHERE table_name='bronze.zakya_sales' LIMIT 1"
# Expected: arrow-odbc

# Switch to connectorx for the parallel bootstrap.
yq -i '.sources[0].transport = "connectorx"' feather.yaml
uv pip install 'feather-etl[connectorx]'

uv run feather extract zakya_sales --refresh
duckdb feather_state.duckdb \
  -c "SELECT transport_used FROM _extract_windows WHERE table_name='bronze.zakya_sales' LIMIT 1"
# Expected: connectorx

# Regression: explicitly pin to pyodbc — byte-identical to pre-#61 behaviour.
yq -i '.sources[0].transport = "pyodbc"' feather.yaml
uv run feather extract zakya_sales --refresh
# Expected: completes; heartbeat log lines and Arrow schema identical to before #61.

# Negative path: bad name.
yq -i '.sources[0].transport = "duckdb-extension"' feather.yaml
uv run feather extract zakya_sales
# Expected: ValueError at config-load with message listing pyodbc / arrow-odbc / connectorx.
```

---

# Part II — Design

## 4. Scope

The change is one new package, one widened source `__init__`, and a one-character config field. Nothing else moves.

**In**

- A new package `src/feather_etl/transports/` housing the `Transport` protocol, three concrete transports, a lazy registry, and a shared heartbeat helper.
- A new optional field on SQL Server source YAML entries: `transport: pyodbc | arrow-odbc | connectorx`. Default `arrow-odbc`. Validated at `from_yaml` time via the transport registry.
- `arrow-odbc>=8.0` becomes a hard dependency. It wraps the Rust `odbc-api` crate; the wheel is small and pulls no Python deps we don't already have transitively.
- `connectorx>=0.4` becomes an optional extra: `pip install 'feather-etl[connectorx]'`. The transport module raises a clean `ImportError` at import time when the extra is missing.
- `SqlServerSource.extract_batches` becomes a thin wrapper: build the SELECT, resolve the transport, delegate. Pyodbc-specific row coercion and Arrow schema construction move into `PyodbcTransport`.
- A `transport_name` attribute on `SqlServerSource`. `extract.py:185` already reads this; nothing in `extract.py` needs to change beyond dropping the stale "#61-pending" comment.

**Out**

- Postgres and MySQL transport swap. Same pattern, different PRs per #61's "Out of scope." `PostgresSource` and `MySQLSource` continue to call `fetch_batches` directly. They do not gain a `transport:` field in this RFC.
- Per-table transport override (the issue mentions `transport_overrides` in curation). #58's brainstorming reversed the direction of curation-JSON-shape changes; per-table override would re-open that wound. Per-source on YAML is the locked-down level for #61. A future RFC may revisit per-table if a concrete need surfaces.
- Removing `pyodbc` from `dependencies`. It is still the fallback transport, and the control-plane queries inside `SqlServerSource` (`check`, `discover`, `get_schema`, `detect_changes`, `list_databases`) keep using it directly. These are cheap one-shot reads; routing them through a Transport adds no value.
- DuckDB's mssql community extension as primary transport. Epic #60 calls this out as "revisit in 6 months when it leaves experimental."
- Any change to the `Destination` protocol or `_extract_windows` schema (both shipped in #62).
- Server-side BCP / bulk export. No LAN-attached SQL Server in scope.

## 5. Why this shape

The three decisions worth pinning so they don't get re-litigated:

### Transport as a per-source field, not a per-table field

**Chose:** `transport:` on the source entry in `feather.yaml`.

**Why not per-table** (the path the original issue floated via `curation.json:transport_overrides`)? Three reasons:

1. The cost is paid per source, not per table. Pyodbc vs arrow-odbc is "what kind of cursor opens against this server"; mixing transports across tables of the same source means juggling two connection pools to the same DSN. Operationally backwards.
2. The mechanism #58 reversed for `column_overrides` applies here verbatim: JSON is the wrong surface for behaviour mutation that isn't dependency information. Resurrecting per-table JSON keys re-opens the same dual-source-of-truth concern.
3. The single legitimate per-table case is "this table needs a specific `partition_on` column." That is data the planner (#63) reads from curation already — and `partition_on` is a parameter to `Transport.stream_batches`, not a different transport.

If per-table override becomes a real need later, it slots in via a curation field that overrides `partition_on` and `partition_num` only, leaving the transport class itself per-source.

### `arrow-odbc` as default, not `pyodbc`

**Chose:** Flip the default to `arrow-odbc` in this PR.

**Why not stay on `pyodbc` for safety?** The default exists to be right for the median case. The median rama-dw case is "extract a wide ERP table from SQL Server and don't blow up RSS." Pyodbc's row-loop coercion is the wrong tool for that median. Defaults that protect a minority at the cost of the majority become noise — every wide-table user has to remember to opt in.

The regression guard against this flip is built into the test surface: every existing `test_sqlserver.py` extract test now runs with `transport="pyodbc"` and patches `transports.pyodbc_transport.pyodbc`. The pyodbc path is the most heavily tested path in the codebase; nothing observable changes there.

### `connectorx` as an optional extra, not a hard dependency

**Chose:** `[project.optional-dependencies] connectorx = ["connectorx>=0.4"]`.

**Why not a hard dep?** Connectorx pulls a sizeable Rust toolchain transitively (it owns its own SQL→Arrow conversion in Rust per source dialect). The wheel is ~30MB. Users who don't extract from SQL Server in parallel — most of them — shouldn't pay that cost.

**Why not a soft import inside one transport instead of an extras section?** Soft import with a clean error message is exactly what we do; the extras section is what tells `pip` the right install target. Both. The error message hard-codes the right install command (`pip install 'feather-etl[connectorx]'`) so the operator never has to figure it out.

## 6. How it works

The mechanism slots into the existing extract flow at one point — between `from_yaml` and `extract_batches`. Everything around it is unchanged.

```
feather extract
  │
  ├─ load_config / SqlServerSource.from_yaml                    (extended)
  │     │
  │     ├─ read entry["transport"] (default "arrow-odbc")
  │     ├─ transports.registry.get_transport_class(name)
  │     │     ├─ unknown name ─▶ raise ValueError(known names)
  │     │     └─ known name   ─▶ lazy-import the class, cache on the source
  │     └─ source.transport_name = name
  │
  ├─ enumerate windows from _extract_windows (#62)              (unchanged)
  │
  ├─ for each window:
  │     │
  │     ├─ source.extract_batches(table, columns, filter, ...) (refactored)
  │     │     │
  │     │     ├─ build query: SELECT [cols] FROM table + WHERE ...
  │     │     ├─ transport = self._transport_cls()
  │     │     └─ yield from transport.stream_batches(
  │     │             connection_string, query,
  │     │             batch_size=..., table_label=...,
  │     │             heartbeat_every_rows=..., heartbeat_every_seconds=...)
  │     │           │
  │     │           ├─ PyodbcTransport — owns the cursor+fetchmany loop
  │     │           ├─ ArrowOdbcTransport — wraps read_arrow_batches_from_odbc
  │     │           └─ ConnectorxTransport — wraps cx.read_sql(...) with ODBC→mssql:// URI
  │     │           │
  │     │           └─ each wraps its iterator in _emit_heartbeats(...)
  │     │
  │     └─ destination.load_batched_append(..., transport_used=source.transport_name)  (unchanged, #62)
```

Three implementation details worth pinning so future maintainers don't relitigate them:

- **Heartbeat is shared, not duplicated.** `transports/base.py` owns `_emit_heartbeats(iterator, ...)`. Every transport wraps its raw batch iterator through it. `sources/fetch_batches.py` (still used by Postgres / MySQL) is refactored to import the same helper. Identical heartbeat log lines across transports is a hard requirement — the operator's mental model of "rows/sec" should not depend on which transport produced them.
- **`SqlServerSource` still uses pyodbc directly for the control plane.** `check`, `discover`, `get_schema`, `detect_changes`, `list_databases` are cheap one-shot queries with simple result shapes. Routing them through a Transport adds an indirection without a payoff. The Transport abstraction is for bulk reads only.
- **The ODBC→connectorx URI translation lives inside `ConnectorxTransport`.** Operators configure one connection string per source (the existing ODBC DRIVER=...;SERVER=...;DATABASE=... shape). Connectorx wants `mssql://user:pass@host:port/db`. The translation is in `_odbc_to_connectorx_uri(odbc_conn) -> str`; the source layer never learns connectorx exists. This is the one place real connection-string parsing happens, so it gets dedicated unit tests covering `SERVER=host,port` (comma separator), bracketed-driver-value parsing, and missing-field fallbacks.

Failure handling:

- Unknown transport name at `from_yaml` → `ValueError` listing known names. Loud. No connection attempt.
- Missing `connectorx` extra at module import → `ImportError` naming the install command. Loud.
- Transport-side errors at extract (network drop, auth failure, query parse error) flow up through `extract_batches` exactly as today. The per-window failure semantics from #62 absorb them: the window is not marked committed in `_extract_windows`, the next run resumes.

## 7. Integration surface

**New**

- `src/feather_etl/transports/__init__.py` — re-exports `Transport`, `get_transport_class`, `_emit_heartbeats`.
- `src/feather_etl/transports/base.py` — `Transport` protocol, `_emit_heartbeats(iterator, ...)` helper. Owns the heartbeat row/time thresholds previously inside `fetch_batches.py`.
- `src/feather_etl/transports/pyodbc_transport.py` — `PyodbcTransport`. Lifts today's pyodbc loop and per-row Python→Arrow coercion out of `sources/sqlserver.py`.
- `src/feather_etl/transports/arrow_odbc_transport.py` — `ArrowOdbcTransport`. Wraps `arrow_odbc.read_arrow_batches_from_odbc`. New default.
- `src/feather_etl/transports/connectorx_transport.py` — `ConnectorxTransport` + `_odbc_to_connectorx_uri`. Lazy-imports `connectorx`; raises `ImportError` with the install command if missing.
- `src/feather_etl/transports/registry.py` — `TRANSPORT_CLASSES` string map + `get_transport_class(name)`. Mirrors `sources/registry.py`.
- `tests/unit/transports/` — protocol, registry, heartbeat helper, and one test file per transport. ~20 tests.
- `tests/unit/sources/test_sqlserver_transport_selection.py` — `from_yaml` resolves the field; unknown names raise; `extract_batches` delegates correctly. ~5 tests.
- `tests/integration/test_transport_equivalence.py` — stub-based, drives all three transports through `SqlServerSource.extract_batches` with the same canned batches and asserts identical schema + row count. 3 parametrize cases.
- `scripts/manual_transport_equivalence.py` — operator-run real-DB smoke; not in CI. Gated on `$FEATHER_TEST_SQLSERVER_DSN`.

**Modified**

- `pyproject.toml` — adds `arrow-odbc>=8.0` to `dependencies` and creates `[project.optional-dependencies] connectorx = ["connectorx>=0.4"]`.
- `src/feather_etl/sources/sqlserver.py` — `__init__` and `from_yaml` accept `transport`; `extract_batches` becomes a thin delegator; pyodbc-specific helpers (`_PYODBC_TYPE_MAP`, `_pyodbc_type_to_arrow`, `_sqlserver_coerce_row`) move into `pyodbc_transport.py`. `_SQL_TYPE_MAP` (used by `get_schema` for INFORMATION_SCHEMA) stays — different code path.
- `src/feather_etl/sources/fetch_batches.py` — `_should_emit_heartbeat` / `_emit_heartbeat` deleted; the function imports `_emit_heartbeats` from `transports.base` instead. Public contract of `fetch_batches` unchanged. Postgres/MySQL keep working.
- `src/feather_etl/extract.py` — one comment removed (the "Until #61's transport registry lands…" note at line 182–184). No functional change.
- `tests/unit/sources/test_sqlserver.py` — `@patch` targets shift for the extract-related tests: `feather_etl.sources.sqlserver.pyodbc` → `feather_etl.transports.pyodbc_transport.pyodbc`. The `source` fixture pins `transport="pyodbc"` so the mocked path is the one being exercised. Control-plane tests (`check`, `discover`, `get_schema`, `detect_changes`, `list_databases`) keep their existing patch target — those still hit pyodbc directly on the source.

**Outside this scope**

- `src/feather_etl/config.py` — `transport` is a SQL-Server-source-specific field that lives on `SqlServerSource.from_yaml`, not on the generic config layer. No new `SourceConfig` field.
- `src/feather_etl/destinations/**`, `state.py`, `extract_windows.py` — all shipped in #62, untouched here.
- `src/feather_etl/sources/postgres.py`, `mysql.py`, `sqlite.py`, file sources — unchanged. Postgres/MySQL transport swap is a follow-up.
- `src/feather_etl/curation.py` — no curation-shape change. Per-table transport override is a future RFC.

## 8. Test design

Three tiers, mirroring §6.

**Unit — `tests/unit/transports/` (the protocol and three transports):**

- `test_heartbeat.py` — row-threshold emit, time-threshold emit, both-zero disables, passthrough yields all batches. Uses an injectable clock.
- `test_registry.py` — all three known names resolve to their classes; unknown name raises with the known list in the message; registry values are strings so import is lazy.
- `test_pyodbc_transport.py` — fully mocked `pyodbc`. Covers: `SET NOCOUNT ON` then SELECT order; per-row `Decimal → float` and `UUID → str` coercion; zero-row queries yield one schema-only batch; `cursor.description is None` yields no batches.
- `test_arrow_odbc_transport.py` — mocks `arrow_odbc.read_arrow_batches_from_odbc` at module level. Verifies: forwarded query/conn_str/batch_size; iterator passthrough; empty result yields no batches.
- `test_connectorx_transport.py` — mocks `connectorx.read_sql`. Verifies: `partition_on` / `partition_num` forwarded; absent `partition_on` falls back to single connection; `_odbc_to_connectorx_uri` translates `SERVER=host,port` and bracketed DRIVER values correctly; missing-`connectorx`-dep raises an `ImportError` whose message names the install command.

**Unit — `tests/unit/sources/test_sqlserver_transport_selection.py`:**

- `from_yaml` with no `transport:` field → `source.transport_name == "arrow-odbc"`.
- Explicit `transport: pyodbc`, `transport: connectorx` → correct resolution.
- Unknown name (`"bogus"`) → `ValueError` listing the three known names.
- `extract_batches` builds the SELECT correctly (columns, filter, watermark) and delegates to the resolved transport's `stream_batches`, threading `batch_size` and `table_label` through.

**Integration — `tests/integration/test_transport_equivalence.py` (in CI):**

- Stub all three `stream_batches` methods to yield the same canned `RecordBatch`es. Drive `SqlServerSource.extract_batches` through each via `transport="pyodbc"|"arrow-odbc"|"connectorx"`. Assert: identical schema (names + types pinned), identical total row count. Proves the source-side delegation does not reshape the data on the way out.

**Operator script — `scripts/manual_transport_equivalence.py` (not in CI):**

- Reads `$FEATHER_TEST_SQLSERVER_DSN` and `$FEATHER_TEST_SQLSERVER_TABLE`. Runs `SELECT TOP 1000 * FROM <table>` through each transport. Prints per-transport row count + schema and a diff banner if any disagree. The reproducible-by-hand version of acceptance criterion #4 against a real server.

**Not added**

- No `feather validate` tests. The command's surface does not change.
- No e2e CLI tier beyond what `test_sqlserver.py` already covers — the unit + integration combination already proves the contract end-to-end.
- No fuzz tests on the ODBC→mssql:// URI translation. The fixed-shape ODBC strings we produce in `_SQLSERVER_CONN_TEMPLATE` cover the format space; bespoke user-supplied connection strings flow through with the regex doing best-effort key extraction. If field extraction ever fails, connectorx itself raises a parse error pointing at the URL — which is fine.

Total new test surface: ~28 unit + 3 integration + 1 manual script.

---

## 9. Where the plan picks up

The implementation decomposition lives in [`docs/superpowers/plans/2026-05-13-61-transport-registry.md`](../plans/2026-05-13-61-transport-registry.md). The plan breaks this design into 8 TDD tasks, each one a commit:

1. Transport protocol + heartbeat helper + lazy registry
2. `PyodbcTransport` (verbatim port of today's loop, regression guard)
3. `ArrowOdbcTransport` (`arrow-odbc>=8.0` hard dep)
4. `ConnectorxTransport` (`[connectorx]` extra; ODBC→`mssql://` URI translation)
5. Wire `SqlServerSource.from_yaml(transport=...)`; thin `extract_batches`; fix existing test patches; refactor `fetch_batches.py` heartbeat helpers
6. Stub-based equivalence test in CI
7. Operator real-DB smoke script
8. Ruff format sweep + full suite green
