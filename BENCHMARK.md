# cgh benchmark: multi-language repo set

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

Tokenizer: `cl100k_base` (tiktoken, with a bytes/4 fallback when tiktoken is absent).
It is not Claude's tokenizer, so the ratios are **relative**, not a billable
figure.

Pinned corpus (8 repos across 5 languages):

- `flask`: https://github.com/pallets/flask @ `49943b5a94413a530761350f04e896f76e8d620e`
- `requests`: https://github.com/psf/requests @ `3c7baaa05ab4aa49cc819ce3a819d260de19c157`
- `fastapi`: https://github.com/fastapi/fastapi @ `b2831196bfee3eb9ce303a3c6440f2a104832954`
- `django`: https://github.com/django/django @ `102c3c327d4974a58883b47a68f8d85da0be3648`
- `express`: https://github.com/expressjs/express @ `43fca52d28a422c8be48fd489cbd4f2d6152bab6`
- `vue-core`: https://github.com/vuejs/core @ `2e2f7c6bef54a85a3ce8db768b11c5562385b65d`
- `gin`: https://github.com/gin-gonic/gin @ `0f09c3a9b4626d9fe9979cf4d0521d6ae32ab646`
- `ripgrep`: https://github.com/BurntSushi/ripgrep @ `178cc027e283fd965cb72d0a7395b219eada170c`

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

| language | repos | questions (correct) | median ratio (naive) | p25-p75 (naive) | median ratio (tight) | p25-p75 (tight) | worst tight |
|---|---|---|---|---|---|---|---|
| python | 4 | 35 (35) | 0.001 | 0.000-0.004 | 0.004 | 0.001-0.020 | 0.538 |
| javascript | 1 | 8 (7) | 0.001 | 0.000-0.013 | 0.006 | 0.003-0.033 | 0.043 |
| typescript | 1 | 9 (9) | 0.000 | 0.000-0.000 | 0.000 | 0.000-0.001 | 0.119 |
| go | 1 | 8 (8) | 0.001 | 0.000-0.013 | 0.003 | 0.001-0.038 | 0.079 |
| rust | 1 | 8 (8) | 0.001 | 0.001-0.002 | 0.005 | 0.003-0.009 | 0.046 |

Per repo:

