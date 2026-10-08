# Security

## What cgh is, and what it is not

cgh is a local tool. It parses your repository into `.codegraph/` on
your machine and serves that index to your agent over MCP (stdio, plus
a loopback HTTP bridge between cgh processes). The index holds the
same information as your source tree, organized for lookup.

cgh does not decide what your agent may read. Since 0.15.0 it no
longer installs blocking hooks or writes deny lists. To keep files out
of an agent's reach, use that agent's own permission rules, for example
`permissions.deny` in Claude Code settings, `.bobignore` for IBM Bob,
or the equivalent in your tool. The agent enforces those itself, which
an outside hook never could do reliably.

## What can leave the machine

cgh itself sends nothing over the network unless you turn it on.
Installed plugins can, as described for each one below.

- **Network fetch** (`fetch_and_index` MCP tool, `cgh fetch`): refused
  unless `.codegraph/config.toml` sets `allow_fetch = true` under
  `[codegraph]`. Even then only `http`/`https` URLs on public hosts are
  fetched (private, loopback and link-local addresses are refused,
  redirects included) and every fetch or refusal is written to
  `.codegraph/activity.log`.
- **Summaries and code generation** (the `cgh-summarize` and
  `cgh-codegen` plugins): the backend is your choice in
  `[plugin.summarize]` / `[plugin.codegen]`. cgh-summarize also runs in
  the background as files are indexed, and with `backend = "auto"` it
  picks the first backend available, which can be a cloud CLI such as
  `claude -p`; set `backend = "ollama"` or `"structural"` to stay
  local. Before a cloud
  backend sees a file, an egress gate checks its findings (below), and
  every cloud call is logged.
- **Vision** (`cgh-vision`): images go to the endpoint you configure.
  A loopback URL stays on the machine; any other URL receives the image
  bytes, and each such call is logged.
- **Bug reports** (`cgh-bugreport`): crash reports stay in a local
  spool until you run `cgh bug send`, which shows the payload and asks
  before sending (skip the question with `--yes`).

## Findings and the egress gate

Scanner plugins attach **findings** to files (`pii.email`,
`secret.aws_key`, `confidential`, `summary`, ...), stored in SQLite next
to the index and queryable with `cgh findings` or the federated
`findings` MCP tool.

The egress gate in the model-backed plugins reads them: a file flagged
`confidential = true` or carrying a block-severity finding (private
keys, cloud credentials) is not sent to a cloud backend, and neither is
a file with PII findings unless `allow_pii = true`. A plugin's own
`egress = "strict"` setting turns the gate into an allowlist: only
files a human labeled non-confidential (`cgh classify label --not`) go
out.

The PII and secret scanners are regex based. They catch common shapes
and miss what they have no pattern for, so treat the gate as a useful
filter, not a guarantee. Findings are stored as the scanner reported
them. Repos indexed before 0.15.0 with `mode = "secure"` may still
hold pseudonyms such as `<pii.email:3fa2c1b4d5>` for older findings;
they are left as they are.

## MCP auth key

`cgh init` generates a cryptographic auth key at `.codegraph/auth.key` (auto-added to `.gitignore`). The owner process and every worker / CLI caller read that file and send it as a `Bearer` token to the owner's loopback HTTP bridge, which compares it in constant time. The file contents are the shared secret: there is no environment-variable hand-off.

```bash
# Key is auto-managed -- no manual steps needed
cgh init          # generates the key and the .codegraph/ index dir
```

The key file has `600` permissions and the `.codegraph/` directory is `700` (owner-only). Never commit either to git.

## Upgrading from secure mode

`mode = "secure"` was removed in 0.15.0. A config that still sets it
loads with the normal behavior, prints a one-line notice on stderr, and
`cgh status` / `cgh doctor` show the same notice; delete the line to
silence it. Run `cgh init` (or `cgh guard`) once: it removes the deny
rules cgh had written to `.claude/settings.local.json` (only the ones
recorded in `.codegraph/guard_denies.json`), cgh's managed block in
`.bobignore`, and the guard hooks cgh had added to Claude Code, Gemini
CLI and Codex configs. Entries you wrote yourself are left alone.

---

[Back to the README](../README.md)
