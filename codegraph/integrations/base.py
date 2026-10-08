# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The AgentIntegration protocol and its five built-in
#              implementations (Claude Code, Cursor, Codex, Gemini CLI,
#              IBM Bob).
#              An integration knows how to detect its tool and install
#              the cgh instructions. Third-party tools register objects
#              with the same shape under the "integration" extension
#              namespace; core is just the first consumer of its own
#              surface.
#
#              The guard methods (guard_spec, install_guard,
#              guard_installed) stay in the protocol so existing
#              third-party integrations keep type-checking, but cgh
#              stopped guarding file access in 0.15.0: every built-in
#              declares level "none" and installs nothing.

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass
class GuardSpec:
    """Kept for protocol compatibility. Since 0.15.0 cgh installs no
    guard, so the built-ins all return level "none".

    level: "enforce", "partial", "advisory" or "none".
    """

    level: str
    note: str = ""


_NO_GUARD = GuardSpec(level="none", note="cgh no longer guards file access")


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
        """Deprecated since 0.15.0; cgh no longer calls it."""
        ...

    def install_guard(self, root: Path) -> bool:
        """Deprecated since 0.15.0; cgh no longer calls it."""
        ...

    def guard_installed(self, root: Path) -> bool:
        """Deprecated since 0.15.0; cgh no longer calls it."""
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
        return _NO_GUARD

    def install_guard(self, root: Path) -> bool:
        return False

    def guard_installed(self, root: Path) -> bool:
        return False


class CursorIntegration:
    name = "cursor"
    display = "Cursor"

    def detect(self, root: Path) -> bool:
        return (root / ".cursor").exists() or (root / ".cursorrules").exists()

    def install_instructions(self, root: Path) -> list[str]:
        from codegraph.integrations.skill_installer import install_cursor

        return install_cursor(root)

    def guard_spec(self) -> GuardSpec:
        return _NO_GUARD

    def install_guard(self, root: Path) -> bool:
        return False

    def guard_installed(self, root: Path) -> bool:
        return False


class GeminiIntegration:
    """Gemini CLI: instructions in GEMINI.md and .gemini/."""

    name = "gemini"
    display = "Gemini CLI"

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
        return _NO_GUARD

    def install_guard(self, root: Path) -> bool:
        return False

    def guard_installed(self, root: Path) -> bool:
        return False


class CodexIntegration:
    """Codex CLI: instructions in AGENTS.md."""

    name = "codex"
    display = "Codex CLI"

    def detect(self, root: Path) -> bool:
        import shutil

        return (root / "AGENTS.md").exists() or shutil.which("codex") is not None

    def install_instructions(self, root: Path) -> list[str]:
        from codegraph.integrations.skill_installer import install_codex

        return install_codex(root)

    def guard_spec(self) -> GuardSpec:
        return _NO_GUARD

    def install_guard(self, root: Path) -> bool:
        return False

    def guard_installed(self, root: Path) -> bool:
        return False


class BobIntegration:
    """IBM Bob (BobShell + the Bob IDE, whose CLI shim is `bobide`): the
    bundled skills install verbatim under .bob/skills/, the usage
    guidelines under .bob/rules/, and the MCP server in .bob/mcp.json,
    the three locations the agent reads."""

    name = "bob"
    display = "IBM Bob"

    def detect(self, root: Path) -> bool:
        import shutil

        return (
            (root / ".bob").exists()
            or (root / ".bobide").exists()
            or (root / ".bobignore").exists()
            or shutil.which("bobide") is not None
            or shutil.which("bob") is not None
        )

    def install_instructions(self, root: Path) -> list[str]:
        from codegraph.integrations.skill_installer import install_bob

        return install_bob(root)

    def guard_spec(self) -> GuardSpec:
        return _NO_GUARD

    def install_guard(self, root: Path) -> bool:
        return False

    def guard_installed(self, root: Path) -> bool:
        return False


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
