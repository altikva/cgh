# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-01
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The plugin boundary ratchet: first-party plugins may
#              import from codegraph.plugin_api (and the bare codegraph
#              package for introspection), nothing else. Anything new
#              that reaches into codegraph internals fails here, which
#              is what keeps API_VERSION an honest promise. Also checks
#              that every re-exported name actually resolves.

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
PLUGIN_SOURCES = [
    p for p in REPO.glob("plugins/*/cgh_*/**/*.py") if "/build/" not in str(p)
]

_IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+(codegraph[\w.]*)", re.MULTILINE)
_ALLOWED = ("codegraph.plugin_api", "codegraph")


def test_plugins_import_only_the_supported_surface():
    assert PLUGIN_SOURCES, "plugin sources not found; repo layout changed?"
    violations: list[str] = []
    for src in PLUGIN_SOURCES:
        text = src.read_text(encoding="utf-8")
        for match in _IMPORT_RE.finditer(text):
            module = match.group(1)
            if module not in _ALLOWED:
                line = text[: match.start()].count("\n") + 1
                violations.append(f"{src.relative_to(REPO)}:{line}: {module}")
    assert not violations, (
        "plugins must import from codegraph.plugin_api only; promote the "
        "surface there instead of reaching into internals:\n" + "\n".join(violations)
    )


@pytest.mark.parametrize(
    "name",
    [
        "record_findings",
        "query_findings",
        "query_findings_ro",
        "findings_for_file",
        "findings_db_path",
        "activity_log",
        "knowledge_record",
        "load_config",
        "find_codegraph_root",
        "git_hash_object",
        "quiet_subprocess_kwargs",
        "is_loopback_url",
        "add_out_option",
        "add_format_option",
        "emit_result",
        "resolve_children",
        "sync_static_rules",
        "loaded_plugins",
        "BaseParser",
        "FileIndex",
        "SectionDef",
        "server_root",
    ],
)
def test_reexported_names_resolve(name):
    import codegraph.plugin_api as api

    assert getattr(api, name) is not None
