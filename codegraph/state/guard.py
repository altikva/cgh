# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The guard: decides whether an agent's native tool call
#              (Read, Grep, Glob, Bash) may touch a file, from the
#              finding store. Blocking facts: a confidential = true
#              finding or any block-severity finding. Bash matching and
#              the fail posture follow the global mode: assist checks
#              read commands and fails open, secure checks any argument
#              hitting a flagged path and fails closed. Also syncs the
#              static Read() deny rules for Claude Code in secure mode,
#              tracked in a sidecar so user-authored rules are never
#              touched.

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

# Commands whose purpose is reading file content. The assist posture
# only guards these; secure guards any command whose arguments hit a
# flagged path.
READ_COMMANDS = {
    "cat", "head", "tail", "less", "more", "sed", "awk", "grep", "rg",
    "cut", "sort", "uniq", "strings", "xxd", "hexdump", "base64", "od",
    "python", "python3", "jq", "open",
}  # fmt: skip

_SIDECAR = "guard_denies.json"


def guard_mode(repo_root: str | Path) -> str:
    from codegraph.core.config import load_config

    return load_config(repo_root).mode


def blocking_paths(repo_root: str | Path) -> set[str]:
    """Absolute paths currently barred: confidential = true, or carrying
    any block-severity finding. Read straight from SQLite, fast enough
    for a per-tool-call hook."""
    from codegraph.state.findings import query_findings

    barred: set[str] = set()
    for row in query_findings(repo_root, severity="block", limit=10000):
        barred.add(row["file"])
    for row in query_findings(repo_root, key_prefix="confidential", limit=10000):
        if row["key"] == "confidential" and str(row["value"]).lower() in (
            "true",
            "yes",
            "1",
        ):
            barred.add(row["file"])
    return barred


def _normalize(root: Path, candidate: str) -> str:
    p = Path(candidate)
    if not p.is_absolute():
        p = root / p
    try:
        return str(p.resolve())
    except OSError:
        return str(p)


_INDEX_DENY = (
    "blocked by cgh guard: the cgh index (.codegraph/) is only served "
    "through the MCP tools; in secure mode its files hold pseudonymized "
    "data and direct reads are denied"
)


def _inside_codegraph(root: Path, target: str) -> bool:
    normalized = _normalize(root, target)
    try:
        return normalized == str(
            (root / ".codegraph").resolve()
        ) or normalized.startswith(str((root / ".codegraph").resolve()) + "/")
    except OSError:
        return False


def check_path(repo_root: str | Path, target: str) -> str | None:
    """Deny reason if `target` is barred, else None."""
    root = Path(repo_root)
    if _normalize(root, target) in blocking_paths(repo_root):
        return f"blocked by cgh guard: {target} is flagged confidential"
    return None


def check_bash(repo_root: str | Path, command: str, mode: str) -> str | None:
    """Deny reason if the shell command would touch a barred path.

    assist: only commands whose pipeline segments start with a known
    read command are guarded (flow over completeness). secure: any
    argument resolving to a barred path denies, whatever the verb
    (a false positive beats a leak).
    """
    root = Path(repo_root)
    # In secure mode the index itself is off-limits to the shell: any
    # command touching .codegraph (sqlite3, cat, cp, ...) is denied. A
    # substring match over-blocks a rare legit command; a false positive
    # beats a leak, the reason points the agent at the MCP tools.
    if mode == "secure" and ".codegraph" in command:
        return _INDEX_DENY

    barred = blocking_paths(repo_root)
    if not barred:
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()

    hit = next((t for t in tokens if _normalize(root, t) in barred), None)
    if hit is None:
        return None
    if mode == "secure":
        return f"blocked by cgh guard: {hit} is flagged confidential"

    segment_heads = {
        seg.strip().split()[0].rsplit("/", 1)[-1]
        for seg in re.split(r"[|;&]+", command)
        if seg.strip()
    }
    if segment_heads & READ_COMMANDS:
        return f"blocked by cgh guard: {hit} is flagged confidential"
    return None


# Shell-tool names across agents: Claude's Bash, Gemini's
# run_shell_command, Codex's shell variants.
_SHELL_TOOLS = {"Bash", "run_shell_command", "shell", "local_shell", "bash"}
# Search/glob tools where a directory target is fine (barred files inside
# are caught when actually read).
_DIR_OK_TOOLS = {"Grep", "Glob", "search_file_content", "glob"}


