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

## Redacting a document

Beyond detecting PII, cgh-pii can produce an anonymized copy of a text
or markdown file:

```bash
cgh pii redact contract.md --only person --out contract.anon.md
cgh pii redact notes.txt --mode pseudonym --in-place
```

`--only` limits the categories (`person`, `location`, `email`,
`phone`, `iban`, `card`, `aws_key`, `private_key`; default: all).
`--mode placeholder` (default) writes numbered tags `[PERSON_1]`,
distinct within the document; `--mode pseudonym` writes a keyed
`<pii.person:hex>`, the same token for the same value across documents
when you export a stable `CGH_REDACT_SECRET` (16+ chars). From code:
`codegraph.sdk.redact_text(text, only=["person"])`.

Two things to know:

- **Names need the NER tier** (`pip install "cgh-pii[ner]"`). The
  regex tier does not detect person names; requesting `person` or
  `location` without NER fails with a clear message. Once a name is
  detected, every literal re-occurrence of it is redacted too, since
  NER can miss repeat mentions.
- **Text files only.** Binary documents (pdf, docx) are not rewritten
  in place; extract their text first (see cgh-docs) and redact that.
