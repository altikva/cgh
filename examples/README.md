# SDK examples

Runnable demonstrations of the embedding surface (`codegraph.sdk`),
the MIT-licensed way to use cgh's bricks inside your own agent,
pipeline or API. One directory per case; each holds a comprehensive
README (install steps included), the code, a sample config when there
is something to customize, and a pytest suite showing how to test
that case without any daemon or network.

Every capability shown here has **three access paths**, and each
case's README covers all three: the **SDK** (these scripts, for your
own code), the **cgh CLI** (the same feature as a verb inside an
indexed repo, `cgh vision`, `cgh summarize run`, `cgh findings`, no
code at all), and **MCP through your agent** (Claude Code, Cursor or
Codex connected to `cgh serve` reads the same results with tools like
`findings`, `summaries` or `corpus_insights`, so the models ran at
indexing time, not at question time).

| Case | Shows | Needs |
|---|---|---|
| [scan-and-gate](scan-and-gate/) | scan text for PII, decide egress before calling a cloud model | cgh-pii |
| [pseudonymize-logs](pseudonymize-logs/) | log findings without ever writing the sensitive value | cgh-pii |
| [summarize-local](summarize-local/) | summarize with the local-only default, cloud behind your gate | cgh-summarize |
| [vision-pipeline](vision-pipeline/) | inventory an image, extract diagram/table/chart as routed | cgh-vision + Ollama |
| [document-diagrams](document-diagrams/) | pull embedded images out of pdf/docx/pptx, extract the schemas | cgh-vision + Ollama |

Quick start, all cases:

```bash
pip install cgh cgh-pii cgh-summarize cgh-vision
python examples/scan-and-gate/scan_and_gate.py
```

The two vision cases talk to a **local Ollama daemon**, which cgh
does not install for you; their READMEs walk through the one-time
setup (`ollama pull qwen2.5vl:3b gemma3:4b`), pointing at an existing
server, and why confidential images should stay on a loopback URL.

Run every example's tests offline:

```bash
pytest examples -q
```
