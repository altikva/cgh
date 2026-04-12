# Parser Plugin Architecture

codegraph uses a plugin system for language support. Each parser is a Python class that extracts symbols from source files and produces a `FileIndex` -- a normalized data structure that the indexer stores in the graph DB.

---

## How It Works

1. Parser modules live in `codegraph/parsers/`
2. Each module decorates a class with `@register_parser(".ext")`
3. On import, `codegraph/parsers/__init__.py` auto-discovers all modules in the package
4. The indexer calls `get_parser(extension)` to resolve the right parser for each file
5. The parser returns a `FileIndex` containing functions, classes, imports, and other symbols

No configuration needed. Drop a file in the parsers directory and it works.

---

## Data Classes

Every parser produces a `FileIndex`. The data classes are defined in `codegraph/parsers/base.py`.

### `FileIndex`

The universal output of every parser:

```python
@dataclass
class FileIndex:
    path: str           # absolute file path
    lang: str           # language identifier ("python", "rust", etc.)

    # Code symbols
    functions: list[SymbolDef]    # functions, methods, handlers
    classes: list[ClassDef]       # classes, structs, interfaces, traits
    imports: list[ImportRef]      # import/require statements

    # Infrastructure
    resources: list[ResourceDef]  # Terraform resources, Docker services, etc.

    # Documentation
    sections: list[SectionDef]    # Markdown headings
    code_refs: list[CodeRef]      # code symbol references found in docs
    links: list[LinkRef]          # internal links between files
```

### `SymbolDef`

A function, method, or callable:

```python
@dataclass
class SymbolDef:
    id: str                   # "<file_path>::<qualified_name>"
    name: str
    file_path: str
    start_line: int
    end_line: int
    docstring: str = ""
    class_name: str | None = None   # set when it's a method
    calls: list[str] = field(default_factory=list)
    kind: str = "function"    # "function", "method", "arrow", "handler"
```

### `ClassDef`

A class, struct, interface, or trait:

```python
@dataclass
class ClassDef:
    id: str
    name: str
    file_path: str
    start_line: int
    end_line: int
    docstring: str = ""
    bases: list[str] = field(default_factory=list)
    kind: str = "class"       # "class", "interface", "struct", "trait"
```

### `ImportRef`

An import or require statement:

```python
@dataclass
class ImportRef:
    source_module: str
    symbols: list[str] = field(default_factory=list)
```

### `ResourceDef`

A generic resource (for Terraform, Docker, K8s, etc.):

```python
@dataclass
class ResourceDef:
    id: str
    name: str
    type: str
    file_path: str
    start_line: int
    end_line: int = 0
    kind: str = "resource"    # "resource", "variable", "output", "service"
```

### `SectionDef`

A documentation section (Markdown heading):

```python
@dataclass
class SectionDef:
    id: str
    title: str
    level: int
    file_path: str
    start_line: int
    end_line: int
    body_preview: str = ""
    anchor: str = ""
```

### `CodeRef` and `LinkRef`

References to code symbols and internal links found in documentation:

```python
@dataclass
class CodeRef:
    symbol: str
    line: int
    context: str = "inline"   # "inline", "fenced", "link"

@dataclass
class LinkRef:
    target: str
    label: str = ""
    line: int = 0
```

---

## BaseParser

All parsers inherit from `BaseParser`:

```python
class BaseParser(ABC):
    lang: str = "unknown"
    extensions: list[str] = []
    extracts: list[str] = []
    description: str = ""
    tree_sitter_lang: str | None = None

    @abstractmethod
    def parse(self, path: Path) -> FileIndex:
        """Parse a source file and return a FileIndex."""
        ...

    def can_parse(self, path: Path) -> bool:
        """Check if this parser can handle a file (default: check extension)."""
        return path.suffix.lower() in self.extensions
```

### Class Attributes

| Attribute | Required | Description |
|-----------|----------|-------------|
| `lang` | Yes | Language identifier (e.g., `"rust"`, `"python"`) |
| `extensions` | Yes | List of file extensions (e.g., `[".rs"]`) |
| `extracts` | Yes | List of symbol types this parser produces |
| `description` | No | One-line description shown in `cgh parsers` |
| `tree_sitter_lang` | No | tree-sitter grammar name (if using tree-sitter) |

### Rules

- `parse()` must not raise exceptions on malformed input. Return partial results instead.
- `id` fields should follow the pattern `"<file_path>::<qualified_name>"`.
- `start_line` and `end_line` are 1-indexed.
- Populate `calls` on `SymbolDef` with names of functions called within the body. These become `CALLS` edges in the graph.
- Populate `bases` on `ClassDef` with parent class names. These become `INHERITS` edges.

---

## Step-by-Step: Adding Rust Support

Here is a complete example of adding a new language parser for Rust.

### Step 1: Create the File

Create `codegraph/parsers/rust.py`:

