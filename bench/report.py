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


def _language(name: str) -> str:
    """The language label a repo is published under (from repos.yaml)."""
    return repos_config()["repos"].get(name, {}).get("language", "unknown")


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
        "language": _language(name),
        "n_questions": len(rows),
        "n_correct": len(correct),
        "naive": summary(naive),
        "tight": summary(tight),
        "naive_ratios": naive,
        "tight_ratios": tight,
        "worst_tight": max(tight) if tight else float("nan"),
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# Tier 2 aggregation
# ---------------------------------------------------------------------------


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Micro (pooled counts) and macro (mean of per-PR) P/R/F1 for a set of
    PRs. Empty input yields NaN so an empty bucket reads as 'n/a', not 0."""
    n = len(rows)
    tp = sum(r["tp"] for r in rows)
    pred = sum(r["pred_count"] for r in rows)
    truth = sum(r["truth_count"] for r in rows)
    micro_p = tp / pred if pred else 0.0
    micro_r = tp / truth if truth else 0.0
    micro_f1 = (
        2 * micro_p * micro_r / (micro_p + micro_r) if (micro_p + micro_r) else 0.0
    )
    macro_p = (sum(r["precision"] for r in rows) / n) if n else float("nan")
    macro_r = (sum(r["recall"] for r in rows) / n) if n else float("nan")
    macro_f1 = (sum(r["f1"] for r in rows) / n) if n else float("nan")
    return {
        "n": n,
        "micro": {
            "precision": micro_p if n else float("nan"),
            "recall": micro_r if n else float("nan"),
            "f1": micro_f1 if n else float("nan"),
        },
        "macro": {"precision": macro_p, "recall": macro_r, "f1": macro_f1},
    }


def tier2_repo(name: str) -> dict[str, Any] | None:
    path = RESULTS_DIR / f"tier2_{name}.jsonl"
    if not path.exists():
        return None
    rows = read_jsonl(path)
    # Bucket by whether the seed has graph dependents. A seed with reverse
    # imports/calls gets a circular recall (1.0); a leaf or test seed has none
    # (circular n/a), so the graph predicts ~nothing and P=R=0. Splitting the
    # meaningful subset out is not dropping the losses: the full per-PR table
    # keeps every row.
    with_deps = [r for r in rows if r["recall_circular_sanity"] is not None]
    leaf = [r for r in rows if r["recall_circular_sanity"] is None]
    circ = [r["recall_circular_sanity"] for r in with_deps]
    return {
        "repo": name,
        "language": _language(name),
        "n_prs": len(rows),
        "all": _aggregate(rows),
        "with_deps": _aggregate(with_deps),
        "leaf": _aggregate(leaf),
        "circular_n": len(circ),
        "circular_min": min(circ) if circ else float("nan"),
        "circular_mean": (sum(circ) / len(circ)) if circ else float("nan"),
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _by_language(aggs: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    """Group aggregates by language, languages in first-seen order."""
    order: list[str] = []
    groups: dict[str, list[dict[str, Any]]] = {}
    for a in aggs:
        lang = a["language"]
        if lang not in groups:
            groups[lang] = []
            order.append(lang)
        groups[lang].append(a)
    return [(lang, groups[lang]) for lang in order]


def _tier1_language_table(aggs: list[dict[str, Any]]) -> str:
    """Per-language tier-1 summary: ratios pooled over every correct question of
    every repo in the language, led by the median."""
    out = [
        "| language | repos | questions (correct) | median ratio (naive) | "
        "p25-p75 (naive) | median ratio (tight) | p25-p75 (tight) | worst tight |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for lang, group in _by_language(aggs):
        naive = [r for a in group for r in a["naive_ratios"]]
        tight = [r for a in group for r in a["tight_ratios"]]
        ns, ts = summary(naive), summary(tight)
        n_correct = sum(a["n_correct"] for a in group)
        n_q = sum(a["n_questions"] for a in group)
        worst = max(tight) if tight else float("nan")
        out.append(
            f"| {lang} | {len(group)} | {n_q} ({n_correct}) | "
            f"{_fmt(ns['median'])} | {_fmt(ns['p25'])}-{_fmt(ns['p75'])} | "
            f"{_fmt(ts['median'])} | {_fmt(ts['p25'])}-{_fmt(ts['p75'])} | "
            f"{_fmt(worst)} |"
        )
    return "\n".join(out)


def _tier1_table(aggs: list[dict[str, Any]]) -> str:
    out = [
        "| repo | language | questions | correct | median ratio (naive) | "
        "p25-p75 (naive) | median ratio (tight) | p25-p75 (tight) | worst tight |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for a in aggs:
        n, t = a["naive"], a["tight"]
        out.append(
            f"| {a['repo']} | {a['language']} | {a['n_questions']} | "
            f"{a['n_correct']} | "
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


def _tier2_language_table(aggs: list[dict[str, Any]]) -> str:
    """Per-language tier-2 summary: recall over all PRs of the language, and
    over just the seeds-with-dependents subset (where the graph can predict)."""
    out = [
        "| language | repos | PRs | all micro R | all macro R | "
        "deps PRs | deps micro R | deps macro R |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for lang, group in _by_language(aggs):
        rows_all = [r for a in group for r in a["rows"]]
        rows_dep = [r for r in rows_all if r["recall_circular_sanity"] is not None]
        agg_all = _aggregate(rows_all)
        agg_dep = _aggregate(rows_dep)
        out.append(
            f"| {lang} | {len(group)} | {len(rows_all)} | "
            f"{_fmt(agg_all['micro']['recall'])} | "
            f"{_fmt(agg_all['macro']['recall'])} | {len(rows_dep)} | "
            f"{_fmt(agg_dep['micro']['recall'])} | "
            f"{_fmt(agg_dep['macro']['recall'])} |"
        )
    return "\n".join(out)


def _tier2_table(aggs: list[dict[str, Any]]) -> str:
    """All-PRs aggregate, recall first. F1 stays in the table but is no longer
    the lead statistic."""
    out = [
        "| repo | language | PRs | micro R | macro R | micro P | macro P | "
        "micro F1 | macro F1 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for a in aggs:
        mi, ma = a["all"]["micro"], a["all"]["macro"]
        out.append(
            f"| {a['repo']} | {a['language']} | {a['n_prs']} | {_fmt(mi['recall'])} | "
            f"{_fmt(ma['recall'])} | {_fmt(mi['precision'])} | "
            f"{_fmt(ma['precision'])} | {_fmt(mi['f1'])} | {_fmt(ma['f1'])} |"
        )
    return "\n".join(out)


def _tier2_split_table(aggs: list[dict[str, Any]]) -> str:
    """Per repo, the all-PRs aggregate split into the two seed buckets, recall
    first. The leaf/test bucket is a seed-selection artifact, labelled as such."""
    out = [
        "| repo | language | bucket | PRs | micro R | macro R | micro P | macro P | "
        "micro F1 | macro F1 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    labels = [
        ("with_deps", "seeds with dependents"),
        ("leaf", "leaf/test seeds (selection artifact)"),
    ]
    for a in aggs:
        for key, label in labels:
            agg = a[key]
            mi, ma = agg["micro"], agg["macro"]
            out.append(
                f"| {a['repo']} | {a['language']} | {label} | {agg['n']} | "
                f"{_fmt(mi['recall'])} | "
                f"{_fmt(ma['recall'])} | {_fmt(mi['precision'])} | "
                f"{_fmt(ma['precision'])} | {_fmt(mi['f1'])} | {_fmt(ma['f1'])} |"
            )
    return "\n".join(out)


def _tier2_detail(aggs: list[dict[str, Any]]) -> str:
    out = [
        "| repo | language | PR | seed | pred | truth | P | R | F1 | circular |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for a in aggs:
        for r in a["rows"]:
            circ = r["recall_circular_sanity"]
            out.append(
                f"| {a['repo']} | {a['language']} | {r['pr']} | `{r['seed']}` | "
                f"{r['pred_count']} | "
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


def _weak_spot_callout(t1: list[dict[str, Any]], t2: list[dict[str, Any]]) -> str:
    """A language-by-language honesty note, highlighting TypeScript (Vue), the
    announced weak spot, with its real numbers from this run."""
    lines: list[str] = []
    for lang, group in _by_language(t2):
        rows_all = [r for a in group for r in a["rows"]]
        rows_dep = [r for r in rows_all if r["recall_circular_sanity"] is not None]
        r_all = _aggregate(rows_all)["micro"]["recall"]
        r_dep = _aggregate(rows_dep)["micro"]["recall"] if rows_dep else float("nan")
        note = ""
        if lang == "typescript":
            note = (
                " — the announced weak spot. cgh locates TS symbols well (tier 1 "
                "above), but its cross-package TS import and call resolution is the "
                "least accurate of the set, so tier-2 recall is the lowest here. "
                "Much of the all-PRs figure is also a seed-selection artifact: the "
                "monorepo's largest-churn files are often `__tests__` specs with no "
                "dependents."
            )
        lines.append(
            f"- **{lang}**: tier-2 recall {_fmt(r_all, 2)} over all PRs, "
            f"{_fmt(r_dep, 2)} over seeds with dependents ({len(rows_dep)} of "
            f"{len(rows_all)} PRs).{note}"
        )
    return "\n".join(lines)


def render_markdown(t1: list[dict[str, Any]], t2: list[dict[str, Any]]) -> str:
    tok = tokenizer_name()
    repos = repos_config()["repos"]
    pins = "\n".join(f"- `{n}`: {s['url']} @ `{s['sha']}`" for n, s in repos.items())
    return f"""# cgh benchmark: multi-language repo set

