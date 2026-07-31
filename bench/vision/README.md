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
