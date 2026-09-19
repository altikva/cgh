# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-15
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: run_generation end to end with a fake backend: it writes the
#              generated file, refuses to clobber without force, refuses a
#              gated reference for a cloud backend, skips the gate for a local
#              backend, confines the target, and can return to stdout without
#              writing.

from __future__ import annotations

import pytest

pytest.importorskip("cgh_codegen")

from cgh_codegen.flow import run_generation
from cgh_codegen.picker import CodeWriteError

from codegraph.plugin_api import ScanFinding
from codegraph.state import findings as store


class FakeBackend:
    def __init__(self, reply: str, *, is_local: bool = True) -> None:
        self._reply = reply
        self.is_local = is_local
        self.name = "fake-local" if is_local else "fake-cloud"

    def generate(self, system: str, user: str) -> tuple[str, float]:
        return self._reply, 0.0


def _repo(tmp_path):
    root = tmp_path
    src = root / "src"
    src.mkdir()
    (src / "order_service.py").write_text(
        "class OrderService:\n    def run(self):\n        return 1\n", encoding="utf-8"
    )
    return root


def test_writes_the_generated_file(tmp_path):
    root = _repo(tmp_path)
    backend = FakeBackend("```python\nclass UserService:\n    pass\n```")
    out = run_generation(
        root,
        "a UserService like the reference",
        "src/user_service.py",
        "src/order_service.py",
        config={},
        backend=backend,
    )
    assert out["written"] is True
    assert out["reference"] == "src/order_service.py"
    target = root / "src" / "user_service.py"
    assert target.read_text(encoding="utf-8") == "class UserService:\n    pass\n"


def test_refuses_to_clobber_without_force(tmp_path):
    root = _repo(tmp_path)
    (root / "src" / "user_service.py").write_text("# existing\n", encoding="utf-8")
    backend = FakeBackend("```python\nx = 1\n```")
    with pytest.raises(CodeWriteError, match="already exists"):
        run_generation(
            root,
            "spec",
            "src/user_service.py",
            "src/order_service.py",
            config={},
            backend=backend,
        )
    # untouched
    assert (root / "src" / "user_service.py").read_text(
        encoding="utf-8"
    ) == "# existing\n"


def test_force_overwrites(tmp_path):
    root = _repo(tmp_path)
    (root / "src" / "user_service.py").write_text("# old\n", encoding="utf-8")
    backend = FakeBackend("```python\nx = 2\n```")
    out = run_generation(
        root,
        "spec",
        "src/user_service.py",
        "src/order_service.py",
        config={},
        backend=backend,
        force=True,
    )
    assert out["written"] is True
    assert (root / "src" / "user_service.py").read_text(encoding="utf-8") == "x = 2\n"


def test_cloud_backend_refused_on_gated_reference(tmp_path):
    root = _repo(tmp_path)
    # Findings are keyed by the absolute path, which is what the flow gates on.
    store.record_findings(
        root,
        str(root / "src" / "order_service.py"),
        "test",
        [ScanFinding(key="confidential", value="true")],
    )
    backend = FakeBackend("```python\nx = 1\n```", is_local=False)
    with pytest.raises(CodeWriteError, match="egress refused"):
        run_generation(
            root,
            "spec",
            "src/user_service.py",
            "src/order_service.py",
            config={"egress": "open"},
            backend=backend,
        )
    assert not (root / "src" / "user_service.py").exists()


def _repo_two_refs(tmp_path):
    """A repo whose target directory holds two same-kind siblings, so the
    auto-picker produces a ranked candidate list to fall through."""
    root = tmp_path
    src = root / "src"
    src.mkdir()
    (src / "order_service.py").write_text(
        "class OrderService:\n    def run(self):\n        return 1\n", encoding="utf-8"
    )
    (src / "account_service.py").write_text(
        "class AccountService:\n    def run(self):\n        return 2\n", encoding="utf-8"
    )
    return root


