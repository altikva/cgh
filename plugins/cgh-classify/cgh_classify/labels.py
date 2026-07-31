# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Human labels, the ground truth the classifier learns from
#              and the only thing that can clear a file in strict mode.
#              A JSON map of absolute path -> bool in .codegraph/,
#              machine-local like everything else in that directory.

from __future__ import annotations

import json
from pathlib import Path


def labels_path(repo_root: str | Path) -> Path:
    return Path(repo_root) / ".codegraph" / "classify_labels.json"


def load_labels(repo_root: str | Path) -> dict[str, bool]:
    p = labels_path(repo_root)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return {str(k): bool(v) for k, v in data.items()}
    except (ValueError, TypeError):
        return {}


def set_label(repo_root: str | Path, file_path: str | Path, confidential: bool) -> None:
    labels = load_labels(repo_root)
    labels[str(Path(file_path).resolve())] = confidential
    p = labels_path(repo_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(labels, indent=1, sort_keys=True), encoding="utf-8")


def remove_label(repo_root: str | Path, file_path: str | Path) -> bool:
    labels = load_labels(repo_root)
    key = str(Path(file_path).resolve())
    if key not in labels:
        return False
    del labels[key]
    labels_path(repo_root).write_text(
        json.dumps(labels, indent=1, sort_keys=True), encoding="utf-8"
    )
    return True
