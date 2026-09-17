// -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
// __creation__ = 2026-09-17
// __author__ = "jndjama (Joy Ndjama)"
// __copyright__ = "Copyright 2026 ALTIKVA."
// __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
// -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
// Description: Pure, side-effect-free resolution helpers for the npx wrapper.
//              targetFor maps a Node platform/arch to the release asset target;
//              assetName builds the file name for a target and variant (sealed
//              -> cgh-<target>, egress -> cgh-egress-<target>, plus .exe on
//              Windows); selectVariant reads a leading --egress flag or the
//              env and returns the variant plus the untouched pass-through
//              args; downloadUrl joins the release base, version and asset. No
//              I/O here so the launcher's logic is unit-testable without a
//              network or a real binary.

'use strict';

// Node (platform, arch) -> the release asset target. These are the targets the
// release matrix builds; anything else has no prebuilt binary. macOS x64
// (Intel) is absent on purpose: GitHub retired the Intel macOS hosted runner
// and PyInstaller cannot cross-compile one, so Intel-Mac users get the
// install-with-Python hint below instead of a download that 404s.
const PLATFORM_MAP = {
  'darwin arm64': 'macos-arm64',
  'linux x64': 'linux-x64',
  'linux arm64': 'linux-arm64',
  'win32 x64': 'windows-x64',
};

function targetFor(platform, arch) {
  const target = PLATFORM_MAP[`${platform} ${arch}`];
  if (!target) {
    const supported = Object.keys(PLATFORM_MAP).join(', ');
    throw new Error(
      `no prebuilt cgh binary for ${platform}/${arch}. Supported: ${supported}. ` +
        'Install with Python instead: uvx cgh (or pip install cgh).',
    );
  }
  return target;
}

function assetName(target, variant) {
  const prefix = variant === 'egress' ? 'cgh-egress' : 'cgh';
  const ext = target.startsWith('windows') ? '.exe' : '';
  return `${prefix}-${target}${ext}`;
}

// A leading --egress selects the egress build; CGH_VARIANT=egress or
// CGH_EGRESS=1 do the same. Everything else is passed through to the binary
// untouched, so tool flags are never swallowed. Only a LEADING --egress is
// consumed, so a stray later occurrence reaches the binary and errors loudly
// rather than being silently reinterpreted.
function selectVariant(argv, env) {
  env = env || {};
  const wantEgress =
    env.CGH_VARIANT === 'egress' || env.CGH_EGRESS === '1' || env.CGH_EGRESS === 'true';
  let variant = wantEgress ? 'egress' : 'sealed';
  const rest = argv.slice();
  if (rest[0] === '--egress') {
    variant = 'egress';
    rest.shift();
  }
  return { variant, rest };
}

function downloadUrl(base, version, asset) {
  return `${base.replace(/\/+$/, '')}/v${version}/${asset}`;
}

module.exports = { PLATFORM_MAP, targetFor, assetName, selectVariant, downloadUrl };
