# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-05-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Language built-in callables: filtered out of CALLS edges so
# they don't become god-nodes accumulating thousands of spurious incoming edges.

from __future__ import annotations

# JavaScript / TypeScript: the callable and namespace properties of the
# ECMAScript global object, then the web platform globals common to browsers
# and Node. A call to one of these never targets a function of the repo.
_JS_TS_BUILTINS: frozenset[str] = frozenset(
    {
        # Function properties of the ECMAScript global object
        "decodeURI",
        "decodeURIComponent",
        "encodeURI",
        "encodeURIComponent",
        "eval",
        "isFinite",
        "isNaN",
        "parseFloat",
        "parseInt",
        # Constructor properties of the ECMAScript global object
        "AggregateError",
        "Array",
        "ArrayBuffer",
        "BigInt",
        "BigInt64Array",
        "BigUint64Array",
        "Boolean",
        "DataView",
        "Date",
        "Error",
        "EvalError",
        "FinalizationRegistry",
        "Float32Array",
        "Float64Array",
        "Function",
        "Int16Array",
        "Int32Array",
        "Int8Array",
        "Map",
        "Number",
        "Object",
        "Promise",
        "Proxy",
        "RangeError",
        "ReferenceError",
        "RegExp",
        "Set",
        "SharedArrayBuffer",
        "String",
        "Symbol",
        "SyntaxError",
        "TypeError",
        "Uint16Array",
        "Uint32Array",
        "Uint8Array",
        "Uint8ClampedArray",
        "URIError",
        "WeakMap",
        "WeakRef",
        "WeakSet",
        # Namespace objects of the global object (ECMA-262, plus Intl from ECMA-402)
        "Atomics",
        "Intl",
        "JSON",
        "Math",
        "Reflect",
        # Web platform globals that browsers and Node both provide
        "AbortController",
        "AbortSignal",
        "atob",
        "Blob",
        "btoa",
        "clearInterval",
        "clearTimeout",
        "console",
        "fetch",
        "File",
        "FormData",
        "Headers",
        "queueMicrotask",
        "Request",
        "Response",
        "setInterval",
        "setTimeout",
        "structuredClone",
        "TextDecoder",
        "TextEncoder",
        "URL",
        "URLSearchParams",
    }
)

# Python: every entry of the built-in functions table (types included, since
# calling one is a conversion), minus the dunder `__import__`.
_PYTHON_BUILTINS: frozenset[str] = frozenset(
    {
        # The "Built-in Functions" table of the Python documentation
        "abs",
        "aiter",
        "all",
        "anext",
        "any",
        "ascii",
        "bin",
        "bool",
        "breakpoint",
        "bytearray",
        "bytes",
        "callable",
        "chr",
        "classmethod",
        "compile",
        "complex",
        "delattr",
        "dict",
        "dir",
        "divmod",
        "enumerate",
        "eval",
        "exec",
        "filter",
        "float",
        "format",
        "frozenset",
        "getattr",
        "globals",
        "hasattr",
        "hash",
        "help",
        "hex",
        "id",
        "input",
        "int",
        "isinstance",
        "issubclass",
        "iter",
        "len",
        "list",
        "locals",
        "map",
        "max",
        "memoryview",
        "min",
        "next",
        "object",
        "oct",
        "open",
        "ord",
        "pow",
        "print",
        "property",
        "range",
        "repr",
        "reversed",
        "round",
        "set",
        "setattr",
        "slice",
        "sorted",
        "staticmethod",
        "str",
        "sum",
        "super",
        "tuple",
        "type",
        "vars",
        "zip",
    }
)

# Go built-in functions and predeclared types.
_GO_BUILTINS: frozenset[str] = frozenset(
    {
        "make",
        "new",
        "len",
        "cap",
        "append",
        "copy",
        "delete",
        "close",
        "panic",
        "recover",
        "print",
        "println",
        "complex",
        "real",
        "imag",
        "min",
        "max",
        "clear",
        # Predeclared types when used as conversions: int(x), string(x), ...
        "bool",
        "byte",
        "rune",
        "string",
        "error",
        "any",
        "comparable",
        "int",
        "int8",
        "int16",
        "int32",
        "int64",
        "uint",
        "uint8",
        "uint16",
        "uint32",
        "uint64",
        "uintptr",
        "float32",
        "float64",
        "complex64",
        "complex128",
    }
)


# Rust built-in macros (collected without the trailing `!`) plus
# commonly-shadowed std method names.
_RUST_BUILTINS: frozenset[str] = frozenset(
    {
        "println",
        "print",
        "eprintln",
        "eprint",
        "format",
        "write",
        "writeln",
        "vec",
        "assert",
        "assert_eq",
        "assert_ne",
        "debug_assert",
        "panic",
        "todo",
        "unimplemented",
        "unreachable",
        "dbg",
        "include",
        "include_bytes",
        "include_str",
        "concat",
        "stringify",
        "env",
        "option_env",
        "cfg",
        "clone",
        "into",
        "from",
        "as_ref",
        "as_mut",
        "borrow",
        "borrow_mut",
        "drop",
        "default",
        "to_string",
        "to_owned",
    }
)


# Java java.lang.Object + commonly-shared method names.
_JAVA_BUILTINS: frozenset[str] = frozenset(
    {
        "toString",
        "hashCode",
        "equals",
        "getClass",
        "clone",
        "wait",
        "notify",
        "notifyAll",
        "println",
        "print",
        "printf",
        "valueOf",
        "format",
        "size",
        "length",
        "isEmpty",
        "contains",
        "get",
        "set",
        "add",
        "remove",
        "put",
        "iterator",
        "hasNext",
        "next",
    }
)


# Map a FileIndex.lang value to the matching built-in set.
# Languages we don't have a list for fall back to the empty frozenset, no
# filtering happens, behaviour matches pre-filter cgh.
_BUILTINS_BY_LANG: dict[str, frozenset[str]] = {
    "python": _PYTHON_BUILTINS,
    "typescript": _JS_TS_BUILTINS,
    "javascript": _JS_TS_BUILTINS,
    "vue": _JS_TS_BUILTINS,
    "go": _GO_BUILTINS,
    "rust": _RUST_BUILTINS,
    "java": _JAVA_BUILTINS,
}


def is_builtin(lang: str, name: str) -> bool:
    """True iff `name` is a language built-in callable for `lang`.

    Used by the indexer to skip CALLS edges that would otherwise accumulate
    on builtin god-nodes. Returns False for unknown languages (safe default:
    no filtering).
    """
    return name in _BUILTINS_BY_LANG.get(lang, frozenset())
