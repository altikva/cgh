# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-15
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The pure code-generation core: prompt assembly, code
#              extraction (fenced block wins, surrounding prose dropped, empty
#              refused), and generate_code end to end against a FakeBackend.

from __future__ import annotations

import pytest

pytest.importorskip("cgh_codegen")

from cgh_codegen.generate import (
    Backend,
    GenerationError,
    build_prompt,
    extract_code,
    generate_code,
)


class FakeBackend:
    """Canned reply, fixed cost. The whole test harness for the real thing."""

    name = "fake"
    is_local = True

    def __init__(self, reply: str, cost: float = 0.01) -> None:
        self._reply = reply
        self._cost = cost
        self.calls: list[tuple[str, str]] = []

    def generate(self, system: str, user: str) -> tuple[str, float]:
        self.calls.append((system, user))
        return self._reply, self._cost


def test_fake_backend_satisfies_protocol():
    assert isinstance(FakeBackend("x"), Backend)


def test_build_prompt_names_target_and_frames_reference_as_example():
    system, user = build_prompt(
        "write tests for UserService",
        [("tests/test_order.py", "def test_order():\n    pass\n")],
        target="tests/test_user.py",
    )
    # the anti-echo instruction: the reference is a style example, not content
    assert "do not reproduce" in system.lower()
    assert "tests/test_user.py" in user  # target named up front
    assert user.index("SPEC:") < user.index("<style_example")
    assert 'path="tests/test_order.py"' in user
    assert "def test_order()" in user


def test_extract_code_prefers_the_fenced_block_and_drops_prose():
    reply = (
        "Sure! Here is the file you asked for:\n\n"
        "```python\ndef f():\n    return 1\n```\n\n"
        "Let me know if you want changes."
    )
    assert extract_code(reply) == "def f():\n    return 1"


def test_extract_code_without_a_fence_is_refused():
    # No fenced block means nothing usable: better to refuse than to write a
    # cheap model's unframed prose to disk.
    assert extract_code("\n\ndef g():\n    return 2\n\n") == ""


def test_extract_code_empty_reply_is_empty():
    assert extract_code("   \n  ") == ""


def test_generate_code_cleans_and_reports():
    backend = FakeBackend("```py\nx = 1\n```", cost=0.02)
    result = generate_code("make x", [("ref.py", "y = 2\n")], backend)
    assert result.code == "x = 1"
    assert result.cost == 0.02
    assert result.backend == "fake"
    # the reference text reached the backend
    assert "y = 2" in backend.calls[0][1]


def test_generate_code_refuses_empty_backend_output():
    # A prose-only apology extracts to nothing: refuse rather than write an
    # empty file over the target.
    with pytest.raises(GenerationError):
        generate_code("make x", [], FakeBackend("I cannot do that."))


def test_generate_code_rejects_empty_spec():
    with pytest.raises(GenerationError):
        generate_code("   ", [], FakeBackend("x = 1"))


class TestNestedFences:
    """Test extract_code with nested fences and build_prompt with existing."""

    def test_nested_fence_is_not_truncated(self):
        # extract_code now extracts to the LAST fence, not the first,
        # so nested fences within the outer block don't truncate the result.
        reply = (
            "```python\n"
            "def generate(prompt):\n"
            "    inner = '''\n"
            "```python\n"
            "print('hello')\n"
            "```\n"
            "    '''\n"
            "    return inner\n"
            "```\n"
        )
        code = extract_code(reply)
        assert "print('hello')" in code
        assert "def generate(prompt):" in code
        # The real regression: everything after the inner closing fence used
        # to be cut off, which truncated the file mid-string literal.
        assert "return inner" in code

    def test_single_block_is_unchanged(self):
        # A single outer fence still works as before.
        reply = "```python\nx = 1\n```"
        assert extract_code(reply) == "x = 1"

    def test_reply_without_a_fence_returns_empty(self):
        # No fence means nothing usable to extract.
        assert extract_code("def foo():\n    pass") == ""

    def test_build_prompt_with_existing_argument(self):
        # When existing is given, build_prompt switches to extend mode.
        import cgh_codegen.generate as gen

        existing_code = "def old():\n    pass\n"
        system, user = build_prompt(
            "add a new function",
            [("ref.py", "x = 1\n")],
            target="existing.py",
            existing=existing_code,
        )
        assert system == gen.EXTEND_SYSTEM_PROMPT
        assert "<file_to_extend" in user
        assert existing_code in user


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
