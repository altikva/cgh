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

from __future__ import annotations

import os
import sys

# Use tomllib (3.11+) or tomli fallback
import tomllib  # type: ignore
from dataclasses import dataclass, field
from pathlib import Path

CODEGRAPH_DIR = ".codegraph"
CONFIG_FILE = "config.toml"
GLOBAL_DIR = Path.home() / ".codegraph"
CLAUDE_HOME = Path.home() / ".claude"

# A config that still says mode = "secure" (removed in 0.15.0) loads as
# assist. The notice goes to stderr once per process; hook commands
# suppress it entirely because agents parse their output.
LEGACY_SECURE_MODE_NOTICE = (
    'mode = "secure" is no longer supported and is ignored since 0.15.0; '
    "cgh does not block file access any more, use your agent's own "
    "permission rules for that"
)

# Keys an older config.toml may still carry that nothing reads since
# 0.15.0: the cgh-summarize settings for the cloud backends and the
# egress gate it dropped in 0.3.0 (this core refuses older releases).
# They load without error and are named in the same one-per-process
# notice, never rejected.
DEAD_CONFIG_KEYS: tuple[tuple[str, ...], ...] = (
    ("plugin", "summarize", "allow_pii"),
    ("plugin", "summarize", "egress"),
    ("plugin", "summarize", "claude_model"),
    ("plugin", "summarize", "gemini_model"),
)

_legacy_mode_warned = False
_legacy_mode_suppressed = False


def suppress_legacy_mode_warning() -> None:
    """Silence the stderr notice for this process (hooks, and commands
    that show the notice in their own output)."""
    global _legacy_mode_suppressed
    _legacy_mode_suppressed = True


def dead_keys_notice(keys: list[str]) -> str:
    """The notice naming config keys nothing reads, "" when there are none."""
    if not keys:
        return ""
    return f"ignored since 0.15.0, delete them: {', '.join(keys)}"


def config_notices(config: CodegraphConfig) -> list[str]:
    """Deprecation notices for a loaded config, one string each."""
    notices = []
    if config.legacy_secure_mode:
        notices.append(LEGACY_SECURE_MODE_NOTICE)
    if config.dead_keys:
        notices.append(dead_keys_notice(config.dead_keys))
    return notices


def _warn_deprecated_config(config: CodegraphConfig) -> None:
    """One stderr line per process, whatever the config carries."""
    global _legacy_mode_warned
    notices = config_notices(config)
    if not notices or _legacy_mode_warned or _legacy_mode_suppressed:
        return
    _legacy_mode_warned = True
    try:
        print(f"cgh: {'; '.join(notices)}", file=sys.stderr)
    except Exception:
        pass  # a closed stderr must never break config loading


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
    # Sibling directories indexed into this graph (`cgh add-dir`). Read
    # here so Terraform module sources can tell an indexed directory from
    # one outside every index.
    extra_dirs: list[str] = field(default_factory=list)
    # [terraform] module_sources: opt-in map from a remote module source
    # package (git URL, registry address) to a local checkout, so a module
    # call with that source links to the module's variables and outputs.
    # Paths are relative to project_root or absolute; never fetched.
    terraform_module_sources: dict[str, str] = field(default_factory=dict)
    # When the parent owner starts, also start the owner of every
    # initialized subrepo whose owner is down. Children started this way
    # live exactly as long as the parent owner.
    federate_auto_up: bool = True

    # Always "assist". The key is still parsed so old configs load; a
    # legacy "secure" value is ignored and only sets the flag below,
    # which `cgh status` and `cgh doctor` report.
    mode: str = "assist"
    legacy_secure_mode: bool = False
    # Keys found in the TOML that nothing reads (see DEAD_CONFIG_KEYS),
    # as "[section] key" labels for the deprecation notice.
    dead_keys: list[str] = field(default_factory=list)

    # Network fetch (fetch_and_index): off unless set to true. Private
    # and loopback hosts are refused regardless (SSRF), and every fetch
    # is audited.
    allow_fetch: bool = False

    # Plugins (proposal 001). enabled = None means "no allowlist, load
    # everything installed that isn't in disabled". plugin_tables carries
    # each [plugin.<name>] TOML table verbatim for that plugin's PluginAPI.
    plugins_enabled: list[str] | None = None
    plugins_disabled: list[str] = field(default_factory=list)
    plugin_tables: dict[str, dict] = field(default_factory=dict)

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

    if os.environ.get("CGH_PRECISE_CALLS"):
        config.precise_calls = os.environ["CGH_PRECISE_CALLS"].lower() in (
            "1",
            "true",
            "yes",
        )

    _warn_deprecated_config(config)

    return config


def _apply_toml(config: CodegraphConfig, data: dict) -> None:
    """Apply TOML data to config object."""
    _collect_dead_keys(config, data)
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
    if "extra_dirs" in cg:
        config.extra_dirs = [str(d) for d in cg["extra_dirs"]]
    if "federate_auto_up" in cg:
        config.federate_auto_up = bool(cg["federate_auto_up"])
    if "mode" in cg:
        # Only "assist" exists now; a legacy "secure" is flagged, not applied.
        config.legacy_secure_mode = str(cg["mode"]).strip().lower() == "secure"
    if "allow_fetch" in cg:
        config.allow_fetch = bool(cg["allow_fetch"])

    terraform = data.get("terraform", {})
    sources = terraform.get("module_sources") if isinstance(terraform, dict) else None
    if isinstance(sources, dict):
        config.terraform_module_sources = {
            str(k): str(v) for k, v in sources.items() if isinstance(v, str)
        }

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


