# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-11
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __contributors__ = ["jndjama (Joy Ndjama)"]
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# __maintainer__ = "jndjama (Joy Ndjama)"
# __email__ = "joy.ndjama@altikva.com"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Terraform HCL parser on the tree-sitter-hcl grammar.
#              One ResourceDef per top-level resource, data, module,
#              variable, output and provider block and per locals entry,
#              each keyed by its Terraform address (google_x.y, data.t.n,
#              module.m, var.v, output.o, local.l) and carrying the
#              addresses its expressions reference, nested blocks, string
#              interpolations and heredocs included. A module call also
#              yields one entry per input argument (module.m.<arg>), and a
#              moved / import / removed block one entry referencing the
#              state addresses it names. A .tfvars file yields one entry
#              per assignment, which references var.<key>.
#              Scoping (which module directory an address resolves in) is
#              the indexer's job: see codegraph/analysis/terraform.py.

from __future__ import annotations

import re
from pathlib import Path

from . import register_parser
from .base import BaseParser, FileIndex, ResourceDef

try:
    import tree_sitter as _ts
    import tree_sitter_hcl as _ts_hcl

    _PARSER = _ts.Parser(_ts.Language(_ts_hcl.language()))
except Exception:  # pragma: no cover - only without the grammar wheel
    _PARSER = None

# Top-level block types that become symbols, with their label count.
_SYMBOL_BLOCKS = {
    "resource": 2,
    "data": 2,
    "module": 1,
    "variable": 1,
    "output": 1,
    "provider": 1,
}
# Module block arguments that are Terraform meta-arguments, not inputs.
_MODULE_META = {"source", "version", "count", "for_each", "providers", "depends_on"}
# Expression roots that never name a block of this module.
_NON_BLOCK_ROOTS = {"each", "count", "self", "path", "terraform"}
_SUMMARY_CAP = 400
_VALUE_CAP = 80


def _text(node) -> str:
    return node.text.decode("utf-8", errors="replace") if node is not None else ""


