# Proposal 005: remote bug reports from third-party installs

Status: revised after council review, 2026-07-29. Depends on 001
(plugins, extension namespaces) and rides the egress gate of 002/003.

## Why

cgh runs on machines its maintainer never sees. When it misbehaves
there (a crash, a scan-error streak, an owner that keeps dying), the
evidence lives in that machine's logs and the report arrives, at best,
as a screenshot. The maintainer needs the trace; the user needs it to
cost one command.

## The two principles everything follows from

**One door.** cgh already has an egress doctrine: nothing leaves
without the gate, every departure is audited. A bug report is an
egress like any other, so it goes THROUGH the existing gate and into
the existing audit stream. No parallel consent system, no plugin-local
policy. In secure mode the report backend is simply not on the
allowlist, so automatic sending is impossible by construction, not by
an `if` in a plugin. There is no `auto_send` option at all: sending is
always an explicit `cgh bug send`, three seconds that keep the promise
legible.

**Additive payloads, never subtractive.** Scrubbing a traceback with
regexes cannot keep the "no file contents" promise: `str(exception)`
happily embeds `KeyError: '<secret>'`, JSON decode extracts, and
source lines quoted by parsers. So the payload is built by an
ALLOWLIST of safe fields, and anything not on the list structurally
cannot appear:

- cgh version, python version, OS name
- the failing command or tool name (name only, never arguments)
- exception type and a normalized stack: module-relative frame paths
  and function names inside cgh's own code only; frames outside cgh
  reduce to `<external>`; no exception message text, ever
- plugin names and versions, the active mode, a random report id

Repo paths, activity-log lines, argument values, exception messages:
not fields, so not sendable. The PII scanner still runs over the
assembled payload as a tripwire (a hit fails the build loudly), but it
is defense in depth, never the primary barrier.

## What ships in v1

The `cgh-bugreport` plugin, and only these pieces:

- **Capture**: an exception reaching the CLI top level or the owner's
  main loop writes an allowlist-built report to
  `.codegraph/bugreports/<id>.json`.
- **`cgh bug preview <id|last>`**: prints the exact raw payload that
  would leave, byte for byte. Not a summary. This is the trust
  feature.
- **`cgh bug send`**: submits through the egress gate to the single
  v1 backend: a GitHub issue on a PRIVATE repo dedicated to reports
  (`github_repo` must be configured; the plugin refuses to send to a
  public repo when `gh` can tell). Dedup by fingerprint: new
  occurrences comment on the existing issue.
- **`cgh bug status`**: the ledger: spooled, sent where, when, issue
  URL. Every send also lands in activity.log.

Fingerprint: hash of exception type + top normalized in-cgh frame.
The cgh version stays OUT of the hash (it goes in the payload),
otherwise every release breaks dedup and known crashes flood back.

Known tradeoff, stated: sending via the user's `gh` credentials links
the report to their GitHub identity. The private repo bounds the
audience; the preview shows there is nothing sensitive to link. An
identity-free path needs a collector, which is exactly what v1
refuses to run.

## Spool lifecycle

The spool is itself an artifact worth protecting:

- capped at 20 reports, oldest dropped;
- purged after 30 days (`cgh bug purge` forces it);
- `.codegraph/` is already gitignored, and the indexer and scanners
  skip `bugreports/` explicitly so cgh never indexes its own spool;
- reports never contain enough to need redaction, per the allowlist,
  but `cgh bug purge <id>` exists anyway.

## Reception side, stated plainly

The maintainer will read these reports with AI agents. A bug report is
therefore untrusted input and a prompt-injection surface: the triage
tooling must treat report text as data, and the private-repo issue
template says so. Retraction: a sent report can be deleted from the
private repo (maintainer-side `cgh bug retract <id>` is a v2
candidate); the incident playbook below covers the case where
something sensitive slipped through anyway.

Before the first release, `docs/BUGREPORT_PLAYBOOK.md` must exist:
who deletes what, where, within what delay, and what gets announced,
when a report turns out to contain something it should not.

## Explicitly out of scope for v1

- The HTTP collector and GCS backends. The payload schema above is
  versioned and IS the contract; an organization that wants a
  collector writes one against it, owns its storage, and carries its
  own GDPR role. cgh's maintainer runs nothing and stores nothing.
- Anomaly detection (scan-error streaks, restart loops). That is
  observability, not bug reporting. When it becomes real, it starts
  as activity.log polling inside the plugin; a core hook surface is
  only considered if polling proves the need. No API gets frozen for
  an undefined feature.
- Any automatic sending, in any mode.

## Decisions (council review, 2026-07-29)

1. **Fingerprint**: exception type + top normalized frame, version
   excluded from the hash.
2. **Secure mode**: manual send only, by construction (backend not on
   the gate's allowlist), not by plugin policy. Assist mode is manual
   too: `auto_send` does not exist.
3. **Anomaly detection**: out of scope, no core hook surface.
