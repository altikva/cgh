# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Rationale comments: the comments that say why code is the way
#              it is. At index time, extract() reads a source file's comments,
#              keeps those opening with one of the TAGS below followed by a
#              colon, or citing an ADR by number or by the path of its
#              Markdown file, and attaches each to the
#              symbol it documents: the function or class right below it,
#              else the innermost one enclosing it, else the module. Stored
#              in the rationale side table, one row per comment.
#              At query time, query() reads them back for a symbol or a file
#              and resolve_adr() links an ADR reference to the indexed
#              Markdown file it names.

from __future__ import annotations

import io
import re
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The tags a rationale comment opens with, upper case as written in code.
# TODO / FIXME are left out on purpose: they track work, not reasons.
TAGS: tuple[str, ...] = (
    "WHY",
    "NOTE",
    "HACK",
    "SAFETY",
    "INVARIANT",
    "IMPORTANT",
    "WARNING",
    "DECISION",
)
# A comment with no tag that cites an ADR is stored under this tag.
ADR_TAG = "ADR"

# Stored text is capped here; the tools trim further for their payloads.
MAX_STORED_TEXT = 500

_TAG_ALT = "|".join(TAGS)
# "WHY: ...", "NOTE(joy): ...", "HACK - ..." is not a tag (a colon is needed).
_TAG_RE = re.compile(rf"^({_TAG_ALT})(?:\([^)]{{0,40}}\))?\s*:\s*(.*)$")
_ADR_ID_RE = re.compile(r"\bADR[-_ ]?(\d{1,5})\b")
_MD_PATH_RE = re.compile(r"[\w./-]+\.mdx?\b")
# A cheap first pass: a file with none of these has no rationale comment, so
# the comment scan is skipped for most files.
_QUICK_RE = re.compile(
    rf"(?:{_TAG_ALT})(?:\([^)]*\))?\s*:"
    r"|ADR[-_ ]?\d|(?i:adr|decision)[\w./-]*\.mdx?\b"
)


@dataclass(frozen=True, slots=True)
class _Style:
    """How one language writes comments and strings."""

    line: tuple[str, ...]
    blocks: tuple[tuple[str, str], ...] = ()
    # String delimiters skipped by the scanner; the ones in ``multiline``
    # may span lines, the others end at the end of the line.
    quotes: str = "\"'"
    multiline: str = ""


_C_BLOCK = (("/*", "*/"),)
_STYLES: dict[str, _Style] = {
    ".js": _Style(("//",), _C_BLOCK, "\"'`", "`"),
    ".jsx": _Style(("//",), _C_BLOCK, "\"'`", "`"),
    ".mjs": _Style(("//",), _C_BLOCK, "\"'`", "`"),
    ".cjs": _Style(("//",), _C_BLOCK, "\"'`", "`"),
    ".ts": _Style(("//",), _C_BLOCK, "\"'`", "`"),
    ".tsx": _Style(("//",), _C_BLOCK, "\"'`", "`"),
    ".vue": _Style(("//",), (*_C_BLOCK, ("<!--", "-->")), "\"'`", "`"),
    ".go": _Style(("//",), _C_BLOCK, "\"'`", "`"),
    # A Rust lifetime ('a) would read as an open string: only " delimits.
    ".rs": _Style(("//",), _C_BLOCK, '"'),
    ".java": _Style(("//",), _C_BLOCK),
    ".cs": _Style(("//",), _C_BLOCK),
    ".rb": _Style(("#",)),
    ".tf": _Style(("#", "//"), _C_BLOCK, '"'),
    ".sql": _Style(("--",), _C_BLOCK),
}
_PYTHON = {".py", ".pyw"}
_PY_FALLBACK = _Style(("#",))


def supported(path: str | Path) -> bool:
    """Whether rationale comments are extracted from files like ``path``."""
    suffix = Path(path).suffix.lower()
    return suffix in _PYTHON or suffix in _STYLES


