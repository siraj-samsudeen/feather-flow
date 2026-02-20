#!/usr/bin/env node
'use strict';

// feather-flow CLI — zero dependencies, Node 18+
// Usage: npx feather-flow [install|uninstall|version]

const fs = require('fs');
const path = require('path');
const os = require('os');
const readline = require('readline');
const { sha256, walkFiles, copyDirRecursive, rmRecursive, generateManifest } = require('./lib/manifest');

// ─────────────────────────────────────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────────────────────────────────────

const HOME = os.homedir();
const INSTALL_DIR = path.join(HOME, '.claude', 'feather-flow');
const SKILLS_LINK_DIR = path.join(HOME, '.claude', 'skills');
const SETTINGS_FILE = path.join(HOME, '.claude', 'settings.json');
const PACKAGE_DIR = path.resolve(__dirname, '..');
const MANIFEST_FILE = path.join(INSTALL_DIR, 'manifest.json');

// Files/dirs to copy from package to install dir
const COPY_ITEMS = [
  { src: 'skills', type: 'dir' },
  { src: 'hooks', type: 'dir' },
  { src: 'docs/CHANGELOG.md', type: 'file' },
  { src: 'VERSION', type: 'file' },
  { src: 'LICENSE', type: 'file' },
  { src: 'ACKNOWLEDGMENTS.md', type: 'file' },
];

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

const BLUE = '\x1b[1;34m';
const YELLOW = '\x1b[1;33m';
const RED = '\x1b[1;31m';
const RESET = '\x1b[0m';

function info(msg) { console.log(`${BLUE}==>${RESET} ${msg}`); }
function warn(msg) { console.log(`${YELLOW}warning:${RESET} ${msg}`); }
function error(msg) { console.error(`${RED}error:${RESET} ${msg}`); process.exit(1); }

