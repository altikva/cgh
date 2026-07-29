# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The AgentIntegration protocol and its five built-in
#              implementations (Claude Code, Cursor, Codex, Gemini CLI,
#              IBM Bob).
#              An integration knows how to detect its tool, install the
#              cgh instructions, and wire the confidentiality guard into
#              the tool's own hook system, each with an honestly declared
#              enforcement level. Third-party tools register objects with
#              the same shape under the "integration" extension namespace;
#              core is just the first consumer of its own surface.
#
#              Guard protocols per tool, verified against vendor docs
#              (2026-07): Claude Code and Gemini CLI both send tool_name /
#              tool_input / cwd on stdin and honor exit 2 + stderr as a
#              deny (Gemini's event is BeforeTool in settings.json).
#              Codex fires PreToolUse for shell commands only, reads a
#              stdout JSON decision, and needs codex_hooks = true in
#              .codex/config.toml; repo-local hook configs have known
#              gaps in interactive sessions, so its level is "partial".

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass
class GuardSpec:
    """How well the guard can enforce on this agent.

    level: "enforce" (veto on reads and shell), "partial" (veto on a
    subset, note says which), "advisory" (can warn, cannot block),
    "none" (no usable hook surface; the MCP-side gate is the only
    barrier).
    """

    level: str
    note: str = ""


@runtime_checkable
class AgentIntegration(Protocol):
    """One AI tool cgh knows how to set up end to end."""

    name: str  # registry key, e.g. "gemini"
    display: str  # human name for status output

    def detect(self, root: Path) -> bool:
        """Is this tool plausibly used here (config dir, binary, ...)?"""
        ...

    def install_instructions(self, root: Path) -> list[str]:
        """Write the cgh instructions where this tool reads them.
        Returns labels of what was written."""
        ...

    def guard_spec(self) -> GuardSpec:
        """Declared enforcement capability, honesty required."""
        ...

    def install_guard(self, root: Path) -> bool:
        """Wire the guard into the tool's hook system. Idempotent.
        Returns True when something was written."""
        ...

    def guard_installed(self, root: Path) -> bool:
        """Is the guard hook present for this tool in this repo?"""
        ...


# ---------------------------------------------------------------------------
# Built-ins
# ---------------------------------------------------------------------------


class ClaudeCodeIntegration:
    name = "claude"
    display = "Claude Code"

    def detect(self, root: Path) -> bool:
        import shutil

        return (root / ".claude").exists() or shutil.which("claude") is not None

    def install_instructions(self, root: Path) -> list[str]:
        from codegraph.integrations.skill_installer import install_claude

        return install_claude(root)

    def guard_spec(self) -> GuardSpec:
        return GuardSpec(level="enforce", note="Read, Grep, Glob and Bash veto")

    def install_guard(self, root: Path) -> bool:
        # The guard rides the shared Claude hook machinery (cgh-guard
        # spec); cgh init / cgh setup claude install every spec at once.
        from codegraph.cli.commands_init import ensure_claude_hooks_installed

        return "confidentiality guard" in ensure_claude_hooks_installed(root)

    def guard_installed(self, root: Path) -> bool:
        return _file_mentions(
            root / ".claude" / "settings.local.json", "cgh-guard"
        ) or (_file_mentions(root / ".claude" / "settings.json", "cgh-guard"))


class CursorIntegration:
    name = "cursor"
    display = "Cursor"

    def detect(self, root: Path) -> bool:
        return (root / ".cursor").exists() or (root / ".cursorrules").exists()

    def install_instructions(self, root: Path) -> list[str]:
        from codegraph.integrations.skill_installer import install_cursor

        return install_cursor(root)

    def guard_spec(self) -> GuardSpec:
        return GuardSpec(level="none", note="no hook surface with a veto")

    def install_guard(self, root: Path) -> bool:
        return False

    def guard_installed(self, root: Path) -> bool:
        return False


class GeminiIntegration:
    """Gemini CLI: BeforeTool hooks in .gemini/settings.json, same stdin
    payload shape as Claude (tool_name / tool_input / cwd) and the same
    deny protocol (exit 2, stderr as reason)."""

    name = "gemini"
    display = "Gemini CLI"

    _MATCHER = "read_file|read_many_files|run_shell_command|search_file_content|glob"
    _MARKER = "cgh-guard"

    def detect(self, root: Path) -> bool:
        import shutil

        return (
            (root / "GEMINI.md").exists()
            or (root / ".gemini").exists()
            or shutil.which("gemini") is not None
        )

    def install_instructions(self, root: Path) -> list[str]:
        from codegraph.integrations.skill_installer import install_gemini

        return install_gemini(root)

    def guard_spec(self) -> GuardSpec:
        return GuardSpec(level="enforce", note="file reads and shell veto")

    def install_guard(self, root: Path) -> bool:
        settings_path = root / ".gemini" / "settings.json"
        settings = _load_json(settings_path)
        bucket = settings.setdefault("hooks", {}).setdefault("BeforeTool", [])
        if any(self._MARKER in json.dumps(entry) for entry in bucket):
            return False
        bucket.append(
            {
                "matcher": self._MATCHER,
                "hooks": [
                    {
                        "type": "command",
                        "command": f"cgh _hook_guard  # {self._MARKER}",
                        "name": "cgh confidentiality guard",
                    }
                ],
            }
        )
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(
            json.dumps(settings, indent=2) + "\n", encoding="utf-8"
        )
        return True

    def guard_installed(self, root: Path) -> bool:
        return _file_mentions(root / ".gemini" / "settings.json", self._MARKER)