# ---------------------------------------------------------------------------
# Comment scanning
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _CommentLine:
    line: int
    text: str
    block: int  # comment lines sharing a block id read as one paragraph run


def _python_comments(src: str) -> list[_CommentLine]:
    """Python comments through the tokenizer, so a # inside a string never
    counts. Falls back to the generic scanner on source it cannot tokenize."""
    out: list[_CommentLine] = []
    block = 0
    prev_line = -2
    prev_alone = False
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type != tokenize.COMMENT:
                continue
            line = tok.start[0]
            alone = not tok.line[: tok.start[1]].strip()
            if not (alone and prev_alone and line == prev_line + 1):
                block += 1
            out.append(_CommentLine(line, tok.string[1:].strip(), block))
            prev_line, prev_alone = line, alone
    except (tokenize.TokenError, SyntaxError):
        return _scan(src, _PY_FALLBACK)
    return out


def _clean_block_line(text: str) -> str:
    """One line of a /* */ block without its decoration (leading *)."""
    text = text.strip()
    while text.startswith("*"):
        text = text[1:]
    return text.strip()


def _scan(src: str, style: _Style) -> list[_CommentLine]:
    """Comments of a C-like or hash-commented source, skipping strings. A
    regex jumps from one comment opener, quote or newline to the next, so
    plain code is never walked a character at a time."""
    out: list[_CommentLine] = []
    special, string_end = _scanner(style)
    n = len(src)
    i = 0
    line = 1
    block = 0
    code_on_line = False
    prev_line = -2
    prev_alone = False
    while i < n:
        m = special.search(src, i)
        if m is None:
            break
        if src[i : m.start()].strip():
            code_on_line = True
        tok = m.group()
        i = m.end()
        if tok == "\n":
            line += 1
            code_on_line = False
        elif tok in style.line:
            end = src.find("\n", i)
            end = n if end == -1 else end
            # Doc-comment flavours: ///, //!, ##
            text = src[i:end].lstrip("/!#").strip()
            alone = not code_on_line
            if not (alone and prev_alone and line == prev_line + 1):
                block += 1
            out.append(_CommentLine(line, text, block))
            prev_line, prev_alone = line, alone
            i = end
        elif tok in string_end:
            # Skip the string; one that may not span lines stops at the end
            # of its line, left for the newline branch above.
            closer = string_end[tok]
            while True:
                s = closer.search(src, i)
                if s is None:
                    i = n
                    break
                part = s.group()
                if part == "\n":
                    if tok not in style.multiline:
                        i = s.start()
                        break
                    line += 1
                elif part.startswith("\\"):
                    line += part.count("\n")
                elif part == tok:
                    i = s.end()
                    break
                i = s.end()
            code_on_line = True
        else:
            stop = next(b[1] for b in style.blocks if b[0] == tok)
            end = src.find(stop, i)
            end = n if end == -1 else end
            body = src[i:end]
            block += 1
            for offset, raw in enumerate(body.split("\n")):
                out.append(_CommentLine(line + offset, _clean_block_line(raw), block))
            line += body.count("\n")
            prev_line, prev_alone = -2, False
            code_on_line = True
            i = min(n, end + len(stop))
    return out


def _scanner(style: _Style) -> tuple[re.Pattern, dict[str, re.Pattern]]:
    """The opener regex of ``style`` and, per quote, the regex finding what
    can end or continue its string (an escape, the quote, a newline)."""
    cached = _SCANNERS.get(style)
    if cached is None:
        tokens = {*style.line, *(b[0] for b in style.blocks), *style.quotes, "\n"}
        alt = "|".join(re.escape(t) for t in sorted(tokens, key=len, reverse=True))
        ends = {
            q: re.compile(r"\\.|" + re.escape(q) + r"|\n", re.DOTALL)
            for q in style.quotes
        }
        cached = (re.compile(alt), ends)
        _SCANNERS[style] = cached
    return cached


_SCANNERS: dict[_Style, tuple[re.Pattern, dict[str, re.Pattern]]] = {}


