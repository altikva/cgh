# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-05
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __contributors__ = ["jndjama (Joy Ndjama)"]
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# __maintainer__ = "jndjama (Joy Ndjama)"
# __email__ = "joy.ndjama@altikva.com"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Fetch a URL, reduce it to searchable text, chunk it, and
#              index it into the FTS db for later `search_fetched`. A
#              fetch is network egress, so it is gated: http/https only,
#              private/loopback/link-local hosts refused (SSRF), refused
#              outright in secure mode unless allow_fetch is set, and
#              every fetch and every refusal is written to the activity
#              log. Results cache by URL with a TTL so a re-fetch inside
#              the window costs nothing.

from __future__ import annotations

import ipaddress
import time
import urllib.request
from html.parser import HTMLParser
from urllib.parse import urlsplit

_MAX_BYTES = 5 * 1024 * 1024
_CHUNK_CHARS = 1500
_TIMEOUT = 20


class FetchError(RuntimeError):
    """The fetch was refused (policy) or failed (network)."""


class _TextExtractor(HTMLParser):
    """Readable text and the <title>, dropping script/style/head noise."""

    _SKIP = {"script", "style", "noscript", "head", "meta", "link"}

    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self._parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag in ("p", "div", "li", "h1", "h2", "h3", "br", "tr"):
            self._parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip_depth and data.strip():
            self._parts.append(data)

    def text(self) -> str:
        raw = "".join(self._parts)
        lines = [ln.strip() for ln in raw.splitlines()]
        return "\n".join(ln for ln in lines if ln)


def _guard_url(url: str, repo_root) -> None:
    """http/https only, and never a private, loopback or link-local
    host: an agent-triggered fetch to 169.254.169.254 or 127.0.0.1 is
    the classic SSRF path to cloud metadata or local services."""
    from codegraph.state.activity import log as _log

    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        _log(repo_root, "fetch_refused", f"scheme {parts.scheme}: {url}")
        raise FetchError(f"only http/https URLs are fetched, not {parts.scheme!r}")
    host = parts.hostname or ""
    if host.lower() in ("localhost", ""):
        _log(repo_root, "fetch_refused", f"local host: {url}")
        raise FetchError("refusing to fetch a local host (SSRF guard)")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and (ip.is_private or ip.is_loopback or ip.is_link_local):
        _log(repo_root, "fetch_refused", f"non-public ip {host}: {url}")
        raise FetchError(f"refusing to fetch a non-public address {host} (SSRF guard)")


def _guard_secure(url: str, config: dict, repo_root) -> None:
    """A fetch reaches the network; in secure mode that needs an explicit
    opt-in (allow_fetch), consistent with 'nothing leaves without a
    gate'. The probe fails closed: unknown mode is treated as secure."""
    from codegraph.state.activity import log as _log

    try:
        from codegraph.core.config import load_config

        mode = load_config(repo_root).mode
    except Exception:
        mode = "secure"
    if mode == "secure" and not config.get("allow_fetch", False):
        _log(repo_root, "fetch_refused", f"secure mode, allow_fetch off: {url}")
        raise FetchError(
            "secure mode refuses network fetches; set [codegraph] allow_fetch = true "
            "to permit them (each fetch is still audited)"
        )


