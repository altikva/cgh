# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-17
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Shared helpers for the benchmark harness: paths, YAML loading,
#              the tiktoken tokenizer with a documented bytes/4 fallback, the
#              git-grep corpus baseline (naive whole-file and tight windowed
#              variants), and median / p25 / p75 aggregation. No cgh product
#              logic lives here; the metrics call cgh's own code and CLI.

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# Repo root is two levels up from this file (bench/_util.py -> repo root).
REPO_ROOT = Path(__file__).resolve().parent.parent
BENCH_DIR = REPO_ROOT / "bench"
CACHE_DIR = REPO_ROOT / ".bench-cache"
RESULTS_DIR = BENCH_DIR / "results"

# A changed line's blast window for the "tight" grep baseline: the agent is
# assumed to read this many lines on each side of a hit, not the whole file.
TIGHT_CONTEXT = 40

# Subprocess wall-clock ceiling. Clones and indexes of these small repos
# finish in seconds; a larger bound only guards against a wedged process.
_TIMEOUT = 600


class BenchError(RuntimeError):
    """A benchmark harness failure (bad config, drifted checkout, missing index)."""


def load_yaml(path: str | Path) -> Any:
    """Parse a YAML file with the safe loader."""
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def repos_config() -> dict[str, Any]:
    """The pinned corpus: {tokenizer, repos: {name: {url, sha}}}."""
    cfg = load_yaml(BENCH_DIR / "repos.yaml")
    if not isinstance(cfg, dict) or "repos" not in cfg:
        raise BenchError("bench/repos.yaml is missing a 'repos' mapping")
    return cfg


def repo_checkout(name: str) -> Path:
    """The on-disk clone for a pinned repo (populated by fetch.py)."""
    return CACHE_DIR / name


# ---------------------------------------------------------------------------
# Tokenizer: tiktoken cl100k_base, bytes/4 fallback.
# ---------------------------------------------------------------------------

_ENCODER: Any = None
_ENCODER_READY = False
_TOKENIZER_NAME = "bytes/4-fallback"


def _encoder(name: str = "cl100k_base") -> Any:
    """Return a cached tiktoken encoder, or None when tiktoken is absent."""
    global _ENCODER, _ENCODER_READY, _TOKENIZER_NAME
    if _ENCODER_READY:
        return _ENCODER
    _ENCODER_READY = True
    try:
        import tiktoken

        _ENCODER = tiktoken.get_encoding(name)
        _TOKENIZER_NAME = name
    except Exception:
        # Documented degraded mode: no tiktoken wheel in the environment.
        # Counts then approximate GPT-family tokenization at ~4 bytes/token,
        # consistently on both sides of every ratio, so the ratio holds even
        # though the absolute figure is coarser.
        _ENCODER = None
        _TOKENIZER_NAME = "bytes/4-fallback"
    return _ENCODER


def count_tokens(text: str, name: str = "cl100k_base") -> int:
    """Token count under the configured encoding, or a bytes/4 estimate."""
    enc = _encoder(name)
    if enc is None:
        return (len(text.encode("utf-8")) + 3) // 4
    return len(enc.encode(text, disallowed_special=()))


def tokenizer_name() -> str:
    """Which tokenizer actually produced the counts in this process."""
    _encoder()
    return _TOKENIZER_NAME


# ---------------------------------------------------------------------------
# Running the cgh CLI under test (the worktree's own module, never a global).
# ---------------------------------------------------------------------------


