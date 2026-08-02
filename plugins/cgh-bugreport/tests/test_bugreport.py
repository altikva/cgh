# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Adversarial payload suite first: exceptions carrying
#              secrets, client paths and source extracts must leave no
#              trace in the built report, frames outside cgh reduce to
#              <external>, fingerprints survive releases. Then spool
#              lifecycle, the excepthook chain, and the send flow with a
#              mocked gh (public repo refused, dedup comments).

from __future__ import annotations

import json
import sys
from argparse import Namespace

import pytest

pytest.importorskip("cgh_bugreport")

from cgh_bugreport.payload import build_report, fingerprint
from cgh_bugreport.spool import (
    list_reports,
    load_report,
    purge,
    spool_dir,
    write_report,
)

SECRET = "AKIAIOSFODNN7EXAMPLE"
CLIENT_PATH = "/Users/victim/clients/acme-fusion/due-diligence.md"


def _boom_external():
    """A message stuffed with everything that must not leak."""
    raise KeyError(f"{SECRET} while parsing {CLIENT_PATH} x=1;y=2")


def _boom_in_cgh():
    """Raises inside a real cgh module so the deepest frame is in-cgh."""
    from codegraph.core.utils import safe_id

    safe_id(None)  # AttributeError inside codegraph/core/utils.py


def _capture(fn) -> tuple[type, BaseException, object]:
    try:
        fn()
    except BaseException as exc:
        return type(exc), exc, exc.__traceback__
    raise AssertionError("fn did not raise")


class TestAllowlistPayload:
    def test_secrets_and_paths_cannot_appear(self):
        exc_type, exc, tb = _capture(_boom_external)
        payload = build_report(exc_type, exc, tb, command="index --root /Users/victim")
        text = json.dumps(payload)

        assert SECRET not in text
        assert "acme-fusion" not in text
        assert "victim" not in text
        assert "x=1" not in text  # no exception message field at all
        assert payload["exception_type"] == "KeyError"
        assert payload["command"] == "index"  # name only, arguments dropped

    def test_frames_outside_cgh_reduce_to_external(self):
        exc_type, exc, tb = _capture(_boom_in_cgh)
        payload = build_report(exc_type, exc, tb)
        assert "<external>" in payload["frames"]  # this test file's frames
        in_cgh = [f for f in payload["frames"] if f != "<external>"]
        assert in_cgh, "the raising cgh frame must be named"
        assert all(f.startswith(("codegraph/", "cgh_")) for f in in_cgh)
        assert all("/Users/" not in f for f in payload["frames"])

    def test_fingerprint_survives_releases_and_line_moves(self):
        frames_v1 = ["<external>", "codegraph/core/utils.py:17 in rows"]
        frames_v2 = ["<external>", "codegraph/core/utils.py:942 in rows"]
        assert fingerprint(KeyError, frames_v1) == fingerprint(KeyError, frames_v2)
        assert fingerprint(KeyError, frames_v1) != fingerprint(ValueError, frames_v1)

    def test_tripwire_refuses_a_leaking_payload(self, monkeypatch):
        pytest.importorskip("cgh_pii")
        import cgh_bugreport.payload as payload_mod

        monkeypatch.setattr(payload_mod, "_cgh_version", lambda: f"leaky {SECRET}")
        exc_type, exc, tb = _capture(_boom_external)
        with pytest.raises(payload_mod.PayloadTripwire):
            build_report(exc_type, exc, tb)


class TestSpool:
    def _payload(self, n: int) -> dict:
        return {
            "report_id": f"id{n:03d}",
            "created_at": f"2026-07-29T10:{n:02d}:00",
            "exception_type": "ValueError",
            "fingerprint": "abc",
            "frames": [],
        }

    def test_cap_drops_oldest(self, tmp_path):
        for n in range(25):
            write_report(tmp_path, self._payload(n))
        assert len(list_reports(tmp_path)) == 20
        assert load_report(tmp_path, "id000") is None
        assert load_report(tmp_path, "last")["report_id"] == "id024"

    def test_purge(self, tmp_path):
        for n in range(3):
            write_report(tmp_path, self._payload(n))
        assert purge(tmp_path, "id001") == 1
        assert purge(tmp_path) == 2
        assert list_reports(tmp_path) == []

    def test_spool_lives_under_codegraph(self, tmp_path):
        write_report(tmp_path, self._payload(1))
        assert spool_dir(tmp_path) == tmp_path / ".codegraph" / "bugreports"