def comments(src: str, suffix: str) -> list[_CommentLine]:
    """Every comment line of ``src``, for a file with extension ``suffix``."""
    suffix = suffix.lower()
    if suffix in _PYTHON:
        return _python_comments(src)
    style = _STYLES.get(suffix)
    return _scan(src, style) if style else []


# ---------------------------------------------------------------------------
# Rationale entries
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Entry:
    """One rationale comment, before it is attached to a symbol."""

    line: int
    end_line: int
    block_end: int
    tag: str
    text: str
    adr: str = ""


def adr_ref(text: str) -> str:
    """The first ADR the text cites: "ADR-0007" or a Markdown path whose
    path names an ADR directory or file. "" when it cites none."""
    m = _ADR_ID_RE.search(text)
    if m:
        return f"ADR-{m.group(1)}"
    for path in _MD_PATH_RE.findall(text):
        low = path.lower()
        if "adr" in low or "decision" in low:
            return path
    return ""


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()[:MAX_STORED_TEXT]


def entries(lines: list[_CommentLine]) -> list[Entry]:
    """Group comment lines into rationale entries. A tagged line opens an
    entry, the untagged lines after it in the same block continue it, and a
    blank line or a new tag ends it. An untagged line citing an ADR is an
    entry of its own, under the ADR tag."""
    out: list[Entry] = []
    block_ends: dict[int, int] = {}
    for c in lines:
        block_ends[c.block] = max(block_ends.get(c.block, 0), c.line)
    current: Entry | None = None
    current_block = -1
    parts: list[str] = []

    def close() -> None:
        nonlocal current, parts
        if current is not None:
            current.text = _collapse(" ".join(parts))
            if not current.adr:
                current.adr = adr_ref(current.text)
            out.append(current)
        current, parts = None, []

    for c in lines:
        if c.block != current_block:
            close()
            current_block = c.block
        m = _TAG_RE.match(c.text)
        if m:
            close()
            current = Entry(c.line, c.line, block_ends[c.block], m.group(1), "")
            parts = [m.group(2)]
            continue
        if not c.text:
            close()
            continue
        if current is not None:
            parts.append(c.text)
            current.end_line = c.line
            continue
        ref = adr_ref(c.text)
        if ref:
            out.append(
                Entry(
                    c.line, c.line, block_ends[c.block], ADR_TAG, _collapse(c.text), ref
                )
            )
    close()
    return [e for e in out if e.text or e.adr]


# ---------------------------------------------------------------------------
# Attachment to symbols
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Symbol:
    start: int
    end: int
    id: str
    name: str  # qualified within the file: "f", "Cls", "Cls.method"
    kind: str


def _symbols(idx: Any) -> list[_Symbol]:
    out: list[_Symbol] = []
    for fn in getattr(idx, "functions", ()) or ():
        qual = fn.id.split("::", 1)[1] if "::" in fn.id else fn.name
        kind = "method" if fn.class_name else "function"
        out.append(_Symbol(fn.start_line, fn.end_line, fn.id, qual, kind))
    for cls in getattr(idx, "classes", ()) or ():
        qual = cls.id.split("::", 1)[1] if "::" in cls.id else cls.name
        out.append(_Symbol(cls.start_line, cls.end_line, cls.id, qual, "class"))
    return out


def _next_code_line(src_lines: list[str], after: int) -> int:
    """The first line after ``after`` that is not a decorator or an
    attribute: where the definition a leading comment documents starts. A
    blank line stops there, so a comment set apart from the definition
    below it is not read as documenting it."""
    j = after + 1
    while j <= len(src_lines):
        s = src_lines[j - 1].strip()
        if not s.startswith(("@", "#[", "[")) or not s:
            return j
        j += 1
    return j


