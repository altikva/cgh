# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-17
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Tier 1 of the benchmark (deterministic, no LLM). For each fixed
#              question, measures the context tokens of the cgh CLI answer
#              against the grep corpus a cgh-less agent would read, published as
#              a naive (whole hit files) and tight (+/-40 lines) interval. The
#              ratio is reported only over questions the cgh answer gets right,
#              so a wrong-but-cheap answer never flatters the tool.

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from bench._util import (
    BENCH_DIR,
    RESULTS_DIR,
    BenchError,
    count_tokens,
    git_grep,
    grep_corpus_tokens,
    load_yaml,
    repo_checkout,
    repos_config,
    run_cgh_cli,
    tokenizer_name,
    write_jsonl,
)


def _cgh_call(cmd: str, arg: str, checkout: Path) -> list[str]:
    """Argument vector for the cgh CLI call that answers a question kind."""
    root = str(checkout)
    if cmd == "lookup":
        return ["lookup", arg, "--root", root]
    if cmd == "callers":
        return ["callers", arg, "--root", root]
    if cmd == "search":
        # --json is the only structured CLI answer among the three; lookup and
        # callers have no --json flag in this cgh line, so their compact text
        # stdout is what is measured (documented in bench/README.md).
        return ["search", "--text", arg, "--json", "--root", root]
    raise BenchError(f"unknown question cmd: {cmd}")


def run_question(q: dict[str, Any], checkout: Path, tokenizer: str) -> dict[str, Any]:
    """Measure one question: cgh answer tokens vs grep corpus tokens."""
    proc = run_cgh_cli(_cgh_call(q["cmd"], q["arg"], checkout))
    cgh_stdout = proc.stdout
    tokens_cgh = count_tokens(cgh_stdout, tokenizer)
    answer_correct = q["expect"] in cgh_stdout

    hits = git_grep(checkout, q["grep_pattern"], word=q.get("word", True))
    naive, tight = grep_corpus_tokens(checkout, hits, tokenizer)

    ratio_naive = tokens_cgh / naive if naive else None
    ratio_tight = tokens_cgh / tight if tight else None

    return {
        "tier": 1,
        "q_id": q["id"],
        "kind": q["kind"],
        "cmd": q["cmd"],
        "arg": q["arg"],
        "grep_pattern": q["grep_pattern"],
        "grep_hit_files": len({h.path for h in hits}),
        "grep_hit_lines": len(hits),
        "tokens_cgh": tokens_cgh,
        "tokens_grep_naive": naive,
        "tokens_grep_tight": tight,
        "ratio_naive": ratio_naive,
        "ratio_tight": ratio_tight,
        "answer_correct": answer_correct,
        "cgh_returncode": proc.returncode,
    }


def run_repo(name: str, tokenizer: str) -> list[dict[str, Any]]:
    """Run every tier-1 question for one repo."""
    checkout = repo_checkout(name)
    if not (checkout / ".cgh").is_dir():
        raise BenchError(
            f"{checkout} is not indexed; run `python -m bench.index` first."
        )
    spec = load_yaml(BENCH_DIR / "questions" / f"{name}.yaml")
    rows: list[dict[str, Any]] = []
    for q in spec["questions"]:
        row = run_question(q, checkout, tokenizer)
        rows.append(row)
        flag = "ok " if row["answer_correct"] else "MISS"
        rn = row["ratio_naive"]
        ratio_txt = f"{rn:.3f}" if rn is not None else "n/a"
        print(
            f"  [{flag}] {row['q_id']:<36} cgh={row['tokens_cgh']:>6} "
            f"grep_naive={row['tokens_grep_naive']:>7} ratio_naive={ratio_txt}"
        )
    return rows


def run_all() -> dict[str, list[dict[str, Any]]]:
    """Run tier 1 for every repo and write results/tier1_<repo>.jsonl."""
    cfg = repos_config()
    tokenizer = cfg.get("tokenizer", "cl100k_base")
    out: dict[str, list[dict[str, Any]]] = {}
    for name in cfg["repos"]:
        print(f"[tier1] {name} (tokenizer={tokenizer_name()})")
        rows = run_repo(name, tokenizer)
        write_jsonl(RESULTS_DIR / f"tier1_{name}.jsonl", rows)
        out[name] = rows
    return out


def main(argv: list[str] | None = None) -> int:
    try:
        run_all()
    except BenchError as exc:
        print(f"[tier1] ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
