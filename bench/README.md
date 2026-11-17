# cgh benchmark harness

Public, reproducible, deterministic. It instruments real cgh tool usage on
pinned public repos instead of diffing two whole agent runs (LLM
non-determinism makes a before/after of full runs measure noise at small n).

The headline metrics are **context-token efficiency** (tier 1) and **impact
precision/recall** (tier 2). Billed API cost is not a headline: with prompt
caching the dollar delta is close to a wash, so it would mislead.

## Replay in one command

From the repo root:

```bash
python -m bench.run              # fetch + index + tier 1 + tier 2 + report
python -m bench.run --tier 1     # tier 1 only
python -m bench.run --tier 2     # tier 2 only (uses the committed PR truth)
python -m bench.run --no-fetch   # reuse the existing .bench-cache/ checkouts
```

`tiktoken` gives real `cl100k_base` counts; without it the harness falls back to
a documented bytes/4 estimate (consistently on both sides of every ratio). To
run with it under uv:

```bash
uv run --with tiktoken python -m bench.run
```

The generated report lands in `BENCHMARK.md` at the repo root, and the raw
per-question / per-PR rows in `bench/results/*.jsonl` (committed).

## What each file is

| File | Role |
|---|---|
| `repos.yaml` | Pinned repos: URL + real SHA, and the tokenizer name. |
| `questions/<repo>.yaml` | Fixed tier-1 questions (locate / callers / fts). |
| `pr_truth/<repo>.yaml` | Mechanically selected merged PRs: number, parent SHA, seed, co-change truth. |
| `fetch.py` | Clone + checkout the pinned SHA, and **verify HEAD == SHA** (abort on drift). |
| `index.py` | Build the cgh graph for each pinned checkout. |
| `context_metric.py` | Tier 1: cgh answer tokens vs grep corpus (naive + tight). |
| `impact_metric.py` | Tier 2: `build_impact_report` prediction vs PR co-change; also `--select`. |
| `report.py` | Aggregate `results/*.jsonl` into tables and rewrite `BENCHMARK.md`. |
| `run.py` | Orchestrator. |
| `_util.py` | Shared helpers (paths, tokenizer, git-grep corpus, percentiles). |

## Re-selecting the tier-2 PRs

PR selection needs the `gh` CLI (authenticated) and network. It is a separate,
explicit step so a normal replay stays offline and deterministic:

```bash
python -m bench.impact_metric --select flask requests          # default N=10
python -m bench.impact_metric --select flask -n 15             # more PRs
```

This rewrites `pr_truth/<repo>.yaml`. The selection is mechanical (see the
`rule` field it writes) so the set cannot be cherry-picked; commit the result.

## Answer formats (tier 1)

`search` emits `--json`; `lookup` and `callers` have no `--json` flag in this
cgh line, so their compact text stdout is what gets tokenised. In every case it
is the actual bytes a cgh-equipped agent reads to answer. The harness adds no
product logic: it only calls existing cgh code (`build_impact_report`,
`get_readonly_connection`, `reverse_import_bfs`) and the cgh CLI.

## Safety

Cloned repos under `.bench-cache/` are untrusted data. Each lives in its own
directory, paths are passed as arguments, and nothing inside them is executed:
the harness only clones, checks out, and parses them through cgh's indexer.
`.bench-cache/` is gitignored.

## Scope

Phase 1 validates tiers 1 and 2 on `pallets/flask` and `psf/requests`. It does
not run the full repo set and does not implement tier 3 (a live two-arm LLM
comparison).
