# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-19
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Codegen system prompts steer the cheap model toward lint-clean code.
#              Assert that SYSTEM_PROMPT and EXTEND_SYSTEM_PROMPT request clean,
#              idiomatic Python: no unused imports, f-strings over concatenation.

from __future__ import annotations

import pytest

pytest.importorskip("cgh_codegen")

from cgh_codegen.generate import EXTEND_SYSTEM_PROMPT, SYSTEM_PROMPT


def test_new_file_prompt_requests_clean_code():
    assert "unused" in SYSTEM_PROMPT.lower()
    assert "f-string" in SYSTEM_PROMPT.lower()


def test_extend_prompt_requests_clean_code():
    assert "unused" in EXTEND_SYSTEM_PROMPT.lower()
    assert "f-string" in EXTEND_SYSTEM_PROMPT.lower()


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
