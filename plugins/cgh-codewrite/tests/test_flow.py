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

pytest.importorskip("cgh_codewrite")

from cgh_codewrite.flow import run_generation
from cgh_codewrite.picker import CodeWriteError

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


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
