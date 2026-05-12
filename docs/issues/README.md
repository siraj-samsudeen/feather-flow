# Issues

Per-issue working notes captured before a design spec exists. Each file here pins requirements, handoff context, and other information that emerged during brainstorming or triage but needs to survive the session.

Files here are typically short-lived — once a design spec lands under `docs/superpowers/specs/`, the spec is the source of truth and the issue note becomes supplementary context. Issue notes persist so future agents reading the spec can trace where the requirements came from.

## Contents

- [`26-discover-views.md`](26-discover-views.md) — Issue #26 (view discovery across DB sources). Captures the R1 (Builder) and R2 (Analyst) requirements, out-of-scope items, and the implementation handoff addendum covering AI-agent consumption and forward-compatibility with the planned SQLite metadata DB. Cited by the design spec at `docs/superpowers/specs/2026-04-21-discover-views-design.md`.
- [`rethink-mode-system.md`](rethink-mode-system.md) — Three symptoms surfaced during the `feather-transform` change (May 2026): verb-equivalence breaks in prod, dual watermark tables, and the deeper observation that modes-as-preset-bundles cannot express what operators want. Recommends removing modes entirely (option C1). Pick up after `feather-transform` lands.
- [`replace-hands-on-test.md`](replace-hands-on-test.md) — pre-existing note; retained for context.

## When to add a doc here

Add a file here when an issue:

- needs requirements capture before a design spec is written,
- has multiple brainstorming rounds that should persist across sessions, or
- will be handed off to a subagent that needs context the git history doesn't provide.

Name the file `<issue-number>-<short-slug>.md` when tied to a GitHub issue. Use a descriptive slug when not.

When adding a doc, update the Contents list above with a one-line summary.
