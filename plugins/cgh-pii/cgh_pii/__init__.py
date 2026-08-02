# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: cgh plugin entry point: registers the inline regex PII and
#              secret scanner, and the optional deferred NER scanner when
#              [plugin.pii] ner = true and presidio is installed.

from __future__ import annotations

import logging

CGH_PLUGIN_API = 1


def register(api) -> None:
    from .regex_scanner import RegexPiiScanner

    disabled = set(api.config.get("disable_keys", []))
    api.register_scanner(RegexPiiScanner(disabled_keys=disabled))

    if api.config.get("ner"):
        try:
            from .ner_scanner import NerScanner

            api.register_scanner(NerScanner())
        except ImportError:
            # register() runs inside the owner: a logger gives the
            # message a level and a module name in owner.log, where a
            # bare stderr print arrived unlabeled.
            logging.getLogger(__name__).warning(
                "ner = true but presidio is not installed; "
                'run pip install "cgh-pii[ner]". NER tier skipped.'
            )
