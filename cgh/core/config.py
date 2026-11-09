# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Configuration system for cgh.
#
# Config resolution order (later wins):
#   1. Defaults (hardcoded)
#   2. Global:  ~/.cgh/config.toml
#   3. Project: .cgh/config.toml
#   4. CLI flags
#
# Config format (TOML):
#
#   [cgh]
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
#   answer_format = "json"    # or "text": grep-like answers, see _text.py
#
#   [tools]
#   expose = "auto"           # or "all" / "core": what tools/list shows,
#   extra = []                # see cgh/server/tool_exposure.py

from __future__ import annotations

import logging
import os
import sys

# Use tomllib (3.11+) or tomli fallback
import tomllib  # type: ignore
from dataclasses import dataclass, field
from pathlib import Path

CGH_DIR = ".cgh"
# The per-repo state dir older versions wrote. A repo indexed before the
# rename still has one; cgh reads it until an init/index/serve renames it to
# CGH_DIR in place (see migrate_state_dir).
LEGACY_CGH_DIR = ".codegraph"
CONFIG_FILE = "config.toml"
GLOBAL_DIR = Path.home() / ".cgh"
CLAUDE_HOME = Path.home() / ".claude"


def state_dir_for(project_root: str | Path) -> Path:
    """The per-repo state dir to read: ``.cgh/`` when it exists, else a legacy
    ``.codegraph/`` when it exists, else ``.cgh/`` (the default for a fresh
    repo). Lets a repo indexed before the rename keep working until an
    init/index/serve migrates it."""
    root = Path(project_root)
    primary = root / CGH_DIR
    if primary.exists():
        return primary
    legacy = root / LEGACY_CGH_DIR
    if legacy.is_dir():
        return legacy
    return primary


def _owner_alive_in(state_dir: Path) -> bool:
    """True when a live owner holds the given state dir (its owner.pid names a
    running process). Keeps migrate_state_dir from renaming a dir with open DB
    handles under it."""
    try:
        pid = int((state_dir / "owner.pid").read_text(encoding="utf-8").split()[0])
    except (OSError, ValueError, IndexError):
        return False
    from cgh.state.pidfile import process_alive

    return process_alive(pid)


def migrate_state_dir(project_root: str | Path) -> bool:
    """Rename a legacy ``.codegraph/`` state dir to ``.cgh/`` in place, once,
    when safe. Called at init/index/serve. No-op when ``.cgh/`` already
    exists, when there is no ``.codegraph/``, or when an owner still runs
    against the legacy dir (renaming it would pull the DB out from under live
    handles). Returns True only when it renamed."""
    root = Path(project_root)
    legacy = root / LEGACY_CGH_DIR
    current = root / CGH_DIR
    if current.exists() or not legacy.is_dir() or _owner_alive_in(legacy):
        return False
    try:
        legacy.rename(current)
    except OSError:
        return False
    return True


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

_log = logging.getLogger(__name__)

# Values [tools] expose accepts. Anything else falls back to "all", so a
# typo never hides a tool. "auto" resolves per client (see resolve_expose).
TOOL_EXPOSE_MODES: tuple[str, ...] = ("auto", "all", "core")

# Values [mcp] answer_format and CGH_ANSWER_FORMAT accept; anything else
# keeps "json".
ANSWER_FORMATS: tuple[str, ...] = ("json", "text")

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


def config_notices(config: CghConfig) -> list[str]:
    """Deprecation notices for a loaded config, one string each."""
    notices = []
    if config.legacy_secure_mode:
        notices.append(LEGACY_SECURE_MODE_NOTICE)
    if config.dead_keys:
        notices.append(dead_keys_notice(config.dead_keys))
    if config.tools_expose_invalid is not None:
        notices.append(
            f"[tools] expose = {config.tools_expose_invalid!r} is not one of "
            f"{', '.join(TOOL_EXPOSE_MODES)}; every tool is listed"
        )
    return notices


def _warn_deprecated_config(config: CghConfig) -> None:
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


def find_cgh_root(start: str | Path) -> Path | None:
    """Walk up from ``start`` to the nearest ancestor that has a .cgh/
    directory, the way git finds its repo root via .git. Returns that
    directory, or None if none is found up to the filesystem root.

    This lets every read command work from a subdirectory of an initialized
    repo: a file deep in the tree still knows it belongs to the cgh root.
    """
    p = Path(start).resolve()
    for d in [p, *p.parents]:
        if (d / CGH_DIR).is_dir() or (d / LEGACY_CGH_DIR).is_dir():
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
    env = os.environ.get("CG_MEMORY_DIR") or os.environ.get("CGH_MEMORY_DIR")
    if env:
        return Path(env).expanduser().resolve()

    cfg = _read_toml(state_dir_for(project_root) / CONFIG_FILE)
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
    env = os.environ.get("CG_PLANS_DIR") or os.environ.get("CGH_PLANS_DIR")
    if env:
        return Path(env).expanduser().resolve()

    cfg = _read_toml(state_dir_for(project_root) / CONFIG_FILE)
    paths = cfg.get("paths") or {}
    configured = paths.get("plans_dir")
    if configured:
        return Path(configured).expanduser().resolve()

    return _claude_plans_dir()