| repo | language | questions | correct | median ratio (naive) | p25-p75 (naive) | median ratio (tight) | p25-p75 (tight) | worst tight |
|---|---|---|---|---|---|---|---|---|
| flask | python | 9 | 9 | 0.000 | 0.000-0.005 | 0.001 | 0.001-0.014 | 0.538 |
| requests | python | 9 | 9 | 0.001 | 0.000-0.002 | 0.004 | 0.001-0.021 | 0.171 |
| fastapi | python | 8 | 8 | 0.003 | 0.002-0.007 | 0.007 | 0.006-0.020 | 0.292 |
| django | python | 9 | 9 | 0.000 | 0.000-0.001 | 0.002 | 0.000-0.003 | 0.390 |
| express | javascript | 8 | 7 | 0.001 | 0.000-0.013 | 0.006 | 0.003-0.033 | 0.043 |
| vue-core | typescript | 9 | 9 | 0.000 | 0.000-0.000 | 0.000 | 0.000-0.001 | 0.119 |
| gin | go | 8 | 8 | 0.001 | 0.000-0.013 | 0.003 | 0.001-0.038 | 0.079 |
| ripgrep | rust | 8 | 8 | 0.001 | 0.001-0.002 | 0.005 | 0.003-0.009 | 0.046 |

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
| flask-fts-session-serializer | fts | yes | 1972 | 6018 | 3664 | 0.3277 | 0.5382 |
| flask-fts-send-attachment | fts | yes | 1799 | 27008 | 7251 | 0.0666 | 0.2481 |
| requests-locate-session | locate | yes | 16 | 80149 | 36793 | 0.0002 | 0.0004 |
| requests-locate-httpadapter | locate | yes | 17 | 75731 | 14475 | 0.0002 | 0.0012 |
| requests-locate-preparedrequest | locate | yes | 16 | 95814 | 27653 | 0.0002 | 0.0006 |
| requests-locate-httpbasicauth | locate | yes | 17 | 24749 | 4118 | 0.0007 | 0.0041 |
| requests-locate-extract-cookies | locate | yes | 20 | 21039 | 4987 | 0.0010 | 0.0040 |
| requests-callers-resolve-redirects | callers | yes | 131 | 58172 | 6141 | 0.0023 | 0.0213 |
| requests-callers-to-key-val-list | callers | yes | 79 | 33010 | 4119 | 0.0024 | 0.0192 |
| requests-fts-transport-adapter | fts | yes | 1743 | 75731 | 19438 | 0.0230 | 0.0897 |
| requests-fts-encode-multipart | fts | yes | 1670 | 85232 | 9758 | 0.0196 | 0.1711 |
| fastapi-locate-fastapi | locate | yes | 23093 | 5847790 | 3793058 | 0.0039 | 0.0061 |
| fastapi-locate-apirouter | locate | yes | 5389 | 765491 | 267925 | 0.0070 | 0.0201 |
| fastapi-locate-depends | locate | yes | 1541 | 968037 | 382185 | 0.0016 | 0.0040 |
| fastapi-locate-httpexception | locate | yes | 4783 | 674648 | 225417 | 0.0071 | 0.0212 |
| fastapi-locate-jsonable-encoder | locate | yes | 1080 | 446360 | 158067 | 0.0024 | 0.0068 |
| fastapi-callers-get-openapi | callers | yes | 95 | 65348 | 18162 | 0.0015 | 0.0052 |
| fastapi-callers-solve-dependencies | callers | yes | 68 | 283457 | 9684 | 0.0002 | 0.0070 |
| fastapi-fts-solve-dependencies | fts | yes | 2830 | 283457 | 9684 | 0.0100 | 0.2922 |
| django-locate-queryset | locate | yes | 17 | 1236862 | 345125 | 0.0000 | 0.0000 |
| django-locate-manager | locate | yes | 83 | 636864 | 81090 | 0.0001 | 0.0010 |
| django-locate-httpresponse | locate | yes | 14 | 756862 | 169060 | 0.0000 | 0.0001 |
| django-locate-get-object-or-404 | locate | yes | 19 | 60375 | 11653 | 0.0003 | 0.0016 |
| django-locate-urlresolver | locate | yes | 17 | 35942 | 9126 | 0.0005 | 0.0019 |
| django-locate-basecommand | locate | yes | 17 | 313515 | 44557 | 0.0001 | 0.0004 |
| django-callers-get-object-or-404 | callers | yes | 105 | 60375 | 11653 | 0.0017 | 0.0090 |
| django-fts-compile-sql | fts | yes | 1653 | 1864608 | 526467 | 0.0009 | 0.0031 |
| django-fts-model-field-column | fts | yes | 1788 | 23492 | 4589 | 0.0761 | 0.3896 |
| express-locate-createapplication | locate | yes | 15 | 376 | 345 | 0.0399 | 0.0435 |
| express-locate-view | locate | yes | 27 | 51007 | 8459 | 0.0005 | 0.0032 |
| express-locate-sendfile | locate | yes | 15 | 45821 | 14739 | 0.0003 | 0.0010 |
| express-locate-acceptparams | locate | yes | 14 | 41692 | 2511 | 0.0003 | 0.0056 |
| express-locate-stringify | locate | yes | 15 | 17176 | 4873 | 0.0009 | 0.0031 |
| express-fts-etag | fts | yes | 757 | 74226 | 25867 | 0.0102 | 0.0293 |
| express-fts-view-engine | fts | yes | 965 | 64443 | 25687 | 0.0150 | 0.0376 |
| express-callers-createapplication | callers | NO | 9 | 376 | 345 | 0.0239 | 0.0261 |
| vue-locate-reactive | locate | yes | 17 | 571124 | 241254 | 0.0000 | 0.0001 |
| vue-locate-ref | locate | yes | 16 | 894492 | 483191 | 0.0000 | 0.0000 |
| vue-locate-computed | locate | yes | 34 | 550045 | 179277 | 0.0001 | 0.0002 |
| vue-locate-effect | locate | yes | 17 | 558298 | 214217 | 0.0000 | 0.0001 |
| vue-locate-definecomponent | locate | yes | 19 | 616154 | 195278 | 0.0000 | 0.0001 |
| vue-locate-watch | locate | yes | 54 | 584642 | 227093 | 0.0001 | 0.0002 |
| vue-callers-trigger | callers | yes | 323 | 655328 | 223497 | 0.0005 | 0.0014 |
| vue-fts-reactive-effect | fts | yes | 1642 | 89241 | 13764 | 0.0184 | 0.1193 |
| vue-fts-template-ast | fts | yes | 1611 | 1160860 | 480934 | 0.0014 | 0.0033 |
| gin-locate-new | locate | yes | 12 | 177276 | 71273 | 0.0001 | 0.0002 |
| gin-locate-context | locate | yes | 12 | 166777 | 99052 | 0.0001 | 0.0001 |
| gin-locate-routergroup | locate | yes | 15 | 46503 | 11314 | 0.0003 | 0.0013 |
| gin-locate-loggerwithconfig | locate | yes | 14 | 25020 | 3742 | 0.0006 | 0.0037 |
| gin-locate-recovery | locate | yes | 27 | 49590 | 8377 | 0.0005 | 0.0032 |
| gin-callers-new | callers | yes | 3270 | 177276 | 71273 | 0.0184 | 0.0459 |
| gin-fts-response-writer | fts | yes | 1526 | 84161 | 19400 | 0.0181 | 0.0787 |
| gin-fts-binding | fts | yes | 1547 | 130110 | 44338 | 0.0119 | 0.0349 |
| ripgrep-locate-run | locate | yes | 109 | 273214 | 52454 | 0.0004 | 0.0021 |
| ripgrep-locate-printer | locate | yes | 14 | 16089 | 4689 | 0.0009 | 0.0030 |
| ripgrep-locate-searcher | locate | yes | 19 | 164408 | 35517 | 0.0001 | 0.0005 |
| ripgrep-locate-searchworker | locate | yes | 15 | 16089 | 2573 | 0.0009 | 0.0058 |
| ripgrep-locate-globmatcher | locate | yes | 19 | 27738 | 2351 | 0.0007 | 0.0081 |
| ripgrep-callers-run | callers | yes | 222 | 273214 | 52454 | 0.0008 | 0.0042 |
| ripgrep-fts-glob-matcher | fts | yes | 1624 | 360398 | 135800 | 0.0045 | 0.0120 |
| ripgrep-fts-searcher | fts | yes | 1627 | 164408 | 35517 | 0.0099 | 0.0458 |

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

