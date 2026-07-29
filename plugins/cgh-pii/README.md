# cgh-pii

PII and secret detection for [cgh](https://github.com/altikva/cgh).
Once installed, every indexed file is scanned inline for personal data
and credentials, and the results land in the finding store:

```bash
pip install cgh-pii
cgh index
cgh findings --key pii.       # emails, phones, IBANs, cards per file
cgh findings --severity block # private keys, cloud credentials
```

Detected keys and their severities:

| Key | What | Severity |
|---|---|---|
| `pii.email` | email addresses | warn |
| `pii.phone` | international-format phone numbers | warn |
| `pii.iban` | IBANs, mod-97 validated | warn |
| `pii.card` | payment card numbers, Luhn validated | warn |
| `secret.aws_key` | AWS access key ids | block |
| `secret.private_key` | PEM private key blocks | block |
| `secret.assignment` | `password = "..."` style hardcoded credentials | warn |

Two deliberate properties:

- **Finding values never contain the matched data.** A finding stores
  the match count and the first line number, not the email or the IBAN
  itself: findings feed the full-text index and must not spread what
  they detect.
- **Validation over recall.** Cards must pass Luhn, IBANs must pass
  mod 97, so a random digit run does not flag a file.

The optional NER tier (person names, locations) installs with
`pip install "cgh-pii[ner]"` and activates with `ner = true` under
`[plugin.pii]`; it runs deferred, off the indexing hot path.
