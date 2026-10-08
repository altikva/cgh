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

## Limits

The PII detection that feeds this is regex based. It catches common
shapes (emails, phone numbers, IBANs, card numbers, keys) and misses
anything it has no pattern for, so a log run through it can still hold
personal data. Pseudonymize what you detect; do not treat the output as
anonymized.

## Tests

```bash
pytest examples/pseudonymize-logs -q
```
