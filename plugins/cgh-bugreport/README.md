# cgh-bugreport

Crash reports for [cgh](https://github.com/altikva/cgh) that keep the
promise the rest of the product makes: nothing leaves the machine
without a gate, and you see exactly what would.

```bash
pip install cgh-bugreport     # capture only: reports spool locally
cgh bug status                # the ledger: spooled, sent, where, when
cgh bug preview last          # the exact raw payload, byte for byte
cgh bug send                  # explicit, always; to a PRIVATE repo only
cgh bug purge                 # drop spooled reports
```

Two properties carry the design:

- **The core never phones home.** All reporting lives in this plugin.
  A cgh install without it provably has no reporting code at all, and
  even with it, the plugin itself has no network code: sending goes
  through your own `gh` CLI and its credentials.
- **Payloads are built by allowlist, never by scrubbing.** A report
  contains versions, OS name, the failing command name, the exception
  TYPE, and stack frames normalized to cgh's own modules (anything
  outside reduces to `<external>`). Exception messages, file paths,
  arguments and log lines are not fields, so they structurally cannot
  leave. The PII scanner runs over the finished payload as a tripwire
  and fails the build loudly if it ever matches.

Sending is always manual (`auto_send` does not exist), refuses public
repositories, dedups by fingerprint (new occurrences comment on the
existing issue), and every send lands in `.codegraph/activity.log`.
With `mode = "secure"` the payload is shown and confirmed before
anything leaves. Spool: capped at 20 reports, purged after 30 days,
never indexed by cgh itself.

```toml
[plugin.bugreport]
# github_repo = "your-org/cgh-crash-reports"   # must be private
```

Known tradeoff, stated: sending through your `gh` credentials links
the report to your GitHub identity. The private repo bounds the
audience; the preview shows there is nothing sensitive to link.
