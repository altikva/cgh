# cgh benchmark: phase 1

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

Tokenizer: `cl100k_base` (tiktoken, with a bytes/4 fallback when tiktoken is absent).
It is not Claude's tokenizer, so the ratios are **relative**, not a billable
figure.

Pinned corpus (phase 1 validates two small Python repos):

- `flask`: https://github.com/pallets/flask @ `49943b5a94413a530761350f04e896f76e8d620e`
- `requests`: https://github.com/psf/requests @ `3c7baaa05ab4aa49cc819ce3a819d260de19c157`

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

| repo | questions | correct | median ratio (naive) | p25-p75 (naive) | median ratio (tight) | p25-p75 (tight) | worst tight |
|---|---|---|---|---|---|---|---|
| flask | 9 | 9 | 0.000 | 0.000-0.005 | 0.001 | 0.001-0.014 | 0.631 |
| requests | 9 | 9 | 0.001 | 0.000-0.002 | 0.004 | 0.001-0.021 | 0.206 |

Per-question detail (losses included):

| q_id | kind | correct | tokens cgh | tokens grep (naive) | tokens grep (tight) | ratio naive | ratio tight |
|---|---|---|---|---|---|---|---|
| flask-locate-flask | locate | yes | 28 | 246079 | 174278 | 0.0001 | 0.0002 |
| flask-locate-blueprint | locate | yes | 35 | 118302 | 40394 | 0.0003 | 0.0009 |
| flask-locate-send-file | locate | yes | 16 | 53574 | 17806 | 0.0003 | 0.0009 |
| flask-locate-jsonify | locate | yes | 18 | 71982 | 20920 | 0.0003 | 0.0009 |
| flask-locate-config | locate | yes | 15 | 45010 | 12672 | 0.0003 | 0.0012 |
| flask-callers-url-for | callers | yes | 681 | 138516 | 47658 | 0.0049 | 0.0143 |
| flask-callers-get-flashed-messages | callers | yes | 52 | 68546 | 9906 | 0.0008 | 0.0052 |
| flask-fts-session-serializer | fts | yes | 2312 | 6018 | 3664 | 0.3842 | 0.6310 |
| flask-fts-send-attachment | fts | yes | 2139 | 27008 | 7251 | 0.0792 | 0.2950 |
| requests-locate-session | locate | yes | 16 | 80149 | 36793 | 0.0002 | 0.0004 |
| requests-locate-httpadapter | locate | yes | 17 | 75731 | 14475 | 0.0002 | 0.0012 |
| requests-locate-preparedrequest | locate | yes | 16 | 95814 | 27653 | 0.0002 | 0.0006 |
| requests-locate-httpbasicauth | locate | yes | 17 | 24749 | 4118 | 0.0007 | 0.0041 |
| requests-locate-extract-cookies | locate | yes | 20 | 21039 | 4987 | 0.0010 | 0.0040 |
| requests-callers-resolve-redirects | callers | yes | 131 | 58172 | 6141 | 0.0023 | 0.0213 |
| requests-callers-to-key-val-list | callers | yes | 79 | 33010 | 4119 | 0.0024 | 0.0192 |
| requests-fts-transport-adapter | fts | yes | 2083 | 75731 | 19438 | 0.0275 | 0.1072 |
| requests-fts-encode-multipart | fts | yes | 2010 | 85232 | 9758 | 0.0236 | 0.2060 |

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

| repo | PRs | micro P | micro R | micro F1 | macro P | macro R | macro F1 |
|---|---|---|---|---|---|---|---|
| flask | 10 | 0.031 | 0.382 | 0.058 | 0.019 | 0.575 | 0.036 |
| requests | 10 | 0.070 | 0.500 | 0.123 | 0.036 | 0.467 | 0.066 |

Precision is low **by design**: `build_impact_report` predicts a safety
superset (everything that transitively imports or calls the seed), so a seed in
a central module flags dozens of files while a PR touched a handful. Recall is
the number that matters for a "what else should I look at" tool, and it is high
on seeds that have dependents. Seeds that are leaf or test files have no
dependents, so the graph predicts little and both numbers collapse to zero;
those PRs are kept in the set, not dropped.

Per-PR detail:

| repo | PR | seed | pred | truth | P | R | F1 | circular |
|---|---|---|---|---|---|---|---|---|
| flask | 6168 | `tests/test_appctx.py` | 0 | 6 | 0.00 | 0.00 | 0.00 | n/a |
| flask | 6133 | `src/flask/sansio/scaffold.py` | 66 | 1 | 0.02 | 1.00 | 0.03 | 1.00 |
| flask | 6096 | `tests/test_testing.py` | 0 | 3 | 0.00 | 0.00 | 0.00 | n/a |
| flask | 5928 | `tests/test_helpers.py` | 0 | 7 | 0.00 | 0.00 | 0.00 | n/a |
| flask | 5917 | `tests/test_basic.py` | 0 | 4 | 0.00 | 0.00 | 0.00 | n/a |
| flask | 5899 | `src/flask/app.py` | 73 | 1 | 0.01 | 1.00 | 0.03 | 1.00 |
| flask | 5898 | `src/flask/helpers.py` | 73 | 1 | 0.01 | 1.00 | 0.03 | 1.00 |
| flask | 5818 | `src/flask/app.py` | 73 | 4 | 0.05 | 1.00 | 0.10 | 1.00 |
| flask | 5736 | `src/flask/sansio/blueprints.py` | 66 | 3 | 0.05 | 1.00 | 0.09 | 1.00 |
| flask | 5731 | `src/flask/typing.py` | 64 | 4 | 0.05 | 0.75 | 0.09 | 1.00 |
| requests | 7505 | `tests/test_requests.py` | 1 | 2 | 0.00 | 0.00 | 0.00 | n/a |
| requests | 7502 | `tests/test_requests.py` | 1 | 1 | 0.00 | 0.00 | 0.00 | n/a |
| requests | 7496 | `src/requests/__init__.py` | 20 | 3 | 0.10 | 0.67 | 0.17 | 1.00 |
| requests | 7441 | `src/requests/models.py` | 20 | 1 | 0.05 | 1.00 | 0.10 | 1.00 |
| requests | 7431 | `src/requests/_types.py` | 19 | 2 | 0.11 | 1.00 | 0.19 | 1.00 |
| requests | 7433 | `tests/test_requests.py` | 1 | 1 | 0.00 | 0.00 | 0.00 | n/a |
| requests | 7427 | `tests/test_utils.py` | 0 | 1 | 0.00 | 0.00 | 0.00 | n/a |
| requests | 7426 | `src/requests/_types.py` | 19 | 1 | 0.05 | 1.00 | 0.10 | 1.00 |
| requests | 7425 | `src/requests/_types.py` | 19 | 1 | 0.05 | 1.00 | 0.10 | 1.00 |
| requests | 7315 | `tests/test_adapters.py` | 0 | 1 | 0.00 | 0.00 | 0.00 | n/a |

### Circular recall (internal consistency check, NOT a quality claim)

`recall_circular_sanity` scores the same prediction against the graph's **own**
reverse imports of the seed. It is ~1.0 by construction and only confirms the
harness's set math lines up with the closure the product walks. It is **not** a
product-quality or F1 claim.

Circular recall this run: flask: 1.000 over 6 seed(s) with reverse imports; requests: 1.000 over 5 seed(s) with reverse imports.

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
