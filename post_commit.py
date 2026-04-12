#!/usr/bin/env python3
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Post-commit pipeline for Claude Code hook.
#   1. Re-index changed files in codegraph
#   2. Detect patterns (openapi regen needed, entity type changes, etc.)
#   3. Store commit context in Ruflo memory
#
# Usage: python -m codegraph.post_commit [--since HEAD~1]

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _git_changed_files(repo_root: Path, since: str = "HEAD~1") -> list[str]:
    """Get files changed since a git ref."""
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACMR", since],
        capture_output=True,
        text=True,
        cwd=str(repo_root),
    )
    return [f.strip() for f in result.stdout.strip().splitlines() if f.strip()]


def _git_commit_info(repo_root: Path) -> dict:
    """Get latest commit info."""
    result = subprocess.run(
        ["git", "log", "-1", "--format=%H%n%s%n%an%n%ai"],
        capture_output=True,
        text=True,
        cwd=str(repo_root),
    )
    lines = result.stdout.strip().splitlines()
    if len(lines) >= 4:
        return {
            "sha": lines[0][:12],
            "message": lines[1],
            "author": lines[2],
            "date": lines[3],
        }
    return {}


def _reindex_codegraph(repo_root: Path, files: list[str]) -> int:
    """Re-index changed files in codegraph. Returns count of indexed files."""
    try:
        from codegraph.indexer import index_file

        indexed = 0
        for rel_path in files:
            full = repo_root / rel_path
            if full.exists():
                if index_file(full, repo_root):
                    indexed += 1
        return indexed
    except Exception as exc:
        print(f"[post-commit] codegraph index error: {exc}", file=sys.stderr)
        return 0


def _detect_patterns(files: list[str]) -> list[str]:
    """Detect actionable patterns from changed files."""
    alerts = []

    router_or_schema = [f for f in files if f.startswith("api/routers/") or f.startswith("api/schemas/")]
    if router_or_schema:
        alerts.append(
            "OPENAPI_REGEN: Router/schema files changed — openapi.json may need regeneration + frontend backlog issue"
        )

    model_files = [f for f in files if f.startswith("api/models/")]
    if model_files:
        alerts.append(
            f"MIGRATION_CHECK: {len(model_files)} model file(s) changed — check if Alembic migration is needed"
        )

    webhook_files = [f for f in files if "webhook" in f.lower()]
    if webhook_files:
        alerts.append("SECURITY_CHECK: Webhook handler changed — verify SSRF guards and signature validation")

    handler_files = [f for f in files if f.startswith("api/handlers/")]
    if handler_files:
        alerts.append(
            f"HANDLER_CHANGE: {len(handler_files)} handler(s) changed — "
            "verify cross-table search includes tags/custom fields if list() modified"
        )

    tenant_files = [f for f in files if "tenant" in f.lower() or "rls" in f.lower() or "auth" in f.lower()]
    if tenant_files:
        alerts.append("TENANT_SECURITY: Tenant/auth file changed — verify RLS context is always set, never swallowed")

    return alerts


def main():
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="HEAD~1")
    ap.add_argument("--root", default=None)
    args = ap.parse_args()

    repo_root = Path(args.root).resolve() if args.root else Path.cwd().resolve()
    since = args.since

    # 1. Get changed files
    files = _git_changed_files(repo_root, since)
    if not files:
        print("[post-commit] no changed files")
        return

    # 2. Re-index codegraph
    indexed = _reindex_codegraph(repo_root, files)

    # 3. Detect patterns
    alerts = _detect_patterns(files)

    # 4. Get commit info
    commit = _git_commit_info(repo_root)

    # 5. Print summary
    print(f"[post-commit] {commit.get('sha', '?')}: {commit.get('message', '?')}")
    print(f"[post-commit] codegraph: {indexed}/{len(files)} files re-indexed")
    for alert in alerts:
        print(f"[post-commit] ⚠ {alert}")

    # 6. Output JSON for Ruflo memory (stdout, captured by hook)
    summary = {
        "commit": commit,
        "files_changed": files,
        "files_count": len(files),
        "codegraph_indexed": indexed,
        "alerts": alerts,
        "py_files": [f for f in files if f.endswith(".py")],
        "md_files": [f for f in files if f.endswith(".md")],
    }
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
