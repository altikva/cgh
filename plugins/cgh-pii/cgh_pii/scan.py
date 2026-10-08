# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-08
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: On-demand regex scan behind `cgh pii scan [PATH...]`. Walks
#              the given files and directories (git-tracked and untracked
#              non-ignored files when the directory is a git work tree,
#              a plain walk otherwise), skips binary and oversized files,
#              and returns one hit per (file, key) with the first line and
#              the match count. Like the index-time findings, a hit never
#              carries the matched text.

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from .regex_scanner import RegexPiiScanner

_MAX_BYTES = 2 * 1024 * 1024
_SKIP_DIRS = {"node_modules", "__pycache__", "venv", "dist", "build"}


@dataclass(frozen=True)
class Hit:
    path: str
    line: int
    key: str
    severity: str
    count: int


def _git_files(directory: Path) -> list[Path] | None:
    """Files git would track in ``directory`` (tracked plus untracked
    non-ignored), or None when it is not a git work tree."""
    kwargs: dict = {}
    try:
        from codegraph.plugin_api import quiet_subprocess_kwargs

        kwargs = quiet_subprocess_kwargs()
    except Exception:
        kwargs = {}
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=directory,
            capture_output=True,
            check=True,
            timeout=60,
            **kwargs,
        ).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None
    return [
        directory / rel for rel in out.decode("utf-8", "replace").split("\0") if rel
    ]


def _walk(directory: Path) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(directory):
        dirnames[:] = [
            d for d in dirnames if not d.startswith(".") and d not in _SKIP_DIRS
        ]
        for name in filenames:
            yield Path(dirpath) / name


def iter_files(paths: Iterable[str | Path]) -> Iterator[Path]:
    seen: set[Path] = set()
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            files = _git_files(p)
            candidates = files if files is not None else _walk(p)
        else:
            candidates = [p]
        for f in candidates:
            key = f.resolve()
            if key in seen or not f.is_file():
                continue
            seen.add(key)
            yield f


def _read_text(path: Path) -> str | None:
    try:
        if path.stat().st_size > _MAX_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data[:8192]:
        return None
    return data.decode("utf-8", errors="replace")


def scan_paths(
    paths: Iterable[str | Path],
    pii: bool = False,
    disabled_keys: set[str] | None = None,
) -> list[Hit]:
    scanner = RegexPiiScanner(disabled_keys=disabled_keys, pii=pii)
    hits: list[Hit] = []
    for path in iter_files(paths):
        text = _read_text(path)
        if text is None:
            continue
        for f in scanner.scan(path, text, None):
            hits.append(
                Hit(
                    path=str(path),
                    line=int(f.line or 0),
                    key=f.key,
                    severity=f.severity,
                    count=int(f.value),
                )
            )
    return hits
