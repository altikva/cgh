# cgh-classify

Human-trainable confidentiality classification for
[cgh](https://github.com/altikva/cgh). You label a few files, a
lightweight local model (TF-IDF + naive Bayes, standard library only)
generalizes to the rest, and the egress gate and guard enforce the
result. Nothing ever leaves the machine.

```bash
pip install cgh-classify
cgh classify label payroll.xlsx            # mark confidential
cgh classify label README.md --not         # mark public
cgh classify train                         # fit + sweep the repo
cgh classify review                        # files the model is unsure about
cgh findings --key confidential
```

## How labels and predictions interact

| Source | Finding written | Effect on the egress gate |
|---|---|---|
| Human label, confidential | `confidential = true` (block) | blocked everywhere |
| Human label, public | `confidential = false` | allowlisted, including strict mode |
| Model prediction, confidential | `confidential = true` (block) | blocked everywhere |
| Model prediction, public | `confidential.predicted = false` | no effect |

The asymmetry is deliberate: a model may block on its own say-so
(worst case, a false positive costs a summary), but only a human label
can clear a file in `mode = "secure"`, where the gate is an allowlist.

## Configuration

```toml
[plugin.classify]
# threshold = 0.7      # predict confidential above this probability
# uncertain_low = 0.35 # review window lower bound
# uncertain_high = 0.65
```

Your labels are the asset: they live in
`.codegraph/classify_labels.json`, the trained model in
`.codegraph/classify_model.json`, both machine-local and cheap to
retrain (`cgh classify train` is instant on thousands of files).