def _fetched_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fetched_chunks (
            url        TEXT NOT NULL,
            title      TEXT NOT NULL DEFAULT '',
            chunk_no   INTEGER NOT NULL,
            text       TEXT NOT NULL,
            fetched_at REAL NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS fetched_url ON fetched_chunks(url)")
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS fetched_fts USING fts5(
            title, text, url UNINDEXED,
            content='fetched_chunks', content_rowid='rowid'
        )
    """)


def _chunks(text: str) -> list[str]:
    out: list[str] = []
    buf = ""
    for para in text.split("\n"):
        if len(buf) + len(para) + 1 > _CHUNK_CHARS and buf:
            out.append(buf.strip())
            buf = ""
        buf += para + "\n"
    if buf.strip():
        out.append(buf.strip())
    return out


def _cached_within_ttl(conn, url: str, ttl_s: float) -> int:
    row = conn.execute(
        "SELECT count(*), max(fetched_at) FROM fetched_chunks WHERE url = ?", (url,)
    ).fetchone()
    if row and row[0] and row[1] and (time.time() - row[1]) < ttl_s:
        return int(row[0])
    return 0


def fetch_and_index(
    repo_root,
    url: str,
    ttl_hours: float = 24.0,
    force: bool = False,
    config: dict | None = None,
) -> dict:
    """Fetch, reduce to text, chunk, index. Returns
    {url, title, chunks, cached}. Raises FetchError on a refused or
    failed fetch."""
    from codegraph.core.fts import commit, get_fts_conn
    from codegraph.state.activity import log as _log

    cfg = config or {}
    _guard_url(url, repo_root)
    _guard_secure(url, cfg, repo_root)

    conn = get_fts_conn(repo_root)
    _fetched_table(conn)
    ttl_s = max(0.0, ttl_hours) * 3600
    if not force:
        cached = _cached_within_ttl(conn, url, ttl_s)
        if cached:
            return {"url": url, "title": "", "chunks": cached, "cached": True}

    req = urllib.request.Request(url, headers={"User-Agent": "cgh-fetch/1"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            raw = resp.read(_MAX_BYTES + 1)
    except Exception as exc:
        _log(repo_root, "fetch_failed", f"{url}: {exc}")
        raise FetchError(f"fetch failed: {exc}") from exc
    if len(raw) > _MAX_BYTES:
        raise FetchError(f"response exceeds {_MAX_BYTES // (1024 * 1024)} MB, refusing")

    body = raw.decode("utf-8", errors="replace")
    parser = _TextExtractor()
    parser.feed(body)
    title = parser.title.strip()[:200]
    chunks = _chunks(parser.text())

    conn.execute("DELETE FROM fetched_chunks WHERE url = ?", (url,))  # replace
    now = time.time()
    for i, chunk in enumerate(chunks):
        cur = conn.execute(
            "INSERT INTO fetched_chunks(url, title, chunk_no, text, fetched_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (url, title, i, chunk, now),
        )
        conn.execute(
            "INSERT INTO fetched_fts(rowid, title, text, url) VALUES (?, ?, ?, ?)",
            (cur.lastrowid, title, chunk, url),
        )
    commit(conn)
    _log(repo_root, "fetch_indexed", f"{url}: {len(chunks)} chunk(s)")
    return {"url": url, "title": title, "chunks": len(chunks), "cached": False}


def search_fetched(repo_root, query: str, limit: int = 10) -> list[dict]:
    """FTS over fetched chunks. Returns [{url, title, snippet, score}]."""
    from codegraph.core.fts import get_fts_conn

    conn = get_fts_conn(repo_root)
    _fetched_table(conn)
    try:
        rows = conn.execute(
            "SELECT c.url, c.title, c.text, rank FROM fetched_fts f "
            "JOIN fetched_chunks c ON c.rowid = f.rowid "
            "WHERE fetched_fts MATCH ? ORDER BY rank LIMIT ?",
            (query, limit),
        ).fetchall()
    except Exception:
        return []
    return [
        {"url": r[0], "title": r[1], "snippet": (r[2] or "")[:300], "score": -r[3]}
        for r in rows
    ]


def purge_fetched(repo_root, url: str = "") -> int:
    """Drop one URL's chunks, or all fetched content. Returns the count
    removed."""
    from codegraph.core.fts import commit, get_fts_conn

    conn = get_fts_conn(repo_root)
    _fetched_table(conn)
    if url:
        n = conn.execute(
            "SELECT count(*) FROM fetched_chunks WHERE url = ?", (url,)
        ).fetchone()[0]
        conn.execute("DELETE FROM fetched_chunks WHERE url = ?", (url,))
    else:
        n = conn.execute("SELECT count(*) FROM fetched_chunks").fetchone()[0]
        conn.execute("DELETE FROM fetched_chunks")
    conn.execute("INSERT INTO fetched_fts(fetched_fts) VALUES('rebuild')")
    commit(conn)
    return int(n)
