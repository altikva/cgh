# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-28
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The Read hook flags an image read twice in the same session
#              with an unchanged fingerprint, through additionalContext only.

from __future__ import annotations

import argparse
import io
import json
import os

import pytest

from codegraph.cli import commands_hooks as h
from codegraph.cli import hook_images


def _run(monkeypatch, capsys, payload: dict) -> str:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    with pytest.raises(SystemExit) as exc:
        h.cmd_hook_precheck_read(argparse.Namespace())
    assert exc.value.code == 0
    return capsys.readouterr().out


@pytest.fixture
def repo(tmp_path):
    (tmp_path / ".codegraph").mkdir()
    return tmp_path


def _payload(path, session="s1", cwd=None):
    p = {"tool_input": {"file_path": str(path)}}
    if session is not None:
        p["session_id"] = session
    if cwd is not None:
        p["cwd"] = str(cwd)
    return p


def _context(out: str) -> str:
    return json.loads(out)["hookSpecificOutput"]["additionalContext"]


def test_first_read_silent_second_flagged(monkeypatch, capsys, repo):
    img = repo / "shot.png"
    img.write_bytes(b"\x89PNG" + b"x" * 3000)
    assert _run(monkeypatch, capsys, _payload(img)) == ""
    ctx = _context(_run(monkeypatch, capsys, _payload(img)))
    assert ctx.startswith(
        "[cgh hook] shot.png was already read in this session (3 KB, at "
    )
    assert ctx.endswith(
        "it is still in the conversation above. Re-read it only if it changed."
    )
    doc = json.loads(_run(monkeypatch, capsys, _payload(img)))
    assert "permissionDecision" not in doc["hookSpecificOutput"]


def test_changed_image_not_flagged(monkeypatch, capsys, repo):
    img = repo / "shot.jpg"
    img.write_bytes(b"a" * 100)
    assert _run(monkeypatch, capsys, _payload(img)) == ""
    img.write_bytes(b"b" * 200)
    assert _run(monkeypatch, capsys, _payload(img)) == ""
    # The changed file replaced the record: a third identical read is flagged.
    assert "already read" in _context(_run(monkeypatch, capsys, _payload(img)))


def test_other_session_not_flagged(monkeypatch, capsys, repo):
    img = repo / "a.webp"
    img.write_bytes(b"w" * 10)
    assert _run(monkeypatch, capsys, _payload(img, "s1")) == ""
    assert _run(monkeypatch, capsys, _payload(img, "s2")) == ""


def test_pdf_ignored(monkeypatch, capsys, repo):
    pdf = repo / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4" + b"x" * 10)
    assert hook_images.reread_hint(_payload(pdf), pdf) is None
    assert hook_images.reread_hint(_payload(pdf), pdf) is None
    assert not (repo / ".codegraph" / "sessions").exists()


def test_no_session_id_silent(monkeypatch, capsys, repo):
    img = repo / "shot.png"
    img.write_bytes(b"p" * 10)
    for _ in range(2):
        assert _run(monkeypatch, capsys, _payload(img, session=None)) == ""
    assert not (repo / ".codegraph" / "sessions").exists()


def test_image_outside_repo_uses_cwd(tmp_path, monkeypatch, capsys, repo):
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    img = outside / "cap.gif"
    img.write_bytes(b"g" * 10)
    payload = _payload(img, cwd=repo)
    assert hook_images.reread_hint(payload, img) is None
    assert "cap.gif" in (hook_images.reread_hint(payload, img) or "")
    record = repo / ".codegraph" / "sessions" / "images-s1.json"
    assert str(img.resolve()) in json.loads(record.read_text())


def test_cap_respected(monkeypatch, repo):
    monkeypatch.setattr(hook_images, "MAX_ENTRIES", 5)
    imgs = []
    for i in range(8):
        img = repo / f"i{i}.png"
        img.write_bytes(bytes([i]) * 10)
        imgs.append(img)
        assert hook_images.reread_hint(_payload(img), img) is None
    record = repo / ".codegraph" / "sessions" / "images-s1.json"
    data = json.loads(record.read_text())
    assert len(data) == 5
    assert str(imgs[-1].resolve()) in data
    assert str(imgs[0].resolve()) not in data


def test_unwritable_sessions_dir_silent(monkeypatch, repo):
    img = repo / "shot.png"
    img.write_bytes(b"p" * 10)
    (repo / ".codegraph" / "sessions").write_text("not a dir")
    assert hook_images.reread_hint(_payload(img), img) is None
    assert hook_images.reread_hint(_payload(img), img) is None