| language | repos | PRs | all micro R | all macro R | deps PRs | deps micro R | deps macro R |
|---|---|---|---|---|---|---|---|
| python | 4 | 40 | 0.284 | 0.323 | 18 | 0.537 | 0.662 |
| javascript | 1 | 10 | 0.000 | 0.000 | 0 | n/a | n/a |
| typescript | 1 | 10 | 0.080 | 0.100 | 1 | 1.000 | 1.000 |
| go | 1 | 10 | 0.150 | 0.158 | 0 | n/a | n/a |
| rust | 1 | 10 | 0.783 | 0.782 | 9 | 0.773 | 0.758 |

All PRs, per repo (recall first):

| repo | language | PRs | micro R | macro R | micro P | macro P | micro F1 | macro F1 |
|---|---|---|---|---|---|---|---|---|
| flask | python | 10 | 0.382 | 0.575 | 0.031 | 0.019 | 0.058 | 0.036 |
| requests | python | 10 | 0.500 | 0.467 | 0.070 | 0.036 | 0.123 | 0.066 |
| fastapi | python | 10 | 0.091 | 0.150 | 0.001 | 0.005 | 0.003 | 0.010 |
| django | python | 10 | 0.091 | 0.100 | 0.009 | 0.002 | 0.017 | 0.004 |
| express | javascript | 10 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| vue-core | typescript | 10 | 0.080 | 0.100 | 0.006 | 0.001 | 0.011 | 0.001 |
| gin | go | 10 | 0.150 | 0.158 | 0.022 | 0.020 | 0.039 | 0.030 |
| ripgrep | rust | 10 | 0.783 | 0.782 | 0.032 | 0.031 | 0.061 | 0.057 |

### Split by seed: does the graph have anything to predict?

