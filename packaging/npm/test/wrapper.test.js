// -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
// __creation__ = 2026-09-17
// __author__ = "jndjama (Joy Ndjama)"
// __copyright__ = "Copyright 2026 ALTIKVA."
// __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
// -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
// Description: Tests for the npx wrapper. Unit tests cover the pure resolution
//              helpers (target, asset name, variant selection, URL). An
//              end-to-end test serves fake "binaries" over a local HTTP server
//              and runs the real launcher, proving the download, checksum,
//              cache, variant selection and arg pass-through all work without a
//              published release or a 30 MB binary.

'use strict';

const { test } = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const os = require('node:os');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawn } = require('node:child_process');

const { targetFor, assetName, selectVariant, downloadUrl } = require('../lib/resolve');

test('targetFor maps the five supported platforms', () => {
  assert.equal(targetFor('darwin', 'arm64'), 'macos-arm64');
  assert.equal(targetFor('darwin', 'x64'), 'macos-x64');
  assert.equal(targetFor('linux', 'x64'), 'linux-x64');
  assert.equal(targetFor('linux', 'arm64'), 'linux-arm64');
  assert.equal(targetFor('win32', 'x64'), 'windows-x64');
});

test('targetFor rejects an unsupported platform with a helpful hint', () => {
  assert.throws(() => targetFor('freebsd', 'x64'), /no prebuilt cgh binary.*uvx cgh/s);
});

test('assetName encodes variant and the Windows .exe', () => {
  assert.equal(assetName('macos-arm64', 'sealed'), 'cgh-macos-arm64');
  assert.equal(assetName('linux-x64', 'egress'), 'cgh-egress-linux-x64');
  assert.equal(assetName('windows-x64', 'sealed'), 'cgh-windows-x64.exe');
  assert.equal(assetName('windows-x64', 'egress'), 'cgh-egress-windows-x64.exe');
});

test('selectVariant consumes a leading --egress and passes the rest through', () => {
  assert.deepEqual(selectVariant(['serve', '--root', '.'], {}), {
    variant: 'sealed',
    rest: ['serve', '--root', '.'],
  });
  assert.deepEqual(selectVariant(['--egress', 'serve'], {}), {
    variant: 'egress',
    rest: ['serve'],
  });
  // env selects egress; a non-leading --egress is left for the binary to reject
  assert.deepEqual(selectVariant(['serve', '--egress'], { CGH_EGRESS: '1' }), {
    variant: 'egress',
    rest: ['serve', '--egress'],
  });
});

test('downloadUrl joins base, version and asset without a double slash', () => {
  assert.equal(
    downloadUrl('https://x/releases/download/', '0.11.8', 'cgh-linux-x64'),
    'https://x/releases/download/v0.11.8/cgh-linux-x64',
  );
});

// End-to-end: serve fake binaries over HTTP, run the real launcher.
test('launcher downloads, verifies, caches and execs the right variant', async (t) => {
  const target = targetFor(process.platform, process.arch);
  const isWin = process.platform === 'win32';
  // A fake "binary": a script that echoes a marker and its args, so we can
  // assert both that it ran and that the wrapper passed the args through.
  const sealed = isWin
    ? '@echo off\r\necho SEALED %*\r\n'
    : '#!/bin/sh\necho "SEALED $@"\n';
  const egress = isWin
    ? '@echo off\r\necho EGRESS %*\r\n'
    : '#!/bin/sh\necho "EGRESS $@"\n';

  const releaseDir = fs.mkdtempSync(path.join(os.tmpdir(), 'cgh-rel-'));
  const cacheRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'cgh-cache-'));
  t.after(() => {
    fs.rmSync(releaseDir, { recursive: true, force: true });
    fs.rmSync(cacheRoot, { recursive: true, force: true });
  });

  const write = (variant, body) => {
    const name = assetName(target, variant);
    const p = path.join(releaseDir, name);
    fs.writeFileSync(p, body);
    const sum = crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');
    fs.writeFileSync(`${p}.sha256`, `${sum}  ${name}\n`);
    return name;
  };
  write('sealed', sealed);
  write('egress', egress);

  const server = http.createServer((req, res) => {
    // URL shape: /v<version>/<asset>
    const file = path.join(releaseDir, path.basename(req.url));
    if (fs.existsSync(file)) {
      res.writeHead(200);
      res.end(fs.readFileSync(file));
    } else {
      res.writeHead(404);
      res.end('nope');
    }
  });
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  t.after(() => server.close());
  const base = `http://127.0.0.1:${server.address().port}`;

  const binPath = path.join(__dirname, '..', 'bin', 'cgh.js');
  // Run the launcher with async spawn, NOT spawnSync: the launcher downloads
  // from the server that lives in THIS process, and a synchronous child would
  // block this event loop so the server could never answer it (a deadlock).
  const run = (args) =>
    new Promise((resolve) => {
      const child = spawn(process.execPath, [binPath, ...args], {
        env: { ...process.env, CGH_DOWNLOAD_BASE: base, CGH_CACHE_DIR: cacheRoot },
      });
      let stdout = '';
      let stderr = '';
      child.stdout.on('data', (d) => (stdout += d));
      child.stderr.on('data', (d) => (stderr += d));
      child.on('close', (status) => resolve({ status, stdout, stderr }));
    });

  // sealed by default, args passed through
  const a = await run(['serve', '--root', '.']);
  assert.equal(a.status, 0, a.stderr);
  assert.match(a.stdout, /SEALED/);
  assert.match(a.stdout, /serve --root \./);

  // --egress selects the egress asset and is stripped from the args
  const b = await run(['--egress', 'status']);
  assert.equal(b.status, 0, b.stderr);
  assert.match(b.stdout, /EGRESS/);
  assert.match(b.stdout, /status/);
  assert.doesNotMatch(b.stdout, /--egress/);

  // second run is served from cache (close the server first, must still work)
  await new Promise((r) => server.close(r));
  const c = await run(['--version']);
  assert.equal(c.status, 0, c.stderr);
  assert.match(c.stdout, /SEALED/);
});
