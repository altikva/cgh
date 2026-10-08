# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: What is left of the confidentiality guard. Since 0.15.0
#              cgh no longer vetoes agent file access: the hook commands
#              always allow, and blocking file reads is the job of the
#              agent's own permission rules. This module removes what
#              older versions wrote into a repo: the Read() deny rules in
#              .claude/settings.local.json (recorded in the
#              guard_denies.json sidecar), the managed .bobignore block,
#              and the guard hook entries cgh added to Claude Code,
#              Gemini CLI and Codex configs. Only entries cgh wrote are
#              touched, identified by the sidecar or by cgh's markers.

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

_SIDECAR = "guard_denies.json"

_BOBIGNORE_START = "# >>> cgh guard (managed, do not edit) >>>"
_BOBIGNORE_END = "# <<< cgh guard <<<"

# Hook markers older versions wrote for the guard, per agent config.
_CLAUDE_GUARD_MARKER = "cgh-guard"
_GEMINI_GUARD_MARKER = "cgh-guard"
_CODEX_GUARD_MARKER = "_hook_guard_codex"


@dataclass
class CleanupReport:
    """What cleanup_guard_leftovers removed. Empty means nothing to do."""

    claude_rules: list[str] = field(default_factory=list)
    bobignore_lines: list[str] = field(default_factory=list)
    hooks: list[str] = field(default_factory=list)  # config files edited
    sidecar_removed: bool = False

    @property
    def changed(self) -> bool:
        return bool(
            self.claude_rules
            or self.bobignore_lines
            or self.hooks
            or self.sidecar_removed
        )


def _sidecar_path(repo_root: str | Path) -> Path:
    return Path(repo_root) / ".codegraph" / _SIDECAR


