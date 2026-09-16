#!/usr/bin/env bash
# Build a self-contained cgh binary (no Python needed at runtime). One-file,
# stripped, with the dev/test/optional deps excluded to keep it small. Run per
# OS/arch on its own runner; PyInstaller does not cross-compile.
#
# This is the LIGHT build: SQLite backend only, no DuckDB. DuckDB's native
# library is ~50 MB (the whole difference between a ~5 MB and a ~55 MB binary),
# and cgh defaults to SQLite when DuckDB is not importable, so the binary just
# works on SQLite. Users who need DuckDB's analytical speed switch to the
# pip/uvx build (`uvx cgh serve`, which bundles DuckDB) and `cgh backend
# duckdb`; there is deliberately no in-place binary self-upgrade.
#
# Two variants, chosen with CGH_VARIANT (default: core):
#
#   core  the default binary. Core plus the pure-Python, LOCAL-ONLY plugins
#         (cgh-pii, cgh-classify). It bundles nothing that can reach the
#         network, so "the core never phones home" stays verifiable by
#         absence, not by trusting a gate inside a stripped binary.
#
#   full  adds the egress-capable plugins (cgh-codegen, cgh-summarize,
#         cgh-bugreport). These sit behind the egress gate and do nothing
#         until configured, but the code is present, so this build is named
#         and shipped separately as the "includes cloud-assisted tools" one.
#
# The heavy, native-dep plugins (cgh-docs -> lxml, cgh-vision -> pillow +
# pypdfium2) are NOT bundled in either variant: a native wheel inside a onefile
# across the whole OS matrix is not worth it. They stay `pip install cgh[...]`.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(cd "$HERE/.." && pwd)"
OUT="${1:-$ROOT/dist-binary}"
VARIANT="${CGH_VARIANT:-core}"
cd "$ROOT"

# Plugins to bundle, as "<dist-name>:<import-name>". The dist name feeds
# --copy-metadata (without which entry-point discovery returns EMPTY in a
# frozen binary and the plugin silently never loads); the import name feeds
# --collect-all. Keep both correct.
CORE_PLUGINS=( "cgh-pii:cgh_pii" "cgh-classify:cgh_classify" )
FULL_EXTRA=( "cgh-codegen:cgh_codegen" "cgh-summarize:cgh_summarize" "cgh-bugreport:cgh_bugreport" )
case "$VARIANT" in
  core) PLUGINS=( "${CORE_PLUGINS[@]}" ) ;;
  full) PLUGINS=( "${CORE_PLUGINS[@]}" "${FULL_EXTRA[@]}" ) ;;
  *) echo "unknown CGH_VARIANT='$VARIANT' (expected: core | full)" >&2; exit 2 ;;
esac

# Split the plugin set into the two flag streams: --with installs the local
# plugin into the build env (uv run), --collect-all + --copy-metadata bake it
# into the binary (pyinstaller).
UV_WITH=()
PY_PLUGIN=()
for p in "${PLUGINS[@]}"; do
  dist="${p%%:*}"; imp="${p##*:}"
  UV_WITH+=( --with "./plugins/$dist" )
  PY_PLUGIN+=( --collect-all "$imp" --copy-metadata "$dist" )
done
echo "variant=$VARIANT  bundling: ${PLUGINS[*]}"

# --strip needs the `strip` tool and a symbol table it understands. It is
# present on the macOS/Linux runners but not on Windows (the PE symbols are
# elsewhere anyway), so only pass it where it exists. A missing strip would
# otherwise abort the whole build.
STRIP_FLAG="--strip"
case "${OS:-}${OSTYPE:-}" in
  *[Ww]indows*|*msys*|*cygwin*) STRIP_FLAG="" ;;
esac

# cgh imports its dependencies lazily (a startup-speed choice), so static
# analysis misses most of them: collect each declared runtime dependency whole.
uv run --with pyinstaller "${UV_WITH[@]}" pyinstaller --noconfirm --onefile $STRIP_FLAG --name cgh \
  --distpath "$OUT/dist" --workpath "$OUT/work" --specpath "$OUT" \
  --collect-all codegraph --collect-all tree_sitter \
  --collect-all tree_sitter_python --collect-all tree_sitter_typescript \
  --collect-all tree_sitter_go --collect-all tree_sitter_rust --collect-all tree_sitter_java \
  --collect-all fastmcp --collect-all pydantic \
  --collect-all starlette --collect-all uvicorn --collect-all anyio \
  --collect-all sniffio --collect-all h11 --collect-all httpx --collect-all httpcore \
  --collect-all rich --collect-all questionary --collect-all watchdog \
  --collect-all rank_bm25 --collect-all yaml --collect-all click \
  --collect-submodules mcp.server --collect-submodules mcp.shared --collect-submodules mcp.types \
  "${PY_PLUGIN[@]}" \
  --hidden-import sqlite3 --hidden-import _sqlite3 --hidden-import sqlite3.dbapi2 \
  --exclude-module duckdb \
  --exclude-module cryptography --exclude-module numpy --exclude-module mlx \
  --exclude-module pytest --exclude-module _pytest --exclude-module pyinstaller \
  --exclude-module jedi --exclude-module tree_sitter_c_sharp --exclude-module tree_sitter_ruby \
  --exclude-module IPython --exclude-module tkinter --exclude-module test \
  "$HERE/cgh_entry.py"
echo "binary: $OUT/dist/cgh"