The bucket boundary is the **circular/dependents** flag: a seed is "with
dependents" when cgh's reverse-import walk from it is non-empty. The
highest-churn seed heuristic often lands on a **leaf or test file** with no
reverse imports, so the graph predicts ~nothing and recall is zero; that is a
**seed-selection artifact**, not a cgh quality result, and the all-PRs row above
understates the meaningful case. The split below separates the two without
dropping any PR; the full per-PR table further down still carries every row,
losses included.

One caveat to read with it: the boundary is reverse-**import** edges only. For
languages where cgh resolves few of those (Go and JavaScript here), even genuine
source seeds fall in the leaf bucket, so that bucket is not purely leaf/test for
those repos. The all-PRs recall still counts their call-edge predictions (Go
reaches 0.15 that way), and the per-language table's `deps PRs` column shows
where the import graph is sparse.

| repo | language | bucket | PRs | micro R | macro R | micro P | macro P | micro F1 | macro F1 |
|---|---|---|---|---|---|---|---|---|---|
| flask | python | seeds with dependents | 6 | 0.929 | 0.958 | 0.031 | 0.032 | 0.061 | 0.060 |
| flask | python | leaf/test seeds (selection artifact) | 4 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| requests | python | seeds with dependents | 5 | 0.875 | 0.933 | 0.072 | 0.072 | 0.133 | 0.132 |
| requests | python | leaf/test seeds (selection artifact) | 5 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| fastapi | python | seeds with dependents | 7 | 0.105 | 0.214 | 0.001 | 0.007 | 0.003 | 0.014 |
| fastapi | python | leaf/test seeds (selection artifact) | 3 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| django | python | seeds with dependents | 0 | n/a | n/a | n/a | n/a | n/a | n/a |
| django | python | leaf/test seeds (selection artifact) | 10 | 0.091 | 0.100 | 0.009 | 0.002 | 0.017 | 0.004 |
| express | javascript | seeds with dependents | 0 | n/a | n/a | n/a | n/a | n/a | n/a |
| express | javascript | leaf/test seeds (selection artifact) | 10 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| vue-core | typescript | seeds with dependents | 1 | 1.000 | 1.000 | 0.007 | 0.007 | 0.013 | 0.013 |
| vue-core | typescript | leaf/test seeds (selection artifact) | 9 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| gin | go | seeds with dependents | 0 | n/a | n/a | n/a | n/a | n/a | n/a |
| gin | go | leaf/test seeds (selection artifact) | 10 | 0.150 | 0.158 | 0.022 | 0.020 | 0.039 | 0.030 |
| ripgrep | rust | seeds with dependents | 9 | 0.773 | 0.758 | 0.034 | 0.033 | 0.066 | 0.061 |
| ripgrep | rust | leaf/test seeds (selection artifact) | 1 | 1.000 | 1.000 | 0.014 | 0.014 | 0.029 | 0.029 |

Full per-PR detail (every PR, nothing dropped):

