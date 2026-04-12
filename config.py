# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Configuration system for codegraph.
#
# Config resolution order (later wins):
#   1. Defaults (hardcoded)
#   2. Global:  ~/.codegraph/config.toml
#   3. Project: .codegraph/config.toml
#   4. CLI flags
#
# Config format (TOML):
#
#   [codegraph]
#   ignore_dirs = [".git", "node_modules", "__pycache__", ".venv"]
#   ignore_patterns = ["*.min.js", "*.bundle.js"]
#   max_file_size_kb = 500
#
#   [parsers]
#   enabled = ["python", "typescript", "terraform", "markdown"]
#   # disabled = ["terraform"]   # uncomment to disable
#
#   [mcp]
#   auto_watch = true
#   reindex_on_start = true
#
#   [ruflo]
#   enabled = false              # auto-detected if not set

from __future__ import annotations

import os

# Use tomllib (3.11+) or tomli fallback
import tomllib  # type: ignore
from dataclasses import dataclass, field
from pathlib import Path

CODEGRAPH_DIR = ".codegraph"
CONFIG_FILE = "config.toml"
GLOBAL_DIR = Path.home() / ".codegraph"
CLAUDE_HOME = Path.home() / ".claude"


def _claude_memory_dir_for(project_root: str | Path) -> Path:
    """
    Claude Code stores per-project memory at
    ~/.claude/projects/-<abs-path-with-slashes-as-dashes>/memory/.
    """
    slug = str(Path(project_root).resolve()).replace("/", "-")
    return CLAUDE_HOME / "projects" / slug / "memory"


def _claude_plans_dir() -> Path:
    """Claude Code stores plan files globally at ~/.claude/plans/."""
    return CLAUDE_HOME / "plans"


def memory_dir(project_root: str | Path) -> Path:
    """
    Resolve the memory directory for this project.
    Order: env var → [paths].memory_dir in config.toml → auto-detect.
    """
    env = os.environ.get("CG_MEMORY_DIR") or os.environ.get("CODEGRAPH_MEMORY_DIR")
    if env:
        return Path(env).expanduser().resolve()

    cfg = _read_toml(Path(project_root) / CODEGRAPH_DIR / CONFIG_FILE)
    paths = cfg.get("paths") or {}
    configured = paths.get("memory_dir")
    if configured:
        return Path(configured).expanduser().resolve()

    return _claude_memory_dir_for(project_root)


def plans_dir(project_root: str | Path) -> Path:
    """
    Resolve the plans directory.
    Order: env var → [paths].plans_dir in config.toml → auto-detect.
    """
    env = os.environ.get("CG_PLANS_DIR") or os.environ.get("CODEGRAPH_PLANS_DIR")
    if env:
        return Path(env).expanduser().resolve()

    cfg = _read_toml(Path(project_root) / CODEGRAPH_DIR / CONFIG_FILE)
    paths = cfg.get("paths") or {}
    configured = paths.get("plans_dir")
    if configured:
        return Path(configured).expanduser().resolve()

    return _claude_plans_dir()


DEFAULT_IGNORE_DIRS = [
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
    "coverage",
    ".coverage",
    "htmlcov",
    ".eggs",
    "*.egg-info",
]

DEFAULT_IGNORE_PATTERNS = [
    "*.min.js",
    "*.bundle.js",
    "*.map",
    "*.pyc",
    "*.pyo",
    "*.so",
    "*.dylib",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
]


@dataclass
class CodegraphConfig:
    """Resolved configuration for a codegraph project."""

    # Core
    project_root: Path = field(default_factory=Path.cwd)
    ignore_dirs: list[str] = field(default_factory=lambda: list(DEFAULT_IGNORE_DIRS))
    ignore_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_IGNORE_PATTERNS))
    max_file_size_kb: int = 500

    # Parsers
    enabled_parsers: list[str] | None = None  # None = all available
    disabled_parsers: list[str] = field(default_factory=list)

    # MCP
    auto_watch: bool = True
    reindex_on_start: bool = True

    # Ruflo
    ruflo_enabled: bool | None = None  # None = auto-detect

    @property
    def codegraph_dir(self) -> Path:
        return self.project_root / CODEGRAPH_DIR

    @property
    def config_path(self) -> Path:
        return self.codegraph_dir / CONFIG_FILE

    @property
    def is_initialized(self) -> bool:
        return self.codegraph_dir.exists()


