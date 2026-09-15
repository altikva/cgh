# Packaging: the standalone cgh binary

`build-binary.sh` produces a self-contained `cgh` executable with no Python
required at runtime. It bundles CPython, the native deps (DuckDB, the
tree-sitter grammars, SQLite) and cgh itself into one file.

```bash
packaging/build-binary.sh [output-dir]   # default: ./dist-binary
```

Notes:
- PyInstaller does not cross-compile: run this once per OS/arch on its own
  runner (macOS arm64/x64, Linux x64/arm64, Windows x64) and publish each
  binary. A release workflow does this across a matrix.
- cgh imports its dependencies lazily (a startup-speed choice), so PyInstaller
  static analysis misses most of them. The script collects each declared
  runtime dependency whole; add a `--collect-all <pkg>` here when a new
  runtime dependency lands, or the frozen binary will fail on first use of it.
- The binary needs the frozen-aware owner spawn in `codegraph/state/ipc.py`
  (`sys.frozen` branch) so `cgh serve` can launch its owner process.
- Size is dominated by the DuckDB native library (~50 MB); a one-file,
  stripped build lands around 54 MB on macOS arm64.
- Third-party (non-first-party) plugins installed via pip are NOT visible to a
  frozen binary (entry-point discovery only sees what was bundled). The binary
  is core plus whatever first-party plugins are collected into it.
