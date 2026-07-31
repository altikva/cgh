# Vision model benchmark (proposal 006)

Synthetic architecture diagrams with exact ground truth; JSON
extraction scored on node precision/recall (fuzzy labels), edge
recall (either direction), zone recall. `pii` counts diagrams
where the raw answer echoed the planted emails/IPs/project ids
(expected: extraction copies labels; anonymization must catch).

| model | json ok | node P | node R | edge P | edge R | zone R | avg s/img | pii echo |
|---|---|---|---|---|---|---|---|---|
| moondream | 0/5 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 1 | 0/5 |
| granite3.2-vision | 4/5 | 0.73 | 0.80 | 0.15 | 0.09 | 0.60 | 41 | 1/5 |
| qwen2.5vl:3b | 5/5 | 1.00 | 0.95 | 0.60 | 0.60 | 0.80 | 8 | 1/5 |
| gemma3:4b | 5/5 | 0.84 | 0.93 | 0.32 | 0.41 | 0.80 | 5 | 1/5 |
| ensemble | 5/5 | 1.00 | 0.95 | 0.74 | 0.80 | 0.80 | 7 | 1/5 |

## Real-image pass (qualitative, outputs gitignored)

Three real cases: two screen *photos* of internal bank architecture
diagrams (moire, small text, low contrast) and one clean screenshot of
a public GCP reference architecture. Local models only; nothing left
the machine. gemma3:4b read the clean screenshot almost perfectly and
produced the richest structure on photos (garbled labels, a few
invented edges); qwen2.5vl:3b was precise but shallower and echoed
real internal IPs into its extraction, live proof the anonymize stage
is mandatory; granite was skeletal with a 3-minute runaway on the
dense case (hard per-call timeout required); moondream cannot hold
the JSON contract.

## Verdict

The **ensemble wins**: qwen2.5vl:3b extracts nodes and zones (perfect
node precision, best JSON discipline), then gemma3:4b reads the
arrows constrained to that node list, edge sets unioned. Constraining
the labels is what unlocks gemma's arrow reading: on the diagram
where both solos scored 0 edges, the ensemble reaches 0.71/0.71.
Averages: node P 1.00 / R 0.95, edge P 0.74 / R 0.80 (best solo:
0.60), zones 0.80, about 7 s per image on Apple Silicon, still fully
local. Dense many-crossing diagrams remain the open weakness (0.30).
Recommendation for cgh-vision: ensemble as the default pipeline,
single-model qwen as the fast setting, hard per-call timeout, photos
treated as best-effort input.
