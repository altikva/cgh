# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: MCP indexing tools: scan_repo, index_changed_files, force_index.

from __future__ import annotations

import json
import os
from pathlib import Path

from codegraph.core.utils import quiet_subprocess_kwargs


def _within_repo(target: Path, root: Path) -> bool:
    """True if ``target`` resolves inside ``root``. Used to keep force_index
    from reading arbitrary absolute paths the repo never declared."""
    try:
        target.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _load_config_toml(root: Path) -> tuple[Path, dict]:
    """Load .codegraph/config.toml. Returns (config_path, data)."""
    import tomllib

    from codegraph.core.config import CODEGRAPH_DIR, CONFIG_FILE

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
        How do I rebuild the whole index?
        Full reindex of the repository: slow on a large repo. Last resort,
        when incremental_reindex is not enough or the index looks wrong;
        refused while another index of this repo runs. Returns files
        indexed, errors and time taken.
        """
        from codegraph.core.db import reset_connection
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
        How do I index files that .gitignore excludes?
        Indexes `paths` even if .gitignore or .git/info/exclude ignores
        them, bypassing ignore rules and the mtime cache. This bypasses
        safety filters: always ask the user. Call with confirmed=False to
        preview, then confirmed=True after approval. Tracked files never
        need it.

        Args:
            paths: list of file or directory paths (relative to repo root or absolute)
            confirmed: must be True to actually index. False returns a preview only.
        """
        from codegraph.indexer import index_file
        from codegraph.parsers import is_supported

        root = _server._root

        # Step 1: Preview, collect files that would be indexed
        preview_files = []
        refused: list[str] = []
        for p in paths:
            target = Path(p) if os.path.isabs(p) else (root / p) if root else Path(p)
            if root is not None and not _within_repo(target, root):
                refused.append(str(target))
                continue
            if target.is_file():
                if is_supported(target):
                    preview_files.append(
                        str(target.relative_to(root) if root else target)
                    )
            elif target.is_dir():
                for dirpath, _, filenames in os.walk(target):
                    for filename in filenames:
                        full = Path(dirpath) / filename
                        if is_supported(full):
                            preview_files.append(
                                str(full.relative_to(root) if root else full)
                            )

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
                    "refused_outside_repo": refused,
                },
                indent=2,
            )

        # Step 2: Confirmed, actually index
        indexed = []
        skipped = []
        errors = []

        for p in paths:
            target = Path(p) if os.path.isabs(p) else (root / p) if root else Path(p)

            if root is not None and not _within_repo(target, root):
                refused.append(str(target))
                continue

            if target.is_file():
                try:
                    ok = index_file(target, root, force=True)
                    if ok:
                        indexed.append(
                            str(target.relative_to(root) if root else target)
                        )
                    else:
                        skipped.append(
                            str(target.relative_to(root) if root else target)
                        )
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
                                indexed.append(
                                    str(full.relative_to(root) if root else full)
                                )
                            else:
                                skipped.append(
                                    str(full.relative_to(root) if root else full)
                                )
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
                "refused_outside_repo": refused,
                "indexed_count": len(indexed),
            },
            indent=2,
        )

    @mcp.tool()
    @_logged_tool
    def incremental_reindex() -> str:
        """
        How do I refresh the index after a pull, checkout or rebase?
        Reindexes only the files whose git blob changed since the last scan
        and advances the freshness marker scan_status reads. Call it when
        scan_status says stale; installed git hooks (`cgh hooks install`)
        run it after pull, merge, checkout and rebase. Falls back to a full
        scan on a very old index. Returns {mode, reindexed_count,
        deleted_count, unchanged_count, errors, elapsed_s}.
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
        Is the index up to date with git?
        Call before trusting graph results after a branch switch, pull,
        rebase or many edits. Returns fresh, indexed_sha, current_sha,
        behind_by, dirty, changed_files, and `indexing` ({pid, since} while
        an index runs). Stale: incremental_reindex. While `indexing` is set
        (even with indexed_sha null on a first index), wait; do not start
        scan_repo, it would be refused.
        """
        from codegraph.state.scan_meta import scan_status as _scan_status

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
        How do I add a sibling repo or folder to the graph?
        Adds `path` (relative to the repo root or absolute) to extra_dirs in
        .codegraph/config.toml, indexes it now and extends the watcher, no
        restart. Use when the user wants cross-repo lookups into a related
        project.

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

        config_path, data = _load_config_toml(root)
        if not config_path.exists():
            return json.dumps(
                {
                    "status": "error",
                    "message": "codegraph not initialized (missing .codegraph/config.toml)",
                }
            )
        extra_dirs = data.get("codegraph", {}).get("extra_dirs", [])

        # Containment: inside the repo root is always fine. Outside it,
        # only directories a human already declared in extra_dirs may be
        # indexed: without this, the tool is an arbitrary-filesystem-read
        # primitive for any prompt-injected MCP client (walk + index makes
        # the content queryable). Declaring a sibling repo is a human
        # decision, made via `cgh add-dir add` or config.toml.
        root_resolved = root.resolve()
        inside_root = resolved == root_resolved or str(resolved).startswith(
            str(root_resolved) + os.sep
        )
        if not inside_root:
            declared: set[str] = set()
            for entry in extra_dirs:
                entry_path = Path(entry)
                if not entry_path.is_absolute():
                    entry_path = root / entry
                try:
                    declared.add(str(entry_path.resolve()))
                except OSError:
                    continue
            if str(resolved) not in declared:
                return json.dumps(
                    {
                        "status": "error",
                        "message": (
                            f"{resolved} is outside the repo root and not "
                            "declared in [codegraph] extra_dirs. Declare it "
                            "first (a human decision): `cgh add-dir add "
                            f"{path}` or add it to .codegraph/config.toml."
                        ),
                    }
                )
            try:
                from codegraph.state.activity import log as _activity

                _activity(
                    root, "index", f"add_directory outside root (declared): {resolved}"
                )
            except Exception:
                pass
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
                if not d.startswith(".")
                and d
                not in {"node_modules", "__pycache__", ".venv", "venv", "dist", "build"}
            ]
            for filename in filenames:
                full = Path(dirpath) / filename
                if not is_supported(full):
                    continue
                try:
                    if index_file(full, root):
                        indexed.append(
                            str(
                                full.relative_to(root) if root in full.parents else full
                            )
                        )
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
                    "Directory added and indexed. File watcher extended, no restart needed."
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
        How do I reindex just the files changed since a git ref?
        Reindexes the files changed since `since` (default HEAD~1, "staged"
        for staged files). Rarely needed: the watcher reindexes files on
        save. After a pull or branch switch prefer incremental_reindex.

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
            # Reject a leading dash so a value like "--output=/path" can't be
            # parsed as a git flag (argument injection via the MCP arg). The
            # trailing "--" keeps the ref from being read as a pathspec.
            if since.startswith("-"):
                return json.dumps({"error": f"invalid git ref: {since!r}"})
            cmd = ["git", "diff", "--name-only", "--diff-filter=ACMR", since, "--"]

        try:
            result = subprocess.run(
                cmd,
                timeout=60,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(root),
                **quiet_subprocess_kwargs(),
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
