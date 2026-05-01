# Changelog

All notable changes to feather-flow will be documented in this file.

## [Unreleased]

## [2.0.1] - 2026-05-01

### Fixed
- **Install symlinks not created** — `bin/cli.js` filtered skills by the
  old `feather:` (colon) prefix, so v2.0.0's `feather-*` (hyphen) skills
  were copied but never symlinked into `~/.claude/skills/`. Skill
  discovery now keys on the presence of a `SKILL.md` file in each
  subdirectory of `skills/`, which is the actual definition of "skill".
- **Stale install messaging** — "Getting started" pointed at deleted
  skills (`/feather:help`, `/feather:workflow`, `/feather:update`).
  Updated to point at `/feather-flow` and the three pipeline skills.
- **Update notification** referenced the deleted `/feather:update`
  skill. Now suggests `npx feather-flow` as the upgrade command.

### Changed
- **Publish workflow trigger:** switched from `on: push: branches: [main]`
  with version auto-bump to `on: push: tags: ['v*']` with a tag-vs-package
  consistency check. Releases now happen only when a `v*` tag is pushed,
  not on every commit to main. Eliminates the rebase race during manual
  releases and stops publishing undocumented patch versions for
  non-release commits.
- **`release-feather-flow` skill:** Step 6 simplified — no longer needs
  the `git pull --rebase` workaround for the auto-bump that no longer
  happens.
- **Uninstall** detects feather-flow symlinks by their target (links
  pointing into the install dir) rather than by name prefix, so it
  correctly cleans up both old `feather:*` and new `feather-*` symlinks.
- **README** rewritten for the four-skill pipeline. Removed the v1
  "Simple Mode / Slice Mode" framing and the table of 23 deleted skills.
- **ACKNOWLEDGMENTS** rewritten — v1 per-skill attribution tables
  (covering removed skills) replaced with a focused note on the
  Superpowers-derived visual-companion pattern in `feather-brainstorm`.
  v1 attribution preserved in git at tag v1.2.7 for anyone tracing
  lineage.

## [2.0.0] - 2026-05-01

### Changed (BREAKING)
- **Skill set rewritten as a 4-skill pipeline.** All previous `feather:*`
  (colon-prefixed) skills are removed. The new pipeline is:
  `feather-brainstorm` → `feather-spec` → `feather-execute-task`,
  with `feather-flow` as the orchestrator/overview skill.
- **Stable-ID scheme:** requirement prefix changed from `R` to `RQ`
  (e.g. `A1.RQ1`) to disambiguate from risk `R1` in design.md.

### Added
- **`feather-brainstorm`** — horizon-expansion skill that captures the
  user's words verbatim (no paraphrasing), with an HTML visual companion
  for layout decisions (uses `open`/`xdg-open`, no external server).
- **`feather-spec`** — single canonical reference for capability/design/
  screen/component/tasks artifacts, EARS patterns, stable IDs, and the
  prove-engine-first task ordering.
- **`feather-execute-task`** — gated task execution loop
  (mini-plan → execute → verify → tick → ask), with a six-category
  finding classifier (task fix, divergence, ambiguity, cross-cutting,
  scope creep, unrelated bug).
- **`feather-flow`** — overview skill for pipeline orientation and
  starting mid-pipeline.
- **`skills/feather-spec/references/example-todo.md`** — canonical
  minimal-mode example.

### Removed
- All 46 prior `feather:*` (colon-prefix) skill files, including the
  Convex-testing additions from v1.2.6 (those will be revisited
  separately if still needed).

### Migration
Anyone with a project initialized on v1.x will not find the old
`feather:*` skills after upgrading. The replacement skills are not
drop-in equivalents; they are a different model of the workflow.
A migration guide will follow if there is demand.

## [1.2.2] - 2026-02-20

### Changed
- **`feather:update` zero-prompt UX** — consolidated into 2 node scripts with `allowed-tools` frontmatter, eliminating all permission prompts
- **`feather:update` clearer merge question** — "Keep mine / Take upstream" replaced with "Keep your version?" yes/no

