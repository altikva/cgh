# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-17
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Tier 2 of the benchmark (deterministic, no LLM). Reuses cgh's own
#              build_impact_report over a read-only graph to predict the blast
#              radius of a seed file, then scores that prediction against real
#              merged-PR co-change (ground truth external to the graph).
#              Precision/recall/F1 are published micro and macro. PR selection
#              is mechanical (--select) and committed as fixed numbers so the
#              set cannot be cherry-picked. A circular recall (truth = the
#              graph's own reverse imports of the seed) is reported separately
#              as an internal consistency check, never as a quality claim.

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from bench._util import (
    BENCH_DIR,
    RESULTS_DIR,
    BenchError,
    index_repo,
    repo_checkout,
    repos_config,
    write_jsonl,
)

_TIMEOUT = 600

# Extensions cgh parses into the graph. A PR touching a code file outside this
# set is not a fair target, so it is excluded by selection.
INDEXED_EXTS = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".go",
    ".rs",
    ".java",
    ".vue",
    ".tf",
}
# What counts as a "code file" for the 2-8 rule. Broader than INDEXED_EXTS so a
# PR that also edits C/C++/Ruby/etc. is seen as touching non-indexed code and
# dropped, rather than silently judged on its Python subset.
CODE_EXTS = INDEXED_EXTS | {
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
    ".rb",
    ".php",
    ".cs",
    ".kt",
    ".swift",
    ".scala",
    ".m",
    ".mm",
    ".pyx",
}

DEFAULT_N = 10


# ---------------------------------------------------------------------------
# git plumbing over a clone
# ---------------------------------------------------------------------------


def _git(clone: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(clone), *args],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
    )
    if proc.returncode != 0:
        raise BenchError(f"git {' '.join(args)} failed in {clone}: {proc.stderr}")
    return proc.stdout


def _owner_repo(name: str) -> str:
    url = repos_config()["repos"][name]["url"].rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    parts = url.split("/")
    return f"{parts[-2]}/{parts[-1]}"


def _changed_code_files(
    clone: Path, parent: str, merge: str
) -> tuple[list[tuple[str, int]], dict[str, str]]:
    """Code files a PR changed, with churn and status, from git diff.

    Returns (``[(path, churn)]`` for code files, ``{path: status}``). ``churn``
    is additions+deletions; ``status`` is git's A/M/D/R letter.
    """
    status: dict[str, str] = {}
    for line in _git(clone, "diff", "--name-status", parent, merge).splitlines():
        cols = line.split("\t")
        if len(cols) < 2:
            continue
        code = cols[0][0]  # R100 -> R
        path = cols[-1]
        status[path] = code

    churn: dict[str, int] = {}
    for line in _git(clone, "diff", "--numstat", parent, merge).splitlines():
        cols = line.split("\t")
        if len(cols) < 3:
            continue
        adds, dels, path = cols[0], cols[1], cols[-1]
        a = int(adds) if adds.isdigit() else 0
        d = int(dels) if dels.isdigit() else 0
        churn[path] = a + d

    code = [(p, churn.get(p, 0)) for p in status if Path(p).suffix in CODE_EXTS]
    return code, status


# ---------------------------------------------------------------------------
# Mechanical PR selection (anti-cherry-pick) -> pr_truth/<repo>.yaml
# ---------------------------------------------------------------------------


