# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Extract a 1-2 line "module docstring" from any supported file,
#              so `architecture_overview` and `domain_map` can summarize
#              each file without having to open it.

from __future__ import annotations

import re
from pathlib import Path

_MAX_CHARS = 240
# Matches a "Description:" marker anywhere in the header (ALTIKVA style)
_HEADER_MARKER = re.compile(r"\bDescription:\s*", re.IGNORECASE)


def extract(path: str | Path, lang: str | None = None) -> str:
    """
    Return a short summary string for the given file (<= 240 chars).
    Returns "" on unreadable / unsupported files.
    """
    p = Path(path)
    suffix = p.suffix.lower()
    try:
        # Only read a small prefix — module docs live in the first ~200 lines
        with open(p, encoding="utf-8", errors="replace") as f:
            head = f.read(8000)
    except OSError:
        return ""

    if not head.strip():
        return ""

    if suffix in (".py",):
        return _trim(_py_doc(head))
    if suffix in (".ts", ".tsx", ".js", ".mjs", ".vue"):
        return _trim(_jsdoc(head))
    if suffix in (".tf", ".tfvars"):
        return _trim(_header_comment(head, prefix="#"))
    if suffix in (".md", ".mdx"):
        return _trim(_md_doc(head))
    if suffix in (".yml", ".yaml"):
        return _trim(_header_comment(head, prefix="#"))
    if suffix in (".json",):
        return _trim(_json_description(head))
    if suffix in (".toml",):
        # Prefer [project].description or [tool.poetry].description
        return _trim(_toml_description(head) or _header_comment(head, prefix="#"))
    if p.name == "Dockerfile":
        return _trim(_header_comment(head, prefix="#"))
    if suffix in (".sh", ".bash"):
        return _trim(_header_comment(head, prefix="#"))

    return ""


def _trim(text: str) -> str:
    """Collapse whitespace, extract a clean header marker if present."""
    if not text:
        return ""
    # If there's a "Description:" marker, start from there
    m = _HEADER_MARKER.search(text)
    if m:
        text = text[m.end() :]
    text = re.sub(r"\s+", " ", text).strip()
    return text[:_MAX_CHARS]


def _py_doc(src: str) -> str:
    """Extract the module-level docstring from Python source."""
    # Skip shebang + encoding + comment headers
    lines = src.splitlines()
    i = 0
    n = len(lines)
    while i < n and (lines[i].startswith("#") or lines[i].strip() == "" or lines[i].startswith("from __future__")):
        i += 1
    if i >= n:
        return _header_comment(src, prefix="#")  # fallback: read banner comments
    first = lines[i].lstrip()
    if first.startswith(('"""', "'''")):
        quote = first[:3]
        # Single-line docstring: """foo"""
        rest = first[3:]
        if quote in rest:
            return rest.split(quote, 1)[0]
        buf = [rest]
        i += 1
        while i < n:
            line = lines[i]
            if quote in line:
                buf.append(line.split(quote, 1)[0])
                break
            buf.append(line)
            i += 1
        return " ".join(buf)
    # No docstring — fallback to comment banner (ALTIKVA style headers)
    return _header_comment(src, prefix="#")


def _jsdoc(src: str) -> str:
    """Extract the first JSDoc block / leading comment from JS/TS/Vue."""
    # Match /** ... */
    m = re.search(r"/\*\*(.*?)\*/", src, re.DOTALL)
    if m:
        body = m.group(1)
        # Strip leading " * " on each line
        cleaned = re.sub(r"^\s*\*\s?", "", body, flags=re.MULTILINE)
        return cleaned.strip()
    # Fallback: leading // comments
    out = []
    for line in src.splitlines():
        s = line.strip()
        if not s:
            if out:
                break
            continue
        if s.startswith("//"):
            out.append(s.lstrip("/ "))
        else:
            break
    return " ".join(out)


def _header_comment(src: str, prefix: str = "#") -> str:
    """Extract a banner comment at the top of the file."""
    out: list[str] = []
    for line in src.splitlines():
        s = line.strip()
        if not s:
            if out:
                break
            continue
        if s.startswith(prefix):
            text = s.lstrip(prefix + " -=*").strip()
            if text and not text.startswith(("#", "-", "=")):
                out.append(text)
        else:
            break
    return " ".join(out)


def _md_doc(src: str) -> str:
    """Markdown: H1 title + first paragraph."""
    lines = src.splitlines()
    title = ""
    body = ""
    for i, line in enumerate(lines):
        if line.startswith("#"):
            title = line.lstrip("# ").strip()
            # Look for the first non-empty paragraph after the title
            for j in range(i + 1, min(i + 20, len(lines))):
                s = lines[j].strip()
                if s and not s.startswith(("#", "-", "*", "|", "```")):
                    body = s
                    break
            break
    if title and body:
        return f"{title} — {body}"
    return title or body


def _toml_description(src: str) -> str:
    """Extract [project].description / [tool.poetry].description from TOML."""
    try:
        import tomllib
    except ImportError:
        return ""
    try:
        data = tomllib.loads(src)
    except Exception:
        return ""
    for path in (("project", "description"), ("tool", "poetry", "description")):
        node = data
        for key in path:
            if not isinstance(node, dict) or key not in node:
                node = None
                break
            node = node[key]
        if isinstance(node, str) and node.strip():
            return node.strip()
    return ""


def _json_description(src: str) -> str:
    """Extract top-level `description` or `name` field from JSON (package.json)."""
    import json as _json

    try:
        data = _json.loads(src)
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    desc = data.get("description")
    name = data.get("name")
    if desc and name:
        return f"{name} — {desc}"
    return desc or name or ""