A public, reproducible benchmark for **cgh**. It does not diff two full agent
runs: LLM non-determinism moves an identical arm by more than 20% at n=2-3, so a
before/after of whole runs measures noise. Instead it instruments real tool
usage on pinned public repos and reports deterministic numbers.

Results are published **by language**: a language where cgh is weaker is
measured and shown, never hidden. The announced weak spot is Vue/TypeScript
cross-package resolution, which this report calls out explicitly below.

The headline is **context-token efficiency** and, in tier 2, impact **recall**.
Billed API cost is deliberately not a headline: prompt cache dominates a real
session, so the dollar figure is close to a wash and would mislead.

Replay everything in one command (from the repo root):

```bash
python -m bench.run            # fetch + index + tier 1 + tier 2 + this report
python -m bench.run --tier 1   # tier 1 only
```

Tokenizer: `{tok}` (tiktoken, with a bytes/4 fallback when tiktoken is absent).
It is not Claude's tokenizer, so the ratios are **relative**, not a billable
figure.

Pinned corpus (8 repos across 5 languages):

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

By language (ratios pooled over every correct question of every repo):

{_tier1_language_table(t1)}

Per repo:

{_tier1_table(t1)}

Per-question detail (losses included):

{_tier1_detail(t1)}

The `tight` column is where the honesty lives: on a rare-keyword concept query a
+/-40-line grep is already small, so cgh's structured answer saves less and can
approach it. That is the weakest case for cgh and it is published alongside the
rest.

