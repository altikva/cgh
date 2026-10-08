# cgh-classify

> **Frozen.** 0.2.0 is the final release. Its main consumers, the core
> egress gate's confidentiality labels and the guard, went away with
> secure mode in cgh 0.15.0, and `cgh[plugins]` no longer installs it.
> It still works and still installs by name (`pip install cgh-classify`),
> gets no new features, and may be removed from the cgh repository
> later. cgh-codegen's egress gate still honors a `confidential` finding
> when one exists.

Human-trainable confidentiality classification for
[cgh](https://github.com/altikva/cgh). You label a few files, a
lightweight local model (TF-IDF + naive Bayes, standard library only)
generalizes to the rest, and the result lands as `confidential`
findings. Nothing ever leaves the machine.

```bash
pip install cgh-classify
cgh classify label payroll.xlsx            # mark confidential
cgh classify label README.md --not         # mark public
cgh classify train                         # fit + sweep the repo
cgh classify review                        # files the model is unsure about
cgh findings --key confidential
```

## How labels and predictions interact

| Source | Finding written | Effect on an egress gate (cgh-codegen) |
|---|---|---|
| Human label, confidential | `confidential = true` (block) | blocked everywhere |
| Human label, public | `confidential = false` | allowlisted, including strict mode |
| Model prediction, confidential | `confidential = true` (block) | blocked everywhere |
| Model prediction, public | `confidential.predicted = false` | no effect |

The asymmetry is deliberate: a model may block on its own say-so
(worst case, a false positive costs a summary), but only a human label
can clear a file under a strict egress gate (`egress = "strict"`),
where the gate is an allowlist.

## Configuration

`cgh classify label` and `cgh classify train` write findings when you
run them. Classifying every file on each index is opt-in since 0.2.0:

```toml
[plugin.classify]
# scan_on_index = false  # re-classify every indexed file
# threshold = 0.7      # predict confidential above this probability
# uncertain_low = 0.35 # review window lower bound
# uncertain_high = 0.65
```

Your labels are the asset: they live in
`.codegraph/classify_labels.json`, the trained model in
`.codegraph/classify_model.json`, both machine-local and cheap to
retrain (`cgh classify train` is instant on thousands of files).
