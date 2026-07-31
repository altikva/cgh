# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-05
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Tests for the ensurepath helper: PATH detection, Windows to MSYS
#              conversion, environment classification, profile selection, and
#              idempotent profile editing.

from __future__ import annotations


from codegraph.state import ensurepath as ep


class TestIsOnPath:
    def test_present(self):
        assert ep.is_on_path("/opt/bin", path_env="/usr/bin:/opt/bin:/bin") is True

    def test_absent(self):
        assert ep.is_on_path("/opt/bin", path_env="/usr/bin:/bin") is False

    def test_normalizes_trailing_slash(self):
        assert ep.is_on_path("/opt/bin/", path_env="/opt/bin") is True


class TestToMsysPath:
    def test_drive_letter(self):
        assert ep.to_msys_path(r"C:\Users\x\Scripts") == "/c/Users/x/Scripts"

    def test_already_posix(self):
        assert ep.to_msys_path("/usr/local/bin") == "/usr/local/bin"

    def test_lowercases_drive(self):
        assert ep.to_msys_path(r"D:\Tools") == "/d/Tools"


class TestDetectEnv:
    def test_gitbash(self):
        assert ep.detect_env({"MSYSTEM": "MINGW64"}) == "gitbash"

    def test_falls_through_to_posix(self, monkeypatch):
        # No MSYSTEM, not nt -> macos or linux depending on the host.
        assert ep.detect_env({}) in ("macos", "linux", "wsl")


class TestShellProfile:
    def test_zsh(self, tmp_path):
        p = ep.shell_profile({"SHELL": "/bin/zsh"}, home=tmp_path)
        assert p == tmp_path / ".zshrc"

    def test_bash(self, tmp_path):
        p = ep.shell_profile({"SHELL": "/usr/bin/bash"}, home=tmp_path)
        assert p == tmp_path / ".bashrc"

    def test_unknown_falls_back_to_profile(self, tmp_path):
        p = ep.shell_profile({"SHELL": "/bin/fish"}, home=tmp_path)
        assert p == tmp_path / ".profile"


class TestPathValueFor:
    def test_gitbash_converts(self):
        assert ep.path_value_for("gitbash", r"C:\py\Scripts") == "/c/py/Scripts"

    def test_linux_keeps(self):
        assert ep.path_value_for("linux", "/home/x/.local/bin") == "/home/x/.local/bin"


class TestAppendToProfile:
    def test_adds_export_line(self, tmp_path):
        profile = tmp_path / ".bashrc"
        result = ep.append_to_profile(profile, "/c/py/Scripts")
        assert result == "added"
        body = profile.read_text()
        assert 'export PATH="$PATH:/c/py/Scripts"' in body
        assert ep.MARKER in body

    def test_idempotent(self, tmp_path):
        profile = tmp_path / ".bashrc"
        ep.append_to_profile(profile, "/c/py/Scripts")
        first = profile.read_text()
        result = ep.append_to_profile(profile, "/c/py/Scripts")
        assert result == "already"
        assert profile.read_text() == first  # unchanged

    def test_preserves_existing_content(self, tmp_path):
        profile = tmp_path / ".bashrc"
        profile.write_text("# my stuff\nexport FOO=1\n")
        ep.append_to_profile(profile, "/opt/bin")
        body = profile.read_text()
        assert "export FOO=1" in body
        assert "/opt/bin" in body

    def test_creates_missing_profile(self, tmp_path):
        profile = tmp_path / "sub" / ".profile"
        ep.append_to_profile(profile, "/opt/bin")
        assert profile.exists()
