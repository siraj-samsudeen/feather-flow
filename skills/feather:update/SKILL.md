---
name: feather:update
description: Check for feather-flow updates and apply them interactively, preserving local skill modifications.
allowed-tools: Bash(node *), Read
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
- `SCRIPTS_DIR` = `INSTALL_DIR/bin` if it contains `update-check.js`, otherwise `TMP_DIR/bin` (bootstrap: the user's install may not have the bin/ scripts yet)

## Phase 1: Preflight check

Run silently:
```bash
node {SCRIPTS_DIR}/update-check.js
```

Parse the JSON output and handle each status:

**If `not_installed`**, show the user:
> feather-flow is not installed. Run `npx feather-flow` to install.

Stop.

**If `check_failed`**, show the user:
> Could not check for updates. Check your internet connection and try again.

Stop.

**If `up_to_date`**, show the user:
> You're on the latest version (v{version}).

Stop.

**If `download_failed`**, show the user:
> Failed to download the update. Check your internet connection and try again.

Stop.

**If `update_available`**, continue to Phase 2.

## Phase 2: Show what's new and confirm

From the preflight JSON, extract `changelog`, `localVersion`, and `remoteVersion`.
Parse the changelog markdown — extract only the sections between the installed
version and the latest version. Condense to key bullet points.

Show the user:

> **feather-flow v{remoteVersion} available** (you have v{localVersion})
>
> **What's new:**
> - {condensed bullet point of each notable change}
> - {another change}
>
> Update now?

Wait for confirmation. If declined, run `rm -rf /tmp/feather-flow-update`
silently and stop.

## Phase 3: Handle modifications

From the preflight JSON, extract `modifications`.

**If `legacy: true`** (no manifest — legacy bash install), show the user:
> This is a legacy install without modification tracking.
> A clean update will replace all files with the new version.
>
> Proceed with clean update?

If yes, proceed to Phase 4 with empty `--keep`. If no, clean up and stop.

**If no files are modified**, proceed directly to Phase 4 with empty `--keep`.

**If files are modified**, show the user a summary:

> {N} file(s) have local modifications:

Then for **each** modified file, read both versions (local and upstream in
TMP_DIR) to understand what changed. Show a semantic summary — don't just
show a raw diff. Ask the user:

> **{filepath}**
>   Your version: {brief description of what the local version does differently}
>   Upstream: {brief description of what the new upstream version does differently}
>
> **Keep your version?**
> - Yes — keep your local changes
> - No — overwrite with the new upstream version

Collect the list of files to keep (those where the user said Yes).

**If deleted files exist**, ask once:
> You deleted {N} file(s) that exist in the new version. Restore them?

## Phase 4: Apply update

Build the `--keep` argument from user choices (comma-separated paths of files
the user chose to keep). If no files to keep, omit the flag.

Run silently:
```bash
node {SCRIPTS_DIR}/apply-update.js {INSTALL_DIR} {TMP_DIR} --cleanup --keep "{keepList}"
```

Parse the JSON output and show the user:

> **Update complete** (v{localVersion} → v{remoteVersion}).
> {updated} files updated, {skipped} files kept as-is.
>
> Restart Claude Code to pick up changes.
