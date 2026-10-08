# cgh-pii

Secret scanning and PII redaction for
[cgh](https://github.com/altikva/cgh).

```bash
pip install cgh-pii
cgh pii scan                 # secrets in the current directory
cgh pii scan src/ deploy/    # or in given files and directories
cgh pii redact contract.md --only person --out contract.anon.md
```

`cgh[plugins]` does not install it since cgh 0.15.0: install it by
name, or add it to a tool install with
`uv tool install --force -U "cgh[plugins]" --with cgh-pii`.

## What regex detection is good for, and what it is not

Secret patterns are precise. A PEM private key header or an AWS access
key id has a fixed shape, so a match is almost always real. That is what
`cgh pii scan` looks for by default. It only knows the shapes in the
table below, though: tokens of other providers (GitHub, Slack, Stripe,
GCP service-account files, ...), a key split across lines or an encoded
value are not matched. A clean run is best effort, not proof that the
tree holds no secret.

PII patterns are not reliable. An email regex flags every author line
and test fixture, a phone regex flags version strings and coordinate
lists, and none of them see a person's name. Cards must pass Luhn and
IBANs mod 97, which removes most random digit runs, but on a real repo
the result is still mostly noise. The PII patterns stay available
behind `--pii` (or `pii = true`) for a quick look; do not treat a clean
run as proof that a repo holds no personal data.

## `cgh pii scan`

```bash
cgh pii scan                 # secrets only, current directory
cgh pii scan --pii           # also emails, phones, IBANs, cards
cgh pii scan --json          # machine-readable hits
```

In a git work tree it scans tracked and untracked files git does not
ignore; elsewhere it walks the directory and skips hidden directories.
Binary files and files over 2 MB are skipped. Each line of output is
one key in one file: `path:line  severity  key  (matches)`, where the
line is the first match. The matched text is never printed.

The exit code is 1 when a block-severity secret is found and 0
otherwise, so it can gate a CI job or a pre-commit hook:

```bash
cgh pii scan || { echo "secret in the tree"; exit 1; }
```

| Key | What | Severity | Default |
|---|---|---|---|
| `secret.aws_key` | AWS access key ids | block | on |
| `secret.private_key` | PEM private key blocks | block | on |
| `secret.assignment` | `password = "..."` style hardcoded credentials | warn | on |
| `pii.email` | email addresses | warn | `--pii` |
| `pii.phone` | international-format phone numbers | warn | `--pii` |
| `pii.iban` | IBANs, mod-97 validated | warn | `--pii` |
| `pii.card` | payment card numbers, Luhn validated | warn | `--pii` |

## Index-time scanning (opt-in)

Until 0.4.0 every indexed file went through the scanner, and the hits
landed in cgh's finding store. That now happens only on request:

```toml
[plugin.pii]
scan_on_index = true   # scan each file as it is indexed
pii = false            # true adds the PII patterns, here and in cgh pii scan
# disable_keys = ["secret.assignment"]  # silence one key
# ner = true           # deferred NER tier (person names, locations)
# llm = true           # deferred LLM tier, see below
```

With it on, `cgh findings --key secret.` and `cgh findings --severity
block` list what the index saw. Finding values hold the match count and
the first line, never the matched data, because findings feed the
full-text index. cgh-codegen's egress gate refuses to send a reference
file that carries a block-severity or `pii.*` finding, so turn this on
if you use cgh-codegen with a cloud model and want that check backed by
data.

`ner` and `llm` only take effect with `scan_on_index = true`; both run
deferred, off the indexing hot path. NER needs
`pip install "cgh-pii[ner]"`.

## Redacting a document

cgh-pii also produces an anonymized copy of a text, markdown or Word
file:

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

Read the output before you share it. The same limits apply as above:
the regex tier misses what does not fit its patterns.

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

## The optional LLM tier

A model can catch what the patterns miss: names in unusual formats,
postal addresses, context-bound identifiers. It needs no extra package
(a stdlib HTTP client), only a reachable model:

```toml
[plugin.pii]
llm_ollama_url = "http://127.0.0.1:11434"   # default; a loopback URL
llm_model = "qwen2.5:3b"                     # any text model you have
# or an OpenAI-compatible endpoint instead of Ollama:
# llm_openai_base_url = "https://llm.internal.acme/v1"
# llm_openai_model = "acme-cor"
# llm_openai_api_key_env = "ACME_LLM_KEY"
```

Try it on one file, without redacting anything, then use it while
redacting:

```bash
cgh pii probe contract.md          # lists what the LLM tier would flag
cgh pii redact contract.md --llm --out contract.anon.md
```

A quote the model invents (not present verbatim in the file) redacts
nothing, so a hallucination can never anonymize the wrong bytes. On the
redact path the LLM categories fold into the redactor's set, with a
catch-all `other` (`[OTHER_1]`) for id numbers, org names and
credentials. `--llm` is wired for text and markdown; docx redaction
uses the regex and NER tiers only.

**Egress is gated.** Probing a file sends its content to the model. A
loopback endpoint stays on the machine. A non-loopback endpoint is
refused unless you set `pii_llm_allow_remote = true`, and every probe,
allowed or denied, is written to the activity log. The endpoint scheme
is pinned to http/https.

With `scan_on_index = true` and `llm = true`, the same probe runs
deferred on every indexed file and records count-only findings
(`pii.llm.person`, `pii.llm.other`, ...).