def attach(
    found: list[Entry], symbols: list[_Symbol], src_lines: list[str]
) -> list[tuple[Any, ...]]:
    """Rationale rows (line, tag, text, symbol_id, symbol, symbol_kind,
    symbol_line, adr) for ``found``: a comment right above a definition
    documents it, any other the innermost symbol enclosing it, a top-level
    one the module (symbol_id and symbol "", kind "module")."""
    rows: list[tuple[Any, ...]] = []
    for e in found:
        target = None
        nxt = _next_code_line(src_lines, e.block_end)
        leading = [s for s in symbols if e.block_end < s.start <= nxt]
        if leading:
            target = min(leading, key=lambda s: (s.start, s.end - s.start))
        else:
            around = [s for s in symbols if s.start <= e.line <= s.end]
            if around:
                target = min(around, key=lambda s: s.end - s.start)
        if target is None:
            rows.append((e.line, e.tag, e.text, "", "", "module", 0, e.adr))
        else:
            rows.append(
                (
                    e.line,
                    e.tag,
                    e.text,
                    target.id,
                    target.name,
                    target.kind,
                    target.start,
                    e.adr,
                )
            )
    return rows


def extract(path: str | Path, idx: Any, src: str | None = None) -> list[tuple]:
    """The rationale rows of one parsed file (see attach for the row shape),
    [] for a language this does not read or a file without any."""
    p = Path(path)
    if not supported(p):
        return []
    if src is None:
        try:
            src = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
    if not _QUICK_RE.search(src):
        return []
    found = entries(comments(src, p.suffix))
    if not found:
        return []
    return attach(found, _symbols(idx), src.splitlines())


# ---------------------------------------------------------------------------
# Query side
# ---------------------------------------------------------------------------

# Payload caps: rationale rides along in symbol_lookup and context_for_task,
# so only a few short items per symbol go there. The rationale tool returns
# more and longer.
RATIONALE_PER_SYMBOL = 3
RATIONALE_TEXT_CAP = 160
CONTEXT_PER_NODE = 2
CONTEXT_TOTAL = 10
TOOL_TEXT_CAP = 300
TOOL_LIMIT_MAX = 200


def _adr_candidates(conn) -> list[str]:
    """Indexed Markdown files whose path reads like an ADR."""
    files = conn.find_nodes("File", where={"lang": "markdown"}, return_fields=["path"])
    out = []
    for row in files:
        path = row.get("path") or ""
        low = path.lower()
        if "adr" in low or "decision" in low:
            out.append(path)
    return sorted(out)


def _strip_dot_slash(path: str) -> str:
    path = path.replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path.lstrip("/")


def resolve_adr(ref: str, candidates: list[str]) -> str:
    """The indexed Markdown file ``ref`` names, "" when none matches.
    "ADR-7" matches a file named 7-*.md, 0007-*.md or adr-0007*.md under an
    ADR directory; a path matches the file it ends."""
    if not ref:
        return ""
    m = re.fullmatch(r"ADR-(\d+)", ref)
    if m:
        num = int(m.group(1))
        name_re = re.compile(rf"(?i)^(?:adr[-_ ]?)?0*{num}(?!\d)")
        for path in candidates:
            if name_re.match(Path(path).name):
                return path
        return ""
    want = _strip_dot_slash(ref)
    for path in candidates:
        norm = path.replace("\\", "/")
        if norm == want or norm.endswith("/" + want):
            return path
    return ""


def link_adrs(conn, rows: list[dict]) -> list[dict]:
    """Set ``adr_file`` on each row whose ADR resolves to an indexed
    Markdown file. Returns ``rows``."""
    refs = {r["adr"] for r in rows if r.get("adr")}
    if refs:
        candidates = _adr_candidates(conn)
        linked = {ref: resolve_adr(ref, candidates) for ref in refs}
        for r in rows:
            if linked.get(r.get("adr") or ""):
                r["adr_file"] = linked[r["adr"]]
    return rows


