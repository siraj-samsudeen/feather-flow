# Vendored superpowers skills

This directory holds **patched copies** of three skills from the
[superpowers](https://github.com/obra/superpowers) plugin:

- [`brainstorming/SKILL.md`](brainstorming/SKILL.md)
- [`writing-plans/SKILL.md`](writing-plans/SKILL.md)
- [`using-git-worktrees/SKILL.md`](using-git-worktrees/SKILL.md)

They are vendored (not modified in-place at the plugin install site) so the
whole team gets identical agent behavior — a teammate clones the repo and
their Claude Code picks up these versions automatically.

## Why vendor, not just override in CLAUDE.md

CLAUDE.md preferences can tweak behavior at the margins, but they can't
restructure a skill. Three of this project's customizations do restructure:

- **writing-plans** — inserts a new *Plan Preflight* review gate between
  self-review and execution handoff, and replaces the "Subagent-Driven vs
  Inline" choice prompt with an autopilot.
- **brainstorming** — adds an atomic-tasks rule and a reference to the
  project's spec template.
- **using-git-worktrees** — adds a new Step 0 that discovers existing
  worktree conventions via `git worktree list` before picking a directory,
  adds `.claude/worktrees/` to the recognized directory list, adds an
  explicit branch-naming step that reads CLAUDE.md (so project conventions
  like `feature/<slug>` win over tool defaults like `claude/<random>`),
  and switches the Python setup path from `poetry` to `uv`.

These are structural edits to the skill files themselves. Vendoring makes
them explicit and reviewable, and stops teammates from getting surprise
behavior differences when upstream changes.

The other superpowers skills (test-driven-development, debugging,
verification-before-completion, subagent-driven-development, etc.) are
unchanged from upstream — we do NOT vendor them. Teammates should install
the superpowers plugin normally and get the full skill set; the three
vendored copies take precedence in this repo.

## What was patched

Each vendored skill has a `NOTES.md` sibling that documents:

- The upstream version it was forked from
- Exactly which patches were applied, with the inserted text
- How to re-sync if upstream ships improvements worth pulling in

See [`brainstorming/NOTES.md`](brainstorming/NOTES.md) and
[`writing-plans/NOTES.md`](writing-plans/NOTES.md).

## Re-syncing with upstream

When a teammate bumps the superpowers plugin past v5.0.7 and wants to pull
improvements into the vendored copies:

1. Read the new upstream `SKILL.md` from
   `~/.claude/plugins/cache/claude-plugins-official/superpowers/<version>/skills/<skill>/SKILL.md`.
2. Compare it against the vendored file to see what upstream changed.
3. Re-apply the patches listed in each `NOTES.md` onto the new upstream text.
4. Bump the "Upstream" line in the skill's `NOTES.md` to the new version.
5. Commit: `chore(agents): re-sync vendored <skill> with superpowers <version>`.

Patches are small and well-commented, so the re-sync is usually a 10-minute
exercise — not a fork burden.

## Related files

- [`../../CLAUDE.md`](../../CLAUDE.md) — declares the behavioral preferences
  (autopilot Subagent-Driven, worktree-by-default) that these patches
  enforce.
- [`../../docs/conventions/spec-template.md`](../../docs/conventions/spec-template.md)
  — the spec template the patched brainstorming skill points at.
