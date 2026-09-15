# cgh-codewrite

A cgh plugin that delegates predictable, pattern-following code (tests,
stubs, config, boilerplate) to a cheap model so the primary model spends
no tokens producing it. Its distinguishing move: cgh picks the reference
file to mirror straight from the code graph, so you do not have to name it.

Installs through cgh's plugin entry point. Inert without cgh.

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

### `cgh codewrite gen`

Generate a file from a spec, mirroring the reference, and write it.

```
cgh codewrite gen --spec "pytest tests for UserService: create, update, delete" \
                  --target tests/test_user_service.py
```

The reference (picked, or forced with `--reference`) is run through the
egress gate before it reaches a cloud model: a confidential or PII-labeled
reference is refused. A local backend skips the gate. An existing target is
never overwritten without `--force`; `--stdout` prints instead of writing.

Configure the backend in `.codegraph/config.toml`:

```toml
[plugin.codewrite]
command = "claude -p"   # any agent CLI, invoked with the prompt on stdin
```

The generated code is a proposal. Verify it by running the type-checker,
linter, or tests, never by trusting that it is correct because a later check
was green. This matters most for generated tests: a green run of tests you
did not read proves nothing.

### `codewrite_pick` and `code_write` (MCP tools)

`codewrite_pick(target, reference?)` returns the selection as JSON.
`code_write(spec, target, reference?, force?)` generates and writes the
file, returning what it wrote, the reference used, the egress decision, and
the cost. Both run inside the owner, so the graph read reuses its
connection.

## License

MIT. Plugins that interact with cgh only through the documented plugin
interfaces are not derivative works of cgh.
