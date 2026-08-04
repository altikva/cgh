# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: CLI commands: init, setup, parsers.

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from rich import box
from rich.panel import Panel
from rich.table import Table

from codegraph.cli import LOGO, console
from codegraph.core.utils import quiet_subprocess_kwargs

# Wildcards that cover every codegraph MCP tool (current + future). The
# two-form shape matches what both older and current Claude Code builds
# accept.
_CODEGRAPH_ALLOW_PATTERNS = ("mcp__codegraph", "mcp__codegraph__*")


def _configure_claude_auto_accept(root: Path) -> list[str]:
    """
    Add codegraph MCP wildcards to .claude/settings.local.json so
    Claude Code doesn't prompt for every tool call. Also removes any
    redundant per-tool entries previously added via the "don't ask
    again" dialog. Returns the list of patterns that were newly added.
    """
    import json as _json

    settings_dir = root / ".claude"
    settings_dir.mkdir(exist_ok=True)
    settings_path = settings_dir / "settings.local.json"

    if settings_path.exists():
        try:
            data = _json.loads(settings_path.read_text(encoding="utf-8") or "{}")
        except Exception:
            data = {}
    else:
        data = {}

    allow = data.setdefault("permissions", {}).setdefault("allow", [])

    # Drop redundant per-tool entries
    allow[:] = [
        item
        for item in allow
        if not (item.startswith("mcp__codegraph__") and item != "mcp__codegraph__*")
    ]

    # Add wildcards if missing
    added: list[str] = []
    for pattern in _CODEGRAPH_ALLOW_PATTERNS:
        if pattern not in allow:
            allow.append(pattern)
            added.append(pattern)

    settings_path.write_text(_json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return added


def _incremental_via_owner(
    root: Path,
    port: int | None,
    console_obj,
    tool_name: str = "incremental_reindex",
    overall_timeout: int = 900,
) -> None:
    """
    Call the running MCP owner to run a scan, while live-tailing
    .codegraph/activity.log so the user sees progress instead of
    staring at a frozen prompt.

    Runs the HTTP POST in a background thread with a generous timeout
    (15 min). The main thread renders a Rich Live view polling the
    activity log every 500ms. Either branch finishing the scan wins.
    """
    if not port:
        console_obj.print("  [yellow]owner port not known, skipping[/yellow]")
        return

    import http.client
    import json as _json
    import threading
    import time as _t

    from rich.live import Live
    from rich.table import Table

    from codegraph.state.activity import tail as _act_tail
    from codegraph.state.auth import ensure_auth_key

    token = ensure_auth_key(root)
    body = _json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": {}},
        }
    )

    result_holder: dict = {"status": None, "body": None, "error": None}
    done = threading.Event()

    def _call() -> None:
        try:
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=overall_timeout)
            c.request(
                "POST",
                "/mcp",
                body=body.encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                    "Authorization": f"Bearer {token}",
                },
            )
            resp = c.getresponse()
            result_holder["status"] = resp.status
            result_holder["body"] = resp.read().decode("utf-8", errors="replace")
            c.close()
        except Exception as exc:
            result_holder["error"] = str(exc)
        finally:
            done.set()

    t = threading.Thread(target=_call, daemon=True)
    t.start()

    def _render() -> Table:
        entries = _act_tail(root, n=8)
        tbl = Table(
            title=f"[bold cyan]owner:[/bold cyan] {tool_name} in progress…",
            title_style="",
            expand=False,
        )
        tbl.add_column("when", style="dim", width=10)
        tbl.add_column("event", width=16)
        tbl.add_column("detail", overflow="fold")
        if not entries:
            tbl.add_row("-", "[dim]waiting[/dim]", "[dim]activity log empty[/dim]")
        now = _t.time()
        for ts, event, detail in entries:
            age = now - ts
            when = f"{int(age)}s ago" if age < 60 else f"{int(age / 60)}m ago"
            style = (
                "green"
                if event.endswith("_end")
                else ("yellow" if "error" in event else "cyan")
            )
            tbl.add_row(when, f"[{style}]{event}[/{style}]", detail)
        return tbl

    try:
        with Live(_render(), console=console_obj, refresh_per_second=2) as live:
            while not done.is_set():
                _t.sleep(0.5)
                live.update(_render())
    except KeyboardInterrupt:
        console_obj.print(
            "\n  [yellow]stopped watching, owner may still be working in the background[/yellow]"
        )
        return

    # Report result
    if result_holder["error"]:
        console_obj.print(
            f"  [yellow]owner call failed:[/yellow] {result_holder['error']}  "
            "[dim](the owner itself may have completed, check `cgh status`)[/dim]"
        )
        return
    if result_holder["status"] != 200:
        console_obj.print(
            f"  [yellow]owner returned HTTP {result_holder['status']}[/yellow]"
        )
        return

    # Try to pull the JSON stats out of the MCP response
    try:
        payload = _json.loads(result_holder["body"] or "{}")
        content = (payload.get("result") or {}).get("content") or []
        text = next((c["text"] for c in content if c.get("type") == "text"), None)
        if text:
            inner = _json.loads(text)
            reindexed = inner.get("reindexed_count") or inner.get("indexed") or 0
            deleted = inner.get("deleted_count") or 0
            elapsed = inner.get("elapsed_s") or inner.get("elapsed") or "?"
            console_obj.print(
                f"  [green]+[/green] owner completed: reindexed={reindexed}, deleted={deleted}, elapsed={elapsed}s"
            )
            return
    except Exception:
        pass
    console_obj.print("  [green]+[/green] owner completed the scan")


