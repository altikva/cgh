# pdf-to-vision

Read a PDF's diagrams with the vision pipeline. `cgh vision` reads a PDF
directly once the pdf extra is installed; this example shows that, plus a
pure-Python fallback that rasterizes pages yourself if you want control.

## The direct way

```bash
pip install "cgh-vision[pdf]"

cgh vision diagram.pdf                 # every page, one report per page
cgh vision diagram.pdf --pages 1-3     # only pages 1 to 3
cgh vision diagram.pdf --format json --out report.json
cgh vision diagram.pdf --hint "labels are in French"
```

## The manual fallback (rasterize yourself)

`rasterize.py` turns each page into a PNG with pypdfium2 (BSD/Apache, no
system binary), then you run `cgh vision page-N.png` on the ones you want.
Handy when you only need a couple of pages, or want to tweak the DPI.

```bash
pip install pypdfium2
python rasterize.py diagram.pdf --scale 2 --out-dir pages/
cgh vision pages/page-1.png
```

## Notes

- Vision runs a model per page, so a long PDF is a lot of inference. Use
  `--pages` (direct) or rasterize only what you need (manual).
- Higher `--scale` / DPI helps thin diagram labels but costs more pixels.
- No network is required once the model is pulled and pypdfium2 installed.
- **A 400 "exceeds the available context size"** means the page's image
  needs a bigger context window. cgh sets `num_ctx` per request (raise it
  with `[plugin.vision] num_ctx = 16384`); if you registered the model by
  hand, add `PARAMETER num_ctx 8192` to its Modelfile.
- **Slow first call on CPU?** The model loads on first use. Warm it once
  with `ollama run <model>`, or raise `[plugin.vision] timeout_s`.
