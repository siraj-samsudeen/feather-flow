---
name: feather:update
description: Check for feather-flow updates and apply them interactively, preserving local skill modifications.
---

# Feather Update

## How this skill works

You are performing an interactive update of feather-flow. Run all commands
**silently** — do NOT show code blocks, raw command output, or step-by-step
narration to the user. Only show the user decision points and summaries as
described below.

## Variables

- `INSTALL_DIR` = `~/.claude/feather-flow`
- `TMP_DIR` = `/tmp/feather-flow-update`
- `SCRIPTS_DIR` = `INSTALL_DIR/bin` if it contains `check-modifications.js`, otherwise `TMP_DIR/bin` (bootstrap: the user's install may not have the bin/ scripts yet)

## Phase 1: Version check

Run silently:
```bash
cat ~/.claude/feather-flow/VERSION 2>/dev/null
```
```bash
curl -fsSL https://raw.githubusercontent.com/siraj-samsudeen/feather-flow/main/VERSION 2>/dev/null
```
If curl fails, fallback: `npm view feather-flow version 2>/dev/null`

**If versions match**, show the user:
> You're on the latest version (v{version}).

Stop.

**If no installed version**, show the user:
> feather-flow is not installed. Run `npx feather-flow` to install.

Stop.

## Phase 2: Download

Run silently:
```bash
rm -rf /tmp/feather-flow-update && git clone --depth 1 https://github.com/siraj-samsudeen/feather-flow.git /tmp/feather-flow-update 2>/dev/null
```

If git clone fails, fallback silently:
```bash
cd /tmp && npm pack feather-flow 2>/dev/null && mkdir -p feather-flow-update && tar -xzf feather-flow-*.tgz -C feather-flow-update --strip-components=1
```

If both fail, tell the user the download failed and stop.

## Phase 3: Show what's new and confirm

Read `TMP_DIR/docs/CHANGELOG.md`. Extract only the sections between the
installed version and the latest version. Condense to key bullet points.

Show the user:

> **feather-flow v{latest} available** (you have v{installed})
>
> **What's new:**
> - {condensed bullet point of each notable change}
> - {another change}
>
> Update now?

Wait for confirmation. If declined, run `rm -rf /tmp/feather-flow-update`
silently and stop.

## Phase 4: Check modifications

Determine SCRIPTS_DIR: check if `INSTALL_DIR/bin/check-modifications.js`
exists. If yes, use `INSTALL_DIR/bin`. If not, use `TMP_DIR/bin`.

Run silently:
```bash
node {SCRIPTS_DIR}/check-modifications.js {INSTALL_DIR} {TMP_DIR}
```

Parse the JSON output.

**If `legacy: true`** (no manifest — legacy bash install), show the user:
> This is a legacy install without modification tracking.
> A clean update will replace all files with the new version.
>
> Proceed with clean update?

If yes, proceed to Phase 5 with empty `--keep`. If no, clean up and stop.

**If no files are modified**, proceed directly to Phase 5 with empty `--keep`.

**If files are modified**, show the user a summary:

> {N} file(s) have local modifications:

Then for **each** modified file, show a semantic summary of the key
difference (read both versions to understand what changed — don't just show
a raw diff). Ask the user:

> **{filepath}**
>   Your version: {brief description of what the local version does differently}
>   Upstream: {brief description of what the new upstream version does differently}
>
> Keep mine / Take upstream

If the user wants to see the full diff before deciding, run
`diff {INSTALL_DIR}/{file} {TMP_DIR}/{file}` and show the output, then ask again.

Collect the list of files to keep.

**If deleted files exist**, ask once:
> You deleted {N} file(s) that exist in the new version. Restore them?

## Phase 5: Apply update

Build the `--keep` argument from user choices (comma-separated paths of files
the user chose to keep). If no files to keep, omit the flag.

Run silently:
```bash
node {SCRIPTS_DIR}/apply-update.js {INSTALL_DIR} {TMP_DIR} --keep "{keepList}"
```

## Phase 6: Clean up and report

Run silently:
```bash
rm -rf /tmp/feather-flow-update
```

Show the user:

> **Update complete** (v{old} → v{new}).
> {updated} files updated, {skipped} files kept as-is.
>
> Restart Claude Code to pick up changes.
