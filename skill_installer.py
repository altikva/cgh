# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Ship codegraph skills and deploy them per AI tool.
#
# Source: codegraph/skills/<name>/SKILL.md (Claude-format with YAML frontmatter)
#
# Deploy targets:
#   - Claude Code:  <project>/.claude/skills/<name>/SKILL.md        (verbatim)
#   - Cursor:       <project>/.cursor/rules/<name>.mdc              (MDC format)
#   - Codex CLI:    <project>/AGENTS.md                             (appended section)
#   - Gemini CLI:   <project>/GEMINI.md                             (appended section)
#
# Single-file agents (Codex/Gemini) get a delimited "codegraph skills" block
# so re-installs can update the block in place without clobbering user content.

from __future__ import annotations

import re
import shutil
from pathlib import Path

_BLOCK_START = "<!-- codegraph-skills:start -->"
_BLOCK_END = "<!-- codegraph-skills:end -->"

_USAGE_BLOCK_START = "<!-- codegraph-usage:start -->"
_USAGE_BLOCK_END = "<!-- codegraph-usage:end -->"

# Canonical "when to use codegraph" guidance — injected into the agent's
# root rules file (CLAUDE.md / AGENTS.md / GEMINI.md) when the user opts in.
_USAGE_BODY = """## codegraph — use MCP tools before Read/Grep

This project is indexed by **codegraph** — a local code graph +
Claude Code memory + plans + persistent knowledge, all exposed via
MCP. Always prefer codegraph tools over reading files directly.

**Workflow matrix — when to call what**

- **Task kickoff / new feature** (*"how does X work"*, *"where to add Y"*):
  1. `mcp__codegraph__context_for_task(task, session_id=<id>)`
     — merges graph + memory + plans + knowledge in one call
  2. `mcp__codegraph__architecture_overview()` or `domain_map(keyword)`
  3. `mcp__codegraph__endpoints(path_pattern)` for API questions
- **Symbol lookup** (*"where is X defined"*, *"what calls Y"*):
  1. `symbol_lookup` / `find_callers` / `find_callees`
  2. `search_symbols` for fuzzy, `fts_search` for docstrings
- **Text/regex pattern search** (*"find every occurrence of X"*):
  1. `pattern_search(pattern, glob?, max_results?)` — INSTEAD of Grep.
     Returns structured {file, line, text}. Then Read only those lines.
- **Known-preference territory** (commit style, naming, workflow):
  1. `memory_search(query, kind="feedback")` BEFORE asking the user
- **User hints at a past plan** (*"the refactor we planned"*):
  1. `plan_search(query)`
- **Problem that might have been solved before**:
  1. `knowledge_search(query)` — persisted learnings across sessions
  2. `knowledge_terms()` for the glossary of captured topics
- **You learn something worth remembering** (pattern / decision /
  gotcha / style / glossary term):
  1. `knowledge_record(title, body, kind, tags, file_refs?)`
- **Before the session gets compacted / summarized**:
  1. `compact_session(session_id, title, digest, tags?)`
- **After `git pull` / `checkout` / `rebase`**:
  1. `scan_status` → `incremental_reindex` if stale
- **Including a sibling repo**: `add_directory(path)` (hot, no restart)

Only use `Read` on the exact line ranges returned by a codegraph tool.
Never `ls`/`find`/`tree` for structure — `architecture_overview` has it.
Never re-derive a fact that could be looked up via `memory_search` or
`knowledge_search`.
"""


# ---------------------------------------------------------------------------
# Source discovery
# ---------------------------------------------------------------------------


def _skills_source_dir() -> Path:
    """Directory inside the installed codegraph package that holds skills."""
    return Path(__file__).parent / "skills"


def list_bundled_skills() -> list[str]:
    """Return names of all skills shipped with codegraph."""
    src = _skills_source_dir()
    if not src.exists():
        return []
    return sorted([d.name for d in src.iterdir() if d.is_dir() and (d / "SKILL.md").exists()])


def _parse_skill(path: Path) -> tuple[dict, str]:
    """
    Parse a Claude-format SKILL.md. Returns (frontmatter_dict, body_markdown).
    Frontmatter is a simple YAML-ish dict (name, description).
    """
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    if not match:
        return {}, text
    raw_fm, body = match.groups()
    fm: dict = {}
    for line in raw_fm.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    return fm, body.lstrip()


def _iter_skills() -> list[tuple[str, dict, str, Path]]:
    """Yield (name, frontmatter, body, skill_dir) for every bundled skill."""
    src = _skills_source_dir()
    if not src.exists():
        return []
    out: list[tuple[str, dict, str, Path]] = []
    for skill_dir in sorted(src.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            continue
        fm, body = _parse_skill(skill_file)
        out.append((skill_dir.name, fm, body, skill_dir))
    return out


# ---------------------------------------------------------------------------
# Per-tool installers
# ---------------------------------------------------------------------------


def install_claude(project_root: str | Path) -> list[str]:
    """Copy skills verbatim to <project>/.claude/skills/<name>/."""
    project_root = Path(project_root)
    dest_root = project_root / ".claude" / "skills"
    dest_root.mkdir(parents=True, exist_ok=True)

    installed: list[str] = []
    for name, _fm, _body, skill_dir in _iter_skills():
        target_dir = dest_root / name
        target_dir.mkdir(parents=True, exist_ok=True)
        for f in skill_dir.rglob("*"):
            if f.is_file():
                rel = f.relative_to(skill_dir)
                dest = target_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest)
        installed.append(name)
    return installed


