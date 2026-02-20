'use strict';

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

function sha256(content) {
  return crypto.createHash('sha256').update(content).digest('hex');
}

function walkFiles(dir, base) {
  base = base || dir;
  let results = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      results = results.concat(walkFiles(full, base));
    } else {
      results.push(path.relative(base, full));
    }
  }
  return results;
}

function copyDirRecursive(src, dest) {
  fs.mkdirSync(dest, { recursive: true });
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    const srcPath = path.join(src, entry.name);
    const destPath = path.join(dest, entry.name);
    if (entry.isDirectory()) {
      copyDirRecursive(srcPath, destPath);
    } else {
      fs.copyFileSync(srcPath, destPath);
    }
  }
}

function rmRecursive(target) {
  fs.rmSync(target, { recursive: true, force: true });
}

function readManifest(installDir) {
  const manifestPath = path.join(installDir, 'manifest.json');
  if (fs.existsSync(manifestPath)) {
    return JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  }
  return null;
}

function generateManifest(installDir, version, manifestPath) {
  manifestPath = manifestPath || path.join(installDir, 'manifest.json');
  const files = {};
  const trackDirs = ['skills', 'hooks'];

  for (const dirName of trackDirs) {
    const dirPath = path.join(installDir, dirName);
    if (fs.existsSync(dirPath)) {
      for (const relFile of walkFiles(dirPath, installDir)) {
        const content = fs.readFileSync(path.join(installDir, relFile));
        files[relFile] = sha256(content);
      }
    }
  }

  const manifest = {
    version,
    installedAt: new Date().toISOString(),
    files,
  };

  fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2) + '\n');
  return manifest;
}

module.exports = {
  sha256,
  walkFiles,
  copyDirRecursive,
  rmRecursive,
  readManifest,
  generateManifest,
};
