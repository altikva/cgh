# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-31
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT (SDK embedding exception, see LICENSE)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The embedding surface: the one import path third-party
#              code may rely on, with a stability contract (SDK_API,
#              SemVer) and the MIT embedding grant from LICENSE.
#              Exposes cgh's bricks as explicit functions taking
#              arguments instead of ambient repo state:
#                scan_text          run installed scanners on content
#                egress_decision    the confidentiality gate, pure
#                pseudonymize       keyed one-way pseudonym, caller key
#                summarize          text summary via cgh-summarize
#                image_*            vision pipeline via cgh-vision
#              plus InMemoryFindingStore for pipelines that want dedup
#              and querying without SQLite. Nothing here reads or
#              writes .codegraph/; the CLI and MCP layers are separate
#              consumers of the same bricks.

from __future__ import annotations

import hashlib
import hmac as _hmac
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from codegraph.plugin_api import ScanFinding

SDK_API = 1

__all__ = [
    "SDK_API",
    "ScanFinding",
    "Verdict",
    "CodegraphError",
    "CapabilityMissing",
    "InMemoryFindingStore",
    "scan_text",
    "egress_decision",
    "pseudonymize",
    "summarize",
    "image_inventory",
    "extract_diagram",
    "extract_table",
    "extract_chart",
]


# The SDK's errors are the public hierarchy: catch CodegraphError to
# handle everything cgh raises on purpose.
from codegraph.errors import CapabilityMissing, CodegraphError  # noqa: E402


# -- text scanning ----------------------------------------------------------


def scan_text(
    text: str,
    path: str = "",
    scanners: Iterable[str] | None = None,
    config: dict | None = None,
) -> list[ScanFinding]:
    """Run installed scanner plugins over content the caller provides.

    ``scanners`` filters by plugin name (e.g. ["pii", "classify"]);
    None runs every installed scanner. ``config`` is currently
    reserved; per-plugin configuration comes from the plugins' own
    defaults when embedding. Deferred scanners run synchronously here:
    the caller owns the scheduling."""
    from codegraph.plugins import load_plugins
    from codegraph.plugins import scanners as _installed

    load_plugins(None)
    wanted = set(scanners) if scanners is not None else None
    out: list[ScanFinding] = []
    for plugin_name, scanner in _installed():
        if wanted is not None and plugin_name not in wanted:
            continue
        found = scanner.scan(Path(path or "content.txt"), text, None) or []
        out.extend(found)
    if wanted:
        missing = wanted - {name for name, _ in _installed()}
        if missing:
            first = sorted(missing)[0]
            raise CapabilityMissing(first, f"cgh-{first}")
    return out


# -- egress gate ------------------------------------------------------------


@dataclass
class Verdict:
    allowed: bool
    reason: str = ""

    def __bool__(self) -> bool:  # if verdict: ...
        return self.allowed


def egress_decision(
    findings: Iterable[ScanFinding],
    mode: str = "secure",
    allow_pii: bool = False,
    labeled_non_confidential: bool = False,
) -> Verdict:
    """May content carrying these findings be sent to a cloud model?

    assist: deny on any block-severity finding, a confidential = true
    finding, or pii.* findings unless allow_pii. secure: all of the
    above, and the gate is an allowlist: content must be explicitly
    labeled non-confidential by the caller or it stays local."""
    findings = list(findings)
    for f in findings:
        if getattr(f, "severity", "info") == "block":
            return Verdict(False, f"block finding: {f.key}")
        if f.key == "confidential" and str(f.value).strip().lower() in (
            "true",
            "yes",
            "1",
        ):
            return Verdict(False, "flagged confidential")
    if not allow_pii and any(f.key.startswith("pii.") for f in findings):
        return Verdict(False, "pii findings present (allow_pii=False)")
    if mode == "secure" and not labeled_non_confidential:
        return Verdict(
            False,
            "secure mode is an allowlist: pass labeled_non_confidential=True "
            "for content a human cleared",
        )
    return Verdict(True)


# -- pseudonymization -------------------------------------------------------


def pseudonymize(key: str, value: str, secret: bytes) -> str:
    """Stable one-way pseudonym for a sensitive value, caller-owned
    secret (32 random bytes you persist yourself). Same secret and
    value, same pseudonym; HMAC does not decode, so the value is not
    recoverable from the output even with the secret."""
    if not isinstance(secret, (bytes, bytearray)) or len(secret) < 16:
        raise ValueError("secret must be at least 16 bytes")
    digest = _hmac.new(secret, str(value).encode("utf-8"), hashlib.sha256).hexdigest()[
        :10
    ]
    return f"<{key}:{digest}>"