def _collect_dead_keys(config: CodegraphConfig, data: dict) -> None:
    for path in DEAD_CONFIG_KEYS:
        node: object = data
        for part in path[:-1]:
            node = node.get(part) if isinstance(node, dict) else None
        if isinstance(node, dict) and path[-1] in node:
            label = f"[{'.'.join(path[:-1])}] {path[-1]}"
            if label not in config.dead_keys:
                config.dead_keys.append(label)


def generate_default_config() -> str:
    """Generate a default config.toml content. Every recognized option
    appears here, active ones with their real defaults and optional
    ones commented out with an explanation, so the file doubles as the
    reference a user edits instead of hunting through the docs."""
    return """# codegraph configuration
# Docs: https://github.com/altikva/cgh/blob/main/docs/CONFIGURATION.md
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
# Let fetch_and_index (MCP) and `cgh fetch` reach the network. Off by
# default; private and loopback hosts stay refused and every fetch is
# logged. cgh does not restrict what your agent reads: use the agent's
# own permission rules for that.
# allow_fetch = false
# Directories to force-index even if .gitignore excludes them (e.g. "docs/",
# generated schema dumps, vendored source you still want in the graph).
# Paths are relative to the project root. Use absolute paths for dirs that
# live outside the repo (sibling repos prefer add_directory / extra_dirs).
# include_dirs = ["docs", "internal/specs"]
# Sibling directories indexed into this repo's graph, managed by
# `cgh add-dir add ../frontend` (kept here so it versions with the repo).
# extra_dirs = ["../my-frontend"]
# Sibling git repos an agent may read at any branch without a checkout
# (pattern_search(repo=, ref=), file_at_ref) are declared in the user's
# global ~/.codegraph/config.toml only, as [codegraph] siblings = ["~/code/
# my-api"]: a project config can come with a cloned repo, so it cannot
# grant access to other repos.
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

[terraform]
# Map a remote module source to a local checkout so module calls with that
# source link to the module's variables and outputs. The key is matched as a
# prefix of the source before its //subdir (git:: and .git are ignored); the
# //subdir is joined to the mapped path and ?ref= is ignored, so the checkout
# may sit at another ref than the one pinned. Nothing is ever fetched.
# module_sources = { "git::https://github.com/acme/tf-modules" = "../tf-modules" }

[mcp]
# Auto-start file watcher when serving
auto_watch = true
# Re-index before starting MCP server
reindex_on_start = true

[plugins]
# Installed plugins (pip install "cgh[plugins]") register themselves;
# these lists narrow or bar them without uninstalling anything.
# enabled = ["docs", "codegen"]
# disabled = ["bugreport"]

# Per-plugin settings live in [plugin.<name>] tables (note: singular).
# A project-level table replaces the same plugin's global table whole.

# [plugin.pii]
# cgh-pii (installed by name, not by cgh[plugins]) scans on demand:
# `cgh pii scan` reports secrets. Nothing runs at index time unless
# scan_on_index is on. See the cgh-pii README. All lines below are OFF by
# default; uncomment to change behavior.
# scan_on_index = false  # run the regex scanner on every indexed file
# pii = false            # add the PII patterns (emails, phones, IBANs,
#                        # cards) to the secret ones, here and in cgh pii scan
# disable_keys = ["pii.phone", "pii.card"]  # silence noisy finding keys
#                        # (regex phones/cards false-positive on number-heavy
#                        # extracted text like diagram PDFs)
# ner = false            # with scan_on_index: person-name / location
#                        # detection via presidio
#                        # (needs: pip install "cgh-pii[ner]")
# llm = false            # with scan_on_index: an LLM tier that catches what regex + NER miss
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

# [plugin.vision]
# `cgh vision <file>` runs on demand. Index-time image reading is opt-in.
# scan_on_index = false  # index images and read each one in the background
# profile = "default"    # or fast (single pass), photo (screen photos)
# nodes_model = "qwen2.5vl:3b"
# edges_model = "gemma3:4b"
# ollama_url = "http://127.0.0.1:11434"  # a non-loopback URL sends images off-machine
# openai_base_url = ""   # any OpenAI-compatible vision endpoint instead
# openai_api_key_env = "OPENAI_API_KEY"  # env var holding the key, if any
# timeout_s = 300        # per model call; raise for a slow CPU cold start
# num_ctx = 8192         # Ollama context window; raise for very dense pages
#                        # (a 400 "exceeds context size" means bump this)
# fallback_model = "gemma3:4b"     # second reader on skeletal results
# hint = ""              # steer extraction ("labels are in French"); appended, never replaces the contract
# prescale = true        # 2x upscale of small images before extraction
# prescale_min_px = 1000 # apply when the smaller dimension is under this
# cache_ttl_hours = 24   # reuse a cached result for the same file+params;
#                        # 0 disables the cache. `cgh vision --force` bypasses it
# cache_dir = ""         # where cached results live (default: a temp dir)
# auto_extract = false   # with scan_on_index: when a local backend is reachable, the background
#                        # scanner also writes a structured <file>.json for
#                        # every indexed image and PDF (no manual cgh vision)
# auto_extract_out = ".codegraph/vision"  # where the sidecars land; "beside"
#                        # writes <file>.json next to the source instead, or
#                        # give any directory (repo-relative or absolute)

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
