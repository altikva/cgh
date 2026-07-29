# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The local report spool: .codegraph/bugreports/<id>.json.
#              Capped at 20 reports (oldest dropped), purged past 30
#              days, and living under .codegraph/ which is gitignored
#              and never indexed. Send metadata is written back into the
#              report file so `cgh bug status` is the complete ledger.

from __future__ import annotations

import json
import time
from pathlib import Path

_DIR = "bugreports"
_CAP = 20
_MAX_AGE_DAYS = 30


def spool_dir(repo_root: str | Path) -> Path:
    return Path(repo_root) / ".codegraph" / _DIR


def write_report(repo_root: str | Path, payload: dict) -> Path:
    d = spool_dir(repo_root)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{payload['report_id']}.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    _enforce_cap(repo_root)
    return path


def list_reports(repo_root: str | Path) -> list[dict]:
    """Newest first."""
    d = spool_dir(repo_root)
    if not d.exists():
        return []
    reports = []
    for f in d.glob("*.json"):
        try:
            reports.append(json.loads(f.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            continue
    return sorted(reports, key=lambda r: r.get("created_at", ""), reverse=True)


def load_report(repo_root: str | Path, report_id: str) -> dict | None:
    reports = list_reports(repo_root)
    if not reports:
        return None
    if report_id in ("", "last"):
        return reports[0]
    return next((r for r in reports if r.get("report_id") == report_id), None)


def mark_sent(repo_root: str | Path, report_id: str, where: str) -> None:
    path = spool_dir(repo_root) / f"{report_id}.json"
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["sent"] = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "to": where}
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except (ValueError, OSError):
        pass


def purge(repo_root: str | Path, report_id: str = "", older_days: int = 0) -> int:
    """Drop one report, or everything past `older_days` (0 = all)."""
    d = spool_dir(repo_root)
    if not d.exists():
        return 0
    dropped = 0
    cutoff = time.time() - older_days * 86400 if older_days else None
    for f in d.glob("*.json"):
        if report_id and f.stem != report_id:
            continue
        if cutoff is not None and f.stat().st_mtime >= cutoff:
            continue
        f.unlink(missing_ok=True)
        dropped += 1
    return dropped


def _enforce_cap(repo_root: str | Path) -> None:
    d = spool_dir(repo_root)
    files = sorted(d.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    for stale in files[_CAP:]:
        stale.unlink(missing_ok=True)
    cutoff = time.time() - _MAX_AGE_DAYS * 86400
    for f in files[:_CAP]:
        if f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)
