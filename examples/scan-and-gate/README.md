# Scan and gate: PII detection before any cloud call

The canonical embedding loop: scan content with the installed
scanners, then let the egress gate decide whether it may reach a
cloud model. Everything here is pure local computation, no daemon, no
network, no model.

## Step 1: install

```bash
pip install cgh cgh-pii
```

The regex tier of cgh-pii needs nothing beyond the standard library.
(An optional NER tier exists behind `pip install "cgh-pii[ner]"`.)

## Step 2: run

```bash
python scan_and_gate.py
```

The script scans an invoice-like text (email, IP), then asks
`sdk.egress_decision` whether it may go to a cloud model. The gate
refuses on block-severity findings, on a `confidential = true` label,
and on PII unless the caller passes `allow_pii=True`.

The scanners are regex based and miss things. Treat the verdict as a
useful filter, not as a guarantee that nothing sensitive leaves.

The verdict is truthy, so the call site reads
`if verdict: call_cloud(...)`.

## Same result without writing code

**cgh CLI**, inside an indexed repo (`cgh init`): the pii scanner runs
during indexing, the gate protects the summarize plugin's cloud
backends, and the human label comes from classify:

```bash
cgh findings                       # the pii.* findings per file
cgh classify label secret.md       # mark confidential (blocks egress)
cgh classify label --not spec.md   # cleared for a strict egress gate
```

**MCP through your agent**: the agent reads the same store through the
`findings` tool.

## Tests

```bash
pytest examples/scan-and-gate -q
```
