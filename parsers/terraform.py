# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-11
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __contributors__ = ["jndjama (Joy Ndjama)"]
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# __maintainer__ = "jndjama (Joy Ndjama)"
# __email__ = "joy.ndjama@altikva.com"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Terraform HCL parser (plugin architecture).
#              Extracts resource blocks, variable/output declarations, and
#              inter-resource references (${resource.type.name.attr}).
#              Migrated from codegraph/parser_terraform.py.

from __future__ import annotations

import re
from pathlib import Path

from . import register_parser
from .base import BaseParser, FileIndex, ImportRef, ResourceDef

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_RE_RESOURCE = re.compile(r'^resource\s+"([^"]+)"\s+"([^"]+)"\s*\{', re.MULTILINE)
_RE_VARIABLE = re.compile(r'^variable\s+"([^"]+)"\s*\{', re.MULTILINE)
_RE_OUTPUT = re.compile(r'^output\s+"([^"]+)"\s*\{', re.MULTILINE)
# References like: resource.google_storage_bucket.my_bucket
# or interpolation: ${google_storage_bucket.my_bucket.id}
_RE_REF = re.compile(r"(?:resource\.|(?<=\$\{))([a-z][a-z0-9_]*)\.([a-zA-Z0-9_-]+)")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_block_end(lines: list[str], start_idx: int) -> int:
    """Return the line index (0-based) where the HCL block starting at
    *start_idx* closes (depth reaches 0).  Returns last line on failure."""
    depth = 0
    for i in range(start_idx, len(lines)):
        depth += lines[i].count("{") - lines[i].count("}")
        if depth <= 0 and i > start_idx:
            return i
    return len(lines) - 1


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


@register_parser(".tf", ".tfvars")
class TerraformParser(BaseParser):
    """Terraform HCL parser using regex + brace-depth tracking."""

    lang = "terraform"
    extensions = [".tf", ".tfvars"]
    extracts = ["resources", "variables", "outputs", "depends_on"]
    description = "Terraform HCL files (.tf, .tfvars)"

    def parse(self, path: Path) -> FileIndex:
        path_str = str(path)
        try:
            text = Path(path_str).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return FileIndex(path=path_str, lang=self.lang)

        lines = text.splitlines()
        index = FileIndex(path=path_str, lang=self.lang)

        # --- Resources ---
        for m in _RE_RESOURCE.finditer(text):
            res_type = m.group(1)
            res_name = m.group(2)
            start_line = text[: m.start()].count("\n") + 1
            end_line = _find_block_end(lines, start_line - 1) + 1

            # Extract raw block text to find references
            block_text = "\n".join(lines[start_line - 1 : end_line])
            refs: list[str] = []
            for ref_m in _RE_REF.finditer(block_text):
                ref_type, ref_name = ref_m.group(1), ref_m.group(2)
                ref_id = f"{path_str}::{ref_type}.{ref_name}"
                if ref_id != f"{path_str}::{res_type}.{res_name}":
                    refs.append(ref_id)

            # De-duplicate while preserving order
            unique_refs = list(dict.fromkeys(refs))

            index.resources.append(
                ResourceDef(
                    id=f"{path_str}::{res_type}.{res_name}",
                    name=res_name,
                    type=res_type,
                    file_path=path_str,
                    start_line=start_line,
                    end_line=end_line,
                    kind="resource",
                )
            )

            # Store inter-resource references as ImportRef entries so the
            # dependency graph (depends_on) is preserved in the FileIndex.
            for ref_id in unique_refs:
                index.imports.append(
                    ImportRef(
                        source_module=ref_id,
                        symbols=[f"{res_type}.{res_name}"],
                    )
                )

        # --- Variables ---
        for m in _RE_VARIABLE.finditer(text):
            var_name = m.group(1)
            start_line = text[: m.start()].count("\n") + 1
            index.resources.append(
                ResourceDef(
                    id=f"{path_str}::var.{var_name}",
                    name=var_name,
                    type=f"var.{var_name}",
                    file_path=path_str,
                    start_line=start_line,
                    end_line=0,
                    kind="variable",
                )
            )

        # --- Outputs ---
        for m in _RE_OUTPUT.finditer(text):
            out_name = m.group(1)
            start_line = text[: m.start()].count("\n") + 1
            index.resources.append(
                ResourceDef(
                    id=f"{path_str}::output.{out_name}",
                    name=out_name,
                    type=f"output.{out_name}",
                    file_path=path_str,
                    start_line=start_line,
                    end_line=0,
                    kind="output",
                )
            )

        return index