def select_prs(name: str, n: int = DEFAULT_N) -> dict[str, Any]:
    """Pick the N most recent merged PRs before the pinned SHA that pass the
    mechanical filter, and write pr_truth/<name>.yaml."""
    cfg = repos_config()
    spec = cfg["repos"][name]
    pinned = spec["sha"]
    base = spec.get("base", "main")
    clone = repo_checkout(name)
    owner_repo = _owner_repo(name)

    raw = subprocess.run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            owner_repo,
            "--state",
            "merged",
            "--base",
            base,
            "--json",
            "number,mergedAt,mergeCommit",
            "--limit",
            "300",
        ],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
    )
    if raw.returncode != 0:
        raise BenchError(f"gh pr list failed for {owner_repo}: {raw.stderr}")
    prs = sorted(json.loads(raw.stdout), key=lambda p: p["mergedAt"], reverse=True)

    selected: list[dict[str, Any]] = []
    for pr in prs:
        if len(selected) >= n:
            break
        merge = (pr.get("mergeCommit") or {}).get("oid")
        if not merge:
            continue
        # Only PRs whose merge commit is an ancestor of the pinned SHA.
        anc = subprocess.run(
            ["git", "-C", str(clone), "merge-base", "--is-ancestor", merge, pinned],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
        )
        if anc.returncode != 0:
            continue
        try:
            parent = _git(clone, "rev-parse", f"{merge}^1").strip()
        except BenchError:
            continue

        code, status = _changed_code_files(clone, parent, merge)
        # Every touched code file must be an indexed language.
        if any(Path(p).suffix not in INDEXED_EXTS for p, _ in code):
            continue
        if not (2 <= len(code) <= 8):
            continue
        # Seed: a file that EXISTS at parent (modified, not added), highest
        # churn, lexicographic tie-break. Added files cannot seed a pre-change
        # graph, so they are only ever truth.
        seed_candidates = sorted(
            [(p, c) for p, c in code if status.get(p) == "M"],
            key=lambda pc: (-pc[1], pc[0]),
        )
        if not seed_candidates:
            continue
        seed = seed_candidates[0][0]
        truth = sorted(p for p, _ in code if p != seed and status.get(p) != "D")
        if not truth:
            continue
        selected.append(
            {
                "number": pr["number"],
                "merged_at": pr["mergedAt"],
                "merge_sha": merge,
                "parent_sha": parent,
                "seed": seed,
                "truth": truth,
            }
        )
        print(f"  [select] PR {pr['number']}: seed={seed} truth={len(truth)} file(s)")

    doc = {
        "repo": owner_repo,
        "pinned_sha": pinned,
        "selection": {
            "n": n,
            "rule": (
                "merged into the default branch, 2-8 changed code files all of indexed "
                "languages, N most recent before pinned_sha; seed = modified "
                "file of highest churn (lexicographic tie-break); truth = the "
                "other changed code files (co-change, external ground truth)."
            ),
        },
        "prs": selected,
    }
    out = BENCH_DIR / "pr_truth" / f"{name}.yaml"
    with open(out, "w", encoding="utf-8") as fh:
        yaml.safe_dump(doc, fh, sort_keys=False, default_flow_style=False)
    print(f"[select] wrote {out} ({len(selected)} PRs)")
    return doc


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def _impact_clone(name: str) -> Path:
    """A dedicated local clone tier 2 checks out PR parents in, so the pinned
    tier-1 index is never disturbed."""
    primary = repo_checkout(name)
    clone = primary.parent / f"{name}.impact"
    if not (clone / ".git").is_dir():
        subprocess.run(
            ["git", "clone", "--local", "--no-hardlinks", str(primary), str(clone)],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            check=True,
        )
    return clone