| repo | language | PR | seed | pred | truth | P | R | F1 | circular |
|---|---|---|---|---|---|---|---|---|---|
| flask | python | 6168 | `tests/test_appctx.py` | 0 | 6 | 0.00 | 0.00 | 0.00 | n/a |
| flask | python | 6133 | `src/flask/sansio/scaffold.py` | 66 | 1 | 0.02 | 1.00 | 0.03 | 1.00 |
| flask | python | 6096 | `tests/test_testing.py` | 0 | 3 | 0.00 | 0.00 | 0.00 | n/a |
| flask | python | 5928 | `tests/test_helpers.py` | 0 | 7 | 0.00 | 0.00 | 0.00 | n/a |
| flask | python | 5917 | `tests/test_basic.py` | 0 | 4 | 0.00 | 0.00 | 0.00 | n/a |
| flask | python | 5899 | `src/flask/app.py` | 73 | 1 | 0.01 | 1.00 | 0.03 | 1.00 |
| flask | python | 5898 | `src/flask/helpers.py` | 73 | 1 | 0.01 | 1.00 | 0.03 | 1.00 |
| flask | python | 5818 | `src/flask/app.py` | 73 | 4 | 0.05 | 1.00 | 0.10 | 1.00 |
| flask | python | 5736 | `src/flask/sansio/blueprints.py` | 66 | 3 | 0.05 | 1.00 | 0.09 | 1.00 |
| flask | python | 5731 | `src/flask/typing.py` | 64 | 4 | 0.05 | 0.75 | 0.09 | 1.00 |
| requests | python | 7505 | `tests/test_requests.py` | 1 | 2 | 0.00 | 0.00 | 0.00 | n/a |
| requests | python | 7502 | `tests/test_requests.py` | 1 | 1 | 0.00 | 0.00 | 0.00 | n/a |
| requests | python | 7496 | `src/requests/__init__.py` | 20 | 3 | 0.10 | 0.67 | 0.17 | 1.00 |
| requests | python | 7441 | `src/requests/models.py` | 20 | 1 | 0.05 | 1.00 | 0.10 | 1.00 |
| requests | python | 7431 | `src/requests/_types.py` | 19 | 2 | 0.11 | 1.00 | 0.19 | 1.00 |
| requests | python | 7433 | `tests/test_requests.py` | 1 | 1 | 0.00 | 0.00 | 0.00 | n/a |
| requests | python | 7427 | `tests/test_utils.py` | 0 | 1 | 0.00 | 0.00 | 0.00 | n/a |
| requests | python | 7426 | `src/requests/_types.py` | 19 | 1 | 0.05 | 1.00 | 0.10 | 1.00 |
| requests | python | 7425 | `src/requests/_types.py` | 19 | 1 | 0.05 | 1.00 | 0.10 | 1.00 |
| requests | python | 7315 | `tests/test_adapters.py` | 0 | 1 | 0.00 | 0.00 | 0.00 | n/a |
| fastapi | python | 16779 | `fastapi/dependencies/utils.py` | 300 | 1 | 0.00 | 0.00 | 0.00 | 1.00 |
| fastapi | python | 16746 | `fastapi/param_functions.py` | 429 | 5 | 0.00 | 0.00 | 0.00 | 1.00 |
| fastapi | python | 16739 | `fastapi/concurrency.py` | 28 | 2 | 0.04 | 0.50 | 0.07 | 1.00 |
| fastapi | python | 16734 | `fastapi/concurrency.py` | 27 | 1 | 0.00 | 0.00 | 0.00 | 1.00 |
| fastapi | python | 16728 | `fastapi/_compat/shared.py` | 68 | 1 | 0.01 | 1.00 | 0.03 | 1.00 |
| fastapi | python | 16722 | `scripts/docs.py` | 0 | 1 | 0.00 | 0.00 | 0.00 | n/a |
| fastapi | python | 16696 | `tests/test_prepare_release.py` | 0 | 1 | 0.00 | 0.00 | 0.00 | n/a |
| fastapi | python | 16598 | `tests/test_forms_optional_unions.py` | 0 | 1 | 0.00 | 0.00 | 0.00 | n/a |
| fastapi | python | 16596 | `fastapi/dependencies/utils.py` | 300 | 5 | 0.00 | 0.00 | 0.00 | 1.00 |
| fastapi | python | 16586 | `fastapi/dependencies/utils.py` | 300 | 4 | 0.00 | 0.00 | 0.00 | 1.00 |
| django | python | 22143 | `tests/sessions_tests/tests.py` | 0 | 1 | 0.00 | 0.00 | 0.00 | n/a |
| django | python | 22279 | `tests/gis_tests/test_geoforms.py` | 0 | 1 | 0.00 | 0.00 | 0.00 | n/a |
| django | python | 22208 | `tests/admin_inlines/tests.py` | 33 | 1 | 0.00 | 0.00 | 0.00 | n/a |
| django | python | 22244 | `tests/serializers/test_jsonl.py` | 0 | 1 | 0.00 | 0.00 | 0.00 | n/a |
| django | python | 22280 | `tests/gis_tests/gdal_tests/test_raster.py` | 0 | 2 | 0.00 | 0.00 | 0.00 | n/a |
| django | python | 22235 | `tests/admin_widgets/tests.py` | 33 | 1 | 0.00 | 0.00 | 0.00 | n/a |
| django | python | 22255 | `django/core/management/commands/createcachetable.py` | 44 | 1 | 0.02 | 1.00 | 0.04 | n/a |
| django | python | 22250 | `tests/invalid_models_tests/test_models.py` | 0 | 1 | 0.00 | 0.00 | 0.00 | n/a |
| django | python | 21534 | `tests/template_tests/syntax_tests/test_firstof.py` | 0 | 1 | 0.00 | 0.00 | 0.00 | n/a |
| django | python | 21857 | `tests/forms_tests/widget_tests/test_textarea.py` | 0 | 1 | 0.00 | 0.00 | 0.00 | n/a |
| express | javascript | 7112 | `test/app.render.js` | 0 | 7 | 0.00 | 0.00 | 0.00 | n/a |
| express | javascript | 7518 | `test/express.static.js` | 0 | 2 | 0.00 | 0.00 | 0.00 | n/a |
| express | javascript | 7494 | `test/express.static.js` | 0 | 2 | 0.00 | 0.00 | 0.00 | n/a |
| express | javascript | 7512 | `lib/response.js` | 0 | 1 | 0.00 | 0.00 | 0.00 | n/a |
| express | javascript | 7509 | `test/app.router.js` | 1 | 1 | 0.00 | 0.00 | 0.00 | n/a |
| express | javascript | 7483 | `examples/mvc/controllers/user/index.js` | 0 | 1 | 0.00 | 0.00 | 0.00 | n/a |
| express | javascript | 7459 | `test/res.send.js` | 0 | 1 | 0.00 | 0.00 | 0.00 | n/a |
| express | javascript | 7366 | `test/req.fresh.js` | 0 | 1 | 0.00 | 0.00 | 0.00 | n/a |
| express | javascript | 4893 | `test/res.send.js` | 0 | 1 | 0.00 | 0.00 | 0.00 | n/a |
| express | javascript | 7233 | `test/res.attachment.js` | 0 | 3 | 0.00 | 0.00 | 0.00 | n/a |
| vue-core | typescript | 16287 | `packages/compiler-sfc/__tests__/compileScript/resolveType.spec.ts` | 15 | 1 | 0.00 | 0.00 | 0.00 | n/a |
| vue-core | typescript | 16253 | `packages/compiler-sfc/__tests__/compileScript/resolveType.spec.ts` | 15 | 3 | 0.00 | 0.00 | 0.00 | n/a |
| vue-core | typescript | 16152 | `packages/compiler-sfc/__tests__/compileTemplate.spec.ts` | 3 | 2 | 0.00 | 0.00 | 0.00 | n/a |
| vue-core | typescript | 16245 | `packages/runtime-core/__tests__/hydration.spec.ts` | 0 | 4 | 0.00 | 0.00 | 0.00 | n/a |
| vue-core | typescript | 16251 | `packages/shared/__tests__/looseEqual.spec.ts` | 0 | 3 | 0.00 | 0.00 | 0.00 | n/a |
| vue-core | typescript | 16244 | `packages/runtime-core/__tests__/hydration.spec.ts` | 0 | 3 | 0.00 | 0.00 | 0.00 | n/a |
| vue-core | typescript | 16239 | `packages/runtime-dom/__tests__/helpers/useCssVars.spec.ts` | 0 | 3 | 0.00 | 0.00 | 0.00 | n/a |
| vue-core | typescript | 16225 | `packages/compiler-ssr/__tests__/ssrElement.spec.ts` | 0 | 2 | 0.00 | 0.00 | 0.00 | n/a |
| vue-core | typescript | 16132 | `packages/runtime-core/__tests__/components/Suspense.spec.ts` | 0 | 2 | 0.00 | 0.00 | 0.00 | n/a |
| vue-core | typescript | 16012 | `packages/shared/src/general.ts` | 303 | 2 | 0.01 | 1.00 | 0.01 | 1.00 |
| gin | go | 4852 | `routes_test.go` | 8 | 1 | 0.00 | 0.00 | 0.00 | n/a |
| gin | go | 4878 | `routergroup_test.go` | 0 | 2 | 0.00 | 0.00 | 0.00 | n/a |
| gin | go | 4854 | `context_test.go` | 17 | 1 | 0.00 | 0.00 | 0.00 | n/a |
| gin | go | 4819 | `routes_test.go` | 8 | 1 | 0.00 | 0.00 | 0.00 | n/a |
| gin | go | 4800 | `gin.go` | 52 | 1 | 0.02 | 1.00 | 0.04 | n/a |
| gin | go | 4806 | `routes_test.go` | 8 | 4 | 0.12 | 0.25 | 0.17 | n/a |
| gin | go | 4814 | `routes_test.go` | 8 | 5 | 0.00 | 0.00 | 0.00 | n/a |
| gin | go | 4805 | `context_test.go` | 17 | 3 | 0.06 | 0.33 | 0.10 | n/a |
| gin | go | 4797 | `routergroup.go` | 15 | 1 | 0.00 | 0.00 | 0.00 | n/a |
| gin | go | 4830 | `gin_test.go` | 1 | 1 | 0.00 | 0.00 | 0.00 | n/a |
| ripgrep | rust | 3561 | `crates/globset/src/lib.rs` | 69 | 1 | 0.01 | 1.00 | 0.03 | n/a |
| ripgrep | rust | 3560 | `crates/ignore/src/dir.rs` | 62 | 1 | 0.02 | 1.00 | 0.03 | 1.00 |
| ripgrep | rust | 3564 | `crates/ignore/src/pathutil.rs` | 52 | 1 | 0.02 | 1.00 | 0.04 | 1.00 |
| ripgrep | rust | 3502 | `crates/ignore/src/types.rs` | 75 | 1 | 0.01 | 1.00 | 0.03 | 1.00 |
| ripgrep | rust | 3482 | `crates/core/flags/defs.rs` | 45 | 5 | 0.09 | 0.80 | 0.16 | 1.00 |
| ripgrep | rust | 3472 | `crates/ignore/src/walk.rs` | 64 | 3 | 0.03 | 0.67 | 0.06 | 1.00 |
| ripgrep | rust | 3468 | `crates/ignore/src/gitignore.rs` | 67 | 2 | 0.01 | 0.50 | 0.03 | 1.00 |
| ripgrep | rust | 3467 | `crates/core/flags/lowargs.rs` | 63 | 7 | 0.10 | 0.86 | 0.17 | 1.00 |
| ripgrep | rust | 3248 | `tests/misc.rs` | 1 | 1 | 0.00 | 0.00 | 0.00 | 1.00 |
| ripgrep | rust | 3195 | `crates/searcher/src/line_buffer.rs` | 67 | 1 | 0.01 | 1.00 | 0.03 | 1.00 |