def _detect_existing_subrepos(root: Path, max_depth: int = 4) -> list[Path]:
    """
    Walk the project up to `max_depth` levels deep looking for nested
    directories that already have a `.codegraph/` of their own. These are
    candidates to federate. Skips the parent's own .codegraph/ and common
    ignore dirs (node_modules, .venv, …) for speed.
    """
    skip_dirs = {
        ".git",
        ".codegraph",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".terraform",
        "dist",
        "build",
        ".next",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }
    found: list[Path] = []

    def walk(d: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            entries = list(d.iterdir())
        except (OSError, PermissionError):
            return
        for entry in entries:
            if not entry.is_dir():
                continue
            if entry.name in skip_dirs or entry.name.startswith("."):
                continue
            if entry == root / ".codegraph":
                continue
            # Found a nested .codegraph/ → it's a candidate subrepo
            if (entry / ".codegraph").is_dir() and entry != root:
                found.append(entry.resolve())
                # Don't descend further, subrepos federate as a whole
                continue
            walk(entry, depth + 1)

    walk(root, 0)
    return found


def _detect_existing_state(root: Path) -> dict:
    """
    Probe the project for existing codegraph state so `cgh init` can
    choose the right index strategy and warn about stale artifacts.
    """
    cg_dir = root / ".codegraph"
    graph_db = cg_dir / "graph.db"
    fts_db = cg_dir / "fts.db"

    state = {
        "initialized": cg_dir.exists(),
        "graph_db_bytes": 0,
        "fts_db_bytes": 0,
        "indexed_files": 0,
        "owner_alive": False,
        "owner_pid": None,
        "owner_port": None,
        "scan_meta": None,
        "extra_dirs": [],
        "agent_blocks": {},  # tool -> True if the codegraph-usage block is already there
        "mcp_server_configured": False,
    }

    if graph_db.exists():
        try:
            state["graph_db_bytes"] = graph_db.stat().st_size
        except OSError:
            pass
    if fts_db.exists():
        try:
            state["fts_db_bytes"] = fts_db.stat().st_size
        except OSError:
            pass

    # File count from the graph (best-effort, readonly, works even if
    # the owner holds the write lock)
    try:
        from codegraph.core.db import get_readonly_connection

        conn = get_readonly_connection(root)
        if conn is not None:
            try:
                state["indexed_files"] = int(conn.count_nodes("File"))
            except Exception:
                pass
    except Exception:
        pass

    # Owner status
    try:
        from codegraph.state.ipc import is_owner_alive, read_owner_pid, read_owner_port

        state["owner_alive"] = is_owner_alive(root)
        state["owner_pid"] = read_owner_pid(root)
        state["owner_port"] = read_owner_port(root)
    except Exception:
        pass

    # Scan meta (last indexed sha + branch)
    try:
        from codegraph.state.scan_meta import read_meta

        state["scan_meta"] = read_meta(root)
    except Exception:
        pass

    # extra_dirs from config.toml
    try:
        import tomllib

        cfg = cg_dir / "config.toml"
        if cfg.exists():
            with open(cfg, "rb") as f:
                state["extra_dirs"] = (
                    tomllib.load(f).get("codegraph", {}).get("extra_dirs", [])
                )
    except Exception:
        pass

    # Existing codegraph-usage blocks in agent root files
    marker = "<!-- codegraph-usage:start -->"
    for tool_key, rel in (
        ("claude", "CLAUDE.md"),
        ("codex", "AGENTS.md"),
        ("gemini", "GEMINI.md"),
        ("cursor", ".cursor/rules/codegraph-usage.mdc"),
    ):
        fp = root / rel
        if fp.exists():
            try:
                content = fp.read_text(encoding="utf-8", errors="replace")
                state["agent_blocks"][tool_key] = (
                    marker in content or tool_key == "cursor"
                )
            except OSError:
                state["agent_blocks"][tool_key] = False

    # Skill files + user modifications
    state["claude_skills_installed"] = []
    state["claude_skills_modified"] = []
    skills_dir = root / ".claude" / "skills"
    if skills_dir.exists():
        try:
            state["claude_skills_installed"] = sorted(
                d.name
                for d in skills_dir.iterdir()
                if d.is_dir() and d.name.startswith("cgh-")
            )
        except OSError:
            pass
        try:
            from codegraph.integrations.skill_installer import detect_modified_skills

            state["claude_skills_modified"] = detect_modified_skills(root)
        except Exception:
            pass

    # MCP server config
    mcp_path = root / ".mcp.json"
    if mcp_path.exists():
        try:
            import json as _json

            data = _json.loads(mcp_path.read_text(encoding="utf-8"))
            state["mcp_server_configured"] = "codegraph" in (
                data.get("mcpServers") or {}
            )
        except Exception:
            pass

    return state


# ---------------------------------------------------------------------------
# Auto-migration: detect a Kuzu graph.db and re-index it into DuckDB
# ---------------------------------------------------------------------------


def _auto_migrate_kuzu_to_duckdb(root: Path) -> None:
    """If ``root/.codegraph/graph.db`` exists and no graph.duckdb is there
    yet, re-index into DuckDB transparently. cgh init is a deliberate
    user action that signals "set this repo up properly", so we don't
    prompt, we just do it and report.

    Mismatch handling: if the verify step finds drift between the two
    backends, both files are kept and we print a warning. cgh status will
    show the "both files" state; the user can re-run ``cgh migrate-to-duckdb
    --force`` once they understand the cause. We do NOT exit cgh init
    on mismatch, the rest of the install flow (MCP server, skills,
    indexing) should still proceed.
    """
    cg = root / ".codegraph"
    kuzu_path = cg / "graph.db"
    duckdb_path = cg / "graph.duckdb"

    if not kuzu_path.exists() or duckdb_path.exists():
        return  # nothing to do, fresh repo or already migrated

    from codegraph.cli.commands_migrate import do_migrate_to_duckdb

    console.print(
        "  [bold]Auto-migrating from Kuzu to DuckDB[/bold]  "
        "[dim](DuckDB is the v0.5 default, ~18× faster index, ~5× smaller DB)[/dim]"
    )
    try:
        result = do_migrate_to_duckdb(root, delete_kuzu=True, force=False)
    except Exception as exc:
        console.print(
            f"    [yellow]Migration failed: {type(exc).__name__}: {exc}[/yellow]"
        )
        console.print(
            "    [dim]Continuing with the existing Kuzu graph. "
            "Run [cyan]cgh migrate-to-duckdb[/cyan] manually to retry.[/dim]\n"
        )
        return

    if result.status == "skipped":
        return
    if result.status == "aborted":
        console.print(f"    [dim]{result.message}[/dim]\n")
        return
    if result.status == "matched":
        console.print(
            f"    [green]+[/green] re-indexed into graph.duckdb "
            f"({result.duckdb_nodes:,} nodes, {result.duckdb_edges:,} edges). "
            "graph.db deleted.\n"
        )
        return
    if result.status in ("stale_kuzu", "kuzu_unreadable"):
        console.print(
            f"    [green]+[/green] re-indexed into graph.duckdb "
            f"({result.duckdb_nodes:,} nodes, {result.duckdb_edges:,} edges). "
            + ("graph.db deleted." if result.kuzu_deleted else "graph.db kept.")
        )
        console.print(
            f"    [dim]Note: {result.message.rstrip('.')}. "
            "DuckDB accepted as canonical.[/dim]\n"
        )
        return
    # mismatched
    console.print(
        f"    [yellow]Counts differ between Kuzu ({result.kuzu_nodes:,} nodes) "
        f"and DuckDB ({result.duckdb_nodes:,} nodes). Kept both files.[/yellow]"
    )
    console.print(f"    [dim]{result.message}[/dim]")
    console.print(
        "    [dim]Inspect with [cyan]cgh status[/cyan], then "
        "[cyan]cgh migrate-to-duckdb --force[/cyan] to retry.[/dim]\n"
    )


def _install_git_reindex_hooks(root: Path) -> None:
    """Install the git hooks that refresh the graph after a pull, merge,
    branch switch, or rebase. Quiet and safe: skips a non-git repo, and skips
    a shared core.hooksPath (with a one-line hint) so init never touches a
    machine-global hooks directory on its own.
    """
    from codegraph.state.git_hooks import hooks_target_info, install_git_hooks

    target, is_shared = hooks_target_info(root)
    if target is None:
        return  # not a git repo
    if is_shared:
        console.print(
            "  [dim]git hooks skipped: core.hooksPath is shared. Run "
            "[/dim][cyan]cgh hooks install --shared[/cyan][dim] to add them there.[/dim]"
        )
        return
    written = install_git_hooks(root)
    if written:
        console.print(
            f"  [green]+[/green] git hooks ({', '.join(written)}) "
            "[dim]reindex after pull / merge / checkout / rebase[/dim]"
        )


# ---------------------------------------------------------------------------
# cmd_init helpers
# ---------------------------------------------------------------------------


def _print_prior_state(prior_state: dict) -> None:
    """Render the 'existing codegraph state detected' summary for an
    already-initialized repo. No-op when the repo is fresh."""
    if not prior_state["initialized"]:
        return
    console.print("  [bold]Existing codegraph state detected:[/bold]\n")
    bits: list[str] = []
    if prior_state["indexed_files"] > 0:
        bits.append(f"{prior_state['indexed_files']:,} files indexed")
    if prior_state["graph_db_bytes"] > 0:
        bits.append(f"graph.db {prior_state['graph_db_bytes'] // 1024} KB")
    if prior_state["owner_alive"]:
        bits.append(
            f"[green]owner running[/green] (pid {prior_state['owner_pid']} port {prior_state['owner_port']})"
        )
    elif prior_state["owner_pid"]:
        bits.append(f"[yellow]stale owner.pid {prior_state['owner_pid']}[/yellow]")
    if prior_state["scan_meta"] and prior_state["scan_meta"].get("git_head"):
        sha = prior_state["scan_meta"]["git_head"][:8]
        branch = prior_state["scan_meta"].get("git_branch") or "?"
        bits.append(f"last scan at {sha} on {branch}")
    if prior_state["extra_dirs"]:
        bits.append(f"{len(prior_state['extra_dirs'])} extra_dirs")
    if prior_state["mcp_server_configured"]:
        bits.append(".mcp.json already has codegraph")
    if prior_state.get("claude_skills_installed"):
        n = len(prior_state["claude_skills_installed"])
        mod = len(prior_state.get("claude_skills_modified") or [])
        label = f"{n} claude skill{'s' if n != 1 else ''}"
        if mod:
            label += f" ([yellow]{mod} modified locally[/yellow])"
        bits.append(label)
    blocks_present = [k for k, v in prior_state["agent_blocks"].items() if v]
    if blocks_present:
        bits.append("agent blocks: " + ", ".join(blocks_present))
    for b in bits:
        console.print(f"    • {b}")
    if not bits:
        console.print("    [dim](initialized but empty, safe to full scan)[/dim]")
    console.print()


def _set_config_mode(root: Path, mode: str) -> bool:
    """Set [codegraph] mode in .codegraph/config.toml, editing in place.
    Replaces the template's commented line or an existing assignment;
    otherwise inserts right under [codegraph]. Returns True on write."""
    import re as _re

    cfg = root / ".codegraph" / "config.toml"
    if not cfg.exists():
        return False
    text = cfg.read_text(encoding="utf-8")
    line = f'mode = "{mode}"'
    new, n = _re.subn(
        r'(?m)^#?\s*mode\s*=\s*"(?:assist|secure)"\s*$', line, text, count=1
    )
    if n == 0:
        new, n = _re.subn(
            r"(?m)^\[codegraph\]\s*$", "[codegraph]\n" + line, text, count=1
        )
    if n == 0:
        new = text.rstrip() + "\n\n[codegraph]\n" + line + "\n"
    cfg.write_text(new, encoding="utf-8")
    return True


def _maybe_enable_secure_mode(root: Path, args: argparse.Namespace, cg_style) -> None:
    """Offer secure mode during the wizard. --secure enables it without
    prompting (and is how parents propagate it to federated children);
    --yes alone never changes posture, non-interactive runs must not
    harden a repo by surprise. Already-secure repos are left alone."""
    from codegraph.state.guard import guard_mode

    if guard_mode(root) == "secure":
        console.print('  [dim]mode = "secure" already set[/dim]\n')
        return

    wants = bool(getattr(args, "secure", False))
    if not wants and not args.yes:
        import questionary

        wants = bool(
            questionary.confirm(
                "Enable secure mode? Guards fail closed, agents are denied "
                "flagged files, egress gates switch to allowlist (default "
                "assist warns without blocking)",
                default=False,
                style=cg_style,
            ).ask()
        )
    if not wants:
        return

    if _set_config_mode(root, "secure"):
        console.print(
            '  [green]+[/green] .codegraph/config.toml [dim](mode = "secure")[/dim]'
        )
        console.print(
            "  [dim]Static deny lists sync as findings appear; run "
            "[cyan]cgh guard sync[/cyan] anytime to refresh them.[/dim]\n"
        )


def _detect_ai_tools(root: Path) -> list[tuple[str, str, bool]]:
    """Probe for installed AI tools, print the detection table, and return
    the full (name, key, detected) list."""
    import shutil

    console.print("  [bold]Detecting AI tools...[/bold]\n")

    all_tools = [
        (
            "Claude Code",
            "claude",
            (root / ".claude").exists() or shutil.which("claude") is not None,
        ),
        (
            "Cursor",
            "cursor",
            (root / ".cursor").exists() or (root / ".cursorrules").exists(),
        ),
        (
            "Codex CLI",
            "codex",
            (root / "AGENTS.md").exists() or shutil.which("codex") is not None,
        ),
        (
            "Gemini CLI",
            "gemini",
            (root / "GEMINI.md").exists()
            or (root / ".gemini").exists()
            or shutil.which("gemini") is not None,
        ),
        (
            "IBM Bob",
            "bob",
            (root / ".bob").exists()
            or (root / ".bobignore").exists()
            or shutil.which("bob") is not None,
        ),
    ]

    for name, _, detected in all_tools:
        icon = "[green]>[/green]" if detected else "[dim]-[/dim]"
        status = "[green]detected[/green]" if detected else "[dim]not found[/dim]"
        console.print(f"    {icon} {name:15s} {status}")

    console.print()
    return all_tools


def _select_tools(
    all_tools: list[tuple[str, str, bool]],
    detected_tools: list[tuple[str, str]],
    args: argparse.Namespace,
    cg_style,
) -> list[str]:
    """Run the interactive multi-select (or auto-pick under --yes) for which
    tools to configure, then print the skipped list. Returns the selected
    tool keys."""
    import questionary

    selected_keys = []
    if detected_tools and not args.yes:
        choices = [
            questionary.Choice(
                title=f"{name}  (MCP server + hooks)",
                value=key,
                checked=True,
            )
            for name, key in detected_tools
        ]
        # Also offer non-detected tools as unchecked
        for name, key, detected in all_tools:
            if not detected:
                choices.append(
                    questionary.Choice(
                        title=f"{name}  [not detected]",
                        value=key,
                        checked=False,
                    )
                )

        selected_keys = questionary.checkbox(
            "Install MCP server for:",
            choices=choices,
            style=cg_style,
            instruction="(space to toggle, enter to confirm)",
        ).ask()

        if selected_keys is None:
            selected_keys = []
    elif args.yes:
        selected_keys = [key for _, key in detected_tools]

    # Show which tools will be skipped (explicit, no silent generation)
    all_keys = [k for _, k, _ in all_tools]
    skipped = [k for k in all_keys if k not in selected_keys]
    if skipped:
        console.print(
            "  [dim]skipping:[/dim] "
            + ", ".join(f"[dim]{k}[/dim]" for k in skipped)
            + "  [dim](no config, no agent block, no skills)[/dim]\n"
        )
    return selected_keys


def _count_parseable_files(root: Path) -> dict[str, int]:
    """Count files the indexer will actually process, by language. Uses
    git ls-files (respects .gitignore) and falls back to glob for a
    non-git repo. Subrepo paths federated above are skipped."""
    import glob

    from codegraph.analysis.federation import child_paths_to_skip, is_under_any
    from codegraph.parsers import get_parser_info

    parsers = get_parser_info()
    ext_to_lang = {ext: info["lang"] for info in parsers for ext in info["extensions"]}

    # Federation skip list, if the user federated subrepos in step 3d above,
    # they should NOT contribute to the file count.
    skip_paths = child_paths_to_skip(root)

    file_counts: dict[str, int] = {}
    try:
        import subprocess

        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(root),
            timeout=30,
            **quiet_subprocess_kwargs(),
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if not line:
                    continue
                if skip_paths and is_under_any(root / line, skip_paths):
                    continue
                suffix = Path(line).suffix.lower()
                lang = ext_to_lang.get(suffix)
                if lang:
                    file_counts[lang] = file_counts.get(lang, 0) + 1
        else:
            raise RuntimeError("git ls-files failed")
    except (subprocess.TimeoutExpired, FileNotFoundError, RuntimeError, OSError):
        # Fallback, glob from project root. Filter out subrepo paths so
        # the count reflects what the indexer will actually process.
        for info in parsers:
            count = 0
            for ext in info["extensions"]:
                for match in glob.glob(
                    f"**/*{ext}", root_dir=str(root), recursive=True
                ):
                    full = root / match
                    if skip_paths and is_under_any(full, skip_paths):
                        continue
                    count += 1
            if count > 0:
                file_counts[info["lang"]] = count

    return file_counts


def _print_file_counts(file_counts: dict[str, int]) -> None:
    """Render the per-language 'Files to index' bar chart."""
    if not file_counts:
        return
    console.print("  [bold]Files to index:[/bold]\n")
    lang_colors = {
        "python": "green",
        "typescript": "blue",
        "javascript": "yellow",
        "terraform": "magenta",
        "markdown": "cyan",
        "vue": "green",
        "nuxt_config": "green",
    }
    for lang, count in sorted(file_counts.items(), key=lambda x: -x[1]):
        color = lang_colors.get(lang, "white")
        bar_len = min(count // 5, 30) or 1
        bar = f"[{color}]{'>' * bar_len}[/{color}]"
        console.print(f"    [{color}]{lang:12s}[/{color}] {count:>5d} files  {bar}")
    console.print()


def _print_init_summary() -> None:
    """Render the closing 'codegraph is ready!' panel."""
    console.print()
    console.print(
        Panel(
            "[bold]codegraph is ready![/bold]\n\n"
            "  [cyan]cgh stats[/cyan]         View graph statistics\n"
            "  [cyan]cgh search X[/cyan]      Find symbols\n"
            "  [cyan]cgh serve[/cyan]         Start MCP server\n"
            "  [cyan]cgh parsers[/cyan]       List supported languages\n"
            "  [cyan]cgh --help[/cyan]        All commands",
            border_style="green",
        )
    )


# ---------------------------------------------------------------------------
# cmd_init
# ---------------------------------------------------------------------------


def _setup_ai_tools(root: Path, args: argparse.Namespace, cg_style) -> None:
    """Init steps 2 to 3c: detect the installed AI tools, install the
    selected integrations (preserving locally edited skills), offer the
    Claude auto-accept and the usage-guidelines injection."""
    import questionary

    # -- Step 2: Detect AI tools --
    all_tools = _detect_ai_tools(root)
    detected_tools = [(name, key) for name, key, detected in all_tools if detected]

    # -- Step 3: Select which tools to configure --
    selected_keys = _select_tools(all_tools, detected_tools, args, cg_style)

    # Check for locally-edited skills before overwriting
    overwrite_skills = True
    if "claude" in selected_keys:
        from codegraph.integrations.skill_installer import detect_modified_skills

        modified = detect_modified_skills(root)
        if modified and not args.yes:
            console.print(
                "  [yellow]You have locally-edited skills in .claude/skills/:[/yellow]"
            )
            for m in modified:
                console.print(
                    f"    • [yellow]{m}[/yellow] (SKILL.md differs from bundled)"
                )
            overwrite_skills = questionary.confirm(
                "Overwrite with the bundled versions? (Your edits will be lost)",
                default=False,
                style=cg_style,
            ).ask()
            if not overwrite_skills:
                console.print(
                    "  [green]Keeping your edits[/green], will refresh only new / unmodified skills.\n"
                )

    for key in selected_keys:
        _install_integration(root, key, overwrite_skills=overwrite_skills)

    if selected_keys:
        console.print()

    # -- Step 3b: offer to auto-accept codegraph MCP tools in Claude Code --
    if "claude" in selected_keys:
        auto_accept = (
            args.yes
            or questionary.confirm(
                "Auto-accept codegraph MCP tool calls in Claude Code (skip the per-call permission prompt)?",
                default=True,
                style=cg_style,
            ).ask()
        )
        if auto_accept:
            added = _configure_claude_auto_accept(root)
            if added:
                console.print(
                    f"    [green]+[/green] .claude/settings.local.json "
                    f"[dim](permissions.allow += {', '.join(added)})[/dim]"
                )
            else:
                console.print(
                    "    [dim]•[/dim] .claude/settings.local.json [dim](already allows codegraph)[/dim]"
                )
            console.print()

    # -- Step 3c: offer to inject codegraph usage guidelines into agent rules --
    if selected_keys:
        from codegraph.integrations.skill_installer import install_usage_guidelines

        target_files = {
            "claude": "CLAUDE.md",
            "codex": "AGENTS.md",
            "gemini": "GEMINI.md",
            "cursor": ".cursor/rules/codegraph-usage.mdc",
        }
        inject_targets = [
            (k, target_files[k]) for k in selected_keys if k in target_files
        ]
        if inject_targets:
            targets_str = ", ".join(f for _, f in inject_targets)
            inject = (
                args.yes
                or questionary.confirm(
                    f"Append codegraph usage guidelines to {targets_str}? "
                    "(Helps agents pick the right tool instead of reading files blindly)",
                    default=True,
                    style=cg_style,
                ).ask()
            )
            if inject:
                for tool_key, _label in inject_targets:
                    written = install_usage_guidelines(root, tool_key)
                    if written:
                        rel = written.replace(str(root) + "/", "")
                        console.print(
                            f"    [green]+[/green] {rel} [dim](codegraph usage block)[/dim]"
                        )
                console.print()


def _offer_federation(root: Path, args: argparse.Namespace, cg_style) -> None:
    """Init step 3e: detect nested repos with their own .codegraph and
    offer to federate them (parent skips indexing, fans out reads)."""
    import questionary

    # -- Step 3e: Detect already-initialized subrepos --
    # Look for nested directories that already have their own .codegraph/.
    # Each is a candidate to federate: the parent will skip indexing them
    # and instead query their own DBs read-only at runtime. Crucial for
    # workspaces containing multiple git repos, without this, the parent
    # would count and try to index every node_modules + child source tree.
    detected_subrepos = _detect_existing_subrepos(root, max_depth=4)
    if detected_subrepos:
        console.print(
            f"  [bold]Detected {len(detected_subrepos)} already-initialized subrepo(s) inside this project:[/bold]\n"
        )
        for s in detected_subrepos:
            try:
                rel = s.relative_to(root)
                shown = f"./{rel}"
            except ValueError:
                shown = str(s)
            git_marker = "[dim](git)[/dim]" if (s / ".git").exists() else ""
            console.print(f"    [cyan]>[/cyan] {shown}  {git_marker}")
        console.print()
        console.print(
            "  [dim]If federated, the parent will skip indexing these and "
            "fan out queries to their existing indexes (read-only).[/dim]\n"
        )
        if (
            args.yes
            or questionary.confirm(
                "Federate them now?",
                default=True,
                style=cg_style,
            ).ask()
        ):
            from codegraph.analysis.federation import add_subrepo

            for s in detected_subrepos:
                try:
                    add_subrepo(root, s)
                    console.print(f"    [green]+[/green] federated {s.name}")
                except ValueError as exc:
                    console.print(f"    [yellow]⚠[/yellow] {s.name}: {exc}")
            console.print()
        else:
            console.print(
                "  [dim]Skipped. To federate later: [cyan]cgh federate add <path>[/cyan][/dim]\n"
            )


def cmd_init(args: argparse.Namespace) -> None:
    import questionary
    from questionary import Style

    from codegraph.core.config import init_project

    root = Path(os.path.abspath(args.root))
    console.print(LOGO)
    console.print(f"  [dim]Project:[/dim] [bold]{root}[/bold]\n")

    cg_style = Style(
        [
            ("qmark", "fg:cyan bold"),
            ("question", "fg:white bold"),
            ("answer", "fg:green bold"),
            ("pointer", "fg:cyan bold"),
            ("highlighted", "fg:cyan bold"),
            ("selected", "fg:green"),
            ("separator", "fg:cyan"),
            # A literal gray, not "white dim": the dim attribute only
            # parses on prompt_toolkit >= 3.0.52 and this must render
            # on every resolvable floor.
            ("instruction", "fg:#808080"),
            ("text", "fg:white"),
        ]
    )

    # -- Step 0: Probe existing state (before anything mutates disk) --
    prior_state = _detect_existing_state(root)
    _print_prior_state(prior_state)

    # -- Auto-migrate Kuzu -> DuckDB before anything else touches the DB --
    _auto_migrate_kuzu_to_duckdb(root)

    # -- Step 1: Create .codegraph/ --
    with console.status("[bold cyan]Setting up codegraph...", spinner="dots"):
        result = init_project(root)

    if result["created"]:
        for f in result["created"]:
            console.print(f"  [green]+[/green] {f}")
    else:
        console.print("  [dim].codegraph/ already exists[/dim]")

    console.print()

    # -- Step 1b: git hooks that keep the graph fresh after pull/merge/checkout --
    _install_git_reindex_hooks(root)

    _setup_ai_tools(root, args, cg_style)

    # -- Step 3d: guard posture (assist stays the default) --
    _maybe_enable_secure_mode(root, args, cg_style)

    _offer_federation(root, args, cg_style)

    # -- Step 4: Detect parseable files --
    file_counts = _count_parseable_files(root)
    _print_file_counts(file_counts)

    total = sum(file_counts.values())

    # -- Step 5: Index now?, branches on prior state --
    if total > 0:
        owner_alive = prior_state.get("owner_alive", False)
        has_data = prior_state.get("indexed_files", 0) > 0
        default_method = "auto"
        choice = None

        if owner_alive:
            console.print(
                "  [yellow]The MCP owner is running, it already watches this repo.[/yellow]"
            )
            if not args.yes:
                choice = (
                    questionary.select(
                        "What do you want to do?",
                        choices=[
                            questionary.Choice(
                                title="Skip, owner keeps the index fresh", value="skip"
                            ),
                            questionary.Choice(
                                title="Incremental rescan through the owner (no lock fight)",
                                value="mcp_scan",
                            ),
                            questionary.Choice(
                                title="Stop owner, wipe DB, full scan  [destructive]",
                                value="reset",
                            ),
                        ],
                        style=cg_style,
                    ).ask()
                    or "skip"
                )
            else:
                choice = "skip"
        elif has_data:
            if not args.yes:
                choice = (
                    questionary.select(
                        f"Index already has {prior_state['indexed_files']:,} files. Action?",
                        choices=[
                            questionary.Choice(
                                title="Incremental (only files changed since last scan)",
                                value="incremental",
                            ),
                            questionary.Choice(
                                title="Full scan (re-parse everything)", value="full"
                            ),
                            questionary.Choice(title="Skip, keep as-is", value="skip"),
                        ],
                        style=cg_style,
                    ).ask()
                    or "incremental"
                )
            else:
                choice = "incremental"
        else:
            if not args.yes:
                do_full = questionary.confirm(
                    f"Run full scan of {total} files now?",
                    default=True,
                    style=cg_style,
                ).ask()
                choice = "full" if do_full else "skip"
            else:
                choice = "full"

        console.print()
        if choice == "full":
            from codegraph.cli.commands_index import cmd_index

            cmd_index(
                argparse.Namespace(root=str(root), verbose=False, method=default_method)
            )
        elif choice == "incremental":
            from codegraph.cli.commands_index import cmd_index

            cmd_index(
                argparse.Namespace(root=str(root), verbose=False, method="incremental")
            )
        elif choice == "mcp_scan":
            _incremental_via_owner(
                root=root,
                port=prior_state.get("owner_port"),
                console_obj=console,
            )
        elif choice == "reset":
            from codegraph.cli.commands_monitor import cmd_reset

            cmd_reset(
                argparse.Namespace(
                    root=str(root), yes=True, drop_extra_dirs=False, no_reindex=False
                )
            )
        else:
            console.print("  [dim]Run 'cgh index' when ready.[/dim]")
    else:
        console.print(
            "  [dim]No parseable files found. Run 'codegraph parsers' to see supported languages.[/dim]"
        )

    # -- Federated children: propagate the init --
    if not getattr(args, "no_children", False):
        _init_children(root, assume_yes=bool(getattr(args, "yes", False)))

    # -- Done --
    _print_init_summary()


def _init_children(root: Path, assume_yes: bool) -> None:
    """Bring every declared subrepo to the current cgh version.

    A parent that upgrades cgh, or re-runs init, propagates to its
    children: uninitialized ones get a full init + index, initialized
    ones get the idempotent refresh (Kuzu auto-migration, hooks, missing
    config, auth key). Each child runs in its own subprocess with
    --no-children, so propagation stays single-level and a federation
    cycle cannot loop. Failures are per-child and never abort the
    parent's init.
    """
    import subprocess
    import sys as _sys

    from codegraph.analysis.federation import resolve_children, verify_child
    from codegraph.core.utils import quiet_subprocess_kwargs

    children = resolve_children(root)
    if not children:
        return

    fresh = [c for c in children if not verify_child(c).ok]
    console.print(
        f"\n[bold]Federated children[/bold] [dim]({len(children)} declared, "
        f"{len(fresh)} need a first init)[/dim]"
    )

    if not assume_yes:
        try:
            answer = (
                console.input(
                    f"  Initialize / refresh the {len(children)} federated "
                    "subrepo(s) now? [Y/n] "
                )
                .strip()
                .lower()
            )
        except EOFError:
            answer = "n"
        if answer in ("n", "no"):
            console.print(
                "  [dim]Skipped. Run[/dim] [cyan]cgh init --yes[/cyan] "
                "[dim]inside each subrepo when ready.[/dim]"
            )
            return

    # Children inherit the parent's posture: a secure parent must not
    # federate into assist children that would leak what it blocks.
    from codegraph.state.guard import guard_mode

    secure_flag = ["--secure"] if guard_mode(root) == "secure" else []

    for child in children:
        was_ok = verify_child(child).ok
        try:
            proc = subprocess.run(
                [
                    _sys.executable,
                    "-m",
                    "codegraph",
                    "init",
                    "--root",
                    str(child),
                    "--yes",
                    "--no-children",
                    *secure_flag,
                ],
                capture_output=True,
                text=True,
                timeout=900,
                **quiet_subprocess_kwargs(),
            )
            if proc.returncode != 0:
                tail = (proc.stderr or proc.stdout or "").strip().splitlines()
                console.print(
                    f"  [red]x[/red] {child.name}: init failed"
                    + (f" [dim]({tail[-1][:100]})[/dim]" if tail else "")
                )
                continue
        except (subprocess.TimeoutExpired, OSError) as exc:
            console.print(f"  [red]x[/red] {child.name}: {exc}")
            continue

        status = verify_child(child)
        if status.ok:
            label = "refreshed" if was_ok else "initialized + indexed"
            console.print(f"  [green]+[/green] {child.name} [dim]({label})[/dim]")
        else:
            console.print(
                f"  [yellow]~[/yellow] {child.name} "
                "[dim]initialized but still no graph DB, run cgh index there[/dim]"
            )


def _claude_hook_specs(cli_prefix: str) -> list[dict]:
    """
    Single source of truth for the cgh-installed Claude Code hooks.

    `target` decides which settings file holds the hook:
      - "shared" → .claude/settings.json   (committed, applies to every
        teammate who clones the repo). Reserved for hooks that fail safely
        when cgh is missing.
      - "local"  → .claude/settings.local.json   (gitignored). Used for
        hooks that hard-require cgh on PATH, so they don't break teammates
        who haven't installed it.
    """
    return [
        {
            "event": "PostToolUse",
            "matcher": "Bash(git commit*)",
            "marker": "cgh-reindex-on-commit",
            "label": "post-commit reindex",
            "target": "shared",
            "command": (
                f"{cli_prefix} index --root . 2>/dev/null || true  "
                f"# cgh-reindex-on-commit"
            ),
            "async": True,
            "statusMessage": "cgh: indexing changes",
        },
        {
            "event": "PreToolUse",
            "matcher": "Grep",
            "marker": "cgh-precheck-grep",
            "label": "pre-Grep symbol hint",
            "target": "local",
            "command": f"{cli_prefix} _hook_precheck_grep  # cgh-precheck-grep",
            "async": False,
        },
        {
            "event": "PreToolUse",
            "matcher": "Read",
            "marker": "cgh-precheck-read",
            "label": "pre-Read outline hint",
            "target": "local",
            "command": f"{cli_prefix} _hook_precheck_read  # cgh-precheck-read",
            "async": False,
        },
        {
            "event": "PreToolUse",
            "matcher": "Read|Grep|Glob|Bash",
            "marker": "cgh-guard",
            "label": "confidentiality guard",
            "target": "local",
            "command": f"{cli_prefix} _hook_guard  # cgh-guard",
            "async": False,
        },
        # Lifecycle hooks (no matcher): session continuity. PreCompact and
        # SessionEnd record an automatic checkpoint marker; SessionStart
        # prints the tiny resume header, the full bundle loads on demand.
        {
            "event": "PreCompact",
            "matcher": "",
            "marker": "cgh-auto-checkpoint",
            "label": "auto checkpoint before compaction",
            "target": "local",
            "command": f"{cli_prefix} _hook_checkpoint  # cgh-auto-checkpoint",
            "async": False,
        },
        {
            "event": "SessionEnd",
            "matcher": "",
            "marker": "cgh-auto-checkpoint-end",
            "label": "auto checkpoint at session end",
            "target": "local",
            "command": f"{cli_prefix} _hook_checkpoint  # cgh-auto-checkpoint-end",
            "async": False,
        },
        {
            "event": "SessionStart",
            "matcher": "",
            "marker": "cgh-resume-header",
            "label": "resume bundle header",
            "target": "local",
            "command": f"{cli_prefix} _hook_resume_header  # cgh-resume-header",
            "async": False,
        },
    ]


def ensure_claude_hooks_installed(root: Path, cli_prefix: str = "cgh") -> list[str]:
    """Standalone entry point for the Claude hook install: load both
    settings files, route every cgh hook spec to the right one, write
    back what changed. Returns the labels of hooks added. Used by the
    AgentIntegration surface; cgh init drives the same machinery with
    its own console output."""
    import json as _json

    settings_dir = Path(root) / ".claude"
    settings_dir.mkdir(parents=True, exist_ok=True)
    shared_path = settings_dir / "settings.json"
    local_path = settings_dir / "settings.local.json"

    def _load(path: Path) -> dict:
        try:
            return (
                _json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            )
        except (ValueError, OSError):
            return {}

    shared = _load(shared_path)
    local = _load(local_path)
    result = _ensure_claude_hooks(shared, local, cli_prefix)
    if result["shared_changed"]:
        shared_path.write_text(_json.dumps(shared, indent=2) + "\n", encoding="utf-8")
    if result["local_changed"]:
        local_path.write_text(_json.dumps(local, indent=2) + "\n", encoding="utf-8")
    return list(result["added"])


def _find_hook(settings: dict, spec: dict) -> bool:
    """True iff `settings` contains a hook entry tagged with spec['marker'].
    Lifecycle hooks carry no matcher; treat missing and empty as equal."""
    bucket = (settings.get("hooks") or {}).get(spec["event"], []) or []
    return any(
        spec["marker"] in str(h.get("hooks", [{}])[0].get("command", ""))
        for h in bucket
        if (h.get("matcher") or "") == (spec["matcher"] or "")
    )


def _drop_hook(settings: dict, spec: dict) -> bool:
    """Remove any hook entry tagged with spec['marker']. Returns True if dropped."""
    hooks_root = settings.get("hooks") or {}
    bucket = hooks_root.get(spec["event"], []) or []
    keep = [
        h
        for h in bucket
        if not (
            (h.get("matcher") or "") == (spec["matcher"] or "")
            and spec["marker"] in str(h.get("hooks", [{}])[0].get("command", ""))
        )
    ]
    if len(keep) == len(bucket):
        return False
    if keep:
        hooks_root[spec["event"]] = keep
    else:
        hooks_root.pop(spec["event"], None)
    return True


def _append_hook(settings: dict, spec: dict) -> None:
    """Append a hook entry built from spec into `settings`."""
    hooks_root = settings.setdefault("hooks", {})
    bucket = hooks_root.setdefault(spec["event"], [])
    entry: dict = {"type": "command", "command": spec["command"]}
    if spec.get("async"):
        entry["async"] = True
    if spec.get("statusMessage"):
        entry["statusMessage"] = spec["statusMessage"]
    wrapper: dict = {"hooks": [entry]}
    if spec.get("matcher"):
        wrapper["matcher"] = spec["matcher"]
    bucket.append(wrapper)


def _hook_launcher(cli_prefix: str) -> str:
    """On Windows, point the hooks at the windowless launcher.

    Hooks fire on every tool call, and each one spawns a fresh process.
    `cgh.exe` is a console application, so Windows gives it a console:
    a cmd window flashes for every Read, Grep and Bash the agent runs,
    and a federated workspace multiplies it. `cghw.exe` is the same
    entry point built from the GUI subsystem, so no window is ever
    created; stdout stays the pipe the hook protocol needs. Only
    swapped when that launcher actually exists next to `cgh`, so an
    older install keeps working.
    """
    import shutil

    if os.name != "nt" or not cli_prefix.endswith("cgh"):
        return cli_prefix
    windowless = shutil.which("cghw")
    return windowless if windowless else cli_prefix


def _ensure_claude_hooks(
    settings_shared: dict, settings_local: dict, cli_prefix: str
) -> dict:
    """
    Idempotently route each cgh hook to the right settings file.

    Returns:
      - added / moved: human-readable labels for breadcrumbs
      - shared_changed / local_changed: True if the corresponding dict
        was mutated (add OR drop). Caller writes each file back only when
        its flag is True so untouched files stay byte-identical on disk.
    """
    added: list[str] = []
    moved: list[str] = []
    shared_changed = False
    local_changed = False

    for spec in _claude_hook_specs(cli_prefix):
        right_is_shared = spec["target"] == "shared"
        right = settings_shared if right_is_shared else settings_local
        wrong = settings_local if right_is_shared else settings_shared

        in_wrong = _drop_hook(wrong, spec)
        if in_wrong:
            if right_is_shared:
                local_changed = True
            else:
                shared_changed = True

        in_right = _find_hook(right, spec)
        if not in_right:
            _append_hook(right, spec)
            if right_is_shared:
                shared_changed = True
            else:
                local_changed = True
            if in_wrong:
                moved.append(spec["label"])
            else:
                added.append(spec["label"])

    return {
        "added": added,
        "moved": moved,
        "shared_changed": shared_changed,
        "local_changed": local_changed,
    }


# ---------------------------------------------------------------------------
# Audit, used by `cgh doctor` to report drift between installed Claude
# integration files and what the current cgh version would write.
# ---------------------------------------------------------------------------

# Markers used to detect installed hooks. Must stay in sync with the spec
# list returned by _claude_hook_specs above. `target` decides which
# settings file should hold the hook:
#   "shared" → .claude/settings.json
#   "local"  → .claude/settings.local.json
_CLAUDE_HOOK_MARKERS = [
    (
        "cgh-reindex-on-commit",
        "PostToolUse",
        "Bash(git commit*)",
        "post-commit reindex",
        "shared",
    ),
    ("cgh-precheck-grep", "PreToolUse", "Grep", "pre-Grep symbol hint", "local"),
    ("cgh-precheck-read", "PreToolUse", "Read", "pre-Read outline hint", "local"),
]


def _bucket_has(bucket: list, matcher: str, marker: str) -> bool:
    """Helper: scan a hooks list for an entry tagged with `marker`."""
    return any(
        marker in str(h.get("hooks", [{}])[0].get("command", ""))
        for h in (bucket or [])
        if h.get("matcher") == matcher
    )


def audit_claude_integration(root: Path) -> dict:
    """
    Inspect the Claude Code integration files in `root` and report what's
    installed, missing, or stale vs the cgh version currently on PATH.

    Read-only, never writes. `cgh doctor` calls this and renders the
    result; `cgh setup claude` is the action side.
    """
    import json as _json

    from codegraph.integrations.skill_installer import (
        _iter_skills,
        detect_modified_skills,
    )

    report: dict = {}

    # .mcp.json, codegraph entry present?
    mcp_path = root / ".mcp.json"
    mcp_ok = False
    if mcp_path.exists():
        try:
            data = _json.loads(mcp_path.read_text(encoding="utf-8"))
            mcp_ok = "codegraph" in (data.get("mcpServers") or {})
        except (OSError, _json.JSONDecodeError):
            mcp_ok = False
    report["mcp_json"] = {
        "status": "ok" if mcp_ok else "missing",
        "path": str(mcp_path.relative_to(root)) if mcp_path.exists() else ".mcp.json",
    }

    # Hooks, count present markers vs expected, per file. A hook found
    # in the wrong file (e.g. pre-Grep stuck in settings.json after the
    # split) counts as installed but misplaced; doctor surfaces it so
    # `cgh setup claude` can move it.
    def _load_settings(path: Path) -> dict:
        if not path.exists():
            return {}
        try:
            return _json.loads(path.read_text(encoding="utf-8")) or {}
        except (OSError, _json.JSONDecodeError):
            return {}

    shared = _load_settings(root / ".claude" / "settings.json")
    local = _load_settings(root / ".claude" / "settings.local.json")

    installed_markers: list[str] = []
    missing_labels: list[str] = []
    misplaced_labels: list[str] = []
    for marker, event, matcher, label, target in _CLAUDE_HOOK_MARKERS:
        right = shared if target == "shared" else local
        wrong = local if target == "shared" else shared
        if _bucket_has(right.get("hooks", {}).get(event, []), matcher, marker):
            installed_markers.append(marker)
        elif _bucket_has(wrong.get("hooks", {}).get(event, []), matcher, marker):
            installed_markers.append(marker)
            misplaced_labels.append(label)
        else:
            missing_labels.append(label)
    expected = len(_CLAUDE_HOOK_MARKERS)
    installed = len(installed_markers)
    if installed == expected and not misplaced_labels:
        hook_status = "ok"
    elif misplaced_labels and not missing_labels:
        hook_status = "misplaced"
    elif installed == 0:
        hook_status = "missing"
    else:
        hook_status = "partial"
    report["hooks"] = {
        "status": hook_status,
        "installed": installed,
        "expected": expected,
        "missing": missing_labels,
        "misplaced": misplaced_labels,
    }

    # Skills, count bundled vs on-disk + modified
    bundled = [name for name, _fm, _body, _d in _iter_skills()]
    skills_dir = root / ".claude" / "skills"
    missing_skills: list[str] = []
    for name in bundled:
        if not (skills_dir / name / "SKILL.md").is_file():
            missing_skills.append(name)
    modified_skills = detect_modified_skills(root)
    if not missing_skills and not modified_skills:
        skills_status = "ok"
    elif missing_skills and not modified_skills:
        skills_status = "missing"
    else:
        skills_status = "stale"
    report["skills"] = {
        "status": skills_status,
        "bundled": len(bundled),
        "installed": len(bundled) - len(missing_skills),
        "missing": missing_skills,
        "modified": modified_skills,
    }

    # CLAUDE.md usage block, present?
    claude_md = root / "CLAUDE.md"
    has_block = False
    if claude_md.exists():
        try:
            text = claude_md.read_text(encoding="utf-8")
            has_block = "<!-- codegraph-usage:start -->" in text
        except OSError:
            has_block = False
    rules_file = root / ".claude" / "rules" / "cgh-usage.md"
    if rules_file.exists():
        report["usage_block"] = {"status": "ok", "path": ".claude/rules/cgh-usage.md"}
    else:
        report["usage_block"] = {
            "status": "ok" if has_block else "missing",
            "path": "CLAUDE.md",
        }

    report["overall"] = (
        "ok"
        if all(
            s["status"] == "ok"
            for s in (
                report["mcp_json"],
                report["hooks"],
                report["skills"],
                report["usage_block"],
            )
        )
        else "drift"
    )
    return report


def _install_integration(root: Path, tool: str, overwrite_skills: bool = True) -> None:
    """Install MCP config + hooks for an AI tool."""
    import json as _json
    import shutil

    # Prefer `cgh` (short alias), fall back to `codegraph`, then python -m
    if shutil.which("cgh"):
        mcp_entry = {
            "command": "cgh",
            "args": ["serve", "--root", ".", "--watch", "--reindex"],
        }
    elif shutil.which("codegraph"):
        mcp_entry = {
            "command": "codegraph",
            "args": ["serve", "--root", ".", "--watch", "--reindex"],
        }
    else:
        mcp_entry = {
            "command": sys.executable,
            "args": ["-m", "codegraph", "serve", "--root", ".", "--watch", "--reindex"],
        }

    from codegraph.integrations.skill_installer import (
        install_bob,
        install_claude,
        install_codex,
        install_cursor,
        install_gemini,
    )

    def _skills_line(tool_label: str, names: list[str]) -> None:
        if names:
            plural = "s" if len(names) != 1 else ""
            joined = ", ".join(names)
            console.print(
                f"    [green]+[/green] {tool_label} [dim]({len(names)} skill{plural}: {joined})[/dim]"
            )

    if tool == "claude":
        mcp_path = root / ".mcp.json"
        if mcp_path.exists():
            data = _json.loads(mcp_path.read_text(encoding="utf-8"))
        else:
            data = {"mcpServers": {}}
        data.setdefault("mcpServers", {})["codegraph"] = mcp_entry
        mcp_path.write_text(_json.dumps(data, indent=2) + "\n", encoding="utf-8")
        console.print("    [green]+[/green] .mcp.json [dim](MCP server)[/dim]")

        # Claude hooks, split across two settings files. Shared hooks
        # (committed, team-wide) go into settings.json; local hooks (depend
        # on cgh being on the user's PATH) go into settings.local.json so
        # they don't break teammates who haven't installed cgh.
        settings_dir = root / ".claude"
        settings_dir.mkdir(exist_ok=True)
        shared_path = settings_dir / "settings.json"
        local_path = settings_dir / "settings.local.json"

        shared = (
            _json.loads(shared_path.read_text(encoding="utf-8"))
            if shared_path.exists()
            else {}
        )
        local = (
            _json.loads(local_path.read_text(encoding="utf-8"))
            if local_path.exists()
            else {}
        )

        cli = mcp_entry["command"]  # cgh / codegraph / python -m codegraph
        cli_prefix = cli if cli != sys.executable else f"{sys.executable} -m codegraph"
        cli_prefix = _hook_launcher(cli_prefix)

        result = _ensure_claude_hooks(shared, local, cli_prefix)

        if result["shared_changed"]:
            shared_path.write_text(
                _json.dumps(shared, indent=2) + "\n", encoding="utf-8"
            )
        if result["local_changed"]:
            local_path.write_text(_json.dumps(local, indent=2) + "\n", encoding="utf-8")

        for label in result["added"]:
            target_file = next(
                (
                    s["target"]
                    for s in _claude_hook_specs(cli_prefix)
                    if s["label"] == label
                ),
                "shared",
            )
            target_name = (
                "settings.json" if target_file == "shared" else "settings.local.json"
            )
            console.print(
                f"    [green]+[/green] .claude/{target_name} [dim]({label})[/dim]"
            )
        for label in result["moved"]:
            console.print(
                f"    [yellow]~[/yellow] {label} [dim](moved to the correct settings file)[/dim]"
            )

        # Skills, may preserve local edits if the user said so
        _skills_line(
            ".claude/skills/", install_claude(root, overwrite_modified=overwrite_skills)
        )

    elif tool == "cursor":
        cursor_dir = root / ".cursor"
        cursor_dir.mkdir(exist_ok=True)
        mcp_path = cursor_dir / "mcp.json"
        data = {"mcpServers": {"codegraph": mcp_entry}}
        mcp_path.write_text(_json.dumps(data, indent=2) + "\n", encoding="utf-8")
        console.print("    [green]+[/green] .cursor/mcp.json [dim](MCP server)[/dim]")
        _skills_line(".cursor/rules/", install_cursor(root))

    elif tool == "codex":
        mcp_path = root / ".mcp.json"
        if mcp_path.exists():
            data = _json.loads(mcp_path.read_text(encoding="utf-8"))
        else:
            data = {"mcpServers": {}}
        data.setdefault("mcpServers", {})["codegraph"] = mcp_entry
        mcp_path.write_text(_json.dumps(data, indent=2) + "\n", encoding="utf-8")
        console.print(
            "    [green]+[/green] .mcp.json [dim](MCP server for Codex)[/dim]"
        )
        _skills_line("AGENTS.md", install_codex(root))

    elif tool == "gemini":
        mcp_path = root / ".mcp.json"
        if mcp_path.exists():
            data = _json.loads(mcp_path.read_text(encoding="utf-8"))
        else:
            data = {"mcpServers": {}}
        data.setdefault("mcpServers", {})["codegraph"] = mcp_entry
        mcp_path.write_text(_json.dumps(data, indent=2) + "\n", encoding="utf-8")
        console.print(
            "    [green]+[/green] .mcp.json [dim](MCP server for Gemini)[/dim]"
        )
        _skills_line("GEMINI.md", install_gemini(root))

    elif tool == "bob":
        # Bob reads project-level MCP servers from .bob/mcp.json (its
        # global file is ~/.bob/mcp_settings.json; project wins).
        bob_dir = root / ".bob"
        bob_dir.mkdir(exist_ok=True)
        mcp_path = bob_dir / "mcp.json"
        if mcp_path.exists():
            data = _json.loads(mcp_path.read_text(encoding="utf-8"))
        else:
            data = {"mcpServers": {}}
        data.setdefault("mcpServers", {})["codegraph"] = mcp_entry
        mcp_path.write_text(_json.dumps(data, indent=2) + "\n", encoding="utf-8")
        console.print("    [green]+[/green] .bob/mcp.json [dim](MCP server)[/dim]")
        _skills_line(".bob/skills/", install_bob(root))


# ---------------------------------------------------------------------------
# cmd_parsers
# ---------------------------------------------------------------------------


def cmd_parsers(args) -> None:
    from codegraph.parsers import get_parser_info, get_supported_extensions

    console.print(LOGO)

    table = Table(
        title="Registered Parsers",
        box=box.ROUNDED,
        title_style="bold cyan",
    )
    table.add_column("Language", style="bold")
    table.add_column("Extensions")
    table.add_column("Extracts", style="dim")
    table.add_column("Description", style="dim", max_width=40)

    colors = {
        "python": "green",
        "typescript": "blue",
        "terraform": "magenta",
        "markdown": "cyan",
        "rust": "red",
        "go": "blue",
        "java": "yellow",
    }

    for info in get_parser_info():
        lang = info["lang"]
        color = colors.get(lang, "white")
        exts = " ".join(f"[{color}]{e}[/{color}]" for e in info["extensions"])
        extracts = ", ".join(info["extracts"][:5])
        table.add_row(
            f"[{color}]{lang}[/{color}]",
            exts,
            extracts,
            info.get("description", ""),
        )

    console.print(table)
    console.print(
        f"\n[dim]Total: {len(get_supported_extensions())} file extensions supported[/dim]"
    )
    console.print(
        "\n[dim]To add a new parser: create a file in codegraph/parsers/[/dim]"
    )
    console.print("[dim]with @register_parser('.ext') and subclass BaseParser.[/dim]")


# ---------------------------------------------------------------------------
# cmd_setup
# ---------------------------------------------------------------------------


def cmd_setup(args) -> None:
    """
    Generate integration files for AI tools.

    Single source of truth: delegates the heavy lifting to
    _install_integration so MCP config, skills, and (for Claude) hooks
    stay in lock-step with `cgh init`. Adds the non-interactive extras
    on top: auto-accept permissions and the usage-guidelines block.
    """
    from codegraph.integrations.skill_installer import install_usage_guidelines

    root = Path(os.path.abspath(args.root))
    target = args.target

    console.print(LOGO)

    valid = ("claude", "cursor", "codex", "gemini", "bob", "all")
    if target not in valid:
        console.print(f"[dim]Unknown target: {target}[/dim]")
        console.print(f"[dim]Options: {', '.join(valid)}[/dim]")
        return

    targets = (
        ["claude", "cursor", "codex", "gemini", "bob"] if target == "all" else [target]
    )

    console.print(Panel(f"[bold]Setup for {target}[/bold]", border_style="cyan"))

    for tool_key in targets:
        console.print(f"\n[bold]{tool_key}[/bold]")
        _install_integration(root, tool_key, overwrite_skills=True)

        # Guard hooks, through the integration surface. Claude's ride the
        # shared hook specs above; Gemini and Codex get their own files.
        from codegraph.integrations.base import get_integration

        integration = get_integration(tool_key)
        if integration is not None and integration.guard_spec().level != "none":
            if integration.install_guard(root):
                console.print(
                    f"    [green]+[/green] guard hook installed "
                    f"[dim]({integration.guard_spec().level})[/dim]"
                )
            elif integration.guard_installed(root):
                console.print("    [dim]• guard hook already present[/dim]")

        if tool_key == "claude":
            added = _configure_claude_auto_accept(root)
            if added:
                console.print(
                    f"    [green]+[/green] .claude/settings.local.json "
                    f"[dim](permissions += {', '.join(added)})[/dim]"
                )

        written = install_usage_guidelines(root, tool_key)
        if written:
            rel = written.replace(str(root) + "/", "")
            console.print(f"    [green]+[/green] {rel} [dim](usage guidelines)[/dim]")

    console.print()
