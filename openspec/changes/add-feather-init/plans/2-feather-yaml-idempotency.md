# 2. feather.yaml idempotency

## 1. Full picture

### What this commit ships?

This commit makes `feather init` idempotent for `feather.yaml` and in doing so lands three reusable patterns in one scenario: the **skip-if-exists branch** inside `_stamp_feather_yaml`, the **`messages` channel** on `InitResult`, and **cli's per-message echo loop** on stderr. 

The observable change is small (one extra branch, one new field, a four-line loop — re-running init no longer clobbers an existing `feather.yaml`); the architectural deposit is the contract that every subsequent file-stamper inherits.

### What is in scope?

- `init.2b` (feather.yaml present is preserved)

### What MUST stay out of this commit?

_Deferred to later commits in this verb. Don't include them now._

- additional `InitResult` fields beyond `target` + `files` + the new `messages` field (do NOT add `feather_etl_source_path` or any other field — design.md Decision 4 shows the final 4-field shape; this commit lands only ONE of those fields)
- `echo` helper in cli (single call site this commit; design.md Decision 6 shows the helper as future tidying — defer until a second call site appears)
- `dir` argument + target-dir resolution (mild risk: touching `cli.py` invites "while I'm here" additions; resist)

### What files this commit creates/modifies?

```
src/feather_etl/
  commands/init/
    core.py                             ← adds messages field; skip-if-exists branch
    cli.py                              ← echoes result.messages to stderr
    cli_test.py                         ← adds init.2b test
```

_No new files. Root `src/feather_etl/cli.py` unchanged._

### What the user sees externally — Before/After state?

**Before:**
- Re-running `feather init` in a directory that already has `feather.yaml` silently overwrites it with the template.
- Operator loses any local edits.

**After:**
- Re-running `feather init` in the same directory leaves the existing `feather.yaml` byte-identical.
- Prints `feather.yaml: present (delete this file and re-run to reset)` on stderr.
- Process exits 0 either way.

### How the runtime flows after this commit — what calls what?

1. **Operator** types `feather init` in a directory that already contains `feather.yaml`.
2. **Console script `feather`** resolves to `feather_etl.cli:app`.
3. **Root `cli.py`** dispatches the `"init"` sub-command.
4. **`commands/init/cli.py`** — `init()` callback runs.
5. **`core.init_project(Path.cwd())`**, which:
   - allocates `files = {}` and `messages: list[str] = []`
   - calls `_stamp_feather_yaml(target, files, messages)`, which:
     - checks `(target / "feather.yaml").exists()`
     - if PRESENT: sets `files["feather.yaml"] = "skipped"`; appends `"feather.yaml: present (delete this file and re-run to reset)"` to `messages`
     - if ABSENT: writes `FEATHER_YAML_TEMPLATE`; sets `files["feather.yaml"] = "created"`; no message appended
   - returns `InitResult(target=target, files=files, messages=messages)`
6. **`cli.py`** iterates `for m in result.messages: typer.echo(m, err=True)`.
7. **Typer exits 0**.

---

## 2. Key terms

_Codebase-specific patterns referenced below._

- **Loud-skip.**
  - Skip path that appends one entry to `InitResult.messages` naming the file and pointing at the reset-by-delete path.
  - Used by reset-by-delete files (`feather.yaml` here; later `pyproject.toml`, `.gitignore`).
  - Distinct from the silent-skip path (used later for `.env`) which appends nothing.
  - Message wording is locked by the `feather.yaml stamping` requirement in spec.md.