## Tier 2: impact recall (deterministic, no LLM)

cgh's own `build_impact_report` predicts the blast radius of a single seed file
over a read-only graph. The prediction (`impacted + tests_to_run`) is scored
against the **co-change ground truth**: the other code files the merged PR
actually touched. That ground truth is external to the graph.

**Read this tier by recall, not F1.** `build_impact_report` predicts a safety
superset on purpose: everything that transitively imports or calls the seed. So
a seed in a central module flags dozens of files while a PR touched a handful,
and precision is low by construction. That also makes F1 a poor summary here (it
punishes the intended superset), so F1 stays in the tables but is not the
headline. Recall is the metric that matters for a "what else should I look at"
tool: did the prediction contain the files the PR actually went on to change?

PRs are selected mechanically and committed as fixed numbers in
`bench/pr_truth/<repo>.yaml` (merged into the default branch, 2-8 changed code
files all of
indexed languages, the 10 most recent before the pinned SHA that pass; seed =
the modified file of highest churn, lexicographic tie-break; truth = the other
changed code files). Nothing here is hand-picked to flatter cgh.

By language (recall over all PRs, and over just the seeds that have graph
dependents):

{_tier2_language_table(t2)}

All PRs, per repo (recall first):

{_tier2_table(t2)}

### Split by seed: does the graph have anything to predict?

The highest-churn seed heuristic sometimes lands on a **leaf or test file**,
which has no reverse imports or calls, so the graph predicts ~nothing and
precision and recall are both zero. That is a **seed-selection artifact**, not a
cgh quality result, so the all-PRs row above understates the meaningful case.
The split below separates **seeds with dependents** (the graph can predict
something) from the leaf/test seeds, without dropping any PR: the full per-PR
table further down still carries every row, losses included.

{_tier2_split_table(t2)}

Full per-PR detail (every PR, nothing dropped):

{_tier2_detail(t2)}

### Circular recall (internal consistency check, NOT a quality claim)

`recall_circular_sanity` scores the same prediction against the graph's **own**
reverse imports of the seed. It is ~1.0 by construction and only confirms the
harness's set math lines up with the closure the product walks. It is **not** a
product-quality or F1 claim.

Circular recall this run: {_circular_line(t2)}.

## Where cgh is weaker (honest, by language)

{_weak_spot_callout(t1, t2)}

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

## Scope

This run covers tiers 1 and 2 over the full multi-language repo set (Python,
JavaScript, TypeScript, Go, Rust). It does **not** implement tier 3, a live
two-arm LLM comparison, which stays a separate task.
"""


def run_all() -> str:
    names = list(repos_config()["repos"])
    t1 = [a for a in (tier1_repo(n) for n in names) if a]
    t2 = [a for a in (tier2_repo(n) for n in names) if a]

    if t1:
        print("\n== Tier 1 by language ==")
        print(_tier1_language_table(t1))
        print("\n== Tier 1 by repo ==")
        print(_tier1_table(t1))
    if t2:
        print("\n== Tier 2 by language (recall) ==")
        print(_tier2_language_table(t2))
        print("\n== Tier 2 (all PRs, recall first) ==")
        print(_tier2_table(t2))
        print("\n== Tier 2 split by seed ==")
        print(_tier2_split_table(t2))
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
