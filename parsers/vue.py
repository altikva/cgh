# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Parser for Vue/Nuxt Single File Components (.vue) and Nuxt conventions.
#              Extracts <script setup> functions, composables, components, routes,
#              middleware, plugins, and Nuxt auto-imports.

from __future__ import annotations

import re
from pathlib import Path

from . import register_parser
from .base import (
    BaseParser,
    ClassDef,
    CodeRef,
    FileIndex,
    ImportRef,
    SectionDef,
    SymbolDef,
)

# ---------------------------------------------------------------------------
# Regex patterns for Vue SFC parsing
# ---------------------------------------------------------------------------

# <script> block extraction
_SCRIPT_RE = re.compile(
    r"<script\b([^>]*)>(.*?)</script>",
    re.DOTALL | re.IGNORECASE,
)

# <template> block
_TEMPLATE_RE = re.compile(
    r"<template\b[^>]*>(.*?)</template>",
    re.DOTALL | re.IGNORECASE,
)

# <style> block
_STYLE_RE = re.compile(
    r"<style\b([^>]*)>(.*?)</style>",
    re.DOTALL | re.IGNORECASE,
)

# Imports
_IMPORT_RE = re.compile(
    r"""import\s+(?:"""
    r"""(?:\{([^}]+)\})|"""  # named: import { X, Y } from
    r"""(\w+)|"""  # default: import X from
    r"""(?:\*\s+as\s+(\w+))"""  # namespace: import * as X from
    r""")\s+from\s+['"]([^'"]+)['"]""",
)

# defineComponent / defineNuxtComponent
_DEFINE_COMPONENT_RE = re.compile(
    r"(?:export\s+default\s+)?defineComponent\s*\(|defineNuxtComponent\s*\(",
)

# <script setup> props/emits/slots
_DEFINE_PROPS_RE = re.compile(r"(?:const\s+(\w+)\s*=\s*)?defineProps\s*[<(]")
_DEFINE_EMITS_RE = re.compile(r"(?:const\s+(\w+)\s*=\s*)?defineEmits\s*[<(]")
_DEFINE_SLOTS_RE = re.compile(r"(?:const\s+(\w+)\s*=\s*)?defineSlots\s*[<(]")
_DEFINE_MODEL_RE = re.compile(r"(?:const\s+(\w+)\s*=\s*)?defineModel\s*[<(]")
_DEFINE_EXPOSE_RE = re.compile(r"defineExpose\s*\(")

# Composables: const X = useY() or function useX()
_COMPOSABLE_USE_RE = re.compile(
    r"(?:const|let)\s+(\w+)\s*=\s*(use\w+)\s*\(",
)
_COMPOSABLE_DEF_RE = re.compile(
    r"(?:export\s+)?(?:async\s+)?function\s+(use[A-Z]\w*)\s*\(",
)

# Functions (arrow + regular)
_FUNC_RE = re.compile(
    r"(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(",
)
_ARROW_RE = re.compile(
    r"(?:export\s+)?(?:const|let)\s+(\w+)\s*=\s*(?:async\s+)?\([^)]*\)\s*(?::\s*\w+\s*)?=>",
)

# Component references in <template>
_COMPONENT_TAG_RE = re.compile(r"<([A-Z][a-zA-Z0-9]+)[\s/>]")
_NUXT_COMPONENT_RE = re.compile(r"<(Nuxt\w+|Lazy\w+|Client\w+)[\s/>]")

# Nuxt auto-imports (conventions)
_NUXT_COMPOSABLES = {
    "useAsyncData",
    "useFetch",
    "useLazyFetch",
    "useLazyAsyncData",
    "useHead",
    "useSeoMeta",
    "useRoute",
    "useRouter",
    "useRuntimeConfig",
    "useState",
    "useCookie",
    "useRequestHeaders",
    "useRequestEvent",
    "useNuxtApp",
    "useNuxtData",
    "useError",
    "useAppConfig",
    "navigateTo",
    "abortNavigation",
    "definePageMeta",
    "defineNuxtRouteMiddleware",
}

