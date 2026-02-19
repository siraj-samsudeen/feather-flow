# Changelog

All notable changes to feather-flow will be documented in this file.

## [Unreleased]

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
