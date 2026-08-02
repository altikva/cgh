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
#              pseudonymizes them). Everything runs against the local
#              Ollama daemon; nothing leaves the machine. Backend
#              unavailability raises so the deferred worker logs it and
#              the image retries on its next change.

from __future__ import annotations

import json
from pathlib import Path

from codegraph.plugin_api import ScanFinding

from .backends import VisionError, available, is_local, ollama_url
from .pipeline import (
    DIAGRAM_KINDS,
    charts_to_markdown,
    extract_charts,
    extract_diagram,
    extract_tables,
    extract_text,
    inventory,
    tables_to_markdown,
)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
_MIN_BYTES = 5 * 1024
_MAX_BYTES = 20 * 1024 * 1024


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

    def scan(self, path: Path, text: str, index) -> list[ScanFinding]:
        p = Path(path)
        if p.suffix.lower() not in IMAGE_SUFFIXES:
            return []
        try:
            size = p.stat().st_size
        except OSError:
            return []
        if size < int(self.config.get("min_bytes", _MIN_BYTES)) or size > int(
            self.config.get("max_bytes", _MAX_BYTES)
        ):
            return []
        if not is_local(self.config):
            # A non-loopback daemon means the image bytes leave this
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
                    f"non-loopback ollama_url {ollama_url(self.config)}"
                )
            self._audit(
                f"image bytes sent to non-loopback ollama_url "
                f"{ollama_url(self.config)}: {p}"
            )
        if not available(self.config):
            # Raise, do not record: the deferred worker logs the reason
            # and the image is retried once a daemon is running.
            raise VisionError(
                "vision backend: Ollama daemon not reachable "
                f"({ollama_url(self.config)})"
            )

        inv = inventory(p, self.config)
        findings = [
            ScanFinding(key="image.content", value=",".join(inv["content"])),
            ScanFinding(key="image.summary", value=inv["summary"][:2000]),
        ]

        if DIAGRAM_KINDS & set(inv["content"]):
            ex = extract_diagram(p, self.config)
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
        if "table" in inv["content"]:
            tables = extract_tables(p, self.config)
            if tables:
                findings.append(
                    ScanFinding(
                        key="table.markdown", value=tables_to_markdown(tables)[:8000]
                    )
                )
        if "chart" in inv["content"]:
            charts = extract_charts(p, self.config)
            if charts:
                findings.append(
                    ScanFinding(
                        key="chart.markdown", value=charts_to_markdown(charts)[:8000]
                    )
                )
        if "dense_text" in inv["content"]:
            txt = extract_text(p, self.config)
            if txt["summary"]:
                findings.append(
                    ScanFinding(key="text.summary", value=txt["summary"][:2000])
                )
        return findings
