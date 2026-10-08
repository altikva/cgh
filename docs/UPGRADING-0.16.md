# Upgrading to cgh 0.16

## What changes

cgh 0.16 stores the calls and other by-name references (class bases,
Markdown mentions and links, route handlers in another file) that it finds
while parsing. Edges between files no longer depend on the order files were
indexed in, and saving a file no longer drops the edges other files had into
it. Expect more CALLS edges than 0.15 showed: on the cgh repo itself, about
a quarter more. Calls are still matched by name, so a common name such as
`get` or `register` links to every function of that name.

## The one-time re-parse

The first index after upgrading parses every file again, even unchanged
ones. It runs on its own: from `cgh index`, or in the background when the
owner starts. Measured on real repos, full re-parse against a fresh index:

| Repo | Files | Re-parse | Fresh index | Peak memory |
|---|---|---|---|---|
| ondonne-api | 1,311 | 100 s | 81 s | 355 MB |
| ondonne-frontend | 1,354 | 42 s | 33 s | 226 MB |
| wb-backend | 571 | 28 s | 22 s | 254 MB |
| cgh | 456 | 24 s | 21 s | 242 MB |
| wb-frontend | 601 | 15 s | 13 s | 214 MB |

While it runs:

- the owner answers tool calls from the graph it is rebuilding, so results
  can be incomplete until it ends (a few minutes on a large repo);
- `cgh status` reads `reindexing` (and `outdated` before it starts, for
  example with no owner running), and `cgh doctor` shows a `!!` line;
- stopping or killing the owner is safe. The re-parse starts over on the
  next start or `cgh index`, and the index is only marked as upgraded once
  every file has been parsed.

Nothing to do by hand. Subrepos of a federated workspace each re-parse
when their own index runs.

## Call origins stay on your machine

The call log now records who triggered each tool call (agent, cli, hook,
internal) and the repo root. It lives in `.codegraph/call_log.db`. cgh
sends it nowhere: no telemetry, and cgh-bugreport crash reports carry only
the fields listed in their payload, which do not include the call log.
`cgh stats`, `cgh logs` and the `call_stats` tool show it locally. An agent
that calls `call_stats` sees it, like any other tool result.

## Rolling back to 0.15

```bash
cgh stop                                     # in each repo with a running owner
uv tool install --force "cgh[plugins]==0.15.0"
```

The index stays usable. cgh 0.15 reads and updates a 0.16 index (status,
stats, lookup, callers, index) and ignores the tables it does not know. There is
no need to delete anything.

While you run 0.15, files it re-indexes behave as they did in 0.15: edges
from other files into them can go missing until the next full index.

When you come back to 0.16, the next `cgh index` or owner start repairs
this by itself. A full index run by 0.15 triggers the one-time re-parse
again; files 0.15 saved one by one (its watcher, `force-index`) are parsed
again individually. The one case it cannot see is 0.15 running
`force-index` on a file that did not change on disk since 0.16 indexed it;
if you did that, rebuild the graph as below.

If you would rather start clean, stop the owner, delete only the graph
file (`.codegraph/graph.duckdb` and its `.wal`, or `.codegraph/graph.sqlite`
for the standalone binary) and index again:

```bash
cgh stop
rm -f .codegraph/graph.duckdb .codegraph/graph.duckdb.wal .codegraph/graph.sqlite
cgh index
```

Do not delete the whole `.codegraph` directory: it also holds your
knowledge, memory index and call log.