```python
from __future__ import annotations

from pathlib import Path

from codegraph.parsers import register_parser
from codegraph.parsers.base import (
    BaseParser,
    ClassDef,
    FileIndex,
    ImportRef,
    SymbolDef,
)


@register_parser(".rs")
class RustParser(BaseParser):
    """Rust source files (tree-sitter)."""

    lang = "rust"
    extensions = [".rs"]
    extracts = ["functions", "structs", "traits", "impls", "imports"]
    description = "Rust source files"
    tree_sitter_lang = "rust"

    def parse(self, path: Path) -> FileIndex:
        source = path.read_text(encoding="utf-8", errors="replace")
        file_path = str(path)

        functions: list[SymbolDef] = []
        classes: list[ClassDef] = []
        imports: list[ImportRef] = []

        # --- tree-sitter parsing ---
        try:
            import tree_sitter_rust as ts_rust
            from tree_sitter import Language, Parser

            RUST_LANG = Language(ts_rust.language())
            parser = Parser(RUST_LANG)
            tree = parser.parse(source.encode("utf-8"))
            root = tree.root_node
        except ImportError:
            # tree-sitter-rust not installed, fall back to regex
            return self._parse_regex(source, file_path)

        # Walk the AST
        for node in self._walk(root):
            if node.type == "function_item":
                name = self._child_text(node, "name", source)
                if name:
                    functions.append(SymbolDef(
                        id=f"{file_path}::{name}",
                        name=name,
                        file_path=file_path,
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        kind="function",
                    ))

            elif node.type == "struct_item":
                name = self._child_text(node, "name", source)
                if name:
                    classes.append(ClassDef(
                        id=f"{file_path}::{name}",
                        name=name,
                        file_path=file_path,
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        kind="struct",
                    ))

            elif node.type == "trait_item":
                name = self._child_text(node, "name", source)
                if name:
                    classes.append(ClassDef(
                        id=f"{file_path}::{name}",
                        name=name,
                        file_path=file_path,
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        kind="trait",
                    ))

            elif node.type == "use_declaration":
                path_text = source[node.start_byte:node.end_byte]
                imports.append(ImportRef(
                    source_module=path_text.replace("use ", "").rstrip(";").strip(),
                ))

        return FileIndex(
            path=file_path,
            lang="rust",
            functions=functions,
            classes=classes,
            imports=imports,
        )

    def _walk(self, node):
        """Depth-first walk of tree-sitter nodes."""
        yield node
        for child in node.children:
            yield from self._walk(child)

    def _child_text(self, node, field_name, source):
        """Get the text of a named child node."""
        child = node.child_by_field_name(field_name)
        if child:
            return source[child.start_byte:child.end_byte]
        return None

    def _parse_regex(self, source, file_path):
        """Fallback regex parser when tree-sitter-rust is not installed."""
        import re

        functions = []
        classes = []

        for i, line in enumerate(source.splitlines(), 1):
            # fn name(...)
            m = re.match(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)", line)
            if m:
                functions.append(SymbolDef(
                    id=f"{file_path}::{m.group(1)}",
                    name=m.group(1),
                    file_path=file_path,
                    start_line=i,
                    end_line=i,
                    kind="function",
                ))

            # struct Name
            m = re.match(r"^\s*(?:pub\s+)?struct\s+(\w+)", line)
            if m:
                classes.append(ClassDef(
                    id=f"{file_path}::{m.group(1)}",
                    name=m.group(1),
                    file_path=file_path,
                    start_line=i,
                    end_line=i,
                    kind="struct",
                ))

        return FileIndex(path=file_path, lang="rust", functions=functions, classes=classes)
```

### Step 2: Install the Grammar (Optional)

If using tree-sitter:

```bash
pip install tree-sitter-rust
# or
uv pip install codegraph[rust]
```

The parser works without tree-sitter (falls back to regex) but tree-sitter gives better results.

### Step 3: Verify

```bash
cgh parsers
```

The new parser should appear in the table:

```text
 Language   Extensions  Extracts                             Description
 rust       .rs         functions, structs, traits, impls    Rust source files
```

Then index and test:

```bash
cgh index
cgh search "main"
cgh lookup my_function
```

### Step 4: Done

No config changes, no registry edits. The `@register_parser` decorator and auto-discovery handle everything.

---

## Existing Parsers

| File | Language | Approach |
|------|----------|----------|
| `python.py` | Python | tree-sitter (`tree-sitter-python`) |
| `typescript.py` | TypeScript, JavaScript | tree-sitter (`tree-sitter-typescript`) |
| `vue.py` | Vue SFC | tree-sitter (extracts `<script>` block) |
| `terraform.py` | Terraform HCL | regex + brace tracking |
| `markdown.py` | Markdown | regex (headings, links, code refs) |

---

## Optional Dependencies

Extra language grammars are declared in `pyproject.toml` as optional dependencies:

```toml
[project.optional-dependencies]
rust = ["tree-sitter-rust>=0.23"]
go = ["tree-sitter-go>=0.23"]
java = ["tree-sitter-java>=0.23"]
all = ["codegraph[rust,go,java]"]
```

Install with:

```bash
pip install codegraph[rust]
pip install codegraph[all]
```
