# cgh-vision

Image understanding for [cgh](https://github.com/altikva/cgh): a
content inventory decides what each image contains, then only the
extractors the content warrants run: architecture diagrams become
markdown plus Mermaid, tables and charts become data, dense text
becomes a summary. Everything runs against a local Ollama daemon;
nothing leaves the machine.

```bash
pip install cgh-vision
ollama pull qwen2.5vl:3b gemma3:4b   # the benchmark-selected pair
cgh vision docs/architecture.png     # one image, markdown on stdout
```

## Pipeline

1. **Inventory** (non-directive: an image is never assumed to be a
   diagram; a logo or a photo costs one call and one summary line).
2. **Diagram extraction** when warranted: structure with the plain
   contract, enrichment over the found labels (title, kinds,
   technologies, legend only if actually drawn), then a second model
   reads the arrows constrained to the found labels. Benchmarked
   ensemble: node precision 1.00, edge recall 0.80.
3. **Table / chart / text extractors** as routed.
4. **Post-processing**: fuzzy-duplicate merge, arrow annotations
   dropped from node lists, reversed-edge dedup, and identity
   separation: IPs, CIDRs, FQDNs, emails and server names split out
   of labels, recorded as `pii.image_identity` findings so the
   secure-at-rest layer pseudonymizes them.

## Findings

| Key | Content |
|---|---|
| `image.content` | detected types (`architecture_diagram,table,...`) |
| `image.summary` | one-sentence description, FTS-searchable |
| `diagram.mermaid` | the generated Mermaid |
| `diagram.entities` | nodes/edges/zones as JSON |
| `table.markdown` / `chart.markdown` | extracted data |
| `text.summary` | dense-text summary |
| `pii.image_identity` | identities read off the image (warn) |

## Configuration

```toml
[plugin.vision]
# profile = "default"        # default | fast (single call) | photo
# nodes_model = "qwen2.5vl:3b"
# edges_model = "gemma3:4b"  # set to "" to disable the edge pass
# ollama_url = "http://127.0.0.1:11434"
# timeout_s = 120
# min_bytes = 5120           # skip icons and badges
# max_bytes = 20971520
```

## Embedding

The pipeline is part of the cgh SDK surface (`codegraph.sdk.image_*`,
MIT): `image_inventory`, `extract_diagram`, `extract_table`,
`extract_chart`. See cgh's `docs/EMBEDDING.md`.
