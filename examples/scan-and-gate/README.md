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

The script scans an invoice-like text (email, IP), then shows the two
postures of `sdk.egress_decision`:

- **assist**: block on what the findings say; PII blocks cloud unless
  `allow_pii=True`.
- **secure**: allowlist semantics; even a clean scan is not enough, a
  human must have labeled the content non-confidential.

The verdict is truthy, so the call site reads
`if verdict: call_cloud(...)`.

## Tests

```bash
pytest examples/scan-and-gate -q
```