### Added
- **`bin/update-check.js`** — single preflight script combining version check, download, and modification detection

## [1.2.1] - 2026-02-20

### Fixed
- **Kept files overwritten on subsequent updates** — manifest now stores upstream hash for kept files so they're always detected as modified (#4)

[Unreleased]: https://github.com/siraj-samsudeen/feather-flow/compare/v2.0.1...HEAD
[2.0.1]: https://github.com/siraj-samsudeen/feather-flow/releases/tag/v2.0.1
[2.0.0]: https://github.com/siraj-samsudeen/feather-flow/releases/tag/v2.0.0
[1.2.2]: https://github.com/siraj-samsudeen/feather-flow/releases/tag/v1.2.2
[1.2.1]: https://github.com/siraj-samsudeen/feather-flow/releases/tag/v1.2.1

## [1.2.0] - 2026-02-20

### Fixed
- **`feather:update` zsh bug** — inline `node -e` scripts caused zsh `!` history expansion errors (#4)
- **`feather:update` noisy UX** — rewritten as a UX script that shows clean summaries instead of step-by-step code narration (#4)
- **Update notification spam** — now notifies once per day instead of every session start

### Added
- **`bin/check-modifications.js`** — standalone script for detecting local modifications against manifest
- **`bin/apply-update.js`** — standalone script for applying updates with per-file keep/take decisions
- **`bin/lib/manifest.js`** — shared module for SHA256 hashing, file walking, and manifest operations

### Changed
- **`bin/cli.js`** — refactored to use shared `bin/lib/manifest.js` module (no behavior change)
- **`hooks/check-update.js`** — cache TTL changed from 6 hours to 24 hours, notification limited to once per day

[Unreleased]: https://github.com/siraj-samsudeen/feather-flow/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/siraj-samsudeen/feather-flow/releases/tag/v1.2.0

## [1.1.0] - 2026-02-17

### Added
- **npm-based installer** — `npx feather-flow` replaces `curl | bash` as the primary install method
- **`feather:update`** — new skill for interactive updates with local modification detection and per-file merge choices
- **Manifest tracking** — SHA256 hashes of all installed files enable local modification detection
- **Background update notifications** — SessionStart hook checks npm registry daily and notifies when updates are available
- **`bin/cli.js`** — zero-dependency Node.js CLI (install, uninstall, version commands)

### Changed
- **`feather:setup-tdd-guard`** is now stack-agnostic — installs only vitest, tdd-guard-vitest, and @vitest/coverage-v8. No React/Convex dependencies.
- **`install.sh`** — deprecated in favor of `npx feather-flow` (still works as fallback)
- Version bumped to 1.1.0

### Added
- **`feather:setup-react-testing`** — new add-on skill for React projects. Installs @testing-library/react, jsdom, @vitejs/plugin-react, and configures vitest for component testing. Requires `feather:setup-tdd-guard` as prerequisite.

## [1.0.0] - 2026-02-11

### Added
- Initial release with 21 feather skills
- `install.sh` — one-command installer (`curl | bash` or local clone)
- `uninstall.sh` — clean removal of files and settings
- Two workflow modes: Simple (single feature) and Slice (multi-day projects)
- TDD enforcement across all execution skills
- Superpowers attribution in ACKNOWLEDGMENTS.md

### Skills included
- **Guides:** `feather:workflow`, `feather:help`
- **Design:** `feather:create-design`, `feather:review-design`, `feather:create-ui-mockup`
- **Spec:** `feather:create-spec`, `feather:derive-tests`
- **Test:** `feather:write-tests`, `feather:setup-tdd-guard`
- **Plan & Execute:** `feather:create-plan`, `feather:execute`, `feather:verify`, `feather:finish`
- **Slice Mode:** `feather:slice-project`, `feather:work-slice`, `feather:add-slice`, `feather:pause-slice`, `feather:resume-slice`
- **Utilities:** `feather:isolate-work`, `feather:create-issue`, `feather:polish`
