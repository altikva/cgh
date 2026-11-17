# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-17
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Aggregate the committed results/*.jsonl into tables and (re)write
#              the repo-root BENCHMARK.md with the real phase-1 numbers. Tier 1
#              is led by the median ratio (p25/p75) over correct answers, naive
#              and tight; tier 2 reports micro and macro precision/recall/F1 and
#              the circular-recall consistency check. The prose (method, honest
#              caveats) lives here so a regeneration keeps it.

from __future__ import annotations

from typing import Any

from bench._util import (
    REPO_ROOT,
    RESULTS_DIR,
    read_jsonl,
    repos_config,
    summary,
    tokenizer_name,
)


def _fmt(x: float, nd: int = 3) -> str:
    return "n/a" if x != x else f"{x:.{nd}f}"


# ---------------------------------------------------------------------------
# Tier 1 aggregation
# ---------------------------------------------------------------------------


def tier1_repo(name: str) -> dict[str, Any] | None:
    path = RESULTS_DIR / f"tier1_{name}.jsonl"
    if not path.exists():
        return None
    rows = read_jsonl(path)
    correct = [r for r in rows if r["answer_correct"]]
    naive = [r["ratio_naive"] for r in correct if r["ratio_naive"] is not None]
    tight = [r["ratio_tight"] for r in correct if r["ratio_tight"] is not None]
    return {
        "repo": name,
        "n_questions": len(rows),
        "n_correct": len(correct),
        "naive": summary(naive),
        "tight": summary(tight),
        "worst_tight": max(tight) if tight else float("nan"),
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# Tier 2 aggregation
# ---------------------------------------------------------------------------


def tier2_repo(name: str) -> dict[str, Any] | None:
    path = RESULTS_DIR / f"tier2_{name}.jsonl"
    if not path.exists():
        return None
    rows = read_jsonl(path)
    tp = sum(r["tp"] for r in rows)
    pred = sum(r["pred_count"] for r in rows)
    truth = sum(r["truth_count"] for r in rows)
    micro_p = tp / pred if pred else 0.0
    micro_r = tp / truth if truth else 0.0
    micro_f1 = (
        2 * micro_p * micro_r / (micro_p + micro_r) if (micro_p + micro_r) else 0.0
    )
    macro_p = sum(r["precision"] for r in rows) / len(rows)
    macro_r = sum(r["recall"] for r in rows) / len(rows)
    macro_f1 = sum(r["f1"] for r in rows) / len(rows)
    circ = [
        r["recall_circular_sanity"]
        for r in rows
        if r["recall_circular_sanity"] is not None
    ]
    return {
        "repo": name,
        "n_prs": len(rows),
        "micro": {"precision": micro_p, "recall": micro_r, "f1": micro_f1},
        "macro": {"precision": macro_p, "recall": macro_r, "f1": macro_f1},
        "circular_n": len(circ),
        "circular_min": min(circ) if circ else float("nan"),
        "circular_mean": (sum(circ) / len(circ)) if circ else float("nan"),
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _tier1_table(aggs: list[dict[str, Any]]) -> str:
    out = [
        "| repo | questions | correct | median ratio (naive) | p25-p75 (naive) | "
        "median ratio (tight) | p25-p75 (tight) | worst tight |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for a in aggs:
        n, t = a["naive"], a["tight"]
        out.append(
            f"| {a['repo']} | {a['n_questions']} | {a['n_correct']} | "
            f"{_fmt(n['median'])} | {_fmt(n['p25'])}-{_fmt(n['p75'])} | "
            f"{_fmt(t['median'])} | {_fmt(t['p25'])}-{_fmt(t['p75'])} | "
            f"{_fmt(a['worst_tight'])} |"
        )
    return "\n".join(out)


def _tier1_detail(aggs: list[dict[str, Any]]) -> str:
    out = [
        "| q_id | kind | correct | tokens cgh | tokens grep (naive) | "
        "tokens grep (tight) | ratio naive | ratio tight |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for a in aggs:
        for r in a["rows"]:
            out.append(
                f"| {r['q_id']} | {r['kind']} | "
                f"{'yes' if r['answer_correct'] else 'NO'} | "
                f"{r['tokens_cgh']} | {r['tokens_grep_naive']} | "
                f"{r['tokens_grep_tight']} | "
                f"{_fmt(r['ratio_naive'], 4)} | {_fmt(r['ratio_tight'], 4)} |"
            )
    return "\n".join(out)


def _tier2_table(aggs: list[dict[str, Any]]) -> str:
    out = [
        "| repo | PRs | micro P | micro R | micro F1 | macro P | macro R | macro F1 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for a in aggs:
        mi, ma = a["micro"], a["macro"]
        out.append(
            f"| {a['repo']} | {a['n_prs']} | {_fmt(mi['precision'])} | "
            f"{_fmt(mi['recall'])} | {_fmt(mi['f1'])} | {_fmt(ma['precision'])} | "
            f"{_fmt(ma['recall'])} | {_fmt(ma['f1'])} |"
        )
    return "\n".join(out)


def _tier2_detail(aggs: list[dict[str, Any]]) -> str:
    out = [
        "| repo | PR | seed | pred | truth | P | R | F1 | circular |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for a in aggs:
        for r in a["rows"]:
            circ = r["recall_circular_sanity"]
            out.append(
                f"| {a['repo']} | {r['pr']} | `{r['seed']}` | {r['pred_count']} | "
                f"{r['truth_count']} | {_fmt(r['precision'], 2)} | "
                f"{_fmt(r['recall'], 2)} | {_fmt(r['f1'], 2)} | "
                f"{'n/a' if circ is None else _fmt(circ, 2)} |"
            )
    return "\n".join(out)


def _circular_line(aggs: list[dict[str, Any]]) -> str:
    bits = []
    for a in aggs:
        if a["circular_n"]:
            bits.append(
                f"{a['repo']}: {a['circular_mean']:.3f} over {a['circular_n']} "
                f"seed(s) with reverse imports"
            )
        else:
            bits.append(f"{a['repo']}: no seed had reverse imports")
    return "; ".join(bits)


def render_markdown(t1: list[dict[str, Any]], t2: list[dict[str, Any]]) -> str:
    tok = tokenizer_name()
    repos = repos_config()["repos"]
    pins = "\n".join(f"- `{n}`: {s['url']} @ `{s['sha']}`" for n, s in repos.items())
    return f"""# cgh benchmark: phase 1

A public, reproducible benchmark for **cgh**. It does not diff two full agent
runs: LLM non-determinism moves an identical arm by more than 20% at n=2-3, so a
before/after of whole runs measures noise. Instead it instruments real tool
usage on pinned public repos and reports deterministic numbers.

The headline is **context-token efficiency** and, in tier 2, impact
precision/recall. Billed API cost is deliberately not a headline: prompt cache
dominates a real session, so the dollar figure is close to a wash and would
mislead.

Replay everything in one command (from the repo root):

```bash
python -m bench.run            # fetch + index + tier 1 + tier 2 + this report
python -m bench.run --tier 1   # tier 1 only
```

Tokenizer: `{tok}` (tiktoken, with a bytes/4 fallback when tiktoken is absent).
It is not Claude's tokenizer, so the ratios are **relative**, not a billable
figure.

Pinned corpus (phase 1 validates two small Python repos):

{pins}

## Tier 1: context tokens per question (deterministic, no LLM)

For each fixed question the cgh CLI answer is tokenised and compared to the grep
corpus a cgh-less agent would read for the same question, found with a
fixed-string `git grep` over tracked files. The grep baseline is published as an
interval, never one flattering number:

- **naive**: every whole file that holds a hit (upper bound on what gets pulled
  into context).
- **tight**: only +/-40 lines around each hit (lower bound).

`ratio = tokens_cgh / tokens_grep`; lower means cgh pulls in less context. The
ratio is aggregated **only over questions the cgh answer gets right**, led by the
median with the p25-p75 spread.

{_tier1_table(t1)}

Per-question detail (losses included):

{_tier1_detail(t1)}

The `tight` column is where the honesty lives: on a rare-keyword concept query a
+/-40-line grep is already small, so cgh's structured answer saves less and can
approach it. That is the weakest case for cgh and it is published alongside the
rest.

## Tier 2: impact precision/recall (deterministic, no LLM)

cgh's own `build_impact_report` predicts the blast radius of a single seed file
over a read-only graph. The prediction (`impacted + tests_to_run`) is scored
against the **co-change ground truth**: the other code files the merged PR
actually touched. That ground truth is external to the graph.

PRs are selected mechanically and committed as fixed numbers in
`bench/pr_truth/<repo>.yaml` (merged into main, 2-8 changed code files all of
indexed languages, the 10 most recent before the pinned SHA that pass; seed =
the modified file of highest churn, lexicographic tie-break; truth = the other
changed code files). Nothing here is hand-picked to flatter cgh.

{_tier2_table(t2)}

Precision is low **by design**: `build_impact_report` predicts a safety
superset (everything that transitively imports or calls the seed), so a seed in
a central module flags dozens of files while a PR touched a handful. Recall is
the number that matters for a "what else should I look at" tool, and it is high
on seeds that have dependents. Seeds that are leaf or test files have no
dependents, so the graph predicts little and both numbers collapse to zero;
those PRs are kept in the set, not dropped.

Per-PR detail:

{_tier2_detail(t2)}

### Circular recall (internal consistency check, NOT a quality claim)

`recall_circular_sanity` scores the same prediction against the graph's **own**
reverse imports of the seed. It is ~1.0 by construction and only confirms the
harness's set math lines up with the closure the product walks. It is **not** a
product-quality or F1 claim.

Circular recall this run: {_circular_line(t2)}.

## Known limitations

- **PR co-change is noisy ground truth.** Files change together for reasons the
  import/call graph cannot see (a release bump, a docs sweep, an unrelated
  drive-by). Recall against it understates a correct prediction whenever a PR
  bundled unrelated edits.
- **impact_report predicts a superset on purpose.** Precision below 1 is the
  expected, intended behaviour of a safety-oriented blast-radius tool, not a
  defect.
- **The grep baseline is a proxy.** A real agent greps more cleverly than a
  fixed pattern and reads less than a whole file, but also chains several
  searches. The naive-to-tight interval brackets that; neither endpoint is "the"
  number.
- **Billed cost is a near wash.** With prompt caching the dollar delta between
  arms is small and cache-state dependent, so it is not featured.
- **The tokenizer is not Claude's.** Ratios are relative, not billable token
  counts.
- **This is a self-reported benchmark.** Everything is pinned and the harness is
  public and replayable, but it is not an independent audit, and the tier-1
  questions were written by the author. Tier 2 mitigates author bias by deriving
  its targets mechanically from real PRs, and the full set is published,
  including the losses.

## Phase scope

Phase 1 validates the harness (tiers 1 and 2) on two small Python repos. It does
**not** run the full repo set and does **not** implement tier 3 (a live
two-arm LLM comparison).
"""


def run_all() -> str:
    names = list(repos_config()["repos"])
    t1 = [a for a in (tier1_repo(n) for n in names) if a]
    t2 = [a for a in (tier2_repo(n) for n in names) if a]

    if t1:
        print("\n== Tier 1 ==")
        print(_tier1_table(t1))
    if t2:
        print("\n== Tier 2 ==")
        print(_tier2_table(t2))
        print("circular sanity:", _circular_line(t2))

    md = render_markdown(t1, t2)
    out = REPO_ROOT / "BENCHMARK.md"
    out.write_text(md, encoding="utf-8")
    print(f"\n[report] wrote {out}")
    return md


def main(argv: list[str] | None = None) -> int:
    run_all()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
