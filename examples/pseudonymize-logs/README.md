# Pseudonymize before logging

Log user activity without ever writing the sensitive value: the
fields your pipeline holds are replaced by keyed one-way pseudonyms.
Stable (same value, same pseudonym, so two events by the same user
still correlate) and irreversible (HMAC does not decode). The scan is
the tripwire proving the safe line carries no PII before it reaches
the log.

## Step 1: install

```bash
pip install cgh cgh-pii
```

## Step 2: run

```bash
python pseudonymize_logs.py
```

The demo generates a throwaway secret. In a real application the
secret is the identity of your pseudonym space: persist it once in
your vault (32 random bytes; the SDK refuses fewer than 16) and reuse
it, otherwise pseudonyms stop correlating across restarts.

## Same result without writing code

Inside a cgh repo in **secure mode** (`cgh init --secure`), this is
automatic: every `pii.*` and `secret.*` finding value is stored as a
keyed one-way pseudonym at write time (`.codegraph/pseudo.key`, per
repo). `cgh findings` and the MCP `findings` tool both show the
pseudonyms, never the raw values, and reading the SQLite files
directly yields nothing recoverable. The SDK function in this example
is the same mechanism with a key you own.

## Tests

```bash
pytest examples/pseudonymize-logs -q
```
