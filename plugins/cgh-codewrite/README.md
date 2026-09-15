# cgh-codewrite

A cgh plugin that delegates predictable, pattern-following code (tests,
stubs, config, boilerplate) to a cheap model so the primary model spends
no tokens producing it. Its distinguishing move: cgh picks the reference
file to mirror straight from the code graph, so you do not have to name it.

Installs through cgh's plugin entry point. Inert without cgh.

## Status

This release ships the reference-selection surface. Code generation (the
model call behind cgh's egress gate) is being added on top of it.

## Surfaces

### `cgh codewrite pick`

Report the existing file a generator should mirror for a target, and why.

```
cgh codewrite pick --target tests/test_user_service.py
# reference: tests/test_order_service.py
# defines a matching symbol, sibling in the same directory
```

The pick combines two signals: the graph (a file defining a symbol
related to the target's name) and the filesystem (a sibling of the same
kind in the target's directory). It degrades to a filesystem-only pick
when the graph is not readable (no index yet, or an owner holds the write
lock). Pass `--reference` to validate a specific file instead.

### `codewrite_pick` (MCP tool)

The same selection for an agent, returning JSON with the chosen
`reference`, the `reason`, the runner-up `candidates`, and whether the
graph was available. Runs inside the owner, so its graph read reuses the
owner's connection.

## License

MIT. Plugins that interact with cgh only through the documented plugin
interfaces are not derivative works of cgh.
