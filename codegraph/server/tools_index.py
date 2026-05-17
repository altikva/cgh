# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: MCP indexing tools — scan_repo, index_changed_files, force_index.

from __future__ import annotations

import json
import os
from pathlib import Path


def _load_config_toml(root: Path) -> tuple[Path, dict]:
    """Load .codegraph/config.toml. Returns (config_path, data)."""
    import tomllib

    from codegraph.config import CODEGRAPH_DIR, CONFIG_FILE

    config_path = root / CODEGRAPH_DIR / CONFIG_FILE
    if not config_path.exists():
        return config_path, {}
    with open(config_path, "rb") as f:
        return config_path, tomllib.load(f)


def register(mcp) -> None:
    """Register indexing tools on the given FastMCP instance."""
    import codegraph.server as _server
    from codegraph.server import _logged_tool

    @mcp.tool()
    @_logged_tool
    def scan_repo(verbose: bool = False) -> str:
        """
        Full re-index of the entire repository.
        Call this after major changes (branch switch, rebase, pull) to refresh
        the graph. Returns stats: files indexed, errors, time elapsed.
        """
        from codegraph.db import reset_connection
        from codegraph.indexer import index_repo

        _server._conn = None
        reset_connection()
        stats = index_repo(_server._root, verbose=verbose)
        return json.dumps(
            {
                "action": "full_reindex",
                "root": str(_server._root),
                **stats,
            },
            indent=2,
        )

    @mcp.tool()
    @_logged_tool
    def force_index(paths: list[str], confirmed: bool = False) -> str:
        """
        Force-index specific files or directories, even if they are in .gitignore
        or .git/info/exclude. Bypasses all ignore rules and mtime cache.

        IMPORTANT: This bypasses safety filters. Always confirm with the user first.
        Call once with confirmed=False (default) to preview what will be indexed,
        then call again with confirmed=True after user approval.

        Args:
            paths: list of file or directory paths (relative to repo root or absolute)
            confirmed: must be True to actually index. False returns a preview only.
        """
        from codegraph.indexer import index_file
        from codegraph.parsers import is_supported

        root = _server._root

        # Step 1: Preview — collect files that would be indexed
        preview_files = []
        for p in paths:
            target = Path(p) if os.path.isabs(p) else (root / p) if root else Path(p)
            if target.is_file():
                if is_supported(target):
                    preview_files.append(str(target.relative_to(root) if root else target))
            elif target.is_dir():
                for dirpath, _, filenames in os.walk(target):
                    for filename in filenames:
                        full = Path(dirpath) / filename
                        if is_supported(full):
                            preview_files.append(str(full.relative_to(root) if root else full))

        if not confirmed:
            return json.dumps(
                {
                    "action": "force_index_preview",
                    "status": "CONFIRMATION_REQUIRED",
                    "message": (
                        f"Force-index will bypass .gitignore and .git/info/exclude for "
                        f"{len(preview_files)} file(s). Ask the user to confirm, then "
                        f"call again with confirmed=True."
                    ),
                    "files_to_index": preview_files,
                    "file_count": len(preview_files),
                },
                indent=2,
            )

        # Step 2: Confirmed — actually index
        indexed = []
        skipped = []
        errors = []

        for p in paths:
            target = Path(p) if os.path.isabs(p) else (root / p) if root else Path(p)

            if target.is_file():
                try:
                    ok = index_file(target, root, force=True)
                    if ok:
                        indexed.append(str(target.relative_to(root) if root else target))
                    else:
                        skipped.append(str(target.relative_to(root) if root else target))
                except Exception as exc:
                    errors.append({"file": str(target), "error": str(exc)})

            elif target.is_dir():
                for dirpath, _, filenames in os.walk(target):
                    for filename in filenames:
                        full = Path(dirpath) / filename
                        if not is_supported(full):
                            continue
                        try:
                            ok = index_file(full, root, force=True)
                            if ok:
                                indexed.append(str(full.relative_to(root) if root else full))
                            else:
                                skipped.append(str(full.relative_to(root) if root else full))
                        except Exception as exc:
                            errors.append({"file": str(full), "error": str(exc)})
            else:
                skipped.append(f"{p} (not found)")

        return json.dumps(
            {
                "action": "force_index",
                "confirmed": True,
                "indexed": indexed,
                "skipped": skipped,
                "errors": errors,
                "indexed_count": len(indexed),
            },
            indent=2,
        )

    @mcp.tool()
    @_logged_tool
    def incremental_reindex() -> str:
        """
        Surgical reindex: compare stored git blob SHAs to the current HEAD
        and re-index only files whose content changed. Much faster than
        scan_repo after `git pull`, `git checkout <branch>`, or `git rebase`.

        Falls back automatically to a full scan if the index is too old
        (pre-0.4 DB without blob SHA tracking).

        Returns JSON: {mode, reindexed_count, deleted_count, unchanged_count,
        errors, elapsed_s}.
        """
        from codegraph.indexer import incremental_reindex as _incr

        root = _server._root
        if root is None:
            return json.dumps({"status": "error", "message": "repo root not set"})
        result = _incr(root)
        # Truncate lists for token economy
        if len(result.get("reindexed", [])) > 100:
            result["reindexed"] = result["reindexed"][:100]
            result["reindexed_truncated"] = True
        if len(result.get("deleted", [])) > 100:
            result["deleted"] = result["deleted"][:100]
            result["deleted_truncated"] = True
        return json.dumps(result, indent=2)

    @mcp.tool()
    @_logged_tool
    def scan_status() -> str:
        """
        Report whether the code graph is fresh relative to the current git HEAD.

        Call this BEFORE trusting symbol_lookup/find_callers results if the user
        mentions a branch switch, rebase, pull, or recent edits. When `fresh` is
        false, call scan_repo to refresh the index.

        Returns JSON with:
          fresh         — true if indexed sha == HEAD and working tree is clean
          indexed_sha   — git commit the graph was built at
          indexed_at    — ISO timestamp of last scan
          current_sha   — git HEAD now
          behind_by     — commits between indexed and HEAD
          dirty         — working tree has uncommitted changes
          changed_files — files modified since indexed_sha (up to 200)
        """
        from codegraph.scan_meta import scan_status as _scan_status

        root = _server._root
        if root is None:
            return json.dumps({"status": "error", "message": "repo root not set"})
        ss = _scan_status(root)
        # Truncate changed_files for token economy
        if len(ss.get("changed_files", [])) > 200:
            ss["changed_files"] = ss["changed_files"][:200]
            ss["changed_files_truncated"] = True
        return json.dumps(ss, indent=2)

    @mcp.tool()
    @_logged_tool
    def add_directory(path: str) -> str:
        """
        Add an external directory to the code graph and hot-index it.

        Use this when the user wants to include a related repo or sub-project
        in the graph so cross-repo symbol lookups work (e.g., a frontend repo
        while this MCP server runs inside the backend repo).

        Behavior:
        - Resolves the path relative to the repo root
        - Persists it to .codegraph/config.toml (extra_dirs)
        - Immediately scans all parseable files in the directory
        - Extends the file watcher to include the new path (hot reload —
          no need to restart the MCP server)

        Args:
            path: absolute or relative path to a directory (e.g., "../frontend")
        """
        from codegraph.cli.commands_graph import _write_extra_dirs
        from codegraph.indexer import index_file
        from codegraph.parsers import is_supported

        root = _server._root
        if root is None:
            return json.dumps({"status": "error", "message": "repo root not set"})

        # Resolve + validate
        target = Path(path) if os.path.isabs(path) else (root / path)
        resolved = target.resolve()
        if not resolved.exists() or not resolved.is_dir():
            return json.dumps(
                {
                    "status": "error",
                    "message": f"Directory does not exist or is not a directory: {resolved}",
                }
            )

        # Persist to config
        config_path, data = _load_config_toml(root)
        if not config_path.exists():
            return json.dumps(
                {
                    "status": "error",
                    "message": "codegraph not initialized (missing .codegraph/config.toml)",
                }
            )
        extra_dirs = data.get("codegraph", {}).get("extra_dirs", [])
        try:
            rel = os.path.relpath(resolved, root)
        except ValueError:
            rel = str(resolved)
        already_configured = rel in extra_dirs
        if not already_configured:
            extra_dirs.append(rel)
            _write_extra_dirs(config_path, data, extra_dirs)

        # Hot-index the directory
        indexed: list[str] = []
        errors: list[dict] = []
        for dirpath, dirnames, filenames in os.walk(resolved):
            # Skip hidden + ignored dirs
            dirnames[:] = [
                d
                for d in dirnames
                if not d.startswith(".") and d not in {"node_modules", "__pycache__", ".venv", "venv", "dist", "build"}
            ]
            for filename in filenames:
                full = Path(dirpath) / filename
                if not is_supported(full):
                    continue
                try:
                    if index_file(full, root):
                        indexed.append(str(full.relative_to(root) if root in full.parents else full))
                except Exception as exc:
                    errors.append({"file": str(full), "error": str(exc)[:200]})

        # Hot-extend the watcher, if one is running
        watcher_extended = False
        try:
            from codegraph import watcher as _watcher_mod

            observer = getattr(_watcher_mod, "_active_observer", None)
            handler = getattr(_watcher_mod, "_active_handler", None)
            if observer is not None and handler is not None:
                observer.schedule(handler, str(resolved), recursive=True)
                watcher_extended = True
        except Exception:
            pass

        return json.dumps(
            {
                "status": "ok",
                "action": "add_directory",
                "path": str(resolved),
                "relative": rel,
                "already_configured": already_configured,
                "indexed_count": len(indexed),
                "error_count": len(errors),
                "errors": errors[:5],
                "watcher_extended": watcher_extended,
                "note": (
                    "Directory added and indexed. File watcher extended — no restart needed."
                    if watcher_extended
                    else "Directory added and indexed. Watcher will pick up changes after next MCP server restart."
                ),
            },
            indent=2,
        )

    @mcp.tool()
    @_logged_tool
    def index_changed_files(since: str = "HEAD~1") -> str:
        """
        Re-index only files changed since a git ref (default: last commit).
        Much faster than a full scan — perfect for post-commit or mid-work refresh.

        Args:
            since: git ref to diff against ("HEAD~1", "main", "abc1234", "HEAD")
                   Use "staged" to index staged files only.
        """
        import subprocess

        from codegraph.indexer import index_file

        root = _server._root

        if since == "staged":
            cmd = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"]
        else:
            cmd = ["git", "diff", "--name-only", "--diff-filter=ACMR", since]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=str(root),
            )
            files = [f.strip() for f in result.stdout.strip().splitlines() if f.strip()]
        except Exception as exc:
            return json.dumps({"error": f"git diff failed: {exc}"})

        indexed = []
        skipped = []
        errors = []
        for rel_path in files:
            full_path = root / rel_path
            if not full_path.exists():
                skipped.append(rel_path)
                continue
            try:
                ok = index_file(full_path, root)
                if ok:
                    indexed.append(rel_path)
                else:
                    skipped.append(rel_path)
            except Exception as exc:
                errors.append({"file": rel_path, "error": str(exc)})

        return json.dumps(
            {
                "action": "incremental_index",
                "since": since,
                "indexed": indexed,
                "skipped": skipped,
                "errors": errors,
                "indexed_count": len(indexed),
            },
            indent=2,
        )
