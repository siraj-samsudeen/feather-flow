---
name: feather:update
description: Check for feather-flow updates and walk through an interactive merge that preserves your local skill modifications.
---

# Feather Update

Update feather-flow to the latest version with interactive merge support.

## Instructions

Execute these steps in order. Stop and wait for user input where indicated.

### Step 1: Check versions

Read the installed version:

```bash
cat ~/.claude/feather-flow/VERSION
```

Check the latest published version:

```bash
npm view feather-flow version 2>/dev/null
```

If `npm view` fails (package not published yet), try fetching from GitHub:

```bash
curl -fsSL https://raw.githubusercontent.com/siraj-samsudeen/feather-flow/main/VERSION
```

**If versions match:** Tell the user "You're on the latest version (vX.Y.Z)" and stop.

**If no installed version found:** Tell the user feather-flow is not installed and suggest `npx feather-flow` to install. Stop.

### Step 2: Show changelog

Fetch the changelog:

```bash
curl -fsSL https://raw.githubusercontent.com/siraj-samsudeen/feather-flow/main/docs/CHANGELOG.md
```

Extract and display only the sections between the current installed version and the latest version. Show this to the user so they can see what changed.

### Step 3: Ask confirmation

Ask the user: **"Update from vX.Y.Z to vA.B.C?"**

Wait for confirmation. If they decline, stop.

### Step 4: Download new version

```bash
cd /tmp && npm pack feather-flow 2>/dev/null
```

If npm pack fails (package not published), clone from GitHub instead:

```bash
cd /tmp && rm -rf feather-flow-update && git clone --depth 1 https://github.com/siraj-samsudeen/feather-flow.git feather-flow-update
```

Extract the package if using npm pack:

```bash
cd /tmp && mkdir -p feather-flow-update && tar -xzf feather-flow-*.tgz -C feather-flow-update --strip-components=1
```

The new files are now in `/tmp/feather-flow-update/`.

### Step 5: Detect local modifications

Read the manifest:

```bash
cat ~/.claude/feather-flow/manifest.json
```

**If no manifest exists** (legacy bash install): Warn the user that local modification detection isn't available for legacy installs. Offer two choices:
- **Clean update** — replace all files with upstream (loses local changes)
- **Cancel** — stop so user can back up modified files first

If they choose clean update, skip to Step 7.

**If manifest exists:** For each file listed in the manifest, compute its current SHA256 hash and compare to the stored hash:

```bash
shasum -a 256 ~/.claude/feather-flow/<filepath>
```

Categorize every file into one of these buckets:

| Category | Meaning |
|----------|---------|
| **Locally modified** | File exists, hash differs from manifest |
| **Locally deleted** | File in manifest but missing from disk |
| **Unmodified** | File exists, hash matches manifest |
| **New upstream** | File in new version but not in manifest |

### Step 6: Interactive merge (the core UX)

**If no files are locally modified:** Tell the user "No local modifications detected" and proceed directly to Step 7.

**If files are locally modified:** Show a summary first:

```
Update summary:
  3 files modified locally
  15 files unchanged (will update automatically)
  2 new files from upstream
  0 files you deleted locally
```

Then for **each locally modified file**, show:

1. The file path
2. A unified diff between the user's current version and the new upstream version
3. Ask the user to choose:
   - **Keep mine** — preserve local version, skip upstream changes for this file
   - **Take upstream** — replace with new version (loses local changes)
   - **Show full comparison** — display both versions side by side for closer inspection, then ask again

Record each choice before moving to the next file.

**For locally deleted files:** Ask once: "You deleted N files that exist in the new version. Restore them?" (yes/no for the batch)

### Step 7: Apply update

Copy files from the downloaded package to `~/.claude/feather-flow/`:

```bash
# For unmodified + "take upstream" files: copy from package
cp /tmp/feather-flow-update/<path> ~/.claude/feather-flow/<path>

# For "keep mine" files: skip (leave user's version in place)

# For new upstream files: copy from package
cp /tmp/feather-flow-update/<path> ~/.claude/feather-flow/<path>
```

Update the VERSION file:

```bash
cp /tmp/feather-flow-update/VERSION ~/.claude/feather-flow/VERSION
```

Regenerate the manifest by computing SHA256 hashes for all installed files:

```bash
node -e "
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const installDir = path.join(require('os').homedir(), '.claude', 'feather-flow');
const files = {};

function walk(dir, base) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(full, base);
    else files[path.relative(base, full)] = crypto.createHash('sha256').update(fs.readFileSync(full)).digest('hex');
  }
}

for (const sub of ['skills', 'hooks']) {
  const p = path.join(installDir, sub);
  if (fs.existsSync(p)) walk(p, installDir);
}

const manifest = {
  version: fs.readFileSync(path.join(installDir, 'VERSION'), 'utf8').trim(),
  installedAt: new Date().toISOString(),
  files
};

fs.writeFileSync(path.join(installDir, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
console.log('Manifest regenerated with ' + Object.keys(files).length + ' files');
"
```

### Step 8: Clean up and finish

```bash
rm -rf /tmp/feather-flow-update /tmp/feather-flow-*.tgz
```

Tell the user:

> **Update complete** (vX.Y.Z -> vA.B.C). Restart Claude Code to pick up changes.

If any files were kept as "keep mine", remind them:

> Note: You kept local modifications in N files. These won't be overwritten by future updates as long as the manifest tracks them.
