# Rethink the mode system (dev / prod / test)

**Status:** Open. Discovered during the `feather-transform` change (May 2026). Captured here so the next round of work picks it up cleanly.
**Personas reference:** [`docs/personas.md`](../personas.md)
**Related architecture:** [`docs/architecture/warehouse-layers.md`](../architecture/warehouse-layers.md)
**Found-by spec:** [`openspec/changes/feather-transform/`](../../openspec/changes/feather-transform/) — see design.md "Mode interaction" section and tasks.md 9.15 docstring.

---

## Summary

The mode system (`dev` / `prod` / `test`) was added as a small per-deployment knob, but as it stands it controls four independent concerns at once and breaks the verb trio's composability promise. Three concrete symptoms surfaced while implementing `feather transform`. They are likely facets of the same root issue: **modes-as-preset-bundles cannot express what operators actually want**. The clean fix is probably to remove modes entirely and replace them with explicit per-concern configuration. Until that lands, this doc records the symptoms so we don't paper over them in feature work.

---

## Symptom 1 — `feather extract && feather transform` is NOT equivalent to `feather run` in prod

### What was promised

The `feather-transform` change ships a verb trio: `extract` / `transform` / `run`. The design.md goal stated *"single execution path for silver/gold across both `feather run` and `feather transform`"* and tasks.md 9.15 (verb-equivalence test) was the load-bearing assertion.

### What is true

- `feather extract` always writes to `bronze.*`. The verb was originally `feather cache`; its purpose was "pull a local bronze cache for dev iteration." Renaming did not change behaviour.
- `feather run` in **prod** mode skips bronze entirely: `pipeline._resolve_target` defaults the target to `silver.*` when no explicit `target_table:` is set, and extraction writes the column-mapped/renamed columns directly into silver.
- Therefore in prod mode, Path A (`feather run`) produces `silver.*` populated by extraction with NO `bronze.*` tables. Path B (`feather extract && feather transform`) produces `bronze.*` populated by extraction PLUS `silver.*` as views over bronze. **Different destination states.**

### Current state in code

`tests/integration/test_transform_command.py::test_verb_equivalence` is parametrized over `{dev, test}` only. Prod is excluded with a docstring that points at this issue doc. The test passes in dev and test.

### Possible resolutions

- **A1 — Make `feather extract` mode-aware.** In prod, `extract` writes column-mapped rows directly to `silver.*`, matching what `feather run`'s extraction step does. Restores three-mode equivalence. Cost: the verb's body has to grow the PyArrow column-rename path and bronze-vs-silver target-derivation logic. The verb stops being "dev-only local bronze pull" and becomes "do whatever run's extraction step does, in whichever mode."
- **A2 — Document the asymmetry as intentional.** The verb trio is composable in dev/test only; prod has its own single-verb path. Update PRD/README. Cheap, honest, but it locks operators into `feather run` in prod and they can never iterate on silver SQL in a prod-shaped config without paying the full extraction cost.
- **A3 — Remove modes (see Symptom 3).** If extraction target is driven by an explicit per-table or per-deployment config rather than a mode preset, this symptom dissolves.

---

## Symptom 2 — Dual watermark tables

### What is true

There are two watermark tables in the destination:

- `_watermarks` — written by `feather run`.
- `_cache_watermarks` — written by `feather extract`.

This split predates `feather-transform`. It exists because cache/extract was conceived as a dev-only verb writing its own state, separate from the production `run` audit trail.

After this change, operators composing `feather extract && feather transform` end up with watermarks split across two tables that have to be reconciled mentally. `tests/integration/test_transform_command.py::test_verb_equivalence` accordingly relaxes its watermark assertion from "byte-identical `_watermarks`" to "same set of watermarked table names across the union of both tables."

### Possible resolutions

- **B1 — Unify the tables.** Single `_watermarks`; distinguish writer by a `trigger` column (now that `_runs.trigger` exists, the pattern is established). One source of truth for "when was this table last extracted." Migration: union the two tables on first connect after upgrade, drop `_cache_watermarks`.
- **B2 — Document the split as intentional.** Status quo with better naming and a note. Lowest cost; leaves operators reconciling.