### Circular recall (internal consistency check, NOT a quality claim)

`recall_circular_sanity` scores the same prediction against the graph's **own**
reverse imports of the seed. It is ~1.0 by construction and only confirms the
harness's set math lines up with the closure the product walks. It is **not** a
product-quality or F1 claim.

Circular recall this run: flask: 1.000 over 6 seed(s) with reverse imports; requests: 1.000 over 5 seed(s) with reverse imports; fastapi: 1.000 over 7 seed(s) with reverse imports; django: no seed had reverse imports; express: no seed had reverse imports; vue-core: 1.000 over 1 seed(s) with reverse imports; gin: no seed had reverse imports; ripgrep: 1.000 over 9 seed(s) with reverse imports.

## Where cgh is weaker (honest, by language)

- **python**: tier-2 recall 0.28 over all PRs, 0.54 over seeds with dependents (18 of 40 PRs).
- **javascript**: tier-2 recall 0.00 over all PRs, n/a over seeds with dependents (0 of 10 PRs). This is the weakest language here. cgh resolves almost no JS import/call dependents for this repo, so the impact report predicts nothing and recall is 0 on every PR. A real weak spot, not a seed artifact.
- **typescript**: tier-2 recall 0.08 over all PRs, 1.00 over seeds with dependents (1 of 10 PRs). This is the announced weak spot. cgh locates TS symbols well (tier 1 above), but its cross-package TS import and call resolution is the least accurate of the set, so tier-2 recall is near the bottom. Much of the all-PRs figure is also a seed-selection artifact: the monorepo's largest-churn files are usually `__tests__` specs with no resolved dependents, so 9 of 10 seeds land in the leaf bucket; the one source seed that had dependents scored recall 1.0.
- **go**: tier-2 recall 0.15 over all PRs, n/a over seeds with dependents (0 of 10 PRs). The Go import graph is sparse here: cgh builds few reverse-import edges, so the circular/dependents bucket is empty even for source seeds (they are not leaf/test files, the import graph just does not resolve them). Call-edge prediction still recovers some recall.
- **rust**: tier-2 recall 0.78 over all PRs, 0.77 over seeds with dependents (9 of 10 PRs). This is the strongest impact language in this set.

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