def run_cgh_cli(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Invoke `python -m cgh <args>` from the repo root.

    Runs the cgh under test (this checkout), not any globally installed
    build. Paths to the untrusted clones are passed as arguments; the
    process never runs with its cwd inside a clone.
    """
    return subprocess.run(
        [sys.executable, "-m", "cgh", *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
        env={**os.environ, "NO_COLOR": "1"},
    )


def index_repo(checkout: Path) -> None:
    """Build the cgh graph for a checkout. Fails loudly on a non-zero exit.

    The checkout gets its own ``.cgh/`` first so cgh's root-walk stops there
    instead of climbing into an enclosing cgh repo (the harness clones live
    under this project's tree).
    """
    (checkout / ".cgh").mkdir(parents=True, exist_ok=True)
    proc = run_cgh_cli(
        ["index", "--root", str(checkout), "--force", "--no-claude-state"]
    )
    if proc.returncode != 0:
        raise BenchError(
            f"cgh index failed for {checkout} (exit {proc.returncode}):\n{proc.stderr}"
        )


# ---------------------------------------------------------------------------
# The grep corpus baseline, from the checkout's tracked files (git grep).
# ---------------------------------------------------------------------------


@dataclass
class GrepHit:
    path: str
    line: int


def git_grep(checkout: Path, pattern: str, word: bool = True) -> list[GrepHit]:
    """Fixed-string git grep over tracked files: the corpus an agent reads.

    Tracked-file scope matches what a developer greps, deterministically and
    without .gitignore ambiguity. ``-I`` skips binary files.
    """
    cmd = ["git", "-C", str(checkout), "grep", "-n", "-I", "-F"]
    if word:
        cmd.append("-w")
    cmd += ["-e", pattern]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_TIMEOUT)
    # git grep exits 1 when there are no matches: that is not an error here.
    if proc.returncode not in (0, 1):
        raise BenchError(f"git grep failed in {checkout}: {proc.stderr}")
    hits: list[GrepHit] = []
    for raw in proc.stdout.splitlines():
        # Format: path:lineno:code
        parts = raw.split(":", 2)
        if len(parts) < 3:
            continue
        path, lineno = parts[0], parts[1]
        if not lineno.isdigit():
            continue
        hits.append(GrepHit(path=path, line=int(lineno)))
    return hits


def _read_lines(checkout: Path, rel_path: str) -> list[str]:
    fp = (checkout / rel_path).resolve()
    # Keep reads inside the checkout even if git grep ever returned an odd path.
    if not fp.is_relative_to(checkout.resolve()):
        return []
    try:
        return fp.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def grep_corpus_tokens(
    checkout: Path, hits: list[GrepHit], tokenizer: str
) -> tuple[int, int]:
    """Token cost of the grep corpus, naive and tight.

    naive: every whole file that holds a hit (upper bound on what an agent
           without cgh would pull into context).
    tight: only +/-TIGHT_CONTEXT lines around each hit, overlapping windows
           merged per file (lower bound).
    """
    by_file: dict[str, list[int]] = {}
    for h in hits:
        by_file.setdefault(h.path, []).append(h.line)

    naive_tokens = 0
    tight_tokens = 0
    for path, lines in by_file.items():
        src = _read_lines(checkout, path)
        if not src:
            continue
        naive_tokens += count_tokens("\n".join(src), tokenizer)

        # Merge the +/-context windows so overlapping hits are not double-counted.
        windows = sorted(
            (max(1, ln - TIGHT_CONTEXT), min(len(src), ln + TIGHT_CONTEXT))
            for ln in lines
        )
        merged: list[tuple[int, int]] = []
        for start, end in windows:
            if merged and start <= merged[-1][1] + 1:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        snippet = "\n".join("\n".join(src[start - 1 : end]) for start, end in merged)
        tight_tokens += count_tokens(snippet, tokenizer)
    return naive_tokens, tight_tokens


# ---------------------------------------------------------------------------
# Aggregation: median-led, p25 / p75. Never a mean as the headline.
# ---------------------------------------------------------------------------


def _percentile(values: list[float], pct: float) -> float:
    """Linear-interpolation percentile over a sorted copy of ``values``."""
    if not values:
        return float("nan")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = pct / 100.0 * (len(ordered) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    frac = rank - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac


def summary(values: list[float]) -> dict[str, float | int]:
    """Median-led summary: n, median, p25, p75, min, max."""
    clean = [v for v in values if v == v]  # drop NaN
    return {
        "n": len(clean),
        "median": _percentile(clean, 50),
        "p25": _percentile(clean, 25),
        "p75": _percentile(clean, 75),
        "min": min(clean) if clean else float("nan"),
        "max": max(clean) if clean else float("nan"),
    }


def write_jsonl(path: str | Path, rows: list[dict[str, Any]]) -> None:
    """Write one JSON object per line, sorted keys for a stable diff."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    """Read a JSONL results file back into a list of objects."""
    out: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out