# definePageMeta / defineNuxtRouteMiddleware
_PAGE_META_RE = re.compile(r"definePageMeta\s*\(\s*\{([^}]*)\}", re.DOTALL)
_MIDDLEWARE_RE = re.compile(
    r"(?:export\s+default\s+)?defineNuxtRouteMiddleware\s*\(",
)

# Nuxt config
_NUXT_CONFIG_RE = re.compile(
    r"(?:export\s+default\s+)?defineNuxtConfig\s*\(",
)
_NITRO_HANDLER_RE = re.compile(
    r"(?:export\s+default\s+)?defineEventHandler\s*\(",
)

# Plugin
_NUXT_PLUGIN_RE = re.compile(
    r"(?:export\s+default\s+)?defineNuxtPlugin\s*\(",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _line_number(text: str, pos: int) -> int:
    """Convert a string position to a 1-based line number."""
    return text[:pos].count("\n") + 1


def _detect_nuxt_convention(file_path: str) -> str | None:
    """Detect Nuxt file convention from path."""
    parts = Path(file_path).parts
    for i, part in enumerate(parts):
        if part == "pages":
            return "page"
        if part == "layouts":
            return "layout"
        if part == "middleware":
            return "middleware"
        if part == "plugins":
            return "plugin"
        if part == "composables":
            return "composable"
        if part == "components":
            return "component"
        if part == "server":
            if i + 1 < len(parts):
                sub = parts[i + 1]
                if sub == "api":
                    return "server_api"
                if sub == "routes":
                    return "server_route"
                if sub == "middleware":
                    return "server_middleware"
            return "server"
        if part == "utils":
            return "util"
        if part == "stores":
            return "store"
    return None


def _component_name_from_path(file_path: str) -> str:
    """Derive a PascalCase component name from file path (Nuxt auto-import)."""
    p = Path(file_path)
    stem = p.stem
    if stem == "index":
        stem = p.parent.name
    # Convert kebab-case/snake_case to PascalCase
    parts = re.split(r"[-_.]", stem)
    return "".join(w.capitalize() for w in parts if w)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


@register_parser(".vue")
class VueParser(BaseParser):
    """Vue/Nuxt SFC parser — extracts script, template refs, composables, and Nuxt conventions."""

    lang = "vue"
    extensions = [".vue"]
    extracts = [
        "functions",
        "composables",
        "components",
        "imports",
        "props",
        "emits",
        "page_meta",
        "middleware",
        "plugins",
    ]

    def parse(self, path: Path) -> FileIndex:
        path_str = str(path)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return FileIndex(path=path_str, lang="vue")

        lines = text.splitlines()
        total_lines = len(lines)
        index = FileIndex(path=path_str, lang="vue")

        nuxt_convention = _detect_nuxt_convention(path_str)
        component_name = _component_name_from_path(path_str)

        # --- Parse <script> blocks ---
        for m in _SCRIPT_RE.finditer(text):
            m.group(1)
            script_body = m.group(2)
            script_start = _line_number(text, m.start())

            # Imports
            for im in _IMPORT_RE.finditer(script_body):
                named = [s.strip() for s in im.group(1).split(",")] if im.group(1) else []
                default = [im.group(2)] if im.group(2) else []
                namespace = [im.group(3)] if im.group(3) else []
                module = im.group(4)
                index.imports.append(
                    ImportRef(
                        source_module=module,
                        symbols=named + default + namespace,
                    )
                )

            # Functions
            for fm in _FUNC_RE.finditer(script_body):
                name = fm.group(1)
                line = script_start + script_body[: fm.start()].count("\n")
                index.functions.append(
                    SymbolDef(
                        id=f"{path_str}::{name}",
                        name=name,
                        file_path=path_str,
                        start_line=line,
                        end_line=line,
                        kind="composable" if name.startswith("use") else "function",
                    )
                )

            # Arrow functions
            for am in _ARROW_RE.finditer(script_body):
                name = am.group(1)
                line = script_start + script_body[: am.start()].count("\n")
                index.functions.append(
                    SymbolDef(
                        id=f"{path_str}::{name}",
                        name=name,
                        file_path=path_str,
                        start_line=line,
                        end_line=line,
                        kind="composable" if name.startswith("use") else "function",
                    )
                )

            # Composable usage (const x = useY())
            for cm in _COMPOSABLE_USE_RE.finditer(script_body):
                cm.group(1)
                composable = cm.group(2)
                line = script_start + script_body[: cm.start()].count("\n")
                index.code_refs.append(
                    CodeRef(
                        symbol=composable,
                        line=line,
                        context="composable_call",
                    )
                )

            # defineProps / defineEmits / defineModel
            for pattern, kind in [
                (_DEFINE_PROPS_RE, "props"),
                (_DEFINE_EMITS_RE, "emits"),
                (_DEFINE_SLOTS_RE, "slots"),
                (_DEFINE_MODEL_RE, "model"),
            ]:
                dm = pattern.search(script_body)
                if dm:
                    line = script_start + script_body[: dm.start()].count("\n")
                    dm.group(1) if dm.group(1) else kind
                    index.functions.append(
                        SymbolDef(
                            id=f"{path_str}::define_{kind}",
                            name=f"define{kind.capitalize()}",
                            file_path=path_str,
                            start_line=line,
                            end_line=line,
                            kind=kind,
                        )
                    )

            # defineComponent
            if _DEFINE_COMPONENT_RE.search(script_body):
                index.classes.append(
                    ClassDef(
                        id=f"{path_str}::{component_name}",
                        name=component_name,
                        file_path=path_str,
                        start_line=script_start,
                        end_line=script_start + script_body.count("\n"),
                        kind="component",
                    )
                )

            # definePageMeta (Nuxt pages)
            pm = _PAGE_META_RE.search(script_body)
            if pm:
                line = script_start + script_body[: pm.start()].count("\n")
                index.sections.append(
                    SectionDef(
                        id=f"{path_str}::page_meta",
                        title=f"Page: {component_name}",
                        level=1,
                        file_path=path_str,
                        start_line=line,
                        end_line=line,
                        body_preview=pm.group(1).strip()[:200],
                        anchor=component_name.lower(),
                    )
                )

            # Nuxt plugin
            if _NUXT_PLUGIN_RE.search(script_body):
                line = script_start + script_body[: _NUXT_PLUGIN_RE.search(script_body).start()].count("\n")
                index.functions.append(
                    SymbolDef(
                        id=f"{path_str}::plugin",
                        name=f"plugin:{component_name}",
                        file_path=path_str,
                        start_line=line,
                        end_line=line,
                        kind="plugin",
                    )
                )

            # Nuxt config
            if _NUXT_CONFIG_RE.search(script_body):
                index.functions.append(
                    SymbolDef(
                        id=f"{path_str}::nuxt_config",
                        name="defineNuxtConfig",
                        file_path=path_str,
                        start_line=script_start,
                        end_line=script_start + script_body.count("\n"),
                        kind="config",
                    )
                )

        # --- Parse <template> for component references ---
        for tm in _TEMPLATE_RE.finditer(text):
            template_body = tm.group(1)
            template_start = _line_number(text, tm.start())

            for cm in _COMPONENT_TAG_RE.finditer(template_body):
                tag = cm.group(1)
                if tag in ("Transition", "TransitionGroup", "KeepAlive", "Teleport", "Suspense", "Slot"):
                    continue
                line = template_start + template_body[: cm.start()].count("\n")
                index.code_refs.append(
                    CodeRef(
                        symbol=tag,
                        line=line,
                        context="template_component",
                    )
                )

        # --- Nuxt convention metadata ---
        if nuxt_convention:
            # Register the file as a "component" class node for graph navigation
            if nuxt_convention == "component" and not any(c.name == component_name for c in index.classes):
                index.classes.append(
                    ClassDef(
                        id=f"{path_str}::{component_name}",
                        name=component_name,
                        file_path=path_str,
                        start_line=1,
                        end_line=total_lines,
                        kind="component",
                        docstring=f"Nuxt auto-imported component ({nuxt_convention})",
                    )
                )

            if nuxt_convention == "page":
                route = _path_to_nuxt_route(path_str)
                index.sections.append(
                    SectionDef(
                        id=f"{path_str}::route",
                        title=f"Route: {route}",
                        level=1,
                        file_path=path_str,
                        start_line=1,
                        end_line=total_lines,
                        body_preview=f"Nuxt page route: {route}",
                        anchor=route.replace("/", "-").strip("-"),
                    )
                )

            if nuxt_convention == "server_api":
                api_route = _path_to_server_route(path_str)
                method = _detect_http_method(path_str)
                index.functions.append(
                    SymbolDef(
                        id=f"{path_str}::handler",
                        name=f"{method} {api_route}",
                        file_path=path_str,
                        start_line=1,
                        end_line=total_lines,
                        kind="api_handler",
                        docstring=f"Nitro API: {method} {api_route}",
                    )
                )

        return index


def _path_to_nuxt_route(file_path: str) -> str:
    """Convert pages/users/[id].vue -> /users/:id"""
    parts = Path(file_path).parts
    try:
        pages_idx = parts.index("pages")
    except ValueError:
        return "/"
    route_parts = []
    for part in parts[pages_idx + 1 :]:
        part = re.sub(r"\.vue$", "", part)
        if part == "index":
            continue
        # [id] -> :id, [...slug] -> :slug*
        part = re.sub(r"\[\.\.\.(\w+)\]", r":\1*", part)
        part = re.sub(r"\[(\w+)\]", r":\1", part)
        route_parts.append(part)
    return "/" + "/".join(route_parts) if route_parts else "/"


def _path_to_server_route(file_path: str) -> str:
    """Convert server/api/users/[id].get.ts -> /api/users/:id"""
    parts = Path(file_path).parts
    try:
        server_idx = parts.index("server")
    except ValueError:
        return "/api"
    route_parts = []
    for part in parts[server_idx + 1 :]:
        part = re.sub(r"\.(get|post|put|patch|delete|head|options)\.\w+$", "", part)
        part = re.sub(r"\.\w+$", "", part)  # strip extension
        if part == "index":
            continue
        part = re.sub(r"\[\.\.\.(\w+)\]", r":\1*", part)
        part = re.sub(r"\[(\w+)\]", r":\1", part)
        route_parts.append(part)
    return "/" + "/".join(route_parts) if route_parts else "/"


def _detect_http_method(file_path: str) -> str:
    """Detect HTTP method from Nitro file naming: users.get.ts -> GET"""
    stem = Path(file_path).stem
    for method in ("get", "post", "put", "patch", "delete", "head", "options"):
        if stem.endswith(f".{method}"):
            return method.upper()
    return "GET"


# ---------------------------------------------------------------------------
# Nuxt config files (.ts) — registered separately
# ---------------------------------------------------------------------------


@register_parser("nuxt.config.ts", "nuxt.config.js", "app.config.ts")
class NuxtConfigParser(BaseParser):
    """Parser for Nuxt/app config files."""

    lang = "nuxt_config"
    extensions = ["nuxt.config.ts", "nuxt.config.js", "app.config.ts"]
    extracts = ["config", "modules", "plugins"]

    def can_parse(self, path: Path) -> bool:
        return path.name in ("nuxt.config.ts", "nuxt.config.js", "app.config.ts")

    def parse(self, path: Path) -> FileIndex:
        path_str = str(path)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return FileIndex(path=path_str, lang="nuxt_config")

        index = FileIndex(path=path_str, lang="nuxt_config")

        # Imports
        for im in _IMPORT_RE.finditer(text):
            named = [s.strip() for s in im.group(1).split(",")] if im.group(1) else []
            default = [im.group(2)] if im.group(2) else []
            module = im.group(4)
            index.imports.append(
                ImportRef(
                    source_module=module,
                    symbols=named + default,
                )
            )

        # defineNuxtConfig
        if _NUXT_CONFIG_RE.search(text):
            index.functions.append(
                SymbolDef(
                    id=f"{path_str}::nuxt_config",
                    name="defineNuxtConfig",
                    file_path=path_str,
                    start_line=1,
                    end_line=len(text.splitlines()),
                    kind="config",
                )
            )

        # Extract modules list
        modules_match = re.search(r"modules\s*:\s*\[(.*?)\]", text, re.DOTALL)
        if modules_match:
            modules_str = modules_match.group(1)
            for mod in re.findall(r"['\"](@?\w[\w/.-]+)['\"]", modules_str):
                index.imports.append(
                    ImportRef(
                        source_module=mod,
                        symbols=["nuxt_module"],
                    )
                )

        return index
