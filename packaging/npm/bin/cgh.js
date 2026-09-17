#!/usr/bin/env node
// -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
// __creation__ = 2026-09-17
// __author__ = "jndjama (Joy Ndjama)"
// __copyright__ = "Copyright 2026 ALTIKVA."
// __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
// -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
// Description: The npx launcher. Resolves the standalone cgh binary for this
//              OS/arch and variant (sealed by default, egress with a leading
//              --egress), downloading it from the matching GitHub Release on
//              first use, verifying its SHA-256 against the published .sha256,
//              caching it under the user's cache dir, then exec-ing it with the
//              remaining args and forwarding its exit code. No binary is bundled
//              in the npm package: it is fetched lazily so a single small
//              package serves every platform, and the variant is a runtime
//              choice the wrapper cannot know at install time. The download base
//              and cache dir are overridable (CGH_DOWNLOAD_BASE, CGH_CACHE_DIR)
//              for private mirrors and for tests.

'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const http = require('http');
const https = require('https');
const crypto = require('crypto');
const { spawnSync } = require('child_process');

const { targetFor, assetName, selectVariant, downloadUrl } = require('../lib/resolve');
const pkg = require('../package.json');

const DEFAULT_BASE = 'https://github.com/altikva/cgh/releases/download';

function log(msg) {
  process.stderr.write(`[cgh] ${msg}\n`);
}

function httpGet(url, redirects) {
  redirects = redirects || 0;
  return new Promise((resolve, reject) => {
    if (redirects > 6) {
      reject(new Error(`too many redirects fetching ${url}`));
      return;
    }
    const client = url.startsWith('http://') ? http : https;
    client
      .get(url, { headers: { 'user-agent': `cgh-npm/${pkg.version}` } }, (res) => {
        const { statusCode, headers } = res;
        if (statusCode >= 300 && statusCode < 400 && headers.location) {
          res.resume();
          resolve(httpGet(new URL(headers.location, url).toString(), redirects + 1));
          return;
        }
        if (statusCode !== 200) {
          res.resume();
          reject(new Error(`GET ${url} -> HTTP ${statusCode}`));
          return;
        }
        const chunks = [];
        res.on('data', (c) => chunks.push(c));
        res.on('end', () => resolve(Buffer.concat(chunks)));
        res.on('error', reject);
      })
      .on('error', reject);
  });
}

// sha256sum / shasum -a 256 write "<64 hex>  <filename>"; take the digest.
function expectedSha(shaText) {
  const m = /^([0-9a-fA-F]{64})\b/.exec(shaText.trim());
  if (!m) {
    throw new Error('could not parse the .sha256 checksum file');
  }
  return m[1].toLowerCase();
}

function sha256(buf) {
  return crypto.createHash('sha256').update(buf).digest('hex');
}

function cacheDir(version) {
  const base =
    process.env.CGH_CACHE_DIR || path.join(os.homedir() || os.tmpdir(), '.cache', 'cgh', 'bin');
  return path.join(base, `v${version}`);
}

async function ensureBinary(variant) {
  const version = pkg.version;
  const target = targetFor(process.platform, process.arch);
  const asset = assetName(target, variant);
  const dir = cacheDir(version);
  const dest = path.join(dir, asset);
  if (fs.existsSync(dest)) {
    return dest;
  }

  const base = process.env.CGH_DOWNLOAD_BASE || DEFAULT_BASE;
  const url = downloadUrl(base, version, asset);
  log(`downloading ${asset} (v${version})...`);
  const [bin, shaText] = await Promise.all([httpGet(url), httpGet(`${url}.sha256`)]);
  const want = expectedSha(shaText.toString('utf8'));
  const got = sha256(bin);
  if (got !== want) {
    throw new Error(`checksum mismatch for ${asset}: expected ${want}, got ${got}`);
  }

  fs.mkdirSync(dir, { recursive: true });
  const tmp = `${dest}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, bin);
  fs.chmodSync(tmp, 0o755);
  fs.renameSync(tmp, dest);
  log(`cached at ${dest}`);
  return dest;
}

async function main() {
  const { variant, rest } = selectVariant(process.argv.slice(2), process.env);
  let bin;
  try {
    bin = await ensureBinary(variant);
  } catch (err) {
    log(`error: ${err.message}`);
    process.exit(1);
  }
  const result = spawnSync(bin, rest, { stdio: 'inherit' });
  if (result.error) {
    log(`failed to run the binary: ${result.error.message}`);
    process.exit(1);
  }
  process.exit(result.status === null ? 1 : result.status);
}

if (require.main === module) {
  main();
}

module.exports = { ensureBinary, cacheDir, expectedSha, sha256 };
