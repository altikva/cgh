# Vision model benchmark (proposal 006)

Measures free local vision models on the exact task cgh-vision would
run: extract nodes, directed edges and zones from an architecture
diagram, as strict JSON.

```bash
uv run --with pillow python gen_diagrams.py   # 5 diagrams + ground truth
ollama pull moondream granite3.2-vision qwen2.5vl:3b gemma3:4b
python run_bench.py                            # all 4, or pass model names
```

Diagrams ramp from 4 to 12 nodes; `d4_pii_bait` plants emails, IPs and
internal hostnames to measure whether models echo them (extraction is
expected to copy labels; the anonymize stage of proposal 006 is what
must catch them). Scoring is exact because the ground truth is the
spec the image was drawn from: node precision/recall with fuzzy label
matching, edge recall (either direction), zone recall, JSON
compliance, seconds per image. Results land in `RESULTS.md` and
`results.json`.

## Real corpus pass

Drop any images into `real/` (gitignored, nothing leaves the machine)
and run `python run_real.py [models...]` ("ensemble" is accepted).
Latest pass, 40 real diagrams (drawio exports, GKE/GCP architectures,
CI/CD, monitoring, plus screen photos): 100% valid JSON, zero empty
extractions across qwen2.5vl:3b, gemma3:4b and the ensemble; the
ensemble averaged 9.4 nodes and 19.5 edges per diagram at ~12 s/image.

## Enriched pipeline (extract.py)

`python extract.py <image> [default|fast|photo]` runs the pipeline the
cgh-vision plugin will ship and prints markdown with a Mermaid block.
Three passes, each shaped by the bench evidence: structure with the
plain contract (best node recall), enrichment over the found labels
(title, kinds, tech, legend, notes; keyed joins are normalized and a
legend may only be reported when actually drawn, echo entries are
filtered), then the constrained edge reading. Post-processing merges
fuzzy-duplicate nodes, drops arrow annotations mistaken for boxes,
dedups reversed edges, and splits identities (IPs, CIDRs, FQDNs,
emails, server names) out of labels into attributes so the anonymize
stage has a clean target.