- **`messages` accumulator.**
  - Mutable `list[str]` allocated in `init_project`, passed by reference to each `_stamp_*` helper, wrapped in frozen `InitResult` at return.
  - Same mutate-then-seal pattern as `files` (Commit 1's plan §2).
  - cli's contract: print every entry verbatim on stderr in order — no parsing, no formatting.

- **Reset-by-delete.**
  - The product contract: init never overwrites a marker file.
  - Operator's recovery for a stale marker is `rm <file>` + re-run.
  - The loud-skip message line is the surface that communicates this rule each time.

---

## 3. Key insights

_Design intents — knowing them prevents over-engineering or premature generalization._

- **Three patterns debut, one scenario lands them.**
  - The single test (`init.2b`) drives skip-if-exists branching + the `messages` field + cli's echo loop into existence simultaneously.
  - None of the three is decoration — each is the first instance of a contract every later per-file stamper that uses reset-by-delete reuses.
  - The right read of this commit is "small behavior, load-bearing patterns" — not "tiny tweak."

- **Idempotent re-run IS the atomicity guarantee.**
  - Decision 7 picked best-effort writes — no two-phase commit, no rollback. The skip-if-exists branch is the mechanism that makes that choice safe: a partial init recovers by re-running.
  - This commit lands the first instance of that branch; `feather.yaml` is the test case for the whole atomicity story.
  - Every later loud-skip file (`pyproject.toml`, `.gitignore`) reuses this same single-branch shape.

- **Core composes, cli prints — "the split" lands here.**
  - Decision 4's "the split" rule (core composes per-file messages; cli prints verbatim) was theoretical in Commit 1 because nothing produced output yet.
  - This commit grounds the rule by introducing the first message and the first echo loop together.
  - Every later loud-skip path inherits the shape without re-deciding.

---

## 4. Tests to write

_Failing tests first (RED), then implementation (GREEN), then 100% line+branch coverage. Not strict TDD._

### feather.yaml present is preserved (`init.2b`)

- **Given:** a working directory containing a `feather.yaml` whose content differs from the template (a sentinel string).
- **When:** the user runs `feather init` with no arguments.
- **Then:** the command exits 0, the file's content is byte-identical to the sentinel, and stderr contains the line `feather.yaml: present (delete this file and re-run to reset)`.

---

## 5. Implementation outline

_The minimum impl needed to pass the above tests — a floor, not a ceiling. Add more only if a test demands it._

### Verb (Core) — `core.py` — extend `InitResult`; add skip-if-exists branch

- **`InitResult.messages` field.**
  - Add `messages: list[str]` to the dataclass; remains `frozen=True`.
  - Field order: `target`, `files`, `messages`. No default — `init_project` always allocates and passes.
- **`_stamp_feather_yaml(target, files, messages)` — extended signature.**
  - Add `messages: list[str]` as the third positional parameter.
  - Branch on `(target / "feather.yaml").exists()`:
    - **Exists arm:** `files["feather.yaml"] = "skipped"`; `messages.append("feather.yaml: present (delete this file and re-run to reset)")`. No write.
    - **Absent arm:** existing behavior (`write_text(FEATHER_YAML_TEMPLATE)` + `files["feather.yaml"] = "created"`). No message appended (create path stays silent).
- **`init_project(target)` — allocate messages and thread through.**
  - Allocate `messages: list[str] = []` alongside `files`.
  - Call `_stamp_feather_yaml(target, files, messages)`.
  - Return `InitResult(target=target, files=files, messages=messages)`.

### Verb (CLI) — `commands/init/cli.py` — echo result.messages to stderr

- After `core.init_project(Path.cwd())`, capture the result into a local variable.
- Add `for m in result.messages: typer.echo(m, err=True)` after the call.
- **No `echo` helper.** Single call site this commit — defer the helper until there's a second.

### Test — `commands/init/cli_test.py` — add init.2b

- **Sentinel content** for the pre-stamped `feather.yaml` must differ from `FEATHER_YAML_TEMPLATE` so the byte-identical assertion is meaningful.
- **`result.stderr`** is available without `mix_stderr=False` (Click 8.2+ default — same convention as Commit 1's tests).
- **Existing Commit 1 tests stay untouched** and continue to pass — neither pre-creates `feather.yaml`, so both still hit the absent arm.

---

## 6. Open questions

_Record any open questions surfaced during plan walk; executor may add more mid-impl._

_None._

---

## 7. Decisions taken

_Record contract changes the plan walk surfaced; note any out-of-band follow-up._

_None._

---

## 8. Deviations

_Record any deviations to this plan, pre-commit with rationale._

_None._
