#!/usr/bin/env node
'use strict';

// Compare installed files against manifest + new version directory.
// Usage: node check-modifications.js <installDir> <newVersionDir>
// Output: JSON to stdout

const fs = require('fs');
const path = require('path');
const { sha256, walkFiles, readManifest } = require('./lib/manifest');

const installDir = process.argv[2];
const newVersionDir = process.argv[3];

if (!installDir || !newVersionDir) {
  console.error('Usage: check-modifications.js <installDir> <newVersionDir>');
  process.exit(1);
}

const manifest = readManifest(installDir);

if (!manifest) {
  console.log(JSON.stringify({ legacy: true, modified: [], deleted: [], unmodified: 0, newUpstream: [] }));
  process.exit(0);
}

const modified = [];
const deleted = [];
let unmodified = 0;

for (const [file, expectedHash] of Object.entries(manifest.files)) {
  const fullPath = path.join(installDir, file);
  if (fs.existsSync(fullPath)) {
    const actualHash = sha256(fs.readFileSync(fullPath));
    if (actualHash !== expectedHash) {
      modified.push(file);
    } else {
      unmodified++;
    }
  } else {
    deleted.push(file);
  }
}

// Find new upstream files (in new version but not in manifest)
const newUpstream = [];
for (const sub of ['skills', 'hooks']) {
  const subDir = path.join(newVersionDir, sub);
  if (fs.existsSync(subDir)) {
    for (const relFile of walkFiles(subDir, newVersionDir)) {
      if (!manifest.files[relFile]) {
        newUpstream.push(relFile);
      }
    }
  }
}

console.log(JSON.stringify({ modified, deleted, unmodified, newUpstream }));