def check_tool_call(
    repo_root: str | Path, tool_name: str, tool_input: dict, mode: str
) -> str | None:
    """Deny reason for one agent tool call, else None. Tool and argument
    names cover Claude Code and Gemini CLI, whose hook payloads share
    the tool_name / tool_input shape."""
    if tool_name in _SHELL_TOOLS:
        command = tool_input.get("command", "")
        if isinstance(command, list):
            command = " ".join(str(c) for c in command)
        return check_bash(repo_root, str(command), mode)

    root = Path(repo_root)

    # Multi-file reads (Gemini's read_many_files): check every path.
    many = tool_input.get("paths")
    if isinstance(many, list):
        for candidate in many:
            if mode == "secure" and _inside_codegraph(root, str(candidate)):
                return _INDEX_DENY
            reason = check_path(repo_root, str(candidate))
            if reason:
                return reason
        return None

    target = (
        tool_input.get("file_path")
        or tool_input.get("absolute_path")
        or tool_input.get("path")
        or ""
    )
    if not target:
        return None
    # The index is denied before the directory shortcut: grepping inside
    # .codegraph is a content read whatever the tool.
    if mode == "secure" and _inside_codegraph(root, str(target)):
        return _INDEX_DENY
    if tool_name in _DIR_OK_TOOLS:
        p = Path(str(target))
        if not p.suffix and not p.is_file():
            return None
    return check_path(repo_root, str(target))


# ---------------------------------------------------------------------------
# Static deny rules for Claude Code (secure mode second layer)
# ---------------------------------------------------------------------------


def _sidecar_path(repo_root: str | Path) -> Path:
    return Path(repo_root) / ".codegraph" / _SIDECAR


def sync_static_rules(repo_root: str | Path) -> tuple[int, int]:
    """Mirror the barred paths into explicit Read() deny rules in
    .claude/settings.local.json. Only rules recorded in the sidecar are
    ever removed, so user-authored deny rules stay untouched. No-op
    outside secure mode. Returns (added, removed)."""
    root = Path(repo_root)
    if guard_mode(root) != "secure":
        return (0, 0)

    settings_path = root / ".claude" / "settings.local.json"
    settings: dict = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return (0, 0)  # never clobber a file we cannot parse

    ours_before: set[str] = set()
    sidecar = _sidecar_path(root)
    if sidecar.exists():
        try:
            ours_before = set(json.loads(sidecar.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            ours_before = set()

    wanted = {f"Read({p})" for p in blocking_paths(root)}
    # The index itself: pseudonymized at rest, served through MCP only.
    wanted.add("Read(.codegraph/**)")
    permissions = settings.setdefault("permissions", {})
    deny = list(permissions.get("deny", []))

    removed = [r for r in deny if r in ours_before and r not in wanted]
    deny = [r for r in deny if r not in removed]
    added = [r for r in sorted(wanted) if r not in deny]
    deny.extend(added)

    if added or removed:
        permissions["deny"] = deny
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(
            json.dumps(settings, indent=2) + "\n", encoding="utf-8"
        )
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps(sorted(wanted)), encoding="utf-8")
    return (len(added), len(removed))


_BOBIGNORE_START = "# >>> cgh guard (managed, do not edit) >>>"
_BOBIGNORE_END = "# <<< cgh guard <<<"


def sync_bobignore(repo_root: str | Path) -> tuple[int, int]:
    """Mirror the barred paths into a managed block in .bobignore, the
    file IBM Bob reads to decide what it may access. Only lines inside
    the marker block are ever touched, so user-authored ignores stay as
    they are. No-op outside secure mode (an existing managed block is
    still cleared). Returns (added, removed)."""
    root = Path(repo_root)
    ignore_path = root / ".bobignore"
    existing = ""
    if ignore_path.exists():
        try:
            existing = ignore_path.read_text(encoding="utf-8")
        except OSError:
            return (0, 0)

    before: set[str] = set()
    block_re = re.compile(
        re.escape(_BOBIGNORE_START) + r"\n(.*?)" + re.escape(_BOBIGNORE_END) + r"\n?",
        re.DOTALL,
    )
    match = block_re.search(existing)
    if match:
        before = {line for line in match.group(1).splitlines() if line.strip()}

    wanted: set[str] = set()
    if guard_mode(root) == "secure":
        wanted.add(".codegraph/")
        for p in blocking_paths(root):
            try:
                wanted.add(Path(p).resolve().relative_to(root.resolve()).as_posix())
            except ValueError:
                continue  # outside the repo, .bobignore cannot express it

    if wanted == before:
        return (0, 0)

    body = (
        _BOBIGNORE_START
        + "\n"
        + "".join(f"{p}\n" for p in sorted(wanted))
        + _BOBIGNORE_END
        + "\n"
    )
    if match:
        new = block_re.sub(lambda _m: body, existing, count=1)
    elif wanted:
        sep = "" if (not existing or existing.endswith("\n")) else "\n"
        new = existing + sep + body
    else:
        return (0, 0)
    ignore_path.write_text(new, encoding="utf-8")
    return (len(wanted - before), len(before - wanted))


def audit(repo_root: str | Path, message: str) -> None:
    try:
        from codegraph.state.activity import log as activity_log

        activity_log(repo_root, "guard", message)
    except Exception:
        pass