def _squash(text: str, cap: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= cap else text[: cap - 3] + "..."


def _label(node) -> str:
    """A block label: a quoted string (its literal content) or an identifier."""
    if node.type == "string_lit":
        raw = _text(node)
        return raw[1:-1] if raw.startswith('"') and raw.endswith('"') else raw
    return _text(node)


def _block_parts(block) -> tuple[str, list[str], object | None]:
    """(block type, labels, body node) of a `block` node."""
    btype = ""
    labels: list[str] = []
    body = None
    for child in block.children:
        if child.type == "identifier" and not btype:
            btype = _text(child)
        elif child.type in ("string_lit", "identifier"):
            labels.append(_label(child))
        elif child.type == "body":
            body = child
    return btype, labels, body


def _attributes(body) -> list[tuple[str, object, object]]:
    """(name, expression node, attribute node) for each attribute directly
    in ``body``."""
    out: list[tuple[str, object, object]] = []
    if body is None:
        return out
    for child in body.children:
        if child.type != "attribute":
            continue
        name = ""
        expr = None
        for part in child.children:
            if part.type == "identifier" and not name:
                name = _text(part)
            elif part.type == "expression":
                expr = part
        if name:
            out.append((name, expr if expr is not None else child, child))
    return out


def _string_value(expr) -> str:
    """The literal content of a plain quoted string expression, else ""."""
    raw = _squash(_text(expr), 4096)
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"' and "${" not in raw:
        return raw[1:-1]
    return ""


def _reference(var_node) -> list[str]:
    """The block addresses a traversal starting at ``var_node`` names.

    ``var.x[0].y`` gives var.x; ``module.m.out`` gives module.m.out (the
    output, which keeps the module address as its prefix); ``t.n.attr``
    gives t.n when ``t`` looks like a provider type (it has an underscore,
    which also keeps for-loop variables like ``s.name`` out).
    """
    root = _text(var_node)
    if root in _NON_BLOCK_ROOTS:
        return []
    parts = [root]
    sib = var_node.next_sibling
    while sib is not None and sib.type in ("get_attr", "index", "splat"):
        if sib.type == "splat":
            break
        if sib.type == "get_attr":
            for c in sib.children:
                if c.type == "identifier":
                    parts.append(_text(c))
                    break
        if len(parts) >= 4:
            break
        sib = sib.next_sibling
    if root in ("var", "local") and len(parts) >= 2:
        return [f"{root}.{parts[1]}"]
    if root == "data" and len(parts) >= 3:
        return [f"data.{parts[1]}.{parts[2]}"]
    if root == "module" and len(parts) >= 2:
        if len(parts) >= 3:
            return [f"module.{parts[1]}.{parts[2]}"]
        return [f"module.{parts[1]}"]
    if root in ("var", "local", "data", "module"):
        return []
    if "_" in root and len(parts) >= 2:
        return [f"{root}.{parts[1]}"]
    return []


def _iterators(node) -> set[str]:
    """Names a `dynamic "x"` block binds under ``node`` (x, or its
    `iterator` argument): x.value inside it is not a resource address."""
    names: set[str] = set()
    stack = [node]
    while stack:
        cur = stack.pop()
        if cur.type == "block":
            btype, labels, body = _block_parts(cur)
            if btype == "dynamic" and labels:
                names.add(labels[0])
                for name, expr, _attr in _attributes(body):
                    if name == "iterator":
                        names.add(_squash(_text(expr), 200))
        stack.extend(cur.children)
    return names


def _refs_in(node) -> list[str]:
    """Every block address referenced under ``node``, in source order."""
    out: list[str] = []
    stack = [node]
    while stack:
        cur = stack.pop()
        if cur.type == "variable_expr":
            out.extend(_reference(cur))
            continue
        if cur.type == "comment":
            continue
        stack.extend(reversed(cur.children))
    bound = _iterators(node)
    return [r for r in dict.fromkeys(out) if r.partition(".")[0] not in bound]


def _summary(head: str, body) -> str:
    """A one-line text-search summary: the header, the top-level arguments
    (key=value, values cut short) and the nested block types."""
    parts = [head]
    for name, expr, _attr in _attributes(body):
        parts.append(f"{name}={_squash(_text(expr), _VALUE_CAP)}")
    nested = []
    if body is not None:
        for child in body.children:
            if child.type == "block":
                btype, labels, _ = _block_parts(child)
                nested.append(" ".join([btype, *labels]))
    if nested:
        parts.append("blocks: " + ", ".join(dict.fromkeys(nested)))
    return _squash("; ".join(parts), _SUMMARY_CAP)


def _lines(node) -> tuple[int, int]:
    return node.start_point[0] + 1, node.end_point[0] + 1


def _top_blocks(root) -> list:
    body = next((c for c in root.children if c.type == "body"), None)
    if body is None:
        return []
    return [c for c in body.children if c.type == "block"]


_RE_INDEX = re.compile(r"\[[^\]]*\]")
_RE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")


def _state_address(expr) -> str:
    """The resource address a moved / import / removed argument names,
    instance keys dropped: module.vpc.google_x.y[0] gives
    module.vpc.google_x.y. "" when the text is not an address."""
    if expr is None:
        return ""
    text = _RE_INDEX.sub("", re.sub(r"\s+", "", _text(expr)))
    parts = text.split(".")
    if not parts or not all(_RE_IDENT.match(p) for p in parts):
        return ""
    return text


# Blocks without a label that name state addresses (Terraform 1.1+ moved,
# 1.5+ import, 1.7+ removed): the argument holding the address the block
# is known by, and the arguments it references.
_ADDRESS_BLOCKS = {
    "moved": ("to", ("from", "to")),
    "import": ("to", ("to",)),
    "removed": ("from", ("from",)),
}


def _address_block(path_str: str, btype: str, block, body) -> ResourceDef | None:
    """A moved / import / removed block, keyed by the address it names.

    Its refs are full state addresses (module.m.google_x.y kept whole): the
    indexer links module.m and, when the module's source is known, the
    resource inside it (see codegraph/analysis/terraform.py)."""
    key_arg, ref_args = _ADDRESS_BLOCKS[btype]
    args = {name: expr for name, expr, _a in _attributes(body)}
    key = _state_address(args.get(key_arg))
    if not key:
        return None
    refs = [a for a in (_state_address(args.get(n)) for n in ref_args) if a]
    start, end = _lines(block)
    return ResourceDef(
        id=f"{path_str}::{btype}.{key}",
        name=key,
        type=btype,
        file_path=path_str,
        start_line=start,
        end_line=end,
        kind=btype,
        address=f"{btype}.{key}",
        refs=list(dict.fromkeys(refs)),
        docstring=_summary(btype, body),
    )


def _module_args(path_str: str, module: str, source: str, attrs) -> list:
    """One entry per input argument of a module call, so a search for an
    input name lands on the call: address module.<m>.<arg>, the argument's
    own lines, its value as the text-search summary. It references the
    module's variable of that name (when the source resolves), nothing
    else: the module block keeps the expression references."""
    out: list[ResourceDef] = []
    for name, expr, attr in attrs:
        if name in _MODULE_META:
            continue
        s, e = _lines(attr)
        address = f"module.{module}.{name}"
        out.append(
            ResourceDef(
                id=f"{path_str}::{address}",
                name=name,
                type=f"module.{module}",
                file_path=path_str,
                start_line=s,
                end_line=e,
                kind="module_arg",
                address=address,
                source=source,
                inputs=[name],
                docstring=_squash(f"{address} = {_text(expr)}", _SUMMARY_CAP),
            )
        )
    return out


def _parse_tf(path_str: str, root) -> list[ResourceDef]:
    out: list[ResourceDef] = []
    for block in _top_blocks(root):
        btype, labels, body = _block_parts(block)
        start, end = _lines(block)
        if btype in _ADDRESS_BLOCKS:
            res = _address_block(path_str, btype, block, body)
            if res is not None:
                out.append(res)
            continue
        if btype == "locals":
            for name, expr, attr in _attributes(body):
                s, e = _lines(attr)
                address = f"local.{name}"
                out.append(
                    ResourceDef(
                        id=f"{path_str}::{address}",
                        name=name,
                        type="local",
                        file_path=path_str,
                        start_line=s,
                        end_line=e,
                        kind="local",
                        address=address,
                        refs=_refs_in(expr),
                        docstring=_squash(f"{address} = {_text(expr)}", _SUMMARY_CAP),
                    )
                )
            continue
        want = _SYMBOL_BLOCKS.get(btype)
        if want is None or len(labels) < want:
            continue
        head = " ".join([btype, *(f'"{lb}"' for lb in labels[:want])])
        res = ResourceDef(
            id="",
            name=labels[want - 1],
            type="",
            file_path=path_str,
            start_line=start,
            end_line=end,
            kind=btype,
            docstring=_summary(head, body),
        )
        attrs = _attributes(body)
        if btype == "resource":
            res.type = labels[0]
            res.address = f"{labels[0]}.{labels[1]}"
        elif btype == "data":
            res.type = labels[0]
            res.address = f"data.{labels[0]}.{labels[1]}"
        elif btype == "module":
            res.address = f"module.{labels[0]}"
            src = next((e for n, e, _a in attrs if n == "source"), None)
            res.source = _string_value(src) if src is not None else ""
            res.type = res.source or "module"
            res.inputs = [n for n, _e, _a in attrs if n not in _MODULE_META]
        elif btype == "variable":
            res.type = f"var.{labels[0]}"
            res.address = f"var.{labels[0]}"
        elif btype == "output":
            res.type = f"output.{labels[0]}"
            res.address = f"output.{labels[0]}"
        elif btype == "provider":
            alias = next((_string_value(e) for n, e, _a in attrs if n == "alias"), "")
            res.type = "provider"
            res.address = f"provider.{labels[0]}" + (f".{alias}" if alias else "")
            res.name = labels[0]
        res.id = f"{path_str}::{res.address}"
        res.refs = [r for r in _refs_in(block) if r != res.address]
        out.append(res)
        if btype == "module":
            out.extend(_module_args(path_str, labels[0], res.source, attrs))
    return out


def _parse_tfvars(path_str: str, root) -> list[ResourceDef]:
    body = next((c for c in root.children if c.type == "body"), None)
    out: list[ResourceDef] = []
    for name, expr, attr in _attributes(body):
        s, e = _lines(attr)
        out.append(
            ResourceDef(
                id=f"{path_str}::tfvars.{name}",
                name=name,
                type="tfvars",
                file_path=path_str,
                start_line=s,
                end_line=e,
                kind="tfvars",
                address=name,
                refs=[f"var.{name}"],
                docstring=_squash(f"{name} = {_text(expr)}", _SUMMARY_CAP),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Fallback without the grammar: the top-level blocks only, no references.
# ---------------------------------------------------------------------------

_RE_BLOCK = re.compile(
    r'^(resource|data)\s+"([^"]+)"\s+"([^"]+)"\s*\{|^(module|variable|output)\s+"([^"]+)"\s*\{',
    re.MULTILINE,
)


def _parse_regex(path_str: str, text: str) -> list[ResourceDef]:
    out: list[ResourceDef] = []
    for m in _RE_BLOCK.finditer(text):
        line = text.count("\n", 0, m.start()) + 1
        if m.group(1):
            kind, rtype, name = m.group(1), m.group(2), m.group(3)
            address = (
                f"{rtype}.{name}" if kind == "resource" else f"data.{rtype}.{name}"
            )
        else:
            kind, name = m.group(4), m.group(5)
            prefix = {"module": "module", "variable": "var", "output": "output"}[kind]
            address = f"{prefix}.{name}"
            rtype = address if kind != "module" else "module"
        out.append(
            ResourceDef(
                id=f"{path_str}::{address}",
                name=name,
                type=rtype,
                file_path=path_str,
                start_line=line,
                end_line=line,
                kind=kind,
                address=address,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


@register_parser(".tf", ".tfvars")
class TerraformParser(BaseParser):
    """Terraform HCL parser on tree-sitter-hcl (regex fallback without it)."""

    lang = "terraform"
    extensions = [".tf", ".tfvars"]
    extracts = [
        "resources",
        "data",
        "modules",
        "variables",
        "outputs",
        "locals",
        "providers",
        "references",
        "tfvars",
    ]
    description = "Terraform HCL files (.tf, .tfvars)"
    tree_sitter_lang = "hcl"

    def parse(self, path: Path) -> FileIndex:
        path_str = str(path)
        try:
            data = Path(path_str).read_bytes()
        except OSError:
            return FileIndex(path=path_str, lang=self.lang)
        return self.parse_bytes(path_str, data)

    def parse_bytes(self, path_str: str, data: bytes) -> FileIndex:
        """Parse ``data`` as the content of ``path_str``, which need not
        exist on disk (a module file read from git at a pinned ref)."""
        index = FileIndex(path=path_str, lang=self.lang)
        tfvars = path_str.endswith(".tfvars")
        if _PARSER is None:
            if not tfvars:
                text = data.decode("utf-8", errors="replace")
                index.resources.extend(_parse_regex(path_str, text))
            return index
        root = _PARSER.parse(data).root_node
        if tfvars:
            index.resources.extend(_parse_tfvars(path_str, root))
        else:
            index.resources.extend(_parse_tf(path_str, root))
        return index