def test_cloud_backend_falls_back_to_next_clean_candidate(tmp_path):
    root = _repo_two_refs(tmp_path)
    # The top-ranked auto-pick (order_service.py, sorted ahead of account_) is
    # gated; the next candidate is clean. The flow should mirror that instead
    # of refusing, so an auto-pick is not stopped by one secret-bearing file.
    store.record_findings(
        root,
        str(root / "src" / "order_service.py"),
        "test",
        [ScanFinding(key="confidential", value="true")],
    )
    backend = FakeBackend(
        "```python\nclass UserService:\n    pass\n```", is_local=False
    )
    out = run_generation(
        root,
        "a UserService like the reference",
        "src/user_service.py",  # no explicit reference: the picker ranks
        config={"egress": "open"},
        backend=backend,
    )
    assert out["written"] is True
    assert out["reference"] == "src/account_service.py"
    # the fallback is surfaced, not silent: the caller can tell the user the
    # top pick was skipped and which reference was mirrored instead
    assert out["ref_fallback"]
    assert "order_service.py" in out["ref_fallback"]
    assert "egress gate" in out["reason"]
    assert (root / "src" / "user_service.py").exists()


def test_cloud_backend_refused_when_all_candidates_gated(tmp_path):
    root = _repo_two_refs(tmp_path)
    for name in ("order_service.py", "account_service.py"):
        store.record_findings(
            root,
            str(root / "src" / name),
            "test",
            [ScanFinding(key="confidential", value="true")],
        )
    backend = FakeBackend("```python\nx = 1\n```", is_local=False)
    with pytest.raises(CodeWriteError, match="every candidate"):
        run_generation(
            root,
            "spec",
            "src/user_service.py",
            config={"egress": "open"},
            backend=backend,
        )
    assert not (root / "src" / "user_service.py").exists()


def test_local_backend_skips_the_gate(tmp_path):
    root = _repo(tmp_path)
    store.record_findings(
        root,
        str(root / "src" / "order_service.py"),
        "test",
        [ScanFinding(key="confidential", value="true")],
    )
    backend = FakeBackend("```python\nx = 1\n```", is_local=True)
    out = run_generation(
        root,
        "spec",
        "src/user_service.py",
        "src/order_service.py",
        config={"egress": "open"},
        backend=backend,
    )
    assert out["written"] is True
    assert out["egress"] == "local backend (no egress)"


def test_stdout_mode_writes_nothing(tmp_path):
    root = _repo(tmp_path)
    backend = FakeBackend("```python\nx = 1\n```")
    out = run_generation(
        root,
        "spec",
        "src/user_service.py",
        "src/order_service.py",
        config={},
        backend=backend,
        to_stdout=True,
    )
    assert out["written"] is False
    assert out["code"] == "x = 1"
    assert not (root / "src" / "user_service.py").exists()


def test_target_outside_repo_is_refused(tmp_path):
    root = _repo(tmp_path)
    backend = FakeBackend("```python\nx = 1\n```")
    with pytest.raises(CodeWriteError):
        run_generation(
            root,
            "spec",
            "../escape.py",
            "src/order_service.py",
            config={},
            backend=backend,
        )


class SequenceBackend:
    """Returns a different reply per call (sticky on the last), and records the
    prompts so a test can assert the failure was fed back on the retry."""

    is_local = True
    name = "seq"

    def __init__(self, replies: list[str]) -> None:
        self._replies = replies
        self.calls: list[tuple[str, str]] = []

    def generate(self, system: str, user: str) -> tuple[str, float]:
        self.calls.append((system, user))
        return self._replies[min(len(self.calls) - 1, len(self._replies) - 1)], 0.0


def test_self_correct_retries_until_verify_passes(tmp_path):
    root = _repo(tmp_path)
    backend = SequenceBackend(
        ["```python\nx = 1  # bad\n```", "```python\nx = 1  # GOOD\n```"]
    )
    out = run_generation(
        root,
        "spec",
        "src/x.py",
        "src/order_service.py",
        config={},
        backend=backend,
        verify="grep -q GOOD src/x.py",
        max_attempts=3,
    )
    assert out["verified"] is True
    assert out["attempts"] == 2
    assert (root / "src" / "x.py").read_text(
        encoding="utf-8"
    ).strip() == "x = 1  # GOOD"
    # the failing attempt's output was fed back into the retry prompt
    assert "check_failure" in backend.calls[1][1]


def test_self_correct_gives_up_after_max_attempts(tmp_path):
    root = _repo(tmp_path)
    backend = SequenceBackend(["```python\nx = 1  # bad\n```"])  # never passes
    out = run_generation(
        root,
        "spec",
        "src/x.py",
        "src/order_service.py",
        config={},
        backend=backend,
        verify="grep -q GOOD src/x.py",
        max_attempts=2,
    )
    assert out["verified"] is False
    assert out["attempts"] == 2  # tried the cap, kept the last attempt


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
