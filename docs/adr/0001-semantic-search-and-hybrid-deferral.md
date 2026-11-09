# ADR-0001: Semantic search shipped; hybrid fusion and graph-boost deferred

Status: accepted (2026-11-08)

## Context

cgh searched code only lexically (SQLite FTS5 BM25 + trigram). We added
semantic search: a lexical embedder (pure-numpy feature hashing, offline, in
the standalone binary) and an opt-in neural embedder (fastembed / bge-small,
about 90 MB, the `cgh[neural]` extra), both behind `cgh semantic` and the
federated MCP tool `semantic_search`. The roadmap's remaining items were a
weighted hybrid fusion (BM25 + vector) and a graph-boost re-ranker.

A proof of concept measured retrieval quality on this repo:

| ranker | R@1 | R@5 | R@10 | MRR |
|---|---|---|---|---|
| BM25 only | 0.67 | 0.73 | 0.87 | 0.722 |
| neural only | 0.73 | 0.87 | 0.87 | 0.804 |
| hybrid RRF 0.5/0.5 | 0.73 | 0.87 | 0.87 | 0.766 |
| hybrid RRF 0.3/0.7 | 0.67 | 0.80 | 0.93 | 0.740 |
| hybrid RRF 0.2/0.8 | 0.67 | 0.80 | 0.93 | 0.749 |

HONESTY CAVEAT: this is 15 queries, all phrased "by concept", over 2266
symbols. Gaps of about 0.04 MRR on 15 queries are not statistically decisive,
and the sample is biased toward the neural embedder's home turf. It shows
direction, not proof. No hybrid weighting beat pure neural on MRR; hybrid only
widened deep recall (R@10). The same weak benchmark that argues against hybrid
also does not, on its own, justify the 90 MB neural dependency against FTS5.

## Decision

DECISION: Close the retrieval work with `semantic_search` (lexical + neural)
as shipped. Defer hybrid fusion and graph-boost to the backlog as measured
probes, not roadmap promises.

- No `hybrid_search` MCP tool. If hybrid is ever wanted, it ships as a ranking
  mode (a parameter) on the existing `semantic_search`, never a new tool, so
  it costs no per-request schema and respects the tool-count reduction work
  (issue #527).
- Graph-boost (re-rank by call-graph proximity) stays a probe: a free-text
  query has no obvious symbol anchor to boost from, so the text-to-graph
  anchor is an unsolved design problem, not a late feature.

## Gate for revisiting

INVARIANT: no hybrid and no graph-boost until a real evaluation set exists,
at least 60 queries, mixed keyword and concept, not concept-only. Build it
from `call_log` (the queries agents already issue through the MCP tools), not
from a future data-collection project. The same set re-validates the neural
embedder against FTS5; if neural does not clearly win there, the 90 MB
dependency is itself up for review.

## Consequences

- The retrieval chantier is done for now; the moat is the graph
  (find_callers, impact_report, dead-code, federation), and the maintainer's
  time is protected for it.
- The roadmap item reads "decided with evidence", not "incomplete".
- The decision is reversible the day the eval set lands.
