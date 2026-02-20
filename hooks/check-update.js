#!/usr/bin/env node
// feather-flow update checker — runs as SessionStart hook
// Reads cached result immediately, spawns background npm check

const fs = require('fs');
const path = require('path');
const os = require('os');
const { spawn } = require('child_process');

const homeDir = os.homedir();
const installDir = path.join(homeDir, '.claude', 'feather-flow');
const cacheDir = path.join(installDir, 'cache');
const cacheFile = path.join(cacheDir, 'update-check.json');
const versionFile = path.join(installDir, 'VERSION');

const CACHE_TTL = 24 * 60 * 60; // 24 hours in seconds

// ── Sync: read cached result and notify if update available (once per day) ──

try {
  if (fs.existsSync(cacheFile)) {
    const cache = JSON.parse(fs.readFileSync(cacheFile, 'utf8'));
    const today = new Date().toISOString().slice(0, 10); // "YYYY-MM-DD"
    if (cache.update_available && cache.installed && cache.latest && cache.notified_date !== today) {
      process.stdout.write(
        `feather-flow update available: v${cache.installed} → v${cache.latest}. Run /feather:update to upgrade.\n`
      );
      // Mark as notified today so we don't prompt again
      cache.notified_date = today;
      fs.writeFileSync(cacheFile, JSON.stringify(cache));
    }
  }
} catch (_) {
  // Cache read/write failed — not critical
}

// ── Async: spawn background process to check npm registry ──

let skipBackground = false;
try {
  if (fs.existsSync(cacheFile)) {
    const cache = JSON.parse(fs.readFileSync(cacheFile, 'utf8'));
    const age = Math.floor(Date.now() / 1000) - (cache.checked || 0);
    if (age < CACHE_TTL) skipBackground = true;
  }
} catch (_) {}

if (!skipBackground) {
  fs.mkdirSync(cacheDir, { recursive: true });

  const child = spawn(process.execPath, ['-e', `
    const fs = require('fs');
    const https = require('https');
    const { execSync } = require('child_process');

    const cacheFile = ${JSON.stringify(cacheFile)};
    const versionFile = ${JSON.stringify(versionFile)};

    let installed = '0.0.0';
    try {
      installed = fs.readFileSync(versionFile, 'utf8').trim();
    } catch (_) {}

    function fetchGitHub() {
      return new Promise((resolve, reject) => {
        https.get('https://raw.githubusercontent.com/siraj-samsudeen/feather-flow/main/VERSION', {
          timeout: 10000
        }, (res) => {
          if (res.statusCode !== 200) return reject(new Error('HTTP ' + res.statusCode));
          let data = '';
          res.on('data', (chunk) => data += chunk);
          res.on('end', () => resolve(data.trim()));
        }).on('error', reject);
      });
    }

    function fetchNpm() {
      try {
        return execSync('npm view feather-flow version', {
          encoding: 'utf8',
          timeout: 10000,
          windowsHide: true
        }).trim();
      } catch (_) {
        return null;
      }
    }

    (async () => {
      let latest = null;
      try {
        latest = await fetchGitHub();
      } catch (_) {
        latest = fetchNpm();
      }

      const result = {
        update_available: latest && installed !== latest,
        installed,
        latest: latest || 'unknown',
        checked: Math.floor(Date.now() / 1000)
      };

      fs.writeFileSync(cacheFile, JSON.stringify(result));
    })();
  `], {
    stdio: 'ignore',
    windowsHide: true,
    detached: true,
  });

  child.unref();
}