DEFAULT_IGNORE_DIRS = [
    ".git",
    ".cgh",
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
class CghConfig:
    """Resolved configuration for a cgh project."""

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
    # see cgh/analysis/precise_calls.py). Off by default: when False, or
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
    # The answer format agents get by default from pattern_search,
    # find_callers, search_symbols and symbol_lookup: "json", or "text" for
    # the grep-like layout. CGH_ANSWER_FORMAT overrides it; an unknown value
    # keeps json. CLI and hook callers always get JSON.
    answer_format: str = "json"

    # Owner log rotation (applied at owner spawn time)
    log_max_mb: int = 5
    log_backup_count: int = 3

    # Federation: child sub-repos with their own .cgh/ index. The
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

    # Half-life, in days, of a knowledge outcome: a verdict this old weighs
    # half as much as a fresh one when entries and files are scored. Zero
    # turns decay off.
    knowledge_half_life_days: float = 30.0

    # Plugins (proposal 001). enabled = None means "no allowlist, load
    # everything installed that isn't in disabled". plugin_tables carries
    # each [plugin.<name>] TOML table verbatim for that plugin's PluginAPI.
    plugins_enabled: list[str] | None = None
    plugins_disabled: list[str] = field(default_factory=list)
    plugin_tables: dict[str, dict] = field(default_factory=dict)

    # [tools]: which MCP tools tools/list shows. "auto" (the default) lists
    # every tool to a tool-search client (Claude Code, which then defers the
    # non-core tail) and only the core set to every other client; "all" lists
    # every tool; "core" lists the core set plus tools_extra plus every plugin
    # tool. Hidden tools stay callable by name. An unknown expose value loads
    # as "all" and keeps the raw value in tools_expose_invalid for the notice.
    tools_expose: str = "auto"
    tools_extra: list[str] = field(default_factory=list)
    tools_expose_invalid: str | None = None

    @property
    def cgh_dir(self) -> Path:
        return state_dir_for(self.project_root)

    @property
    def config_path(self) -> Path:
        return self.cgh_dir / CONFIG_FILE

    @property
    def is_initialized(self) -> bool:
        return self.cgh_dir.exists()


def _read_toml(path: Path) -> dict:
    """Read a TOML file. Returns empty dict if missing or unreadable."""
    if not path.exists() or tomllib is None:
        return {}
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def load_config(project_root: str | Path | None = None) -> CghConfig:
    """
    Load config with resolution: defaults -> global -> project -> env.
    """
    root = Path(project_root).resolve() if project_root else Path.cwd().resolve()
    config = CghConfig(project_root=root)

    # Global config
    global_data = _read_toml(GLOBAL_DIR / CONFIG_FILE)
    _apply_toml(config, global_data)

    # Project config
    project_data = _read_toml(state_dir_for(root) / CONFIG_FILE)
    _apply_toml(config, project_data)

    # Env overrides
    if os.environ.get("CGH_DIR"):
        override_dir = Path(os.environ["CGH_DIR"]).resolve()
        config.project_root = (
            override_dir.parent if override_dir.name == CGH_DIR else override_dir
        )

    if os.environ.get("CGH_ROOT"):
        config.project_root = Path(os.environ["CGH_ROOT"]).resolve()

    env_format = os.environ.get("CGH_ANSWER_FORMAT", "").strip().lower()
    if env_format in ANSWER_FORMATS:
        config.answer_format = env_format

    if os.environ.get("CGH_PRECISE_CALLS"):
        config.precise_calls = os.environ["CGH_PRECISE_CALLS"].lower() in (
            "1",
            "true",
            "yes",
        )

    _warn_deprecated_config(config)

    return config


def _apply_toml(config: CghConfig, data: dict) -> None:
    """Apply TOML data to config object."""
    _collect_dead_keys(config, data)
    cg = data.get("cgh", {})
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
    if "knowledge_half_life_days" in cg:
        try:
            config.knowledge_half_life_days = max(
                0.0, float(cg["knowledge_half_life_days"])
            )
        except (TypeError, ValueError):
            pass  # a malformed value keeps the default rather than break loading

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
    fmt = str(mcp.get("answer_format", "")).strip().lower()
    if fmt in ANSWER_FORMATS:
        config.answer_format = fmt

    _apply_tools_table(config, data.get("tools"))

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


def _apply_tools_table(config: CghConfig, tools: object) -> None:
    """Read [tools] expose / extra. Fails open: an unknown expose value
    means "all", and a malformed extra is dropped, so a bad config can
    only list more tools than intended, never fewer."""
    if not isinstance(tools, dict):
        return
    if "expose" in tools:
        raw = tools["expose"]
        value = raw.strip().lower() if isinstance(raw, str) else ""
        if value in TOOL_EXPOSE_MODES:
            config.tools_expose = value
            config.tools_expose_invalid = None
        else:
            _log.warning(
                "[tools] expose = %r is not one of %s; listing every tool",
                raw,
                ", ".join(TOOL_EXPOSE_MODES),
            )
            config.tools_expose = "all"
            config.tools_expose_invalid = str(raw)
    if "extra" in tools:
        raw_extra = tools["extra"]
        if not isinstance(raw_extra, list):
            _log.warning("[tools] extra must be a list of tool names; ignored")
            config.tools_extra = []
            return
        names: list[str] = []
        for item in raw_extra:
            name = item.strip() if isinstance(item, str) else ""
            if not name:
                _log.warning("[tools] extra entry %r is not a tool name; ignored", item)
                continue
            if name not in names:
                names.append(name)
        config.tools_extra = names


def _collect_dead_keys(config: CghConfig, data: dict) -> None:
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
    return """# cgh configuration
# Docs: https://github.com/altikva/cgh/blob/main/docs/CONFIGURATION.md
# Every option cgh reads is listed here. Active lines are the real
# defaults; commented lines are optional features, uncomment to enable.

[cgh]
# Directories to skip during indexing (in addition to .gitignore)
ignore_dirs = [
    ".git", ".cgh", "node_modules", "__pycache__",
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
# global ~/.cgh/config.toml only, as [cgh] siblings = ["~/code/
# my-api"]: a project config can come with a cloned repo, so it cannot
# grant access to other repos.
# Opt-in precise CALLS resolution for Python (requires `pip install cgh[lsp]`).
# Off by default; uses jedi for goto-definition so cross-file call edges are
# exact instead of name-matched. Env override: CGH_PRECISE_CALLS=1
# precise_calls = false
# Owner log rotation (.cgh/owner.log), checked when an owner spawns.
# Zero log_max_mb disables rotation; zero log_backup_count truncates in place.
# log_max_mb = 5
# log_backup_count = 3
# Federated subrepos (see `cgh federate add`). When the owner of this repo
# starts, it also starts the owner of every initialized subrepo listed here,
# unless federate_auto_up is set to false. Children started this way stop
# on their own shortly after the parent owner exits.
# subrepos = ["./child-repo"]
# federate_auto_up = true
# Knowledge outcomes (knowledge_outcome) fade with age: a verdict this many
# days old weighs half as much as a fresh one. 0 turns the decay off.
# knowledge_half_life_days = 30

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
# Default answer of pattern_search, find_callers, search_symbols and
# symbol_lookup for agents: "json", or "text" for a grep-like layout that
# weighs fewer tokens. A call's own format argument wins.
# answer_format = "json"

[tools]
# Which MCP tools tools/list shows. "auto" (the default) lists every tool
# to a client that carries tool search (Claude Code, which defers the rest
# itself) and only the core set to every other client, so clients that load
# every schema into the context are not flooded. "all" lists every tool;
# "core" lists a small core set plus the names in extra plus every plugin
# tool. A hidden tool stays callable by name, but an agent only calls what
# it sees listed.
# expose = "auto"
# extra = ["find_callees", "impact_of"]

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
# auto_extract_out = ".cgh/vision"  # where the sidecars land; "beside"
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
    Initialize cgh in a directory.
    Creates .cgh/ and config.toml.
    Returns status dict.
    """
    # A repo indexed before the rename keeps its index: move .codegraph/ to
    # .cgh/ in place rather than re-indexing from scratch.
    migrate_state_dir(root)
    cg_dir = root / CGH_DIR
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
    from cgh.state.auth import (
        ensure_auth_key,
        ensure_gitignore_has_auth_key,
        get_auth_key_path,
    )

    key_path = get_auth_key_path(root)
    if not key_path.exists():
        ensure_auth_key(root)
        created.append(str(key_path))

    # Add .cgh to .gitignore if not already there
    gitignore = root / ".gitignore"
    if gitignore.exists():
        # A user/template .gitignore may not be UTF-8 (e.g. a CP1252 em dash
        # in a header comment). We only scan for a substring, so decode
        # leniently instead of crashing init on the encoding.
        content = gitignore.read_text(encoding="utf-8", errors="replace")
        if ".cgh" not in content:
            with open(gitignore, "a", encoding="utf-8") as f:
                f.write("\n# cgh index\n.cgh/\n")
            created.append(".gitignore (updated)")

    # Ensure auth.key is in .gitignore
    ensure_gitignore_has_auth_key(root)

    return {
        "root": str(root),
        "cgh_dir": str(cg_dir),
        "created": created,
        "config_path": str(config_path),
    }
