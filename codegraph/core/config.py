# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
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
#   log_max_mb = 5            # rotate owner.log at this size; 0 disables
#   log_backup_count = 3      # keep this many owner.log.N backups; 0 truncates
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


def find_codegraph_root(start: str | Path) -> Path | None:
    """Walk up from ``start`` to the nearest ancestor that has a .codegraph/
    directory, the way git finds its repo root via .git. Returns that
    directory, or None if none is found up to the filesystem root.

    This lets every read command work from a subdirectory of an initialized
    repo: a file deep in the tree still knows it belongs to the cgh root.
    """
    p = Path(start).resolve()
    for d in [p, *p.parents]:
        if (d / CODEGRAPH_DIR).is_dir():
            return d
    return None


def _claude_project_slug_from_abs(abs_path: str) -> str:
    """Slug Claude Code uses for ~/.claude/projects/<slug>/.

    Every path separator becomes a dash. On POSIX ``/Users/joy/x`` becomes
    ``-Users-joy-x`` (the leading slash gives the leading dash). On Windows
    ``C:\\Users\\x`` becomes ``C--Users-x``: the drive colon and each
    backslash both turn into a dash. Verified against real Claude Code
    project directories on both platforms.
    """
    slug = abs_path
    for sep in (":", "\\", "/"):
        slug = slug.replace(sep, "-")
    return slug


def _claude_memory_dir_for(project_root: str | Path) -> Path:
    """
    Claude Code stores per-project memory at
    ~/.claude/projects/<slug>/memory/.
    """
    slug = _claude_project_slug_from_abs(str(Path(project_root).resolve()))
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
    ignore_patterns: list[str] = field(
        default_factory=lambda: list(DEFAULT_IGNORE_PATTERNS)
    )
    max_file_size_kb: int = 500
    # Dirs to force-index even if gitignored (relative to project_root or absolute).
    include_dirs: list[str] = field(default_factory=list)
    # Opt-in precise CALLS resolution for Python via jedi (proof of concept,
    # see codegraph/analysis/precise_calls.py). Off by default: when False, or
    # when the optional `jedi` extra is not installed, the indexer keeps using
    # the name-matched resolver and behavior is unchanged. Enable with this
    # flag in config.toml or the CGH_PRECISE_CALLS env var.
    precise_calls: bool = False

    # Parsers
    enabled_parsers: list[str] | None = None  # None = all available
    disabled_parsers: list[str] = field(default_factory=list)

    # MCP
    auto_watch: bool = True
    reindex_on_start: bool = True

    # Owner log rotation (applied at owner spawn time)
    log_max_mb: int = 5
    log_backup_count: int = 3

    # Federation: child sub-repos with their own .codegraph/ index. The
    # parent acts as a passe-plat, indexes only files outside any subrepo,
    # then federates queries (read-only) to the children's databases.
    # Paths are relative to project_root or absolute.
    subrepos: list[str] = field(default_factory=list)
    # When the parent owner starts, also start the owner of every
    # initialized subrepo whose owner is down. Children started this way
    # live exactly as long as the parent owner.
    federate_auto_up: bool = True

    # Global posture. "assist" optimizes for token savings and flow;
    # "secure" is assist plus enforcement: egress gates go to allowlist
    # mode, guards fail closed, nothing is turned off. Consumers (egress
    # gate, guard) derive their defaults from this; each stays
    # individually overridable in its own section.
    mode: str = "assist"  # "assist" | "secure"

    # Network fetch (fetch_and_index). Always off in secure mode unless
    # this is set; assist mode allows it. Private/loopback hosts are
    # refused regardless (SSRF), and every fetch is audited.
    allow_fetch: bool = False

    # Plugins (proposal 001). enabled = None means "no allowlist, load
    # everything installed that isn't in disabled". plugin_tables carries
    # each [plugin.<name>] TOML table verbatim for that plugin's PluginAPI.
    plugins_enabled: list[str] | None = None
    plugins_disabled: list[str] = field(default_factory=list)
    plugin_tables: dict[str, dict] = field(default_factory=dict)

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
        config.project_root = (
            override_dir.parent if override_dir.name == CODEGRAPH_DIR else override_dir
        )

    if os.environ.get("CODEGRAPH_ROOT"):
        config.project_root = Path(os.environ["CODEGRAPH_ROOT"]).resolve()

    if os.environ.get("CODEGRAPH_RUFLO_ENABLED"):
        config.ruflo_enabled = os.environ["CODEGRAPH_RUFLO_ENABLED"].lower() in (
            "1",
            "true",
            "yes",
        )

    if os.environ.get("CGH_PRECISE_CALLS"):
        config.precise_calls = os.environ["CGH_PRECISE_CALLS"].lower() in (
            "1",
            "true",
            "yes",
        )

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
    if "include_dirs" in cg:
        config.include_dirs = list(cg["include_dirs"])
    if "precise_calls" in cg:
        config.precise_calls = bool(cg["precise_calls"])
    if "log_max_mb" in cg:
        config.log_max_mb = int(cg["log_max_mb"])
    if "log_backup_count" in cg:
        config.log_backup_count = int(cg["log_backup_count"])
    if "subrepos" in cg:
        config.subrepos = list(cg["subrepos"])
    if "federate_auto_up" in cg:
        config.federate_auto_up = bool(cg["federate_auto_up"])
    if "mode" in cg:
        value = str(cg["mode"]).strip().lower()
        if value in ("assist", "secure"):
            config.mode = value
    if "allow_fetch" in cg:
        config.allow_fetch = bool(cg["allow_fetch"])

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

    plugins = data.get("plugins", {})
    if "enabled" in plugins:
        config.plugins_enabled = list(plugins["enabled"])
    if "disabled" in plugins:
        config.plugins_disabled = list(plugins["disabled"])

    # [plugin.<name>] tables pass through verbatim; project-level tables
    # replace global ones per plugin name (table-level, not key-merge).
    for name, table in (data.get("plugin") or {}).items():
        if isinstance(table, dict):
            config.plugin_tables[name] = table

    ruflo = data.get("ruflo", {})
    if "enabled" in ruflo:
        config.ruflo_enabled = ruflo["enabled"]


