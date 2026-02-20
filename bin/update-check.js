#!/usr/bin/env node
'use strict';

// Single preflight script for feather:update.
// Does version check + download + modification check in one shot.
// Output: JSON to stdout

const fs = require('fs');
const path = require('path');
const https = require('https');
const { execSync } = require('child_process');
const { sha256, walkFiles, readManifest } = require('./lib/manifest');

const INSTALL_DIR = path.join(process.env.HOME, '.claude', 'feather-flow');
const TMP_DIR = '/tmp/feather-flow-update';
const VERSION_URL = 'https://raw.githubusercontent.com/siraj-samsudeen/feather-flow/main/VERSION';
const REPO_URL = 'https://github.com/siraj-samsudeen/feather-flow.git';

function output(obj) {
  console.log(JSON.stringify(obj));
}

function fetchVersion() {
  return new Promise((resolve) => {
    https.get(VERSION_URL, (res) => {
      if (res.statusCode !== 200) {
        resolve(null);
        res.resume();
        return;
      }
      let data = '';
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => resolve(data.trim()));
    }).on('error', () => resolve(null));
  });
}

function download() {
  try {
    execSync(`rm -rf ${TMP_DIR}`, { stdio: 'ignore' });
  } catch { /* ignore */ }

  // Try git clone first
  try {
    execSync(`git clone --depth 1 ${REPO_URL} ${TMP_DIR}`, { stdio: 'ignore' });
    return true;
  } catch { /* fall through */ }

  // Fallback to npm pack
  try {
    execSync(`rm -rf ${TMP_DIR}`, { stdio: 'ignore' });
    execSync(
      `cd /tmp && npm pack feather-flow 2>/dev/null && mkdir -p feather-flow-update && tar -xzf feather-flow-*.tgz -C feather-flow-update --strip-components=1`,
      { stdio: 'ignore' }
    );
    return true;
  } catch { /* fall through */ }

  return false;
}

function checkModifications() {
  const manifest = readManifest(INSTALL_DIR);

  if (!manifest) {
    return { legacy: true };
  }

  const modified = [];
  const deleted = [];
  let unmodified = 0;

  for (const [file, expectedHash] of Object.entries(manifest.files)) {
    const fullPath = path.join(INSTALL_DIR, file);
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

  // Find new upstream files
  const newUpstream = [];
  for (const sub of ['skills', 'hooks']) {
    const subDir = path.join(TMP_DIR, sub);
    if (fs.existsSync(subDir)) {
      for (const relFile of walkFiles(subDir, TMP_DIR)) {
        if (!manifest.files[relFile]) {
          newUpstream.push(relFile);
        }
      }
    }
  }

  return { modified, deleted, unmodified, newUpstream };
}

async function main() {
  // Step 1: Check local version
  const versionFile = path.join(INSTALL_DIR, 'VERSION');
  if (!fs.existsSync(versionFile)) {
    output({ status: 'not_installed' });
    return;
  }
  const localVersion = fs.readFileSync(versionFile, 'utf8').trim();

  // Step 2: Check remote version
  let remoteVersion = await fetchVersion();
  if (!remoteVersion) {
    try {
      remoteVersion = execSync('npm view feather-flow version 2>/dev/null', { encoding: 'utf8' }).trim();
    } catch {
      output({ status: 'check_failed' });
      return;
    }
  }

  if (localVersion === remoteVersion) {
    output({ status: 'up_to_date', version: localVersion });
    return;
  }

  // Step 3: Download
  if (!download()) {
    output({ status: 'download_failed' });
    return;
  }

  // Step 4: Read changelog
  let changelog = '';
  const changelogPath = path.join(TMP_DIR, 'docs', 'CHANGELOG.md');
  if (fs.existsSync(changelogPath)) {
    changelog = fs.readFileSync(changelogPath, 'utf8');
  }

  // Step 5: Check modifications
  const modifications = checkModifications();

  output({
    status: 'update_available',
    localVersion,
    remoteVersion,
    changelog,
    modifications,
  });
}

main();
