# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Report payloads built by ALLOWLIST. Every field is
#              enumerated here; exception messages, file paths outside
#              cgh's own modules, command arguments and log lines are not
#              fields, so they structurally cannot appear. The PII
#              scanner runs over the finished payload as a tripwire and
#              fails the build loudly on any match. Fingerprints exclude
#              the version and the line number so a known crash dedups
#              across releases.

from __future__ import annotations

import hashlib
import platform
import time
import traceback
import uuid
from pathlib import Path

_MAX_FRAMES = 20


class PayloadTripwire(RuntimeError):
    """The finished payload matched a PII/secret pattern. This should be
    impossible by construction; failing loudly beats sending."""


def _own_package_dirs() -> dict[str, Path]:
    """Packages whose frames may appear by name: cgh core and cgh_*
    plugins. Everything else reduces to <external>."""
    dirs: dict[str, Path] = {}
    try:
        import codegraph

        dirs["codegraph"] = Path(codegraph.__file__).parent
    except Exception:
        pass
    import sys

    for name, module in list(sys.modules.items()):
        if name.startswith("cgh_") and "." not in name:
            f = getattr(module, "__file__", None)
            if f:
                dirs[name] = Path(f).parent
    return dirs


def _normalize_frame(filename: str, lineno: int, func: str) -> str:
    """`codegraph/state/watcher.py:123 in _flush` for in-cgh frames,
    `<external>` for everything else. The path is package-relative, so
    nothing about the machine or the repo appears."""
    path = Path(filename)
    for pkg, pkg_dir in _own_package_dirs().items():
        try:
            rel = path.resolve().relative_to(pkg_dir.resolve())
        except (ValueError, OSError):
            continue
        return f"{pkg}/{rel.as_posix()}:{lineno} in {func}"
    return "<external>"


def normalized_frames(tb) -> list[str]:
    frames: list[str] = []
    for fs in traceback.extract_tb(tb)[-_MAX_FRAMES:]:
        frames.append(_normalize_frame(fs.filename, fs.lineno or 0, fs.name))
    return frames


def fingerprint(exc_type: type, frames: list[str]) -> str:
    """Stable across releases: exception type + the deepest in-cgh frame
    stripped of its line number. The cgh version goes in the payload,
    never in the key, so one known crash stays one issue."""
    anchor = "<external>"
    for frame in reversed(frames):
        if frame != "<external>":
            anchor = frame.split(":", 1)[0] + " in " + frame.rsplit(" in ", 1)[-1]
            break
    digest = hashlib.sha256(f"{exc_type.__name__}|{anchor}".encode()).hexdigest()
    return digest[:16]


def build_report(exc_type: type, exc_value, tb, command: str = "") -> dict:
    """The whole payload. Add a field here or it cannot exist."""
    frames = normalized_frames(tb)
    payload = {
        "report_id": uuid.uuid4().hex[:12],
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "cgh_version": _cgh_version(),
        "python_version": platform.python_version(),
        "os": platform.system(),
        "command": command.split()[0] if command else "",
        "mode": _mode(),
        "plugins": _plugin_versions(),
        "exception_type": exc_type.__name__,
        "frames": frames,
        "fingerprint": fingerprint(exc_type, frames),
    }
    _tripwire(payload)
    return payload


def _tripwire(payload: dict) -> None:
    """Run the PII regex tier over the serialized payload. Defense in
    depth only: the allowlist is the barrier, this is the alarm."""
    import json

    try:
        from cgh_pii.regex_scanner import RegexPiiScanner
    except ImportError:
        return
    text = json.dumps(payload)
    findings = RegexPiiScanner().scan(Path("payload.json"), text, None)
    if findings:
        keys = ", ".join(sorted({f.key for f in findings}))
        raise PayloadTripwire(
            f"refusing to build report: payload matched {keys}. "
            "This is a cgh-bugreport bug, please report it manually."
        )


def _cgh_version() -> str:
    try:
        from importlib.metadata import version

        return version("cgh")
    except Exception:
        return "unknown"


def _mode() -> str:
    try:
        from codegraph.plugin_api import load_config

        return load_config(None).mode
    except Exception:
        return "unknown"


def _plugin_versions() -> list[str]:
    try:
        from codegraph.plugin_api import loaded_plugins

        return [
            f"{p.name}=={p.version or '?'}"
            for p in loaded_plugins()
            if p.status == "active"
        ]
    except Exception:
        return []