class CodexIntegration:
    """Codex CLI: PreToolUse hooks in .codex/hooks.json, stdout JSON
    decision, shell interception only, gated behind codex_hooks = true
    in .codex/config.toml. Repo-local hook configs have known gaps in
    interactive sessions, hence the partial level."""

    name = "codex"
    display = "Codex CLI"

    _MARKER = "_hook_guard_codex"

    def detect(self, root: Path) -> bool:
        import shutil

        return (root / "AGENTS.md").exists() or shutil.which("codex") is not None

    def install_instructions(self, root: Path) -> list[str]:
        from codegraph.integrations.skill_installer import install_codex

        return install_codex(root)

    def guard_spec(self) -> GuardSpec:
        return GuardSpec(
            level="partial",
            note="shell veto only; file reads have no hook, repo-local "
            "hooks may not fire in interactive sessions",
        )

    def install_guard(self, root: Path) -> bool:
        wrote = False
        hooks_path = root / ".codex" / "hooks.json"
        hooks = _load_json(hooks_path)
        bucket = hooks.setdefault("hooks", {}).setdefault("PreToolUse", [])
        if not any(self._MARKER in json.dumps(entry) for entry in bucket):
            bucket.append({"command": f"cgh {self._MARKER}"})
            hooks_path.parent.mkdir(parents=True, exist_ok=True)
            hooks_path.write_text(json.dumps(hooks, indent=2) + "\n", encoding="utf-8")
            wrote = True

        # Hooks are silent no-ops without the feature flag.
        config_path = root / ".codex" / "config.toml"
        text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
        if "codex_hooks" not in text:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            addition = (
                "\n[features]\ncodex_hooks = true\n"
                if text
                else "[features]\ncodex_hooks = true\n"
            )
            config_path.write_text(text + addition, encoding="utf-8")
            wrote = True
        return wrote

    def guard_installed(self, root: Path) -> bool:
        return _file_mentions(root / ".codex" / "hooks.json", self._MARKER)


class BobIntegration:
    """IBM Bob (BobShell + the Bob IDE): instructions land as plain
    markdown in .bob/rules/, loaded alphabetically into every mode, and
    the guard mirrors barred paths into a managed .bobignore block, the
    file Bob honors when deciding what it may access. Static denies
    only: Bob publishes no pre-tool hook with a veto, so the level is
    "partial" and `cgh guard sync` keeps the block fresh."""

    name = "bob"
    display = "IBM Bob"

    def detect(self, root: Path) -> bool:
        import shutil

        return (
            (root / ".bob").exists()
            or (root / ".bobignore").exists()
            or shutil.which("bob") is not None
        )

    def install_instructions(self, root: Path) -> list[str]:
        from codegraph.integrations.skill_installer import install_bob

        return install_bob(root)

    def guard_spec(self) -> GuardSpec:
        return GuardSpec(
            level="partial",
            note="static .bobignore denies in secure mode, refreshed by "
            "cgh guard sync; no dynamic veto hook",
        )

    def install_guard(self, root: Path) -> bool:
        from codegraph.state.guard import sync_bobignore

        added, removed = sync_bobignore(root)
        return bool(added or removed)

    def guard_installed(self, root: Path) -> bool:
        from codegraph.state.guard import _BOBIGNORE_START

        return _file_mentions(root / ".bobignore", _BOBIGNORE_START)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_BUILTINS: list[AgentIntegration] = [
    ClaudeCodeIntegration(),
    CursorIntegration(),
    CodexIntegration(),
    GeminiIntegration(),
    BobIntegration(),
]


def all_integrations() -> list[AgentIntegration]:
    """Built-ins plus plugin-registered integrations (the "integration"
    extension namespace), plugin ones last, first name wins."""
    out = list(_BUILTINS)
    try:
        from codegraph.plugins import get_extensions

        seen = {i.name for i in out}
        for obj in get_extensions("integration"):
            if isinstance(obj, AgentIntegration) and obj.name not in seen:
                out.append(obj)
                seen.add(obj.name)
    except Exception:
        pass
    return out


def get_integration(name: str) -> AgentIntegration | None:
    for integration in all_integrations():
        if integration.name == name:
            return integration
    return None


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def _file_mentions(path: Path, needle: str) -> bool:
    try:
        return path.exists() and needle in path.read_text(encoding="utf-8")
    except OSError:
        return False