# -- summaries (cgh-summarize) ----------------------------------------------


def summarize(
    text: str,
    config: dict | None = None,
    cloud_allowed: bool = False,
) -> str:
    """Summarize text through the cgh-summarize backends. Defaults are
    the safe ones: cloud_allowed=False restricts the pick to local
    backends (ollama, structural); pass True after your own
    egress_decision. Returns the summary, or raises CapabilityMissing
    when cgh-summarize is not installed."""
    try:
        from cgh_summarize.backends import pick_backend
    except ImportError as exc:
        raise CapabilityMissing("summarize", "cgh-summarize") from exc
    from cgh_summarize.backends import StructuralBackend

    cfg = dict(config or {})
    backend = pick_backend(cfg, cloud_allowed=cloud_allowed)
    if backend is None:
        return ""
    # The structural backend summarizes parser outlines, which plain
    # text does not have; its SDK equivalent is an honest excerpt.
    if isinstance(backend, StructuralBackend):
        return _excerpt(text)
    language = str(cfg.get("language", "en"))
    prompt = (
        f"Summarize the following content for a software engineer, in "
        f"{language}, 5 sentences at most, plain prose, no preamble.\n"
        "OUTLINE:\n\nEXCERPT:\n" + text[:4000]
    )
    try:
        return (backend.summarize(prompt, cfg) or "").strip()
    except Exception:
        # A picked backend can still fail (daemon up but model not
        # pulled, CLI auth expired); the excerpt keeps the contract:
        # some summary, never an exception mid-pipeline.
        return _excerpt(text)


def _excerpt(text: str, limit: int = 300) -> str:
    collapsed = " ".join(text.split())
    return collapsed[:limit] + ("..." if len(collapsed) > limit else "")


# -- vision (cgh-vision) ----------------------------------------------------


def _vision():
    try:
        import cgh_vision  # type: ignore[import-not-found]

        return cgh_vision
    except ImportError as exc:
        raise CapabilityMissing("vision", "cgh-vision") from exc


def image_inventory(path: str | Path, config: dict | None = None) -> dict:
    """Content inventory of an image: {summary, content: [types],
    text_density}. Requires cgh-vision."""
    return _vision().inventory(Path(path), config or {})


def extract_diagram(path: str | Path, config: dict | None = None) -> dict:
    """Architecture extraction (nodes, edges, zones, markdown, mermaid)
    of a diagram image. Requires cgh-vision."""
    return _vision().extract_diagram(Path(path), config or {})


def extract_table(path: str | Path, config: dict | None = None) -> list[dict]:
    """Tables in an image as {title, columns, rows}. Requires
    cgh-vision."""
    return _vision().extract_tables(Path(path), config or {})


def extract_chart(path: str | Path, config: dict | None = None) -> list[dict]:
    """Charts in an image as {type, title, values, insight}. Requires
    cgh-vision."""
    return _vision().extract_charts(Path(path), config or {})


# -- optional in-memory store ------------------------------------------------


@dataclass
class _Record:
    path: str
    scanner: str
    finding: ScanFinding
    blob_sha: str = ""


@dataclass
class InMemoryFindingStore:
    """A minimal finding store for pipelines that want dedup and
    querying without SQLite. Same replace-per-scanner semantics as the
    repo store; nothing is persisted."""

    _records: list[_Record] = field(default_factory=list)
    _scanned: set[tuple[str, str, str]] = field(default_factory=set)

    def record(
        self,
        path: str,
        scanner: str,
        findings: Iterable[ScanFinding],
        blob_sha: str = "",
    ) -> int:
        self._records = [
            r for r in self._records if not (r.path == path and r.scanner == scanner)
        ]
        items = list(findings)
        self._records.extend(
            _Record(path=path, scanner=scanner, finding=f, blob_sha=blob_sha)
            for f in items
        )
        self._scanned.add((path, scanner, blob_sha))
        return len(items)

    def already_scanned(self, path: str, scanner: str, blob_sha: str) -> bool:
        return (path, scanner, blob_sha) in self._scanned

    def query(
        self, key_prefix: str = "", severity: str = "", path: str = ""
    ) -> list[ScanFinding]:
        out = []
        for r in self._records:
            f = r.finding
            if key_prefix and not f.key.startswith(key_prefix):
                continue
            if severity and getattr(f, "severity", "info") != severity:
                continue
            if path and r.path != path:
                continue
            out.append(f)
        return out
