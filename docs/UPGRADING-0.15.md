# Upgrading to cgh 0.15

## Who is affected

Everyone upgrading from 0.14 or older, and most of all:

- repos that ran with `mode = "secure"` or used `cgh guard`;
- anyone relying on cgh-pii, cgh-vision, cgh-summarize or cgh-classify,
  which `cgh[plugins]` no longer installs;
- scripts that call `fetch_and_index` or `cgh fetch`.

## What your agent can read now

cgh no longer blocks anything. Its guard hooks still run (older configs
call them) but always allow, and cgh writes no deny rules.

Deny rules an older cgh wrote stay in place and keep working: the
`Read()` entries in `.claude/settings.local.json` and the managed block
in `.bobignore`. `cgh init`, `cgh setup` and `cgh guard` say how many
remain. To drop them (and only them, your own entries stay):

```bash
cgh guard --remove-rules
```

To keep a file from your agent from now on, use the agent's own
permission rules, for example `permissions.deny` in Claude Code.

## When code leaves the machine

- `fetch_and_index` and `cgh fetch` need `allow_fetch = true` under
  `[codegraph]` in `.codegraph/config.toml`, for everyone.
- cgh-codegen sends the reference file, any file it extends and the spec
  to the model you configured, after a secret check. The check is regex
  based: it blocks known secret formats (private keys, AWS and GCP keys,
  GitHub, Slack, Stripe and bearer tokens, hardcoded credentials) and is
  best effort, not a guarantee.
- cgh-summarize 0.3.0 uses local backends only (Ollama or a loopback
  OpenAI-compatible server), and only when you run `cgh summarize run`.
- cgh-vision sends images only to the endpoint you configure. A loopback
  URL stays on the machine.

See [SECURITY.md](SECURITY.md) for the details.

## Upgrade

```bash
uv tool install --force -U "cgh[plugins]"
```

Without `-U`, uv keeps the plugin versions it resolved before. To keep
cgh-pii or cgh-vision, name them:

```bash
uv tool install --force -U "cgh[plugins]" --with cgh-pii --with cgh-vision
```

cgh 0.15 refuses first-party plugins older than cgh-pii 0.4.0,
cgh-summarize 0.3.0, cgh-classify 0.2.0 and cgh-vision 0.6.0: they are not
loaded, and `cgh plugins` and `cgh doctor` show them with the upgrade
command. Third-party plugins are not affected.

Then, in each repo:

1. Stop the owner that an older cgh started, so the next agent call
   starts a current one: `cgh stop --root <repo>`. An old owner lacks the
   new tools, and commands such as `cgh impact` stop with a message
   until it is replaced.
2. Refresh the rules cgh installed for your agent:
   `cgh setup <agent>` (`claude`, `cursor`, `codex`, `gemini`, `bob` or
   `all`).

## Old findings

Findings recorded by older scanners stay in `.codegraph/findings.db`.
cgh-summarize and cgh-vision stored text derived from your files
(summaries, extracted tables and diagrams), which can quote a secret or
personal data in clear. cgh-pii and cgh-classify stored counts, labels
and line numbers, which say where sensitive data sits. Older pii findings
written in secure mode keep their pseudonyms.

There is no purge command. With the owner stopped (`cgh stop --root
<repo>`), from the repo root:

```bash
sqlite3 .codegraph/findings.db "DELETE FROM findings WHERE scanner IN ('classify', 'pii-regex', 'pii-ner', 'pii-llm', 'summarize', 'vision'); VACUUM; PRAGMA wal_checkpoint(TRUNCATE);"
```

`VACUUM` rewrites the file so the deleted rows do not linger in free
pages, and the checkpoint empties the write-ahead log. Findings of other
scanners are kept.

## Rolling back to 0.14.5

```bash
cgh stop --root <repo>
uv tool install --force "cgh[plugins]==0.14.5"
```

The index format did not change: cgh 0.14.5 opens a repo indexed by 0.15
as is. Checked on a repo indexed and given a knowledge entry by 0.15:
0.14.5 `cgh status`, `cgh lookup`, `cgh callers`, `cgh outline`,
`cgh impact`, `cgh papercut` and `cgh index` all work, an owner started
by 0.14.5 serves it, and an entry 0.14.5 writes reads back under 0.15.

`cgh[plugins]==0.14.5` brings back that release's full plugin set
(cgh-pii 0.3.1, cgh-vision 0.5.0, cgh-summarize 0.2.4, cgh-classify 0.1.3
when nothing newer is installed), with their index-time scanners. Deny
rules removed with `cgh guard --remove-rules` do not come back by
themselves. Rerun `cgh setup <agent>` after rolling back so the agent
rules match 0.14.5.

---

[Back to the README](../README.md)
