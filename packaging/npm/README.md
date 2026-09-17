# @altikva/cgh

Run [**cgh**](https://github.com/altikva/cgh) — a local code graph + memory +
plans + knowledge layer for AI coding agents — with no Python installed. This
package is a thin launcher: on first use it downloads the standalone `cgh`
binary for your OS from the matching GitHub Release, verifies its SHA-256,
caches it, and runs it.

```bash
# one-off, no install
npx @altikva/cgh --version
npx @altikva/cgh serve --root .        # start the MCP server for this repo

# or install the `cgh` command globally
npm install -g @altikva/cgh
cgh serve --root .
```

## Sealed vs egress

The binary ships in two variants, and this launcher picks between them:

- **Sealed (default).** The core graph, the MCP tools, memory / plans /
  knowledge, and the local-only plugins (PII scrubbing, classification). It
  contains no code that can reach the network, so it cannot phone home.
- **Egress.** Adds the plugins that can call an external model (code
  generation, summarization, bug reports). They stay behind cgh's egress gate
  and do nothing until configured, and can target a local model too.

```bash
npx @altikva/cgh --egress serve        # fetch and run the egress build
CGH_EGRESS=1 npx @altikva/cgh serve    # same, via env
```

A leading `--egress` is consumed by the launcher; everything after it is passed
straight to `cgh`.

## What you get vs pip / uvx

This launcher runs the **light SQLite build**. If you want DuckDB's analytical
speed, or the heavy `docs` / `vision` plugins, install with Python instead:

```bash
uvx cgh serve                 # bundles DuckDB
pip install "cgh[full]"       # DuckDB + every first-party plugin
```

## Environment

| Variable | Effect |
|---|---|
| `CGH_EGRESS=1` / `CGH_VARIANT=egress` | Use the egress build. |
| `CGH_CACHE_DIR` | Where the binary is cached (default: `~/.cache/cgh/bin`). |
| `CGH_DOWNLOAD_BASE` | Override the release download base (private mirrors, testing). |

## Supported platforms

macOS (arm64, x64), Linux (x64, arm64), Windows (x64). On anything else the
launcher tells you to `uvx cgh` instead.

## License

MIT AND CC-BY-NC-SA-4.0, the same as cgh itself. See the
[main repository](https://github.com/altikva/cgh).
