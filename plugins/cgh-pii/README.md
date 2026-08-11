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

## The optional LLM tier

A third tier probes each file with a local or configured LLM and flags
the PII the regex and NER tiers miss: names in unusual formats,
quasi-identifiers, postal addresses, context-bound identifiers. It needs
no extra package (a stdlib HTTP client), only a reachable model. Turn it
on under `[plugin.pii]`:

```toml
[plugin.pii]
llm = true
llm_ollama_url = "http://127.0.0.1:11434"   # default; a loopback URL
llm_model = "qwen2.5:3b"                     # any text model you have
# or an OpenAI-compatible endpoint instead of Ollama:
# llm_openai_base_url = "https://llm.internal.acme/v1"
# llm_openai_model = "acme-cor"
# llm_openai_api_key_env = "ACME_LLM_KEY"
```

Like NER, it runs **deferred**, never on the inline hot path (an LLM call
per file is heavy), and its findings carry only a count
(`pii.llm.person`, `pii.llm.other`, ...), never the matched text.

**Egress is gated.** Probing a file sends its content to the model. A
**loopback** endpoint stays on the machine and is free. A **non-loopback**
endpoint (an enterprise LLM) is egress: it is refused unless you set
`pii_llm_allow_remote = true`, and every probe, allowed or denied, is
written to the activity log. The endpoint scheme is pinned to http/https.
This mirrors the `fetch_and_index` egress gate: nothing leaves without an
explicit opt-in, and every departure is audited.

Try it on one file before trusting it, without redacting anything:

```bash
cgh pii probe contract.md          # lists what the LLM tier would flag
cgh pii redact contract.md --llm --out contract.anon.md
```

A quote the model invents (not present verbatim in the file) redacts
nothing, so a hallucination can never anonymize the wrong bytes. On the
redact path the LLM categories fold into the redactor's set, with a
catch-all `other` (`[OTHER_1]`) for id numbers, org names and
credentials. `--llm` is wired for text and markdown; docx redaction still
uses the regex and NER tiers only.

## Redacting a document

Beyond detecting PII, cgh-pii can produce an anonymized copy of a text
or markdown file:

```bash
cgh pii redact contract.md --only person --out contract.anon.md
cgh pii redact notes.txt --mode pseudonym --in-place
cgh pii redact report.docx --only person --out report.anon.docx
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
- **Text, markdown and docx.** Word documents are redacted with the
  `docx` extra (`pip install "cgh-pii[docx]"`), body paragraphs and
  table cells, one shared token map across the whole file. Formatting
  inside a changed paragraph is flattened (it is the only way to
  redact PII split across runs, like a bold surname); unchanged
  paragraphs keep their formatting. A docx needs `--out` or
  `--in-place`. PDF is not supported: real pdf redaction needs an
  AGPL library; extract the pdf text (see cgh-docs) and redact that.