def _read_toml(path: Path) -> dict:
    """Read a TOML file. Returns empty dict if missing or unreadable."""
    if not path.exists() or tomllib is None:
        return {}
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def load_config(project_root: str | Path | None = None) -> CodegraphConfig:
    """
    Load config with resolution: defaults -> global -> project -> env.
    """
    root = Path(project_root).resolve() if project_root else Path.cwd().resolve()
    config = CodegraphConfig(project_root=root)

    # Global config
    global_data = _read_toml(GLOBAL_DIR / CONFIG_FILE)
    _apply_toml(config, global_data)

    # Project config
    project_data = _read_toml(root / CODEGRAPH_DIR / CONFIG_FILE)
    _apply_toml(config, project_data)

    # Env overrides
    if os.environ.get("CODEGRAPH_DIR"):
        override_dir = Path(os.environ["CODEGRAPH_DIR"]).resolve()
        config.project_root = override_dir.parent if override_dir.name == CODEGRAPH_DIR else override_dir

    if os.environ.get("CODEGRAPH_ROOT"):
        config.project_root = Path(os.environ["CODEGRAPH_ROOT"]).resolve()

    if os.environ.get("CODEGRAPH_RUFLO_ENABLED"):
        config.ruflo_enabled = os.environ["CODEGRAPH_RUFLO_ENABLED"].lower() in ("1", "true", "yes")

    return config


def _apply_toml(config: CodegraphConfig, data: dict) -> None:
    """Apply TOML data to config object."""
    cg = data.get("codegraph", {})
    if "ignore_dirs" in cg:
        config.ignore_dirs = cg["ignore_dirs"]
    if "ignore_patterns" in cg:
        config.ignore_patterns = cg["ignore_patterns"]
    if "max_file_size_kb" in cg:
        config.max_file_size_kb = cg["max_file_size_kb"]

    parsers = data.get("parsers", {})
    if "enabled" in parsers:
        config.enabled_parsers = parsers["enabled"]
    if "disabled" in parsers:
        config.disabled_parsers = parsers["disabled"]

    mcp = data.get("mcp", {})
    if "auto_watch" in mcp:
        config.auto_watch = mcp["auto_watch"]
    if "reindex_on_start" in mcp:
        config.reindex_on_start = mcp["reindex_on_start"]

    ruflo = data.get("ruflo", {})
    if "enabled" in ruflo:
        config.ruflo_enabled = ruflo["enabled"]


def generate_default_config() -> str:
    """Generate a default config.toml content."""
    return """# codegraph configuration
# Docs: https://github.com/altikva/codegraph

[codegraph]
# Directories to skip during indexing (in addition to .gitignore)
ignore_dirs = [
    ".git", ".codegraph", "node_modules", "__pycache__",
    ".venv", "venv", ".terraform", "dist", "build", ".next",
]
# File patterns to skip
ignore_patterns = ["*.min.js", "*.bundle.js", "*.map"]
# Skip files larger than this (KB)
max_file_size_kb = 500

[parsers]
# Uncomment to restrict which parsers are active:
# enabled = ["python", "typescript", "markdown"]
# Uncomment to disable specific parsers:
# disabled = ["terraform"]

[mcp]
# Auto-start file watcher when serving
auto_watch = true
# Re-index before starting MCP server
reindex_on_start = true

[ruflo]
# Ruflo integration (auto-detected if not set)
# enabled = true

[paths]
# Where to look for Claude Code memory and plan files. Default is the
# auto-detected Claude Code location (~/.claude/...). Env vars
# CG_MEMORY_DIR / CG_PLANS_DIR override both config and auto-detect.
#
# memory_dir = "~/.claude/projects/-my-slug/memory"
# plans_dir  = "~/.claude/plans"

[roles]
# Override file-role classification for this project.
# Built-in defaults cover FastAPI, Flask, Django, Express, Nuxt, Next.js,
# Remix, Terraform, and conventional folder names (/handlers/, /services/,
# /models/, /components/, etc).
#
# Use this section if your layout differs — e.g. "src/domain/handlers/":
#
#   "/src/domain/handlers/"   = "handler:application"
#   "/src/adapters/"          = "provider:infra"
#   "/pkg/internal/services/" = "service:application"
#
# Syntax: "<path_fragment>" = "<role>:<layer>"
#   role  — free-form narrow category (shown in architecture_overview)
#   layer — one of: presentation, application, domain, infra, test, doc, other
"""


def init_project(root: Path) -> dict:
    """
    Initialize codegraph in a directory.
    Creates .codegraph/ and config.toml.
    Returns status dict.
    """
    cg_dir = root / CODEGRAPH_DIR
    created = []

    if not cg_dir.exists():
        cg_dir.mkdir(parents=True)
        created.append(str(cg_dir))

    config_path = cg_dir / CONFIG_FILE
    if not config_path.exists():
        config_path.write_text(generate_default_config())
        created.append(str(config_path))

    # Generate auth key
    from .auth import ensure_auth_key, ensure_gitignore_has_auth_key, get_auth_key_path

    key_path = get_auth_key_path(root)
    if not key_path.exists():
        ensure_auth_key(root)
        created.append(str(key_path))

    # Add .codegraph to .gitignore if not already there
    gitignore = root / ".gitignore"
    if gitignore.exists():
        content = gitignore.read_text()
        if ".codegraph" not in content:
            with open(gitignore, "a") as f:
                f.write("\n# codegraph index\n.codegraph/\n")
            created.append(".gitignore (updated)")

    # Ensure auth.key is in .gitignore
    ensure_gitignore_has_auth_key(root)

    return {
        "root": str(root),
        "codegraph_dir": str(cg_dir),
        "created": created,
        "config_path": str(config_path),
    }
