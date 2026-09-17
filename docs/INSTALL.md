# Install

**Python 3.11, 3.12, 3.13, or 3.14.** The default install pulls the DuckDB
graph backend, which ships wheels on every supported Python. No Python at
all? Run the standalone binary through `npx @altikva/cgh`, or download it from
the [latest release](https://github.com/altikva/cgh/releases/latest).

### One-line install

The install scripts detect your environment (macOS, Linux, WSL, Git Bash, or
native Windows), install cgh, and offer to add the `cgh` command to your PATH.

macOS, Linux, WSL, Git Bash ([install.sh](./install.sh)):

```bash
curl -fsSL https://raw.githubusercontent.com/altikva/cgh/main/install.sh | bash

# Core + the five first-party plugins in one shot:
curl -fsSL https://raw.githubusercontent.com/altikva/cgh/main/install.sh | CGH_PLUGINS=1 bash
```

Windows PowerShell ([install.ps1](./install.ps1)):

```powershell
irm https://raw.githubusercontent.com/altikva/cgh/main/install.ps1 | iex

# Core + the five first-party plugins in one shot:
$env:CGH_PLUGINS = 1; irm https://raw.githubusercontent.com/altikva/cgh/main/install.ps1 | iex
```

Both try uv, then pipx, then pip, and move on to the next one when an
install fails instead of giving up. Behind a corporate network:

| Variable | For |
|---|---|
| `CGH_INDEX_URL` | an internal PyPI mirror (Nexus, Artifactory) instead of pypi.org |
| `CGH_TRUSTED_HOST` | that mirror serving a self-signed certificate |
| `CGH_TIMEOUT`, `CGH_RETRIES` | a slow or flaky link (defaults: 120 s, 5 tries) |

```bash
curl -fsSL .../install.sh | CGH_INDEX_URL=https://nexus.corp/repository/pypi/simple bash
```

```powershell
$env:CGH_INDEX_URL = "https://nexus.corp/repository/pypi/simple"; irm .../install.ps1 | iex
```

The variables reach uv, pipx and pip alike; an index you already
export as `PIP_INDEX_URL` is honored and never overwritten.

### With pip, pipx, or uv

```bash
# From PyPI (most users)
pip install cgh

# Isolated install that also handles PATH for you
pipx install cgh        # or: uv tool install cgh

# From source (for development)
git clone https://github.com/altikva/cgh.git
cd cgh
uv pip install -e .     # or: pip install -e .
```

Optional extras (none are required; the core install is lean and works on Python 3.11 through 3.14):

```bash
pip install "cgh[plugins]" # the five first-party plugins (docs, pii, summarize, classify, bugreport)
pip install "cgh[langs]"   # C# and Ruby parsers (tree-sitter grammars, abi3 wheels)
pip install "cgh[lsp]"     # precise cross-file Python call resolution (jedi)

# Combine extras in one bracket, comma-separated:
pip install "cgh[langs,lsp]"

# Or everything above, in one shot:
pip install "cgh[full]"
```

Quote the package spec (`"cgh[...]"`) so zsh and bash do not try to glob the brackets. The same form works with `pipx install`, `uv tool install`, `uv pip install`, and from a source checkout: `pip install -e ".[langs,lsp]"`.

```bash
cgh --version
cgh init           # initialize in any project
cgh serve          # start the MCP server for Claude / Cursor / Codex / Gemini / Bob
cgh stop           # stop this repo's owner + worker (alias of serve --stop)
```

### If `cgh` is not found after install

`pip install` drops the `cgh` executable in a Scripts directory that is not
always on PATH (common on Windows). Two fixes:

```bash
# Run it as a module, no PATH change needed
python -m cgh --version

# Or add the cgh command's directory to your PATH
python -m cgh ensurepath
```

`pipx install cgh` and `uv tool install cgh` avoid this by managing PATH
themselves. The Python import name is still `codegraph` (e.g.
`from codegraph.parsers import ...`); only the CLI is named `cgh`, same
pattern as `pip install pillow` then `import PIL`.

---

---

[Back to the README](../README.md)
