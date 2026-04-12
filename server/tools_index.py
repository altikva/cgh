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
        from codegraph.indexer import _PARSERS, index_file

        root = _server._root

        # Step 1: Preview — collect files that would be indexed
        preview_files = []
        for p in paths:
            target = Path(p) if os.path.isabs(p) else (root / p) if root else Path(p)
            if target.is_file():
                if target.suffix.lower() in _PARSERS:
                    preview_files.append(str(target.relative_to(root) if root else target))
            elif target.is_dir():
                for dirpath, _, filenames in os.walk(target):
                    for filename in filenames:
                        full = Path(dirpath) / filename
                        if full.suffix.lower() in _PARSERS:
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
                        if full.suffix.lower() not in _PARSERS:
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