def _prf1(pred: set[str], truth: set[str]) -> dict[str, float]:
    tp = len(pred & truth)
    precision = tp / len(pred) if pred else 0.0
    recall = tp / len(truth) if truth else 0.0
    f1 = (
        (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    )
    return {"precision": precision, "recall": recall, "f1": f1, "tp": tp}


def _circular_recall(conn: Any, clone: Path, seed: str, pred: set[str]) -> float | None:
    """Recall against the graph's OWN reverse imports of the seed. Reuses
    cgh's reverse_import_bfs, so it checks the harness's set math wires up to
    the same closure the product walks; it is ~1.0 by construction, not a
    quality signal."""
    from cgh.analysis.impact import reverse_import_bfs

    abs_seed = str((clone / seed).resolve())
    reached, _ = reverse_import_bfs(conn, [abs_seed], max_depth=3)
    root = clone.resolve()
    circ: set[str] = set()
    for p in reached:
        try:
            circ.add(str(Path(p).resolve().relative_to(root)))
        except (ValueError, OSError):
            continue
    if not circ:
        return None
    return len(pred & circ) / len(circ)


def _predict_in_process(clone: Path, seed: str) -> tuple[set[str], float | None]:
    """Index the checkout and run build_impact_report; return (pred, circular).

    Runs in its own process (see ``_predict``). DuckDB refuses to reopen the
    same graph file path within one long-lived process, and every PR reuses the
    one ``.cgh/graph.duckdb`` path, so each prediction is isolated this way.
    """
    from cgh.analysis.impact import build_impact_report
    from cgh.core.db import get_readonly_connection

    shutil.rmtree(clone / ".cgh", ignore_errors=True)
    index_repo(clone)

    conn = get_readonly_connection(clone)
    if conn is None:
        raise BenchError(f"could not open graph read-only for {clone}")
    report = build_impact_report(conn, str(clone), [seed])
    pred = {e["file"] for e in report.get("impacted", [])}
    pred |= {t["file"] for t in report.get("tests_to_run", [])}
    circular = _circular_recall(conn, clone, seed, pred)
    return pred, circular


def _predict(clone: Path, seed: str) -> tuple[set[str], float | None]:
    """Predict in an isolated subprocess so DuckDB never sees the same graph
    file path twice in one process. The worker calls get_readonly_connection
    and build_impact_report directly; only the transport is a subprocess."""
    proc = subprocess.run(
        [sys.executable, "-m", "bench.impact_metric", "--predict", str(clone), seed],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    if proc.returncode != 0:
        raise BenchError(
            f"impact prediction failed for {clone} seed={seed}:\n{proc.stderr}"
        )
    # The worker prints a single JSON line last; ignore any index chatter above.
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")][-1]
    data = json.loads(line)
    circ = data["circular"]
    return set(data["pred"]), (None if circ is None else float(circ))


def evaluate(name: str) -> list[dict[str, Any]]:
    """Score every PR in pr_truth/<name>.yaml and write tier2_<name>.jsonl."""
    truth_path = BENCH_DIR / "pr_truth" / f"{name}.yaml"
    if not truth_path.exists():
        raise BenchError(
            f"{truth_path} missing; run `python -m bench.impact_metric --select {name}`."
        )
    doc = yaml.safe_load(truth_path.read_text(encoding="utf-8"))
    clone = _impact_clone(name)

    rows: list[dict[str, Any]] = []
    for pr in doc["prs"]:
        seed = pr["seed"]
        truth = set(pr["truth"])
        _git(clone, "checkout", "--force", pr["parent_sha"])
        pred, circular = _predict(clone, seed)
        scores = _prf1(pred, truth)
        row = {
            "tier": 2,
            "repo": name,
            "pr": pr["number"],
            "parent_sha": pr["parent_sha"],
            "seed": seed,
            "truth": sorted(truth),
            "pred_count": len(pred),
            "truth_count": len(truth),
            **scores,
            "recall_circular_sanity": circular,
        }
        rows.append(row)
        print(
            f"  [PR {pr['number']:>5}] P={scores['precision']:.2f} "
            f"R={scores['recall']:.2f} F1={scores['f1']:.2f} "
            f"pred={len(pred)} truth={len(truth)} "
            f"circular={circular if circular is None else round(circular, 3)}"
        )
    write_jsonl(RESULTS_DIR / f"tier2_{name}.jsonl", rows)
    return rows


def evaluate_all() -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for name in repos_config()["repos"]:
        print(f"[tier2] {name}")
        out[name] = evaluate(name)
    return out


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        if argv and argv[0] == "--predict":
            # Isolated worker: index + impact report for one (clone, seed).
            clone, seed = Path(argv[1]), argv[2]
            pred, circular = _predict_in_process(clone, seed)
            print(json.dumps({"pred": sorted(pred), "circular": circular}))
        elif argv and argv[0] == "--select":
            n = DEFAULT_N
            names = []
            i = 1
            while i < len(argv):
                if argv[i] == "-n":
                    n = int(argv[i + 1])
                    i += 2
                else:
                    names.append(argv[i])
                    i += 1
            for name in names or list(repos_config()["repos"]):
                print(f"[select] {name} (n={n})")
                select_prs(name, n)
        else:
            evaluate_all()
    except BenchError as exc:
        print(f"[tier2] ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
