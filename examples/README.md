# SDK examples

Runnable demonstrations of the embedding surface (`codegraph.sdk`),
the MIT-licensed way to use cgh's bricks inside your own agent,
pipeline or API. Each script is self-contained:

```bash
pip install cgh cgh-pii            # scan_and_gate, pseudonymize_logs
pip install cgh-summarize          # summarize_local
pip install cgh-vision             # vision_pipeline, document_diagrams (needs Ollama)
pip install "pypdf[image]"         # document_diagrams, PDF input only

python examples/scan_and_gate.py
python examples/pseudonymize_logs.py
python examples/summarize_local.py
python examples/vision_pipeline.py path/to/diagram.png
python examples/document_diagrams.py design-note.docx slides.pptx spec.pdf
```

| Script | Shows |
|---|---|
| `scan_and_gate.py` | scan text for PII, decide egress before calling a cloud model |
| `pseudonymize_logs.py` | log findings without ever writing the sensitive value |
| `summarize_local.py` | summarize text with the local-only default |
| `vision_pipeline.py` | inventory an image, extract diagram/table/chart as routed |
| `document_diagrams.py` | pull embedded images out of pdf/docx/pptx and extract the architecture schemas they carry |

The full contract (stability, what the SDK does not expose, licensing)
lives in [docs/EMBEDDING.md](../docs/EMBEDDING.md).