def test_fingerprint_reads_both_edges(tmp_path):
    big = tmp_path / "big.png"
    big.write_bytes(b"a" * (200 * 1024))
    first = hook_images.fingerprint(big)
    st = big.stat()
    data = bytearray(big.read_bytes())
    data[-1] = ord("b")
    big.write_bytes(bytes(data))
    os.utime(big, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert hook_images.fingerprint(big) != first


def _text(repo, name="spec.md", size=10 * 1024):
    doc = repo / name
    doc.write_text("# Spec\n" + "line of the spec\n" * (size // 17))
    return doc


def _agent(path, agent, session="s1"):
    p = _payload(path, session)
    if agent is not None:
        p["agent_id"] = agent
    return p


def test_text_read_twice_by_the_same_agent_is_flagged(monkeypatch, capsys, repo):
    doc = _text(repo)
    assert _run(monkeypatch, capsys, _agent(doc, "a1")) == ""
    ctx = _context(_run(monkeypatch, capsys, _agent(doc, "a1")))
    assert ctx.startswith("[cgh hook] spec.md was already read in full in this session")
    assert ctx.endswith("it is still in the conversation above.")


def test_text_read_by_another_agent_points_to_the_part_it_needs(
    monkeypatch, capsys, repo
):
    doc = _text(repo)
    assert _run(monkeypatch, capsys, _agent(doc, "a1")) == ""
    ctx = _context(_run(monkeypatch, capsys, _agent(doc, "a2")))
    assert "was read in full by another agent of this session" in ctx
    assert "file_summary" in ctx or "offset/limit" in ctx
    doc_out = json.loads(_run(monkeypatch, capsys, _agent(doc, "a3")))
    assert "permissionDecision" not in doc_out["hookSpecificOutput"]


def test_text_outside_the_size_band_is_not_tracked(monkeypatch, capsys, repo):
    small = _text(repo, "small.md", 1024)
    big = _text(repo, "big.md", 40 * 1024)
    for doc in (small, big):
        assert hook_images.text_reread_hint(_agent(doc, "a1"), doc) is None
        assert hook_images.text_reread_hint(_agent(doc, "a2"), doc) is None


def test_changed_text_and_ranged_reads_are_not_flagged(monkeypatch, capsys, repo):
    doc = _text(repo)
    assert _run(monkeypatch, capsys, _agent(doc, "a1")) == ""
    doc.write_text(doc.read_text() + "one more line\n")
    assert hook_images.text_reread_hint(_agent(doc, "a2"), doc) is None
    ranged = _agent(doc, "a3")
    ranged["tool_input"]["offset"] = 1
    assert _run(monkeypatch, capsys, ranged) == ""


def test_text_hint_needs_a_session(monkeypatch, capsys, repo):
    doc = _text(repo)
    for _ in range(2):
        assert hook_images.text_reread_hint(_payload(doc, session=None), doc) is None


def _deny(out: str) -> str | None:
    spec = json.loads(out)["hookSpecificOutput"] if out else {}
    if spec.get("permissionDecision") == "deny":
        return spec.get("permissionDecisionReason", "")
    return None


def test_with_answers_another_agent_is_denied_with_the_plan_once(
    monkeypatch, capsys, repo
):
    monkeypatch.setenv("CGH_HOOK_ANSWERS", "1")
    doc = _text(repo)
    assert _run(monkeypatch, capsys, _agent(doc, "a1")) == ""
    reason = _deny(_run(monkeypatch, capsys, _agent(doc, "a2")))
    assert reason and "another agent of this session read it in full" in reason
    assert "repeat the same Read and it goes through" in reason
    # The agent insists: its repeat goes through, silently.
    assert _run(monkeypatch, capsys, _agent(doc, "a2")) == ""
    # A third agent is answered again.
    assert _deny(_run(monkeypatch, capsys, _agent(doc, "a3")))


def test_with_answers_the_same_agent_only_gets_the_note(monkeypatch, capsys, repo):
    monkeypatch.setenv("CGH_HOOK_ANSWERS", "1")
    doc = _text(repo)
    assert _run(monkeypatch, capsys, _agent(doc, "a1")) == ""
    out = _run(monkeypatch, capsys, _agent(doc, "a1"))
    assert _deny(out) is None and "still in the conversation" in _context(out)


def test_without_answers_another_agent_only_gets_the_note(monkeypatch, capsys, repo):
    monkeypatch.setenv("CGH_HOOK_ANSWERS", "0")
    doc = _text(repo)
    assert _run(monkeypatch, capsys, _agent(doc, "a1")) == ""
    out = _run(monkeypatch, capsys, _agent(doc, "a2"))
    assert _deny(out) is None and "another agent" in _context(out)
