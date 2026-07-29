# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The deferred summarize scanner. Skips small files, asks
#              the gate whether cloud backends may see the content, picks
#              the first available backend within that constraint, builds
#              a structural scaffold plus a capped excerpt as the prompt,
#              and records the result as a `summary` finding (FTS-fed).
#              Changed content keeps its old summary while drift stays
#              under 30% of lines and fewer than 5 changes accumulated.
#              Every cloud call and every gate denial is audit-logged.

from __future__ import annotations

import json
from pathlib import Path

from codegraph.plugin_api import ScanFinding

from .backends import pick_backend
from .gate import cloud_allowed

_EXCERPT_CHARS = 2000
_SUMMARY_CHARS = 2000
_DRIFT_THRESHOLD = 0.30
_MAX_CARRIES = 5


def build_prompt(path: Path, text: str, language: str) -> str:
    """Structural outline (via whatever parser claims the file) plus a
    capped excerpt. The outline anchors the model on structure; the
    excerpt gives it prose to work with."""
    outline_lines: list[str] = []
    try:
        from codegraph.parsers import get_parser_for_path

        parser = get_parser_for_path(path)
        if parser is not None:
            idx = parser.parse(path)
            for fn in idx.functions[:30]:
                doc = f" | {fn.docstring.splitlines()[0]}" if fn.docstring else ""
                outline_lines.append(f"fn {fn.name}{doc}")
            for cls in idx.classes[:15]:
                outline_lines.append(f"class {cls.name}")
            for sec in idx.sections[:40]:
                outline_lines.append(f"{'#' * max(sec.level, 1)} {sec.title}")
            for res in idx.resources[:20]:
                outline_lines.append(f"resource {res.type} {res.name}")
    except Exception:
        pass

    return (
        f"Summarize the file {path.name} for a software engineer, in "
        f"{language}, 5 sentences at most, plain prose, no preamble.\n"
        "OUTLINE:\n" + "\n".join(outline_lines) + "\n"
        "EXCERPT:\n" + text[:_EXCERPT_CHARS]
    )


class SummarizeScanner:
    """Deferred scanner writing `summary` findings."""

    name = "summarize"
    deferred = True

    def __init__(self, config: dict, repo_root, extras_fn=None) -> None:
        self.config = config
        self.repo_root = repo_root
        # Callable returning summarize.backend extension objects; a
        # callable so late-registered backends are still seen.
        self._extras_fn = extras_fn or (lambda: [])

    def scan(self, path: Path, text: str, index) -> list[ScanFinding]:
        min_kb = float(self.config.get("min_kb", 4))
        if len(text) < min_kb * 1024:
            return []

        carried = self._carry_forward(path, text)
        if carried is not None:
            return carried

        allowed, reason = cloud_allowed(self.repo_root, str(path), self.config)
        backend = pick_backend(
            self.config, extras=list(self._extras_fn()), cloud_allowed=allowed
        )
        if backend is None:
            return []
        if not allowed:
            self._audit(f"egress denied ({reason}), using {backend.name}: {path}")

        prompt = build_prompt(path, text, str(self.config.get("language", "en")))
        summary = (backend.summarize(prompt, self.config) or "").strip()
        if not summary:
            return []
        if getattr(backend, "egress", "cloud") == "cloud":
            self._audit(f"egress: sent to {backend.name}: {path}")

        return self._findings(
            summary[:_SUMMARY_CHARS], text, scans=0, backend=backend.name
        )

    # -- helpers ----------------------------------------------------------

    def _findings(
        self, summary: str, text: str, scans: int, backend: str
    ) -> list[ScanFinding]:
        meta = json.dumps(
            {"lines": text.count("\n") + 1, "scans": scans, "backend": backend}
        )
        return [
            ScanFinding(key="summary", value=summary, line=0, severity="info"),
            ScanFinding(key="summary.meta", value=meta, line=0, severity="info"),
        ]

    def _carry_forward(self, path: Path, text: str) -> list[ScanFinding] | None:
        """Keep the previous summary while the content drift stays under
        the threshold and fewer than _MAX_CARRIES changes accumulated."""
        from codegraph.state.findings import findings_for_file

        rows = {
            r["key"]: r["value"]
            for r in findings_for_file(self.repo_root, str(path), key_prefix="summary")
        }
        old_summary = rows.get("summary")
        if not old_summary:
            return None
        try:
            meta = json.loads(rows.get("summary.meta", "{}"))
            old_lines = int(meta.get("lines", 0))
            scans = int(meta.get("scans", 0))
            backend = str(meta.get("backend", ""))
        except (ValueError, TypeError):
            return None
        new_lines = text.count("\n") + 1
        if old_lines <= 0 or scans + 1 >= _MAX_CARRIES:
            return None
        drift = abs(new_lines - old_lines) / max(old_lines, 1)
        if drift >= _DRIFT_THRESHOLD:
            return None
        # Re-record with the old line count so drift accumulates across
        # carries instead of resetting at each small change.
        meta_out = json.dumps(
            {"lines": old_lines, "scans": scans + 1, "backend": backend}
        )
        return [
            ScanFinding(key="summary", value=old_summary, line=0, severity="info"),
            ScanFinding(key="summary.meta", value=meta_out, line=0, severity="info"),
        ]

    def _audit(self, message: str) -> None:
        try:
            from codegraph.state.activity import log as activity_log

            activity_log(self.repo_root, "summarize", message)
        except Exception:
            pass