def _load_json(path: Path) -> dict | None:
    """Parsed JSON object, {} when absent, None when unreadable (the
    caller then leaves the file alone: never clobber what we cannot
    parse)."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _remove_claude_rules(root: Path) -> list[str] | None:
    """Drop the Read() deny rules listed in the sidecar from
    .claude/settings.local.json. Returns the removed rules, or None when
    the settings file could not be parsed (the sidecar is then kept so a
    later run can finish the job)."""
    sidecar = _sidecar_path(root)
    if not sidecar.exists():
        return []
    try:
        ours = {str(r) for r in json.loads(sidecar.read_text(encoding="utf-8"))}
    except (ValueError, OSError, TypeError):
        ours = set()
    if not ours:
        return []

    settings_path = root / ".claude" / "settings.local.json"
    settings = _load_json(settings_path)
    if settings is None:
        return None
    permissions = settings.get("permissions")
    if not isinstance(permissions, dict):
        return []
    deny = permissions.get("deny")
    if not isinstance(deny, list):
        return []
    removed = [r for r in deny if r in ours]
    if not removed:
        return []
    kept = [r for r in deny if r not in ours]
    if kept:
        permissions["deny"] = kept
    else:
        permissions.pop("deny", None)
        if not permissions:
            settings.pop("permissions", None)
    _write_json(settings_path, settings)
    return removed


def _remove_bobignore_block(root: Path) -> list[str]:
    """Remove cgh's managed block from .bobignore, keeping every line
    outside it. Deletes the file when nothing else was in it."""
    ignore_path = root / ".bobignore"
    if not ignore_path.exists():
        return []
    try:
        # Lenient decode: a non-UTF-8 ignore file must not crash cleanup.
        existing = ignore_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    block_re = re.compile(
        re.escape(_BOBIGNORE_START) + r"\n(.*?)" + re.escape(_BOBIGNORE_END) + r"\n?",
        re.DOTALL,
    )
    match = block_re.search(existing)
    if not match:
        return []
    lines = [line for line in match.group(1).splitlines() if line.strip()]
    rest = block_re.sub("", existing, count=1)
    if rest.strip():
        ignore_path.write_text(rest, encoding="utf-8")
    else:
        ignore_path.unlink()
    # An empty managed block still counts as a removal of the markers.
    return lines or [_BOBIGNORE_START]


def _is_guard_command(entry: object, marker: str) -> bool:
    return isinstance(entry, dict) and (
        marker in str(entry.get("command", ""))
        and "_hook_guard" in str(entry.get("command", ""))
    )


def _drop_marked(bucket: list, marker: str) -> list:
    """Drop cgh's guard hook from a hook bucket and nothing else. A group
    ({matcher, hooks: [...]}) loses only the guard command; the group goes
    only when that leaves it empty, so a user hook sharing it survives. A
    flat entry is dropped when it is the guard command itself."""
    kept: list = []
    for entry in bucket:
        inner = entry.get("hooks") if isinstance(entry, dict) else None
        if isinstance(inner, list):
            rest = [h for h in inner if not _is_guard_command(h, marker)]
            if len(rest) == len(inner):
                kept.append(entry)
            elif rest:
                kept.append({**entry, "hooks": rest})
            continue
        if not _is_guard_command(entry, marker):
            kept.append(entry)
    return kept


def _remove_guard_hooks(root: Path) -> list[str]:
    """Remove the guard hook entries cgh installed, by marker. Returns
    the config files edited."""
    edited: list[str] = []

    # Claude Code: the cgh-guard PreToolUse entry, in either settings file.
    for name in ("settings.local.json", "settings.json"):
        path = root / ".claude" / name
        data = _load_json(path)
        if not data:
            continue
        hooks = data.get("hooks")
        if not isinstance(hooks, dict):
            continue
        bucket = hooks.get("PreToolUse")
        if not isinstance(bucket, list):
            continue
        kept = _drop_marked(bucket, _CLAUDE_GUARD_MARKER)
        if kept != bucket:
            if kept:
                hooks["PreToolUse"] = kept
            else:
                hooks.pop("PreToolUse")
            _write_json(path, data)
            edited.append(f".claude/{name}")

    # Gemini CLI: the BeforeTool entry tagged cgh-guard.
    path = root / ".gemini" / "settings.json"
    data = _load_json(path)
    if data:
        hooks = data.get("hooks")
        bucket = hooks.get("BeforeTool") if isinstance(hooks, dict) else None
        if isinstance(bucket, list):
            kept = _drop_marked(bucket, _GEMINI_GUARD_MARKER)
            if kept != bucket:
                if kept:
                    hooks["BeforeTool"] = kept
                else:
                    hooks.pop("BeforeTool")
                _write_json(path, data)
                edited.append(".gemini/settings.json")

    # Codex: the PreToolUse entry running _hook_guard_codex. The
    # codex_hooks feature flag stays: other hooks may rely on it.
    path = root / ".codex" / "hooks.json"
    data = _load_json(path)
    if data:
        hooks = data.get("hooks")
        bucket = hooks.get("PreToolUse") if isinstance(hooks, dict) else None
        if isinstance(bucket, list):
            kept = _drop_marked(bucket, _CODEX_GUARD_MARKER)
            if kept != bucket:
                if kept:
                    hooks["PreToolUse"] = kept
                else:
                    hooks.pop("PreToolUse")
                _write_json(path, data)
                edited.append(".codex/hooks.json")
    return edited


def cleanup_guard_leftovers(repo_root: str | Path) -> CleanupReport:
    """Remove every guard artifact older cgh versions wrote into this
    repo, and nothing else. Idempotent; safe to run on every init."""
    root = Path(repo_root)
    report = CleanupReport()

    removed = _remove_claude_rules(root)
    if removed is not None:
        report.claude_rules = removed
        sidecar = _sidecar_path(root)
        if sidecar.exists():
            try:
                sidecar.unlink()
                report.sidecar_removed = True
            except OSError:
                pass

    report.bobignore_lines = _remove_bobignore_block(root)
    report.hooks = _remove_guard_hooks(root)
    return report


def sync_static_rules(repo_root: str | Path) -> tuple[int, int]:
    """Deprecated since 0.15.0, kept for the plugin API. cgh no longer
    writes deny rules; this removes the ones it wrote before. Returns
    (added, removed), added is always 0."""
    root = Path(repo_root)
    removed = _remove_claude_rules(root)
    if removed is None:
        return (0, 0)
    try:
        _sidecar_path(root).unlink(missing_ok=True)
    except OSError:
        pass
    return (0, len(removed))
