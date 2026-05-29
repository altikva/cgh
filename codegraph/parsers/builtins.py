# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-05-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Language built-in callables — filtered out of CALLS edges so
# they don't become god-nodes accumulating thousands of spurious incoming edges.

from __future__ import annotations

# JavaScript / TypeScript ECMAScript + Web/Node common globals.
_JS_TS_BUILTINS: frozenset[str] = frozenset(
    {
        # ECMAScript constructors / coercions
        "String", "Number", "Boolean", "Object", "Array", "Symbol", "BigInt",
        "Date", "RegExp", "Error", "TypeError", "RangeError", "SyntaxError",
        "ReferenceError", "EvalError", "URIError",
        "Promise", "Map", "Set", "WeakMap", "WeakSet", "JSON", "Math",
        "Reflect", "Proxy", "Intl",
        # Global functions
        "parseInt", "parseFloat", "isNaN", "isFinite",
        "encodeURIComponent", "decodeURIComponent", "encodeURI", "decodeURI",
        # Browser / Node common globals
        "URL", "URLSearchParams", "FormData", "Blob", "File",
        "Headers", "Request", "Response", "AbortController", "AbortSignal",
        "TextEncoder", "TextDecoder", "console",
        "setTimeout", "setInterval", "clearTimeout", "clearInterval",
        "queueMicrotask", "structuredClone", "fetch",
    }
)


# Python builtins (the callable ones — types and built-in functions).
_PYTHON_BUILTINS: frozenset[str] = frozenset(
    {
        "str", "int", "float", "bool", "list", "dict", "set", "tuple", "bytes",
        "bytearray", "complex", "frozenset", "object",
        "len", "range", "enumerate", "zip", "map", "filter", "sum", "min", "max",
        "print", "open", "isinstance", "issubclass", "type", "super",
        "sorted", "reversed", "any", "all", "abs", "round", "next", "iter",
        "hash", "id", "repr", "callable", "getattr", "setattr", "hasattr",
        "delattr", "vars", "dir", "globals", "locals",
        "input", "format", "ord", "chr", "bin", "oct", "hex", "pow", "divmod",
        "compile", "eval", "exec", "help", "memoryview", "property",
        "classmethod", "staticmethod",
    }
)


# Map a FileIndex.lang value to the matching built-in set.
# Languages we don't have a list for fall back to the empty frozenset — no
# filtering happens, behaviour matches pre-filter cgh.
_BUILTINS_BY_LANG: dict[str, frozenset[str]] = {
    "python": _PYTHON_BUILTINS,
    "typescript": _JS_TS_BUILTINS,
    "javascript": _JS_TS_BUILTINS,
    "vue": _JS_TS_BUILTINS,
}


def is_builtin(lang: str, name: str) -> bool:
    """True iff `name` is a language built-in callable for `lang`.

    Used by the indexer to skip CALLS edges that would otherwise accumulate
    on builtin god-nodes. Returns False for unknown languages (safe default:
    no filtering).
    """
    return name in _BUILTINS_BY_LANG.get(lang, frozenset())
