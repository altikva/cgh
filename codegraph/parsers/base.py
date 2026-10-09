# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Base class for all codegraph parsers.
#              Subclass this + use @register_parser to add a new language.

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Shared data classes, all parsers produce these
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CallRef:
    """One call a function makes, with the shape the call resolver needs.

    ``receiver`` is "" for a bare call ``f()``, "self" for a call on the
    instance or class (self, cls, this), "super" for a call on the parent
    class, the dotted receiver text for ``a.b.f()`` ("a.b"), "C()" for a
    call on what a plain call returns (``C(x).f()``, ``new C().f()``), or
    "?" for any other receiver (a subscript, a literal, a chained call).
    ``module`` and ``symbol`` describe the import binding the bare name, or
    the receiver's first segment: ``from m import s`` gives ("m", "s") and
    ``import m`` gives ("m", ""); both stay "" when nothing imports it.
    ``name`` is the callee's defined name: an aliased import
    (``from m import s as t; t()``) records "s".
    """

    name: str
    receiver: str = ""
    module: str = ""
    symbol: str = ""


@dataclass(slots=True)
class SymbolDef:
    """A function, method, or callable."""

    id: str  # "<file_path>::<qualified_name>"
    name: str
    file_path: str
    start_line: int
    end_line: int
    docstring: str = ""
    class_name: str | None = None  # set when it's a method
    calls: list[str] = field(default_factory=list)
    kind: str = "function"  # "function", "method", "arrow", "handler", etc.
    # Filled by the parsers that know call shapes and imports (Python and
    # TS/JS). Empty for the others, whose calls are linked by name alone.
    call_refs: list[CallRef] = field(default_factory=list)


# An import binding: local name -> (module, symbol), symbol "" for a module.
Bindings = dict[str, tuple[str, str]]


def bind_calls(raw: list[tuple[str, str]], bindings: Bindings) -> list[CallRef]:
    """Attach import bindings to raw (name, receiver) calls, deduplicated.

    A bare call takes the binding of its own name (and the imported name when
    it was aliased); an attribute call takes the binding of its receiver's
    first segment. ``self``, ``super`` and ``?`` receivers are never bound.
    Star imports sit in ``bindings`` under "*<module>" as (module, "*"); a
    bare call nothing else binds gets them all, as ("m1|m2", "*").
    """
    stars = "|".join(sorted(m for k, (m, s) in bindings.items() if s == "*"))
    out: dict[tuple[str, str, str, str], CallRef] = {}
    for name, receiver in raw:
        module = symbol = ""
        if not receiver:
            bound = bindings.get(name)
            if bound is not None:
                module, symbol = bound
                if symbol and symbol != "default":
                    name = symbol.rsplit(".", 1)[-1]
            elif stars:
                module, symbol = stars, "*"
        elif receiver not in ("self", "super", "?"):
            bound = bindings.get(receiver.split(".", 1)[0].removesuffix("()"))
            if bound is not None:
                module, symbol = bound
        ref = CallRef(name=name, receiver=receiver, module=module, symbol=symbol)
        out.setdefault((name, receiver, module, symbol), ref)
    return list(out.values())


@dataclass(slots=True)
class ClassDef:
    """A class, struct, interface, trait, type, etc."""

    id: str
    name: str
    file_path: str
    start_line: int
    end_line: int
    docstring: str = ""
    bases: list[str] = field(default_factory=list)
    kind: str = "class"  # "class", "interface", "struct", "trait", "type"


@dataclass(slots=True)
class ImportRef:
    """An import/require/include statement."""

    source_module: str
    symbols: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ResourceDef:
    """A generic resource (Terraform resource, Docker service, K8s manifest, etc.)."""

    id: str
    name: str
    type: str
    file_path: str
    start_line: int
    end_line: int = 0
    kind: str = "resource"  # "resource", "variable", "output", "service"
    # Terraform: the in-module address ("google_x.y", "data.t.n", "var.v",
    # "local.l", "module.m", "output.o"), the addresses the block's
    # expressions reference (as written: "module.m.out" keeps the output),
    # a module block's raw `source` and its input argument names, and a
    # one-line summary for text search.
    address: str = ""
    refs: list[str] = field(default_factory=list)
    source: str = ""
    inputs: list[str] = field(default_factory=list)
    docstring: str = ""


@dataclass(slots=True)
class SectionDef:
    """A documentation section (Markdown heading, RST section, etc.)."""

    id: str
    title: str
    level: int
    file_path: str
    start_line: int
    end_line: int
    body_preview: str = ""
    anchor: str = ""
    # "doc" for prose headings, "config" for the keys config files expose
    # through this same model. Without it a docs view is mostly YAML.
    kind: str = "doc"


@dataclass(slots=True)
class CodeRef:
    """A reference to a code symbol found in docs."""

    symbol: str
    line: int
    context: str = "inline"  # "inline", "fenced", "link"


@dataclass(slots=True)
class LinkRef:
    """An internal link between files."""

    target: str
    label: str = ""
    line: int = 0


@dataclass(slots=True)
class FileIndex:
    """
    The universal output of every parser.
    Each parser populates whichever fields are relevant.
    """

    path: str
    lang: str

    # Code symbols
    functions: list[SymbolDef] = field(default_factory=list)
    classes: list[ClassDef] = field(default_factory=list)
    imports: list[ImportRef] = field(default_factory=list)

    # Infrastructure / config
    resources: list[ResourceDef] = field(default_factory=list)

    # Documentation
    sections: list[SectionDef] = field(default_factory=list)
    code_refs: list[CodeRef] = field(default_factory=list)
    links: list[LinkRef] = field(default_factory=list)

    # Full text a parser extracted for a binary/compound format (pdf, xlsx,
    # docx). Scanners (PII, secrets, summaries) run on THIS when set, not on
    # the raw file bytes: reading a pdf or a zip-based xlsx as text yields
    # binary noise (false-positive card/phone matches) and hides the real
    # cell / page content (missed PII). Empty for source files, whose raw
    # text is already the right thing to scan.
    scan_text: str = ""


# ---------------------------------------------------------------------------
# Base parser
# ---------------------------------------------------------------------------


class BaseParser(ABC):
    """
    Abstract base class for all codegraph parsers.

    To add a new language:
      1. Create a new file in codegraph/parsers/
      2. Subclass BaseParser
      3. Set class attributes: lang, extensions, extracts
      4. Implement parse()
      5. Decorate with @register_parser(".ext1", ".ext2")

    That's it, codegraph auto-discovers and uses it.
    """

    # --- Class attributes (override in subclass) ---
    lang: str = "unknown"
    extensions: list[str] = []
    extracts: list[str] = []  # e.g., ["functions", "classes", "imports"]
    description: str = ""  # one-line human description
    tree_sitter_lang: str | None = None  # if using tree-sitter, the grammar name

    @abstractmethod
    def parse(self, path: Path) -> FileIndex:
        """
        Parse a source file and return a FileIndex.
        Must not raise on malformed input. Return partial results instead.
        """
        ...

    def can_parse(self, path: Path) -> bool:
        """Check if this parser can handle a file (default: check extension)."""
        return path.suffix.lower() in self.extensions

    def __repr__(self) -> str:
        exts = ", ".join(self.extensions)
        return f"<{self.__class__.__name__} lang={self.lang} exts=[{exts}]>"