def generate_default_config() -> str:
    """Generate a default config.toml content. Every recognized option
    appears here, active ones with their real defaults and optional
    ones commented out with an explanation, so the file doubles as the
    reference a user edits instead of hunting through the docs."""
    return """# codegraph configuration
# Docs: https://github.com/altikva/codegraph
# Every option cgh reads is listed here. Active lines are the real
# defaults; commented lines are optional features, uncomment to enable.

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
# Guard posture. "assist": scanners flag findings, the guard warns and
# fails open. "secure": everything assist does, plus the guard fails
# closed, blocks reads of flagged files in hooked agents, and mirrors
# barred paths into static deny lists (Claude settings, .bobignore).
# allow_fetch = false   # let fetch_and_index reach the network in secure mode
# mode = "assist"
# Directories to force-index even if .gitignore excludes them (e.g. "docs/",
# generated schema dumps, vendored source you still want in the graph).
# Paths are relative to the project root. Use absolute paths for dirs that
# live outside the repo (sibling repos prefer add_directory / extra_dirs).
# include_dirs = ["docs", "internal/specs"]
# Sibling directories indexed into this repo's graph, managed by
# `cgh add-dir add ../frontend` (kept here so it versions with the repo).
# extra_dirs = ["../my-frontend"]
# Opt-in precise CALLS resolution for Python (requires `pip install cgh[lsp]`).
# Off by default; uses jedi for goto-definition so cross-file call edges are
# exact instead of name-matched. Env override: CGH_PRECISE_CALLS=1
# precise_calls = false
# Owner log rotation (.codegraph/owner.log), checked when an owner spawns.
# Zero log_max_mb disables rotation; zero log_backup_count truncates in place.
# log_max_mb = 5
# log_backup_count = 3
# Federated subrepos (see `cgh federate add`). When the owner of this repo
# starts, it also starts the owner of every initialized subrepo listed here,
# unless federate_auto_up is set to false. Children started this way stop
# on their own shortly after the parent owner exits.
# subrepos = ["./child-repo"]
# federate_auto_up = true

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

[plugins]
# Installed plugins (pip install "cgh[plugins]") register themselves;
# these lists narrow or bar them without uninstalling anything.
# enabled = ["pii", "summarize"]
# disabled = ["classify"]

# Per-plugin settings live in [plugin.<name>] tables (note: singular).
# A project-level table replaces the same plugin's global table whole.

# [plugin.pii]
# Regex PII + secret detection runs inline by default (emails, phones,
# IBANs, cards, keys). See the cgh-pii README. All lines below are OFF by
# default; uncomment to change behavior.
# disable_keys = ["pii.phone", "pii.card"]  # silence noisy finding keys
#                        # (regex phones/cards false-positive on number-heavy
#                        # extracted text like diagram PDFs)
# ner = false            # add person-name / location detection via presidio
#                        # (needs: pip install "cgh-pii[ner]")
# llm = false            # add an LLM tier that catches what regex + NER miss
#                        # (names in odd formats, quasi-identifiers, addresses)
#                        # and, with context, avoids much of the regex noise.
#                        # Runs deferred; emits count-only pii.llm.* findings.
# llm_model = "qwen2.5:3b"        # Ollama model; if this one is not pulled,
#                                 # an installed generative model is auto-picked
# llm_ollama_url = "http://127.0.0.1:11434"
# llm_openai_base_url = ""        # an OpenAI-compatible endpoint instead of Ollama
# llm_openai_model = ""
# llm_openai_api_key_env = "OPENAI_API_KEY"
# pii_llm_allow_remote = false    # a NON-loopback LLM endpoint sees file
#                                 # content (egress): opt-in required, and every
#                                 # probe, allowed or denied, is audited

# [plugin.classify]
# threshold = 0.7        # predict confidential above this probability
# uncertain_low = 0.35   # review window lower bound
# uncertain_high = 0.65  # review window upper bound

# [plugin.summarize]
# backend = "auto"       # or cli:claude, cli:gemini, cli:codex, cli:bob,
#                        # ollama, openai, structural
# min_kb = 4             # skip files smaller than this
# allow_pii = false      # let files with PII findings reach cloud backends
# language = "en"        # summary language
# claude_model = "haiku"
# gemini_model = "gemini-2.5-flash"
# ollama_model = "qwen2.5:1.5b"   # if this one is not pulled, an installed
#                                 # generative model is auto-picked; if none
#                                 # is installed the tier degrades (no summary)
# ollama_url = "http://127.0.0.1:11434"
# openai_base_url = ""   # any OpenAI-compatible endpoint, e.g. vLLM
# openai_model = ""
# openai_api_key_env = "OPENAI_API_KEY"

# [plugin.vision]
# profile = "default"    # or fast (single pass), photo (screen photos)
# nodes_model = "qwen2.5vl:3b"
# edges_model = "gemma3:4b"
# ollama_url = "http://127.0.0.1:11434"  # loopback only in secure mode
# openai_base_url = ""   # any OpenAI-compatible vision endpoint instead
# openai_api_key_env = "OPENAI_API_KEY"  # env var holding the key, if any
# timeout_s = 300        # per model call; raise for a slow CPU cold start
# num_ctx = 8192         # Ollama context window; raise for very dense pages
#                        # (a 400 "exceeds context size" means bump this)
# fallback_model = "gemma3:4b"     # second reader on skeletal results
# hint = ""              # steer extraction ("labels are in French"); appended, never replaces the contract
# prescale = true        # 2x upscale of small images before extraction
# prescale_min_px = 1000 # apply when the smaller dimension is under this

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
# Use this section if your layout differs, e.g. "src/domain/handlers/":
#
#   "/src/domain/handlers/"   = "handler:application"
#   "/src/adapters/"          = "provider:infra"
#   "/pkg/internal/services/" = "service:application"
#
# Syntax: "<path_fragment>" = "<role>:<layer>"
#   role, free-form narrow category (shown in architecture_overview)
#   layer, one of: presentation, application, domain, infra, test, doc, other
"""


