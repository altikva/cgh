# Security

## Findings, modes and the guard

Scanner plugins attach **findings** to files (`pii.email`,
`secret.aws_key`, `confidential`, `summary`, ...), stored in SQLite next
to the index and queryable anytime with `cgh findings` or the federated
`findings` MCP tool, even while a server is running.

One switch names your posture in `.codegraph/config.toml`:

```toml
[codegraph]
mode = "assist"   # default: optimize for token savings and flow
# mode = "secure" # assist + enforcement: nothing turns off, gates are added
```

What the findings feed:

- **The egress gate** (cgh-summarize): a file flagged confidential, or
  carrying secrets or PII, never reaches a cloud model. In `secure`
  mode the gate is an allowlist: only files a human labeled
  non-confidential go out. Every cloud send is logged.
- **The guard**: `cgh init` / `cgh setup` install pre-tool-use hooks in
  your agents, so the agent's own Read, Grep and shell calls are denied
  on flagged files, with a named reason. `cgh guard status` shows the
  honest per-agent map: Claude Code and Gemini CLI enforce (read and
  shell veto), Codex CLI is partial (shell veto only), IBM Bob is
  partial (static `.bobignore` denies), agents without a veto surface
  are listed unprotected. In `secure` mode the guard fails closed and
  flagged paths also sync into static deny lists (Claude settings,
  `.bobignore`).

This is policy enforcement inside cooperating agent frameworks, not a
sandbox: an agent free to run arbitrary code can read what the OS
allows. The guard narrows the path; `mode = "secure"` narrows it hard.

---

## Security

### MCP Auth Key

`cgh init` generates a cryptographic auth key at `.codegraph/auth.key` (auto-added to `.gitignore`). The owner process and every worker / CLI caller read that file and send it as a `Bearer` token to the owner's loopback HTTP bridge, which compares it in constant time. The file contents are the shared secret: there is no environment-variable hand-off.

```bash
# Key is auto-managed -- no manual steps needed
cgh init          # generates the key and the .codegraph/ index dir
```

The key file has `600` permissions and the `.codegraph/` directory is `700` (owner-only). Never commit either to git.

### Sensitive data at rest (secure mode)

In `mode = "secure"`, the index never stores the sensitive datum
itself. Every `pii.*` and `secret.*` finding value is replaced at
write time by a keyed one-way pseudonym (`<pii.email:3fa2c1b4d5>`),
computed with a per-repo HMAC key (`.codegraph/pseudo.key`, `600`).
Pseudonyms are stable, so dedup and cross-file search keep working,
but irreversible: reading the SQLite files directly, even with the
key, yields nothing recoverable, because HMAC does not decode and
the raw value was never written.

The guard closes the direct path too: in secure mode an agent's Read,
Grep or shell call touching `.codegraph/` is denied with a reason
pointing at the MCP tools, and the static deny lists (Claude settings,
`.bobignore`) carry a standing index entry. This is policy inside
cooperating agent frameworks, not a sandbox; the pseudonymization is
what protects against a process that reads the files anyway: there is
nothing sensitive in them to find.

---

---

[Back to the README](../README.md)
