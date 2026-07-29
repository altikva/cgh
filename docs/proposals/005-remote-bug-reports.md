# Proposal 005: remote bug reports from third-party installs

Status: draft, for discussion. Depends on 001 (plugins, extension
namespaces) and inherits the egress doctrine of 002/003.

## Why

cgh runs on machines its maintainer never sees. When it misbehaves
there (a crash, a scan-error streak, an owner that keeps dying, a guard
that fails), the evidence lives in that machine's `owner.log` and
`activity.log`, and the report arrives, at best, as a screenshot. The
maintainer needs the trace; the user needs it to cost one command, or
zero.

## The constraint that shapes everything

cgh spent the last releases building one promise: nothing leaves the
machine without a gate, and every departure is logged. Telemetry that
betrayed this would poison the whole product. So:

1. **The core never phones home.** All reporting lives in a first-party
   plugin, `cgh-bugreport`. A user who never installs it has a cgh with
   zero network egress code outside the loopback MCP server. This is a
   property worth advertising, and it is only true if the core stays
   clean.
2. **Opt-in, twice.** Installing the plugin is consent to CAPTURE
   (local spool). SENDING is a second consent: `auto_send = false` by
   default, reports leave via an explicit `cgh bug send`, and `cgh init`
   asks the question when the plugin is present rather than defaulting.
3. **Reports are scrubbed like everything else.** The payload passes
   the same PII regex tier the finding store uses before it is even
   spooled: no file contents, ever; paths reduced to repo-relative;
   command arguments masked; emails, keys and tokens replaced by their
   finding keys. What cgh would block at its own egress gate, it must
   not ship in a bug report.
4. **Transparent ledger.** `cgh bug status` lists every report:
   spooled, sent where, when, with what id. Every send lands in
   activity.log, the same audit stream as the gates and the guard.

## What gets captured

- **Crashes**: an exception reaching the CLI top level or the owner's
  main loop writes a report to `.codegraph/bugreports/<id>.json`.
- **Anomalies** (owner side, cheap heuristics): a scan-error streak
  above a threshold, an owner restart loop (pidfile churn), a guard
  failing closed repeatedly. One report per anomaly kind per day.

A report contains: cgh version, python version, OS, the command or
tool name, the scrubbed traceback, the last 20 scrubbed activity.log
lines, plugin list with versions, the `mode`, and a random report id.
No repo name unless configured, no user identity unless the optional
`reporter` field is set, no env vars, no file contents.

## Backends, pluggable like everything else

`cgh-bugreport` consumes the `bugreport.backend` extension namespace
and ships three:

| Backend | Transport | Fits |
|---|---|---|
| `github` | `gh issue create` on a configured repo, one issue per report, dedup by fingerprint into comments | OSS default, needs gh auth on the machine |
| `http` | POST JSON to a configured endpoint (a Cloud Run collector, any webhook) | organizations: one collector, zero per-machine auth beyond a token |
| `gcs` | upload to a bucket via a signed URL the collector hands out, or gcloud when present | heavy traces, air-gapped-ish flows |

The `http` backend is the realistic organization default: ALTIKVA (or
any org) runs a small collector on Cloud Run, third-party installs
POST scrubbed reports to it, and the collector owns storage, dedup and
GitHub forwarding. The collector itself is out of scope for cgh, but
the payload schema in this proposal is its contract.

```toml
[plugin.bugreport]
# backend = "github"            # or "http", "gcs"
# github_repo = "altikva/cgh"
# http_url = ""                 # e.g. the org's Cloud Run collector
# http_token_env = "CGH_BUG_TOKEN"
# auto_send = false             # true = send on capture, still scrubbed
# reporter = ""                 # optional free-form identity
```

## Failure honesty

The reporter must never make things worse: capture and send are
wrapped so a failing reporter is a stderr line, not a second crash.
Spool is capped (oldest reports dropped past N), sends are retried at
most once, and `cgh bug send` exits nonzero when nothing could leave,
so scripts can tell.

## Open questions

1. Fingerprint for dedup: hash of (exception type, top frame, cgh
   version)? The github backend needs it to comment instead of
   flooding issues.
2. Should `secure` mode force `auto_send = false` even if configured
   true (leaning yes: secure means a human approves every departure)?
3. Does the anomaly detector belong in the plugin (polling
   activity.log) or as a core hook surface the plugin subscribes to
   (cleaner, one more API to freeze)?
