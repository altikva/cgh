# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-16
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Where the proxy keeps what it saw.
#              CaptureStore.save   one exchange as a 0600 JSON file, raw
#                                  bodies included, the oldest pruned past
#                                  `keep`; a summary line appended to
#                                  journal.jsonl, which holds no body
#              CaptureStore.recent the last journal lines
#              CaptureStore.load   one full capture by id
#              kept_headers        the header allow-list stored with a capture

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from . import ProxyError

# Allow-list, not a deny-list: a credential header the deny-list did not
# foresee would otherwise land on disk.
_KEPT_HEADERS = {
    "content-type",
    "anthropic-version",
    "anthropic-beta",
    "user-agent",
    "x-app",
    "request-id",
    "x-request-id",
    "x-litellm-model-id",
    "x-litellm-response-cost",
}


def default_dir() -> Path:
    return Path.home() / ".cache" / "cgh" / "proxy"


def kept_headers(headers) -> dict[str, str]:
    return {k.lower(): v for k, v in headers.items() if k.lower() in _KEPT_HEADERS}


def _write_private(path: Path, text: str) -> None:
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    tmp.replace(path)


class CaptureStore:
    """Captures of the last ``keep`` exchanges plus a body-free journal.
    keep=0 stores no capture at all; the journal is still written."""

    def __init__(self, root: Path, keep: int) -> None:
        if keep < 0:
            raise ProxyError("keep must be 0 or more")
        self.root = root
        self.keep = keep
        self._lock = threading.Lock()
        self._seq = 0
        root.mkdir(parents=True, exist_ok=True)
        os.chmod(root, 0o700)

    @property
    def journal(self) -> Path:
        return self.root / "journal.jsonl"

    def _captures(self) -> list[Path]:
        return sorted(self.root.glob("cap-*.json"))

    def new_id(self, ts: float) -> str:
        """Short enough to type into `cgh proxy show`, sorted by time."""
        with self._lock:
            self._seq += 1
            stamp = time.strftime("%y%m%d-%H%M%S", time.localtime(ts))
            return f"{stamp}-{self._seq % 10_000:04d}"

    def save(self, cap_id: str, summary: dict, capture: dict) -> None:
        line = json.dumps({"id": cap_id, **summary}, ensure_ascii=False)
        with self._lock:
            if self.keep:
                _write_private(
                    self.root / f"cap-{cap_id}.json",
                    json.dumps(
                        {"id": cap_id, "summary": summary, **capture},
                        ensure_ascii=False,
                    ),
                )
                for old in self._captures()[: -self.keep]:
                    old.unlink(missing_ok=True)
            fd = os.open(self.journal, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def recent(self, limit: int = 20) -> list[dict]:
        if not self.journal.exists():
            return []
        lines = self.journal.read_text(encoding="utf-8").splitlines()[-limit:]
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    def load(self, cap_id: str) -> dict:
        if not cap_id or any(c not in "0123456789-" for c in cap_id):
            raise ProxyError(f"not a capture id: {cap_id!r}")
        path = self.root / f"cap-{cap_id}.json"
        if not path.exists():
            raise ProxyError(f"capture {cap_id} is gone: older captures are pruned")
        return json.loads(path.read_text(encoding="utf-8"))