def item(row: dict, text_cap: int, short=None, with_symbol: bool = True) -> dict:
    """One rationale row as a payload item, its text cut at ``text_cap``
    characters. ``with_symbol`` adds where it is and what it documents
    (left out where the item already sits under its symbol)."""
    text = row.get("text") or ""
    if len(text) > text_cap:
        text = text[: text_cap - 3].rstrip() + "..."
    out: dict[str, Any] = {}
    if with_symbol:
        fp = row["file_path"]
        out["file"] = short(fp) if short else fp
    out.update({"line": row["line"], "tag": row["tag"], "text": text})
    if with_symbol:
        out["symbol"] = row.get("symbol") or ""
        out["symbol_kind"] = row.get("symbol_kind") or "module"
    if row.get("adr"):
        out["adr"] = row["adr"]
        if row.get("adr_file"):
            out["adr_file"] = short(row["adr_file"]) if short else row["adr_file"]
    return out


def _looks_like_path(target: str) -> bool:
    if "/" in target or "\\" in target:
        return True
    return bool(Path(target).suffix) and supported(target)


def query(conn, target: str = "", tag: str = "", limit: int = 50) -> list[dict]:
    """Rationale rows for a symbol name (bare, or Cls.method; a class also
    returns its members'), a file path (absolute or a path suffix), or every
    row when ``target`` is empty; optionally one ``tag`` only. Rows are
    dicts over graph_model.RATIONALE_COLUMNS plus ``adr_file`` when the ADR
    resolved, ordered by file and line, at most ``limit``."""
    tag = tag.strip().upper()
    target = target.strip()
    if target and _looks_like_path(target):
        rows = conn.rationale_rows(file_suffix=target, tag=tag, limit=limit)
    else:
        rows = conn.rationale_rows(symbol=target, tag=tag, limit=limit)
    return link_adrs(conn, rows)


def for_symbol_ids(conn, ids: list[str]) -> dict[str, list[dict]]:
    """{symbol id: rationale rows}, ordered by line, ADRs linked."""
    if not ids:
        return {}
    out: dict[str, list[dict]] = {}
    for row in link_adrs(conn, conn.rationale_rows(symbol_ids=ids)):
        out.setdefault(row["symbol_id"], []).append(row)
    return out


def for_definitions(conn, defs: list[tuple[str, int]]) -> dict[tuple[str, int], list]:
    """{(file_path, start_line): rationale rows} for the symbols starting at
    those lines, read with one query over their files, ADRs linked."""
    files = sorted({fp for fp, _ in defs})
    if not files:
        return {}
    wanted = set(defs)
    rows = [
        r
        for r in conn.rationale_rows(file_paths=files)
        if r.get("symbol_id") and (r["file_path"], r["symbol_line"]) in wanted
    ]
    out: dict[tuple[str, int], list[dict]] = {}
    for row in link_adrs(conn, rows):
        out.setdefault((row["file_path"], row["symbol_line"]), []).append(row)
    return out


def payload(
    per_scope: list[tuple[str, list[dict]]],
    target: str,
    tag: str,
    limit: int,
    short_path=None,
) -> dict:
    """The rationale tool / `cgh why --json` payload from each scope's rows
    (fetched with ``limit`` + 1 so a cut shows): {target, tag, total,
    truncated, rationale: [{scope, file, line, tag, text, symbol,
    symbol_kind, adr?, adr_file?}]}."""
    items: list[dict] = []
    truncated = False
    for scope, rows in per_scope:
        if len(rows) > limit:
            truncated = True
        for r in rows[:limit]:
            items.append({"scope": scope, **item(r, TOOL_TEXT_CAP, short_path)})
    if len(items) > limit * max(1, len(per_scope)):
        truncated = True
    return {
        "target": target or None,
        "tag": tag.strip().upper() or None,
        "total": len(items),
        "truncated": truncated,
        "rationale": items,
    }


def clamp_limit(limit: int) -> int:
    """The row cap a caller asked for, within 1..TOOL_LIMIT_MAX."""
    return max(1, min(int(limit or 50), TOOL_LIMIT_MAX))