B1 is strictly cleaner. B2 is what we have today.

---

## Symptom 3 — The mode system doesn't decompose

This is the deeper issue. The other two symptoms are facets.

### What modes control today (four orthogonal concerns)

1. **Target schema for extraction.** `dev`/`test` → `bronze.<name>`. `prod` → `silver.<name>`.
2. **Column filtering during extraction.** `prod` with `column_map` → extract only mapped columns, rename via PyArrow. `dev`/`test` → extract all source columns.
3. **Row limiting during extraction.** `test` → apply `defaults.row_limit`. `dev`/`prod` → no limit.
4. **Transform DDL kind.** `dev`/`test` → all transforms as VIEWs (`force_views=True`). `prod` → gold marked `-- materialized: true` becomes a TABLE; everything else stays a VIEW.

These four concerns rarely need to vary together. Operators routinely want combinations the three-preset system cannot express:

- "Prod schema and column filtering, but views-only DDL during iteration." (The deleted `--force-views` flag was a workaround.)
- "Dev's full-column bronze landing, but test's row limits, so I can profile without a 50M-row pull."
- "Prod's silver-direct extraction, but no column filtering, because I want to see what would land before writing a column_map."

Each "I want mode X for concern A and mode Y for concern B" is a sign that the abstraction is wrong.

### What the symptoms look like in tests and code

- The `--force-views` discussion in `feather-transform` started as "we need this for iteration" and ended in "delete it; use `--mode dev`." That's a workaround masquerading as a feature.
- `feather extract` being "dev-only" (Symptom 1) is really "extract uses dev's target-schema concern." If concern 1 were per-table or per-deployment instead of mode-derived, the verb would compose cleanly.
- The dual watermark tables (Symptom 2) exist because verbs were grouped by mode-coupling ("this verb is dev-only, give it its own state").

### Possible resolutions

- **C1 — Remove modes entirely.** Replace with explicit per-concern config keys:
  - Target schema: per-table `target_table:` (already supported) with a deployment-wide default.
  - Column filtering: presence of `column_map:` triggers it; no mode coupling.
  - Row limiting: `--limit` CLI flag, or `defaults.row_limit` YAML key, applied regardless of mode.
  - DDL kind: `--materialize` flag honoring `-- materialized: true` (default), or `--force-views`. Per-invocation, not per-deployment.

  This is the deepest change but probably the right one. The four concerns become four small honest controls. Symptoms 1 and 2 disappear because nothing is "mode-coupled" anymore.

- **C2 — Reduce modes from three to two.** Collapse `dev` and `test` (the only difference is row_limit, which can be a flag). Keeps the mode concept but trims the preset count. Doesn't solve the decomposition problem — still bundles four concerns.

- **C3 — Status quo.** Keep modes, accept the constraints. Document the workarounds as they come up.

---

## Recommendation

Pick this up as the next work after `feather-transform` lands. Likely sequence:

1. **Decide C1 vs C2 vs C3** — the design call.
2. If C1: design a small replacement that makes the four concerns explicit. Update `feather.yaml` schema and the CLI flags. Migrate existing deployments (a one-line YAML edit per deployment).
3. With C1 done, Symptoms 1 and 2 likely dissolve without separate work — the verb-composability assertion holds in all configurations, and the watermark tables can be unified without a "verb category" distinction.

If C1 is too big a swing, pick A1 + B1 as targeted fixes against the current mode system.

---

## What `feather-transform` does NOT do about this

The change ships honestly:

- **Verb-equivalence test runs in dev/test only.** Prod is excluded with a docstring pointing here.
- **Watermark assertion is union-relaxed** in the equivalence test. Stays dual in code.
- **`design.md` carries a "Known limitation"** pointer at this doc.
- **README/PRD** describe the verb trio as the dev iteration loop, without overclaiming prod-mode equivalence.

The point of capturing these here is so the next change has the receipts when it starts.
