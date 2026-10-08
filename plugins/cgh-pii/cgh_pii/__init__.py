# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: cgh plugin entry point. Always registers the `cgh pii` CLI
#              (scan, redact, probe). Index-time scanning is opt-in since
#              0.4.0: with [plugin.pii] scan_on_index = true the inline
#              regex scanner runs on every indexed file (secrets only
#              unless pii = true), plus the deferred NER and LLM tiers
#              when ner / llm are set.

from __future__ import annotations

import logging

CGH_PLUGIN_API = 1


def as_bool(value) -> bool:
    """TOML gives real booleans; tolerate "true"/"1" strings from
    hand-edited configs."""
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def register(api) -> None:
    from .cli import make_cli_registrar

    api.register_cli(make_cli_registrar(api.config, api.repo_root))

    # codegraph.sdk.scan_text(scanners=["pii"]) runs this one on text the
    # caller hands over; it never touches the index. It keeps the PII
    # patterns unless pii = false is set, so SDK callers see the same
    # findings as before 0.4.0.
    from .regex_scanner import RegexPiiScanner

    disabled = set(api.config.get("disable_keys", []))
    api.register_extension(
        "scanner.on_demand",
        RegexPiiScanner(
            disabled_keys=disabled, pii=as_bool(api.config.get("pii", True))
        ),
    )

    # Scanning every file on every index cost CPU for findings agents
    # rarely read. Secrets are checked on demand with `cgh pii scan`;
    # scan_on_index = true restores the index-time scanners.
    if not as_bool(api.config.get("scan_on_index", False)):
        return

    api.register_scanner(
        RegexPiiScanner(
            disabled_keys=disabled, pii=as_bool(api.config.get("pii", False))
        )
    )

    if as_bool(api.config.get("ner", False)):
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

    # Deferred LLM tier: an extra pass that catches PII the regex and NER
    # tiers miss, by probing each file with a local or configured LLM.
    # Off by default (an LLM call per file is heavy, and a cloud endpoint
    # is egress); enable with [plugin.pii] llm = true.
    if as_bool(api.config.get("llm", False)):
        from .llm_scanner import LlmPiiScanner

        api.register_scanner(LlmPiiScanner(api.repo_root, api.config))
