#!/usr/bin/env node
'use strict';

// Apply a feather-flow update, respecting user decisions about modified files.
// Usage: node apply-update.js <installDir> <newVersionDir> [--keep "file1,file2"]
// Output: JSON summary to stdout

const fs = require('fs');
const path = require('path');
const { walkFiles, readManifest, generateManifest } = require('./lib/manifest');

const args = process.argv.slice(2);
const installDir = args[0];
const newVersionDir = args[1];

if (!installDir || !newVersionDir) {
  console.error('Usage: apply-update.js <installDir> <newVersionDir> [--keep "file1,file2"]');
  process.exit(1);
}

// Parse --keep flag
const keepFiles = new Set();
for (let i = 2; i < args.length; i++) {
  if (args[i] === '--keep' && args[i + 1]) {
    args[i + 1].split(',').forEach(f => keepFiles.add(f.trim()));
    i++;
  }
}

const manifest = readManifest(installDir);
let updated = 0;
let skipped = 0;

// Update tracked files from manifest
if (manifest) {
  for (const file of Object.keys(manifest.files)) {
    if (keepFiles.has(file)) {
      skipped++;
      continue;
    }
    const src = path.join(newVersionDir, file);
    const dst = path.join(installDir, file);
    if (fs.existsSync(src)) {
      fs.mkdirSync(path.dirname(dst), { recursive: true });
      fs.copyFileSync(src, dst);
      updated++;
    }
  }
}

// Copy new upstream files (not in manifest)
for (const sub of ['skills', 'hooks']) {
  const subDir = path.join(newVersionDir, sub);
  if (fs.existsSync(subDir)) {
    for (const relFile of walkFiles(subDir, newVersionDir)) {
      const isTracked = manifest && manifest.files[relFile];
      if (!isTracked && !keepFiles.has(relFile)) {
        const src = path.join(newVersionDir, relFile);
        const dst = path.join(installDir, relFile);
        fs.mkdirSync(path.dirname(dst), { recursive: true });
        fs.copyFileSync(src, dst);
        updated++;
      }
    }
  }
}

// Copy non-tracked files (VERSION, docs, LICENSE, etc.)
for (const file of ['VERSION', 'docs/CHANGELOG.md', 'LICENSE', 'ACKNOWLEDGMENTS.md']) {
  const src = path.join(newVersionDir, file);
  const dst = path.join(installDir, file);
  if (fs.existsSync(src)) {
    fs.mkdirSync(path.dirname(dst), { recursive: true });
    fs.copyFileSync(src, dst);
  }
}

// Also copy bin/ directory so future updates have the scripts locally
const binSrc = path.join(newVersionDir, 'bin');
if (fs.existsSync(binSrc)) {
  for (const relFile of walkFiles(binSrc, newVersionDir)) {
    const src = path.join(newVersionDir, relFile);
    const dst = path.join(installDir, relFile);
    fs.mkdirSync(path.dirname(dst), { recursive: true });
    fs.copyFileSync(src, dst);
  }
}

// Regenerate manifest
const newVersion = fs.readFileSync(path.join(installDir, 'VERSION'), 'utf8').trim();
const newManifest = generateManifest(installDir, newVersion);

console.log(JSON.stringify({
  updated,
  skipped,
  totalTracked: Object.keys(newManifest.files).length,
  version: newVersion,
}));