class TestExcepthook:
    def test_hook_spools_and_chains(self, tmp_path, monkeypatch, capsys):
        import cgh_bugreport

        (tmp_path / ".codegraph").mkdir()
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(cgh_bugreport, "_installed", False)
        seen = []
        monkeypatch.setattr(sys, "excepthook", lambda t, v, b: seen.append(t))

        cgh_bugreport._install_excepthook()
        exc_type, exc, tb = _capture(_boom_external)
        sys.excepthook(exc_type, exc, tb)

        assert seen == [KeyError]  # previous hook still ran
        reports = list_reports(tmp_path)
        assert len(reports) == 1
        assert "spooled locally" in capsys.readouterr().err


class TestSend:
    def _gh_script(self, responses: dict):
        """Fake gh: maps the first two args to a canned (code, stdout)."""
        calls: list[list[str]] = []

        def fake_run(cmd, *args, **kwargs):
            calls.append(cmd)
            key = " ".join(cmd[1:3])
            code, out = responses.get(key, (0, ""))
            from types import SimpleNamespace

            return SimpleNamespace(returncode=code, stdout=out, stderr="")

        return calls, fake_run

    def _spooled(self, tmp_path) -> None:
        (tmp_path / ".codegraph").mkdir(exist_ok=True)
        exc_type, exc, tb = _capture(_boom_external)
        write_report(tmp_path, build_report(exc_type, exc, tb))

    def test_public_repo_refused(self, tmp_path, monkeypatch):
        from cgh_bugreport.cli import _dispatch

        self._spooled(tmp_path)
        calls, fake = self._gh_script({"repo view": (0, "PUBLIC\n")})
        monkeypatch.setattr("subprocess.run", fake)

        with pytest.raises(SystemExit):
            _dispatch(
                Namespace(action="send", report="last", root=str(tmp_path), yes=True),
                {"github_repo": "org/reports"},
            )
        assert not any("issue" in " ".join(c) for c in calls)

    def test_private_repo_creates_then_comments(self, tmp_path, monkeypatch):
        from cgh_bugreport.cli import _dispatch
        from cgh_bugreport.spool import list_reports

        self._spooled(tmp_path)
        calls, fake = self._gh_script(
            {"repo view": (0, "PRIVATE\n"), "issue list": (0, "\n")}
        )
        monkeypatch.setattr("subprocess.run", fake)

        _dispatch(
            Namespace(action="send", report="last", root=str(tmp_path), yes=True),
            {"github_repo": "org/reports"},
        )
        created = [c for c in calls if c[1:3] == ["issue", "create"]]
        assert len(created) == 1
        assert "fp:" in " ".join(created[0])
        assert list_reports(tmp_path)[0]["sent"]["to"].startswith("org/reports")

        # Second report, same crash: gh finds the issue, we comment.
        purge(tmp_path)  # same-second timestamps tie, start clean
        self._spooled(tmp_path)
        calls2, fake2 = self._gh_script(
            {"repo view": (0, "PRIVATE\n"), "issue list": (0, "42\n")}
        )
        monkeypatch.setattr("subprocess.run", fake2)
        _dispatch(
            Namespace(action="send", report="last", root=str(tmp_path), yes=True),
            {"github_repo": "org/reports"},
        )
        assert any(c[1:3] == ["issue", "comment"] for c in calls2)
        assert not any(c[1:3] == ["issue", "create"] for c in calls2)

    def test_gh_failure_refuses(self, tmp_path, monkeypatch):
        from cgh_bugreport.cli import _dispatch

        self._spooled(tmp_path)
        _calls, fake = self._gh_script({"repo view": (1, "")})
        monkeypatch.setattr("subprocess.run", fake)
        with pytest.raises(SystemExit):
            _dispatch(
                Namespace(action="send", report="last", root=str(tmp_path), yes=True),
                {"github_repo": "org/reports"},
            )


class TestModeProbe:
    def test_probe_failure_reads_as_secure(self, monkeypatch, tmp_path):
        """The mode gates the pre-send confirmation, so an unreadable
        config must land on the strict branch, never the permissive."""
        from cgh_bugreport.cli import _mode

        import codegraph.plugin_api as api

        def boom(root):
            raise OSError("unreadable config")

        monkeypatch.setattr(api, "load_config", boom)
        assert _mode(tmp_path) == "secure"