def install_cursor(project_root: Path) -> list[str]:
    """
    Emit Cursor MDC rules to <project>/.cursor/rules/<name>.mdc.
    Cursor uses MDC format: YAML frontmatter + markdown body.
    """
    rules_dir = project_root / ".cursor" / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)

    installed: list[str] = []
    for name, fm, body, _skill_dir in _iter_skills():
        mdc = _format_mdc(fm, body)
        (rules_dir / f"{name}.mdc").write_text(mdc, encoding="utf-8")
        installed.append(name)
    return installed


def install_codex(project_root: Path) -> list[str]:
    """Append/update a codegraph-skills block in <project>/AGENTS.md."""
    return _install_agents_md(project_root / "AGENTS.md")


def install_gemini(project_root: Path) -> list[str]:
    """Append/update a codegraph-skills block in <project>/GEMINI.md."""
    return _install_agents_md(project_root / "GEMINI.md")


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


def _format_mdc(fm: dict, body: str) -> str:
    """Format a skill as a Cursor MDC rule."""
    description = fm.get("description", "").strip()
    name = fm.get("name", "skill")
    lines = [
        "---",
        f"description: {description}",
        "alwaysApply: false",
        f"# codegraph-skill: {name}",
        "---",
        "",
        body.rstrip(),
        "",
    ]
    return "\n".join(lines)


def _format_agents_md_section() -> tuple[str, list[str]]:
    """Build the markdown block for AGENTS.md / GEMINI.md."""
    parts = [
        _BLOCK_START,
        "",
        "## codegraph skills",
        "",
        "These guidelines come from codegraph (auto-maintained — edit via `cgh setup`).",
        "",
    ]
    names: list[str] = []
    for name, fm, body, _skill_dir in _iter_skills():
        description = fm.get("description", "")
        parts.append(f"### {name}")
        parts.append("")
        if description:
            parts.append(f"_{description}_")
            parts.append("")
        parts.append(body.rstrip())
        parts.append("")
        names.append(name)
    parts.append(_BLOCK_END)
    return "\n".join(parts) + "\n", names


def _install_agents_md(target: Path) -> list[str]:
    """Write or update the codegraph-skills block in AGENTS.md / GEMINI.md."""
    block, names = _format_agents_md_section()
    if not names:
        return []

    if target.exists():
        existing = target.read_text(encoding="utf-8")
        pattern = re.compile(
            re.escape(_BLOCK_START) + r".*?" + re.escape(_BLOCK_END) + r"\n?",
            re.DOTALL,
        )
        if pattern.search(existing):
            new = pattern.sub(block, existing)
        else:
            sep = "" if existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
            new = existing + sep + block
        target.write_text(new, encoding="utf-8")
    else:
        header = (
            "# AI agent instructions for this repository\n\n"
            "This file is read by Codex CLI / Gemini CLI at the start of every session.\n\n"
        )
        target.write_text(header + block, encoding="utf-8")

    return names


# ---------------------------------------------------------------------------
# Convenience: install for a chosen set of tools
# ---------------------------------------------------------------------------


def install_usage_guidelines(project_root: str | Path, tool: str) -> str | None:
    """
    Inject a "when to use codegraph" block into the agent's root rules file.
    The block is marked with delimiters so repeated installs update in place.

    Targets:
      claude  → ./CLAUDE.md   (or ./.claude/CLAUDE.md if the project uses that)
      codex   → ./AGENTS.md
      gemini  → ./GEMINI.md
      cursor  → ./.cursor/rules/codegraph-usage.mdc

    Returns the path written (as str) or None if skipped.
    """
    project_root = Path(project_root)
    if tool == "cursor":
        target = project_root / ".cursor" / "rules" / "codegraph-usage.mdc"
        target.parent.mkdir(parents=True, exist_ok=True)
        mdc = (
            "---\n"
            "description: When and how to use the codegraph MCP tools for this repo.\n"
            "alwaysApply: true\n"
            "---\n\n" + _USAGE_BODY
        )
        target.write_text(mdc, encoding="utf-8")
        return str(target)

    # Pick the root file per tool
    if tool == "claude":
        target = project_root / "CLAUDE.md"
    elif tool == "codex":
        target = project_root / "AGENTS.md"
    elif tool == "gemini":
        target = project_root / "GEMINI.md"
    else:
        return None

    block = _USAGE_BLOCK_START + "\n\n" + _USAGE_BODY.rstrip() + "\n\n" + _USAGE_BLOCK_END + "\n"

    if target.exists():
        existing = target.read_text(encoding="utf-8")
        pattern = re.compile(
            re.escape(_USAGE_BLOCK_START) + r".*?" + re.escape(_USAGE_BLOCK_END) + r"\n?",
            re.DOTALL,
        )
        if pattern.search(existing):
            new = pattern.sub(block, existing)
        else:
            sep = "\n" if existing.endswith("\n") else "\n\n"
            new = existing + sep + "\n" + block
        target.write_text(new, encoding="utf-8")
    else:
        header = "# Agent instructions for this repository\n\n"
        target.write_text(header + block, encoding="utf-8")

    return str(target)


def install_for_tools(project_root: Path, tools: list[str]) -> dict[str, list[str]]:
    """
    Install skills for each tool in `tools` (subset of: claude, cursor, codex, gemini).
    Returns {tool: [skill_names_installed]}.
    """
    result: dict[str, list[str]] = {}
    if "claude" in tools:
        result["claude"] = install_claude(project_root)
    if "cursor" in tools:
        result["cursor"] = install_cursor(project_root)
    if "codex" in tools:
        result["codex"] = install_codex(project_root)
    if "gemini" in tools:
        result["gemini"] = install_gemini(project_root)
    return result
