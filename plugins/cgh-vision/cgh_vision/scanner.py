# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-31
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The deferred vision scanner: images picked up by the
#              indexer run through the pipeline off the hot path.
#              Findings: image.content and image.summary for every
#              image, diagram.mermaid / diagram.entities for diagrams,
#              table.markdown, chart.markdown, text.summary as routed.
#              Identities separated from labels become image.identity
#              findings (pii-prefixed key so the secure-at-rest layer
#              pseudonymizes them). With [plugin.vision] auto_extract on,
#              the same background pass also writes a <file>.json sidecar
#              holding the full structured extraction (images and PDFs),
#              so a whole repo of diagrams gets extracted with no manual
#              `cgh vision` call. Everything runs against the local Ollama
#              daemon; nothing leaves the machine. Backend unavailability
#              raises so the deferred worker logs it and the file retries
#              on its next change.

from __future__ import annotations

import json
from pathlib import Path

from codegraph.plugin_api import ScanFinding

from . import cache
from .backends import VisionError, available, endpoint_url, is_local
from .pipeline import route_structured

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
_MIN_BYTES = 5 * 1024
_MAX_BYTES = 20 * 1024 * 1024


def _as_bool(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


class VisionScanner:
    """Deferred scanner turning repo images into findings."""

    name = "vision"
    deferred = True

    def __init__(self, config: dict, repo_root) -> None:
        self.config = dict(config or {})
        self.repo_root = repo_root

    def _audit(self, message: str) -> None:
        # Best-effort: a failed audit write must not block the scan
        # (the egress decision itself already happened above it).
        try:
            from codegraph.plugin_api import activity_log

            activity_log(self.repo_root, "vision", message)
        except Exception:
            pass

    def _gate(self, p: Path) -> bool:
        """Shared pre-flight for images and PDFs. Returns True to proceed,
        False to skip (out of the size bounds). Raises VisionError when the
        egress posture forbids the call (secure mode, non-loopback) or the
        backend is unreachable, so the deferred worker logs it and the file
        is retried on its next change."""
        try:
            size = p.stat().st_size
        except OSError:
            return False
        if size < int(self.config.get("min_bytes", _MIN_BYTES)) or size > int(
            self.config.get("max_bytes", _MAX_BYTES)
        ):
            return False
        if not is_local(self.config):
            # A non-loopback daemon means the file bytes leave this
            # machine. Secure mode refuses outright; assist mode
            # proceeds but the departure lands in the audit trail.
            # The mode probe fails CLOSED: unknown mode is secure.
            try:
                from codegraph.plugin_api import load_config

                mode = load_config(self.repo_root).mode
            except Exception:
                mode = "secure"
            if mode == "secure":
                raise VisionError(
                    "secure mode: refusing to send image bytes to the "
                    f"non-loopback endpoint {endpoint_url(self.config)}"
                )
            self._audit(
                f"image bytes sent to non-loopback endpoint "
                f"{endpoint_url(self.config)}: {p}"
            )
        if not available(self.config):
            # Raise, do not record: the deferred worker logs the reason
            # and the file is retried once the backend is running.
            raise VisionError(
                f"vision backend not reachable ({endpoint_url(self.config)})"
            )
        return True

    def scan(self, path: Path, text: str, index) -> list[ScanFinding]:
        p = Path(path)
        suffix = p.suffix.lower()
        auto = _as_bool(self.config.get("auto_extract", False))

        if suffix == ".pdf":
            # PDFs are indexed and PII-scanned by cgh-docs on their extracted
            # text; the vision scanner only touches them to write the
            # structured sidecar when auto-extract asks for it.
            if auto:
                self._auto_extract_pdf(p)
            return []

        if suffix not in IMAGE_SUFFIXES:
            return []
        if not self._gate(p):
            return []

        img_bytes = p.read_bytes()
        # Reuse a cached extraction only in auto mode, where the CLI and the
        # background pass share results by file fingerprint. The findings
        # pass (auto off) always recomputes so a stale cache never shapes
        # what gets indexed.
        structured = cache.get(self.config, img_bytes) if auto else None
        if structured is None:
            structured = route_structured(p, self.config)
            if auto:
                cache.put(self.config, img_bytes, structured)

        findings = self._findings_from(structured)
        if auto:
            self._write_sidecar(p, structured)
        return findings

    def _findings_from(self, structured: dict) -> list[ScanFinding]:
        inv = structured.get("inventory") or {"content": [], "summary": ""}
        findings = [
            ScanFinding(key="image.content", value=",".join(inv.get("content", []))),
            ScanFinding(key="image.summary", value=(inv.get("summary") or "")[:2000]),
        ]
        ex = structured.get("diagram")
        if ex:
            findings.append(
                ScanFinding(key="diagram.mermaid", value=ex["mermaid"][:8000])
            )
            findings.append(
                ScanFinding(
                    key="diagram.entities",
                    value=json.dumps(
                        {
                            "nodes": [
                                {k: n[k] for k in ("label", "kind", "tech")}
                                for n in ex["nodes"]
                            ],
                            "edges": ex["edges"],
                            "zones": ex["zones"],
                        }
                    )[:8000],
                )
            )
            for n in ex["nodes"]:
                for ident in n["identities"]:
                    findings.append(
                        ScanFinding(
                            key="pii.image_identity", value=ident, severity="warn"
                        )
                    )
        tables = structured.get("tables")
        if tables:
            from .pipeline import tables_to_markdown

            findings.append(
                ScanFinding(
                    key="table.markdown", value=tables_to_markdown(tables)[:8000]
                )
            )
        charts = structured.get("charts")
        if charts:
            from .pipeline import charts_to_markdown

            findings.append(
                ScanFinding(
                    key="chart.markdown", value=charts_to_markdown(charts)[:8000]
                )
            )
        txt = structured.get("text")
        if txt and txt.get("summary"):
            findings.append(
                ScanFinding(key="text.summary", value=txt["summary"][:2000])
            )
        return findings

    def _auto_extract_pdf(self, p: Path) -> None:
        if not self._gate(p):
            return
        try:
            from .pdf_render import PdfRenderError, iter_pdf_pages
        except ImportError:
            # The [pdf] extra (pypdfium2) is not installed; nothing to do.
            return
        pages: list[dict] = []
        try:
            for page_no, png in iter_pdf_pages(p, pages=""):
                try:
                    png_bytes = png.read_bytes()
                    res = cache.get(self.config, png_bytes)
                    if res is None:
                        res = route_structured(png, self.config)
                        cache.put(self.config, png_bytes, res)
                    res = dict(res)
                    res["page"] = page_no
                    pages.append(res)
                finally:
                    png.unlink(missing_ok=True)
        except PdfRenderError:
            return
        if pages:
            self._write_sidecar(p, {"pdf": p.name, "pages": pages})

    def _sidecar_path(self, p: Path) -> Path:
        """Where the extraction sidecar lands. `auto_extract_out` defaults to
        `.codegraph/vision` (outside the working tree, never committed by
        accident, cleaned with the index); `beside` writes `<file>.json`
        next to the source; any other value is a directory (absolute, or
        relative to the repo root) under which the file's repo-relative path
        is mirrored."""
        out = str(self.config.get("auto_extract_out", ".codegraph/vision")).strip()
        if out.lower() in {"beside", "."}:
            return p.with_name(p.name + ".json")
        base = Path(out)
        if not base.is_absolute():
            base = Path(self.repo_root) / base
        try:
            rel = p.resolve().relative_to(Path(self.repo_root).resolve())
        except ValueError:
            rel = Path(p.name)
        return base / rel.parent / (p.name + ".json")

    def _write_sidecar(self, p: Path, structured: dict) -> None:
        try:
            target = self._sidecar_path(p)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(structured, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            self._audit(f"vision auto-extract wrote {target}")
        except (OSError, TypeError, ValueError):
            # Best-effort: a failed sidecar write must never break indexing.
            pass
