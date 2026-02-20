# Changelog

All notable changes to feather-flow will be documented in this file.

## [Unreleased]

## [1.2.2] - 2026-02-20

### Changed
- **`feather:update` zero-prompt UX** — consolidated into 2 node scripts with `allowed-tools` frontmatter, eliminating all permission prompts
- **`feather:update` clearer merge question** — "Keep mine / Take upstream" replaced with "Keep your version?" yes/no

### Added
- **`bin/update-check.js`** — single preflight script combining version check, download, and modification detection

## [1.2.1] - 2026-02-20

### Fixed
- **Kept files overwritten on subsequent updates** — manifest now stores upstream hash for kept files so they're always detected as modified (#4)

[Unreleased]: https://github.com/siraj-samsudeen/feather-flow/compare/v1.2.2...HEAD
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
