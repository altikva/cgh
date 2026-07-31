# Embedding cgh in your own code

`codegraph.sdk` is the one import path third-party code may rely on.
It exposes cgh's bricks as explicit functions, no CLI, no owner
process, no MCP server, no `.codegraph/` directory. Everything else
under `codegraph.*` is internal and changes without notice.

Licensing: code exercised solely through this surface may be used
under the MIT license alone, including commercially (the "SDK
embedding exception" in [LICENSE](../LICENSE)). The graph index, MCP
server, federation and shared memory are not part of the surface and
remain under the dual license.

Stability: `sdk.SDK_API` is `1`; the surface follows SemVer, with a
one-release deprecation window and runtime warnings while the project
is 0.x.

```bash
pip install cgh cgh-pii            # core + the scanners you need
pip install cgh-summarize          # optional: text summaries
```

## Scan text and decide egress inside an agent loop

```python
from codegraph import sdk

findings = sdk.scan_text(document_text, path="upload.txt", scanners=["pii"])

verdict = sdk.egress_decision(
    findings,
    mode="secure",                  # allowlist semantics, the default
    allow_pii=False,
    labeled_non_confidential=user_cleared,
)
if verdict:
    response = call_cloud_model(document_text)
else:
    log.info("kept local: %s", verdict.reason)
```

## Pseudonymize before logging or storing

```python
import secrets
KEY = secrets.token_bytes(32)       # persist this yourself, once

for f in findings:
    if f.key.startswith("pii."):
        log.warning("found %s at line %s", sdk.pseudonymize(f.key, f.value, KEY), f.line)
```

Same key and value give the same pseudonym, so joins and dedup keep
working; HMAC does not decode, so the raw value cannot be recovered
from the output.

## Summarize with the safe default

```python
summary = sdk.summarize(document_text)                  # local backends only
summary = sdk.summarize(document_text, cloud_allowed=bool(verdict))
```

## Image pipeline in a batch job (requires cgh-vision)

```python
inv = sdk.image_inventory("docs/architecture.png")
if "architecture_diagram" in inv["content"]:
    extraction = sdk.extract_diagram("docs/architecture.png")
elif "table" in inv["content"]:
    tables = sdk.extract_table("docs/architecture.png")
```

Until `cgh-vision` ships, these raise `sdk.CapabilityMissing` naming
the package; the same error shape covers any scanner you request but
did not install.

## Keep findings without a database

```python
store = sdk.InMemoryFindingStore()
if not store.already_scanned(path, "pii", sha):
    store.record(path, "pii", sdk.scan_text(text, path=path, scanners=["pii"]), sha)
blockers = store.query(severity="block")
```

## What the SDK does not give

The code graph index, the MCP server, federation, shared memory and
checkpoints are repo-level features of the cgh tool, not SDK bricks.
Use `cgh init` + MCP for those.
