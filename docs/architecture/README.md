# Architecture

Project-level architectural decisions for feather-flow. Every doc here captures a decision with long half-life — something that shapes many features, not a single implementation.

Read the relevant doc before making a decision in its domain. Cite it by filename and version in feature specs and plans.

## Contents

- [`warehouse-layers.md`](warehouse-layers.md) — the four-layer warehouse model (Bronze → Silver → Gold → Departmental Data Marts). Pins what belongs in each layer, how to decide the layer for a new transform, and naming conventions. Source of truth for all transform design.

## When to add a doc here

Add a file here when a decision:

- affects multiple features (not just one),
- would benefit future agents who weren't in the conversation where it was made, and
- is stable — not expected to change every month.

For decisions tied to a single feature, put them in that feature's spec under `docs/superpowers/specs/`. For conventions about *how we work* (code layout, spec templates, testing patterns), use `docs/conventions/` instead.

When adding a doc, update the Contents list above with a one-line summary.
