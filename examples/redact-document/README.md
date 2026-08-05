# Redact a document: anonymize only what you choose

Produce an anonymized copy of text. Pick the PII categories to remove
(just names, or emails and IBANs, or everything), and each is replaced
with a stable token: the same value always maps to the same token, so
the redacted text stays readable and internally consistent.

## Step 1: install

```bash
pip install cgh cgh-pii
pip install "cgh-pii[ner]"     # only if you redact person/location names
```

The regex categories (email, phone, iban, card, aws_key, private_key)
work with `cgh-pii` alone. Person and location **names** come from the
NER tier, which needs the `[ner]` extra; asking for `person` without it
fails with a clear message.

## Step 2: run

```bash
python redact_document.py
```

From your own code:

```python
from codegraph import sdk

clean = sdk.redact_text(text, only=["person"])          # names to [PERSON_1]
clean = sdk.redact_text(text, only=["email", "iban"])   # regex categories
clean = sdk.redact_text(text, mode="pseudonym", secret=key)  # <pii.email:hex>
```

- `only` limits the categories (default: all).
- `mode="placeholder"` (default) writes numbered tags `[PERSON_1]`,
  distinct within the document. `mode="pseudonym"` writes a keyed
  `<pii.person:hex>`; pass the same `secret` (16+ bytes) to get the
  same token for the same value across documents.
- A name detected once is redacted at every occurrence, even the ones
  the NER model missed on its own.

## The same, without code

`cgh pii redact` does this from the shell, on text, markdown and docx:

```bash
cgh pii redact contract.md --only person --out contract.anon.md
cgh pii redact report.docx --only person --out report.anon.docx   # needs cgh-pii[docx]
```

Word documents redact body paragraphs and table cells; formatting
inside a changed paragraph is flattened. PDF is not supported: extract
its text first (see cgh-docs) and redact that.

## Tests

```bash
pytest examples/redact-document -q
```
