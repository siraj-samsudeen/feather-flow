---
name: release-feather-flow
description: Release a new version of feather-flow. Enforces changelog, version bump, tag, and publish steps.
---

# Release feather-flow

## Prerequisites

- Working directory must be the feather-flow repo root (verify `package.json` has `"name": "feather-flow"`)
- All changes must be committed (clean working tree)
- You must be on the `main` branch

## Instructions

Run each step in order. Stop if any check fails.

### Step 1: Pre-flight checks

Run silently and verify:
```bash
git status --porcelain
git branch --show-current
```

If working tree is dirty, tell the user and stop.
If not on `main`, tell the user and stop.

### Step 2: Determine version

Read the current version from `package.json`. Ask the user:

> Current version: v{current}
> What version are you releasing? (e.g. 1.2.0)

### Step 3: Verify changelog

Read `docs/CHANGELOG.md`. Check that:
1. The `[Unreleased]` section has content (not empty)
2. There is NO existing `## [{new-version}]` section

If `[Unreleased]` is empty, tell the user:
> No changes listed under [Unreleased] in CHANGELOG.md. Add changelog entries first.

Stop.

### Step 4: Finalize changelog

Move entries from `[Unreleased]` to a new `## [{version}] - {today's date}` section.
Keep `[Unreleased]` as an empty section at the top.

Add/update comparison links at the bottom of the file (following keepachangelog format):
```
[Unreleased]: https://github.com/siraj-samsudeen/feather-flow/compare/v{version}...HEAD
[{version}]: https://github.com/siraj-samsudeen/feather-flow/releases/tag/v{version}
```

Commit:
```bash
git add docs/CHANGELOG.md
git commit -m "docs: update changelog for v{version}"
```

### Step 5: Bump version

Update version in both files:
- `package.json` — the `"version"` field
- `VERSION` — the plain text version

Commit (message is just the version number, like get-shit-done style):
```bash
git add package.json VERSION
git commit -m "{version}"
```

### Step 6: Tag and push

Push the changelog + version commits, then create and push the tag.
The tag push is what triggers the publish workflow:

```bash
git push origin main
git tag v{version}
git push origin v{version}
```

(As of v2.0.1 the publish workflow triggers on `v*` tag pushes only —
no auto-bump, no rebase dance. Pushing main alone does not publish.)

### Step 7: Verify npm publish

The repo uses trusted publishing via GitHub Actions (`publish.yml`). The
tag push in Step 6 triggers the publish automatically. Do NOT run
`npm publish` manually.

Wait 30 seconds, then verify:
```bash
gh run list --repo siraj-samsudeen/feather-flow --limit 1
```

If the workflow completed successfully, proceed. If it failed, show the user
the error and suggest checking the Actions tab.

### Step 8: Confirm

Show the user:

> Released feather-flow v{version}
> - npm: https://www.npmjs.com/package/feather-flow
> - GitHub: https://github.com/siraj-samsudeen/feather-flow/releases/tag/v{version}
