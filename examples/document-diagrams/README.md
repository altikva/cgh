# Document diagrams: pdf / docx / pptx to architecture schemas

Documents carry diagrams too. This case pulls the embedded images out
of a `.pdf`, `.docx` or `.pptx`, asks the vision inventory what each
one contains, and extracts recognized schemas to markdown + Mermaid.

## Step 1: install

```bash
pip install cgh cgh-vision
pip install "pypdf[image]"     # only if you feed PDFs
```

Office formats need nothing extra: `.docx` and `.pptx` are zip
containers, the embedded media comes out with the standard library.

## Step 2: Ollama

Same requirement as the [vision-pipeline](../vision-pipeline/) case
(cgh-vision does not install Ollama for you; see that README for the
full setup, remote-server configuration and the loopback egress
warning):

```bash
ollama pull qwen2.5vl:3b gemma3:4b
```

## Step 3: run

```bash
python document_diagrams.py design-note.docx slides.pptx spec.pdf
```

For every embedded image the inventory decides the route; only
diagrams reach the extractor. Each extracted schema lands as a
markdown report (components, zones, Mermaid) next to the document,
with identities (IPs, hostnames) separated from labels.

## Same result without writing code

Partially, today. Inside an indexed repo, **cgh-docs** turns the TEXT
of pdf/docx/xlsx into searchable sections (`cgh init`, then the
`search_docs` and `doc_outline` MCP tools, or `cgh outline` for
markdown), and standalone images in the repo go through the vision
scanner automatically. What the CLI does not do yet is pull the
images embedded inside a document, which is exactly what this
example adds on top with fifty lines of SDK code.

## Tests

`test_document_diagrams.py` builds a minimal docx in memory (a zip
with a `word/media/` entry) and fakes the model transport, so it runs
without Ollama and without sample files:

```bash
pytest examples/document-diagrams -q
```
