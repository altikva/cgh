# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-14
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Lightweight parser for config, devops, and data files.
#
# These files don't produce function/class symbols, but they become
# File nodes in the graph (searchable, visible in architecture_overview,
# reachable by pattern_search). YAML/JSON get top-level key extraction;
# Dockerfiles get stage extraction.

from __future__ import annotations

import re
from pathlib import Path

from . import register_parser
from .base import BaseParser, FileIndex, ResourceDef

# ---------------------------------------------------------------------------
# Dockerfile parser, extracts FROM stages
# ---------------------------------------------------------------------------


@register_parser(".dockerfile")
class DockerfileParser(BaseParser):
    """Dockerfile, extracts FROM stages and build targets."""

    lang = "dockerfile"
    extensions = [".dockerfile"]
    extracts = ["resources"]

    def parse(self, path: Path) -> FileIndex:
        idx = FileIndex(path=str(path), lang=self.lang)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return idx
        for i, line in enumerate(text.splitlines(), 1):
            m = re.match(r"^FROM\s+(\S+)(?:\s+[Aa][Ss]\s+(\S+))?", line)
            if m:
                image, alias = m.group(1), m.group(2)
                name = alias or image.split(":")[0].split("/")[-1]
                idx.resources.append(
                    ResourceDef(
                        id=f"{path}::stage:{name}",
                        name=name,
                        type="docker_stage",
                        file_path=str(path),
                        start_line=i,
                        end_line=i,
                        kind="resource",
                    )
                )
        return idx


# ---------------------------------------------------------------------------
# XML, file node only (no symbol extraction)
#
# YAML / TOML / JSON live in config_data.py and SQL lives in sql.py: they parse
# structured config into sections / resources instead of bare File nodes.
# ---------------------------------------------------------------------------


@register_parser(".xml", ".xsl", ".xslt", ".svg")
class XmlParser(BaseParser):
    """XML/SVG files, indexed as File nodes."""

    lang = "xml"
    extensions = [".xml", ".xsl", ".xslt", ".svg"]
    extracts = []

    def parse(self, path: Path) -> FileIndex:
        return FileIndex(path=str(path), lang=self.lang)


# ---------------------------------------------------------------------------
# Docker Compose + misc config
# ---------------------------------------------------------------------------


@register_parser(".env", ".ini", ".cfg", ".conf", ".properties")
class ConfigParser(BaseParser):
    """Generic config files, indexed as File nodes."""

    lang = "config"
    extensions = [".env", ".ini", ".cfg", ".conf", ".properties"]
    extracts = []

    def parse(self, path: Path) -> FileIndex:
        return FileIndex(path=str(path), lang=self.lang)


@register_parser(".sh", ".bash", ".zsh")
class ShellParser(BaseParser):
    """Shell scripts, extracts function definitions."""

    lang = "shell"
    extensions = [".sh", ".bash", ".zsh"]
    extracts = ["functions"]

    def parse(self, path: Path) -> FileIndex:
        from .base import SymbolDef

        idx = FileIndex(path=str(path), lang=self.lang)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return idx
        for i, line in enumerate(text.splitlines(), 1):
            m = re.match(r"^(?:function\s+)?(\w+)\s*\(\s*\)", line)
            if m:
                name = m.group(1)
                idx.functions.append(
                    SymbolDef(
                        id=f"{path}::{name}",
                        name=name,
                        file_path=str(path),
                        start_line=i,
                        end_line=i,
                        kind="function",
                    )
                )
        return idx
