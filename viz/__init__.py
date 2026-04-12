# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Visualization package — Mermaid diagram generators and HTML rendering.

from codegraph.viz.html import generate_html, open_in_browser
from codegraph.viz.mermaid import (
    mermaid_calls,
    mermaid_classes,
    mermaid_docs,
    mermaid_imports,
    mermaid_overview,
)

__all__ = [
    "mermaid_calls",
    "mermaid_classes",
    "mermaid_docs",
    "mermaid_imports",
    "mermaid_overview",
    "generate_html",
    "open_in_browser",
]