def resolve_include_dirs(project_root: str | Path) -> list[Path]:
    """Return the config's include_dirs as absolute, existing directories."""
    cfg = load_config(project_root)
    root = Path(project_root).resolve()
    out: list[Path] = []
    for entry in cfg.include_dirs:
        p = Path(entry).expanduser()
        if not p.is_absolute():
            p = root / p
        p = p.resolve()
        if p.exists() and p.is_dir():
            out.append(p)
    return out


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

    # Restrict the index dir to the owner: auth.key lives here and is the
    # whole loopback-auth boundary. No-op on filesystems without POSIX modes.
    try:
        cg_dir.chmod(0o700)
    except OSError:
        pass

    config_path = cg_dir / CONFIG_FILE
    if not config_path.exists():
        config_path.write_text(generate_default_config(), encoding="utf-8")
        created.append(str(config_path))

    # Generate auth key
    from codegraph.state.auth import (
        ensure_auth_key,
        ensure_gitignore_has_auth_key,
        get_auth_key_path,
    )

    key_path = get_auth_key_path(root)
    if not key_path.exists():
        ensure_auth_key(root)
        created.append(str(key_path))

    # Add .codegraph to .gitignore if not already there
    gitignore = root / ".gitignore"
    if gitignore.exists():
        # A user/template .gitignore may not be UTF-8 (e.g. a CP1252 em dash
        # in a header comment). We only scan for a substring, so decode
        # leniently instead of crashing init on the encoding.
        content = gitignore.read_text(encoding="utf-8", errors="replace")
        if ".codegraph" not in content:
            with open(gitignore, "a", encoding="utf-8") as f:
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
