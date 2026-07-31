# Vision model benchmark (proposal 006)

Synthetic architecture diagrams with exact ground truth; JSON
extraction scored on node precision/recall (fuzzy labels), edge
recall (either direction), zone recall. `pii` counts diagrams
where the raw answer echoed the planted emails/IPs/project ids
(expected: extraction copies labels; anonymization must catch).

| model | json ok | node P | node R | edge R | zone R | avg s/img | pii echo |
|---|---|---|---|---|---|---|---|
| moondream | 0/5 | 0.00 | 0.00 | 0.00 | 0.00 | 0 | 0/5 |
| granite3.2-vision | 4/5 | 0.73 | 0.80 | 0.09 | 0.60 | 26 | 1/5 |
| qwen2.5vl:3b | 5/5 | 1.00 | 0.95 | 0.60 | 0.80 | 2 | 1/5 |
| gemma3:4b | 5/5 | 0.84 | 0.93 | 0.41 | 0.80 | 4 | 1/5 |

## Real-image pass (qualitative, outputs gitignored)

Three real cases: two screen *photos* of internal bank architecture
diagrams (moire, small text, low contrast) and one clean screenshot of
a public GCP reference architecture. Local models only; nothing left
the machine.

- **gemma3:4b** read the clean screenshot almost perfectly (16 nodes,
  every product name right, one link label mistaken for a node) and
  produced the richest structure on both photos, with garbled labels
  and a few invented edges (hub bias toward the central box).
- **qwen2.5vl:3b** was precise but shallower on photos (fewer nodes),
  and echoed real internal IPs from a photo into its extraction,
  live proof that the anonymize stage of proposal 006 is mandatory.
- **granite3.2-vision** returned valid but skeletal extractions
  (2-7 nodes), and on the dense synthetic diagram ran away for 104 s
  before failing: the plugin needs a hard per-call timeout.
- **moondream** cannot follow the JSON contract at all.

## Verdict

`qwen2.5vl:3b` as default (perfect node precision, best JSON
discipline, 1-3 s/image on Apple Silicon), `gemma3:4b` as the
robustness alternative (only model reading arrows on every diagram,
best on photos). Screen photos should be treated as best-effort input;
clean exports extract reliably. Every capable model copies planted
and real identifiers into its output: anonymization is a hard
requirement regardless of backend locality.
