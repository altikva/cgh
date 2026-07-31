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


@dataclass
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


@dataclass
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


@dataclass
class ImportRef:
    """An import/require/include statement."""

    source_module: str
    symbols: list[str] = field(default_factory=list)


@dataclass
class ResourceDef:
    """A generic resource (Terraform resource, Docker service, K8s manifest, etc.)."""

    id: str
    name: str
    type: str
    file_path: str
    start_line: int
    end_line: int = 0
    kind: str = "resource"  # "resource", "variable", "output", "service"


@dataclass
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


@dataclass
class CodeRef:
    """A reference to a code symbol found in docs."""

    symbol: str
    line: int
    context: str = "inline"  # "inline", "fenced", "link"


@dataclass
class LinkRef:
    """An internal link between files."""

    target: str
    label: str = ""
    line: int = 0


@dataclass
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