function ask(question) {
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  return new Promise((resolve) => {
    rl.question(question, (answer) => {
      rl.close();
      resolve(answer.trim().toLowerCase());
    });
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// Settings.json patching
// ─────────────────────────────────────────────────────────────────────────────

const HOOK_COMMAND = `node "${path.join(INSTALL_DIR, 'hooks', 'check-update.js')}"`;

function patchSettings() {
  if (!fs.existsSync(SETTINGS_FILE)) {
    info('No ~/.claude/settings.json found — skipping hook setup');
    return;
  }

  const raw = fs.readFileSync(SETTINGS_FILE, 'utf8');
  const settings = JSON.parse(raw);

  // Ensure hooks.SessionStart array exists
  if (!settings.hooks) settings.hooks = {};
  if (!Array.isArray(settings.hooks.SessionStart)) settings.hooks.SessionStart = [];

  // Check if our hook already exists
  const alreadyExists = settings.hooks.SessionStart.some((entry) =>
    entry.hooks && entry.hooks.some((h) => h.command && h.command.includes('feather-flow'))
  );

  if (!alreadyExists) {
    settings.hooks.SessionStart.push({
      hooks: [{ type: 'command', command: HOOK_COMMAND }],
    });
    info('Added SessionStart hook for update checks');
  }

  // Atomic write
  const tmpFile = SETTINGS_FILE + '.tmp';
  fs.writeFileSync(tmpFile, JSON.stringify(settings, null, 2) + '\n');
  fs.renameSync(tmpFile, SETTINGS_FILE);
}

function unpatchSettings() {
  if (!fs.existsSync(SETTINGS_FILE)) return;

  const raw = fs.readFileSync(SETTINGS_FILE, 'utf8');
  const settings = JSON.parse(raw);

  if (settings.hooks && Array.isArray(settings.hooks.SessionStart)) {
    settings.hooks.SessionStart = settings.hooks.SessionStart.filter((entry) =>
      !(entry.hooks && entry.hooks.some((h) => h.command && h.command.includes('feather-flow')))
    );

    // Clean up empty array
    if (settings.hooks.SessionStart.length === 0) {
      delete settings.hooks.SessionStart;
    }
    // Clean up empty hooks object
    if (Object.keys(settings.hooks).length === 0) {
      delete settings.hooks;
    }
  }

  const tmpFile = SETTINGS_FILE + '.tmp';
  fs.writeFileSync(tmpFile, JSON.stringify(settings, null, 2) + '\n');
  fs.renameSync(tmpFile, SETTINGS_FILE);
}

// ─────────────────────────────────────────────────────────────────────────────
// Install
// ─────────────────────────────────────────────────────────────────────────────

async function install() {
  const newVersion = fs.readFileSync(path.join(PACKAGE_DIR, 'VERSION'), 'utf8').trim();

  // Detect existing install
  let installType = 'fresh';
  if (fs.existsSync(MANIFEST_FILE)) {
    const manifest = JSON.parse(fs.readFileSync(MANIFEST_FILE, 'utf8'));
    if (manifest.version === newVersion) {
      info(`feather-flow v${newVersion} is already installed`);
      info('Re-installing to ensure all files are up to date...');
    } else {
      info(`Upgrading feather-flow from v${manifest.version} to v${newVersion}`);
    }
    installType = 'npm-upgrade';
  } else if (fs.existsSync(path.join(INSTALL_DIR, 'VERSION'))) {
    const oldVersion = fs.readFileSync(path.join(INSTALL_DIR, 'VERSION'), 'utf8').trim();
    info(`Detected legacy bash install (v${oldVersion}) — upgrading to npm-based install v${newVersion}`);
    installType = 'legacy-upgrade';
  } else {
    info(`Installing feather-flow v${newVersion}`);
  }

  // Check for legacy non-symlink feather skills
  if (installType === 'fresh' && fs.existsSync(SKILLS_LINK_DIR)) {
    const legacyDirs = fs.readdirSync(SKILLS_LINK_DIR, { withFileTypes: true })
      .filter((e) => e.isDirectory() && !e.isSymbolicLink() && e.name.startsWith('feather:'));

    if (legacyDirs.length > 0) {
      warn('Found old feather skills (non-symlinked) in ~/.claude/skills/:');
      legacyDirs.forEach((d) => console.log(`    ${d.name}`));
      const answer = await ask('\n    Remove old feather skills? [y/N] ');
      if (answer === 'y') {
        legacyDirs.forEach((d) => rmRecursive(path.join(SKILLS_LINK_DIR, d.name)));
        info('Removed old feather skills');
      } else {
        info('Keeping old skills — you can remove them manually later');
      }
    }
  }

  // For legacy/npm upgrades: remove old install (clean slate)
  if (installType === 'legacy-upgrade') {
    rmRecursive(INSTALL_DIR);
  }

  // Copy files
  fs.mkdirSync(INSTALL_DIR, { recursive: true });
  fs.mkdirSync(path.join(INSTALL_DIR, 'docs'), { recursive: true });

  for (const item of COPY_ITEMS) {
    const src = path.join(PACKAGE_DIR, item.src);
    const dest = path.join(INSTALL_DIR, item.src);
    if (!fs.existsSync(src)) {
      warn(`Source not found: ${item.src} — skipping`);
      continue;
    }
    if (item.type === 'dir') {
      // For npm-upgrade, we need to handle existing files carefully
      // For now, do a full copy (the update skill handles per-file merges)
      if (fs.existsSync(dest)) rmRecursive(dest);
      copyDirRecursive(src, dest);
    } else {
      fs.mkdirSync(path.dirname(dest), { recursive: true });
      fs.copyFileSync(src, dest);
    }
  }

  info(`Installed to ${INSTALL_DIR}`);

  // Create symlinks
  fs.mkdirSync(SKILLS_LINK_DIR, { recursive: true });
  const skillsDir = path.join(INSTALL_DIR, 'skills');
  let linkCount = 0;

  for (const entry of fs.readdirSync(skillsDir, { withFileTypes: true })) {
    if (!entry.isDirectory() || !entry.name.startsWith('feather:')) continue;

    const linkPath = path.join(SKILLS_LINK_DIR, entry.name);
    const targetPath = path.join(skillsDir, entry.name);

    // Remove existing symlink or directory
    try {
      const stat = fs.lstatSync(linkPath);
      if (stat.isSymbolicLink() || stat.isDirectory()) {
        rmRecursive(linkPath);
      }
    } catch (_) {
      // doesn't exist — fine
    }

    fs.symlinkSync(targetPath, linkPath);
    linkCount++;
  }

  info(`${linkCount} skill symlinks created`);

  // Generate manifest
  const manifest = generateManifest(INSTALL_DIR, newVersion, MANIFEST_FILE);
  info(`Manifest generated with ${Object.keys(manifest.files).length} tracked files`);

  // Patch settings.json
  patchSettings();

  // Success banner
  console.log('');
  console.log('───────────────────────────────────────────────────');
  console.log(`  feather-flow v${newVersion} installed successfully`);
  console.log('───────────────────────────────────────────────────');
  console.log('');
  console.log('  Getting started:');
  console.log('');
  console.log('    1. Open Claude Code in any project');
  console.log('    2. Type /feather:help to see all commands');
  console.log('    3. Type /feather:workflow to start a guided workflow');
  console.log('');
  console.log(`  Installed to:  ${INSTALL_DIR}`);
  console.log(`  Skills dir:    ${skillsDir}`);
  console.log('');
  console.log('  To update later, run: /feather:update (inside Claude Code)');
  console.log('');
}

// ─────────────────────────────────────────────────────────────────────────────
// Uninstall
// ─────────────────────────────────────────────────────────────────────────────

function uninstall() {
  if (!fs.existsSync(INSTALL_DIR)) {
    info('feather-flow is not installed — nothing to do');
    return;
  }

  info('Uninstalling feather-flow...');

  // Remove symlinks
  if (fs.existsSync(SKILLS_LINK_DIR)) {
    let removed = 0;
    for (const entry of fs.readdirSync(SKILLS_LINK_DIR, { withFileTypes: true })) {
      if (entry.name.startsWith('feather:') && entry.isSymbolicLink()) {
        fs.unlinkSync(path.join(SKILLS_LINK_DIR, entry.name));
        removed++;
      }
    }
    info(`Removed ${removed} skill symlinks`);
  }

  // Remove install directory
  rmRecursive(INSTALL_DIR);
  info('Removed ~/.claude/feather-flow/');

  // Unpatch settings
  unpatchSettings();
  info('Removed SessionStart hook from settings.json');

  console.log('');
  console.log('  feather-flow has been uninstalled.');
  console.log('  Restart Claude Code to complete removal.');
  console.log('');
}

// ─────────────────────────────────────────────────────────────────────────────
// Version
// ─────────────────────────────────────────────────────────────────────────────

function version() {
  const ver = fs.readFileSync(path.join(PACKAGE_DIR, 'VERSION'), 'utf8').trim();
  console.log(`feather-flow v${ver}`);

  if (fs.existsSync(path.join(INSTALL_DIR, 'VERSION'))) {
    const installed = fs.readFileSync(path.join(INSTALL_DIR, 'VERSION'), 'utf8').trim();
    console.log(`  Installed: v${installed}`);
  } else {
    console.log('  Not installed');
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Main
// ─────────────────────────────────────────────────────────────────────────────

const command = process.argv[2] || 'install';

switch (command) {
  case 'install':
    install().catch((err) => error(err.message));
    break;
  case 'uninstall':
    uninstall();
    break;
  case 'version':
  case '--version':
  case '-v':
    version();
    break;
  default:
    console.log('Usage: feather-flow [install|uninstall|version]');
    process.exit(1);
}
