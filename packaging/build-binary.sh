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
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(cd "$HERE/.." && pwd)"
OUT="${1:-$ROOT/dist-binary}"
cd "$ROOT"

# cgh imports its dependencies lazily (a startup-speed choice), so static
# analysis misses most of them: collect each declared runtime dependency whole.
uv run --with pyinstaller pyinstaller --noconfirm --onefile --strip --name cgh \
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
  --hidden-import sqlite3 --hidden-import _sqlite3 --hidden-import sqlite3.dbapi2 \
  --exclude-module duckdb \
  --exclude-module cryptography --exclude-module numpy --exclude-module mlx \
  --exclude-module pytest --exclude-module _pytest --exclude-module pyinstaller \
  --exclude-module jedi --exclude-module tree_sitter_c_sharp --exclude-module tree_sitter_ruby \
  --exclude-module IPython --exclude-module tkinter --exclude-module test \
  "$HERE/cgh_entry.py"
echo "binary: $OUT/dist/cgh"
