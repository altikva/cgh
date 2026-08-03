# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-03
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The plugin registry loads once per process and the CLI
#              loads it before --root is parsed, so scanners can be
#              constructed with repo_root=None. The scan sites late-bind
#              the authoritative root (the OneDrive/Windows crash:
#              Path(None) in every scanner on repos indexed from
#              outside their directory).

from __future__ import annotations

from codegraph.indexer import _bind_scanner_root


class _Scanner:
    name = "fake"

    def __init__(self, repo_root):
        self.repo_root = repo_root


def test_rootless_scanner_gets_the_scan_root(tmp_path):
    s = _Scanner(repo_root=None)
    _bind_scanner_root(s, tmp_path)
    assert s.repo_root == tmp_path


def test_bound_scanner_keeps_its_root(tmp_path):
    s = _Scanner(repo_root="/somewhere/else")
    _bind_scanner_root(s, tmp_path)
    assert s.repo_root == "/somewhere/else"


def test_scanner_without_the_attribute_is_left_alone(tmp_path):
    class Bare:
        name = "bare"

    s = Bare()
    _bind_scanner_root(s, tmp_path)
    assert not hasattr(s, "repo_root")


def test_deferred_process_repairs_the_binding(tmp_path, monkeypatch):
    import codegraph.plugins as plugins
    import codegraph.state.deferred_scan as ds

    seen_roots: list = []

    class Recorder:
        name = "recorder"
        deferred = True
        repo_root = None

        def scan(self, path, text, index):
            seen_roots.append(self.repo_root)
            return []

    monkeypatch.setattr(plugins, "scanners", lambda: [("recorder", Recorder())])
    f = tmp_path / "a.txt"
    f.write_text("hello", encoding="utf-8")
    ds._process(str(tmp_path), str(f), "sha1")
    assert seen_roots == [str(tmp_path)]
