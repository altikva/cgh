# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-25
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Redaction of every secret.* kind the regex scanner reports:
#              each kind masked in the aws_key style, a mixed document with
#              several kinds, a docx, and the guarantee that no fake token
#              survives in the output. Every fake token is assembled at
#              runtime so no token literal sits in the repo.

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("cgh_pii")

from cgh_pii.redact import SECRET_CATEGORIES, redact
from cgh_pii.regex_scanner import RegexPiiScanner

# Secret categories only: the default set also asks for the NER tier,
# which the plugin test environment does not install.
_SECRETS = list(SECRET_CATEGORIES)
_ALNUM = "aB3dE5fG7hJ9kL1mN2pQ4rS6tU8vW0xYz"  # 34 characters


def _tail(n: int, alphabet: str = _ALNUM) -> str:
    return (alphabet * (n // len(alphabet) + 1))[:n]


def _fakes() -> dict[str, list[str]]:
    """Redaction category -> fake values the scanner flags."""
    return {
        "aws_key": ["AK" + "IA" + "Q" * 4 + "7" * 4 + "EXAMPLEZ"],
        "private_key": ["-----BEGIN " + "RSA PRIVATE" + " KEY-----"],
        "anthropic_key": ["sk-" + "ant-" + "api03-" + _tail(40)],
        "openai_key": [
            "sk-" + "proj-" + _tail(40),
            "sk-" + "svcacct-" + _tail(41),
            "sk-" + "admin-" + _tail(42),
        ],
        "github_token": [
            "gh" + "p_" + _tail(36),
            "gh" + "s_" + _tail(40),
            "github" + "_pat_" + _tail(60, _ALNUM + "_"),
        ],
        "gitlab_token": ["gl" + "pat-" + _tail(20)],
        "slack_token": ["xo" + "xb-" + _tail(12) + "-" + _tail(12)],
        "google_api_key": ["AI" + "za" + _tail(35)],
        "stripe_key": [
            "sk" + "_live_" + _tail(24),
            "rk" + "_live_" + _tail(30),
        ],
        "assignment": ["hunter2" + "-correct-" + "horse"],
    }


def _line(cat: str, value: str) -> str:
    if cat == "assignment":
        return 'password = "' + value + '"'
    return "value: " + value


def _scan(text: str):
    return RegexPiiScanner(pii=False).scan(Path("x.txt"), text, None)


def _scan_keys(text: str) -> set[str]:
    return {f.key for f in _scan(text)}


def test_every_scanner_secret_kind_is_a_redaction_category():
    # A new secret.* kind in the scanner must land here with a fake value.
    assert set(SECRET_CATEGORIES) == set(_fakes())


def test_fakes_are_flagged_by_the_scanner():
    for cat, values in _fakes().items():
        for value in values:
            assert "secret." + cat in _scan_keys(_line(cat, value)), value


@pytest.mark.parametrize("cat", sorted(_fakes()))
def test_each_kind_is_masked_in_aws_key_style(cat):
    for value in _fakes()[cat]:
        text = _line(cat, value)
        out, counts = redact(text, only=_SECRETS)
        token = f"[{cat.upper()}_1]"
        assert value not in out
        assert token in out
        assert counts == {cat: 1}


def test_only_selects_a_token_kind():
    gh = _fakes()["github_token"][0]
    aws = _fakes()["aws_key"][0]
    out, counts = redact(f"{gh} and {aws}", only=["github_token"])
    assert out == f"[GITHUB_TOKEN_1] and {aws}"
    assert counts == {"github_token": 1}


def test_pseudonym_mode_masks_tokens():
    gl = _fakes()["gitlab_token"][0]
    out, _ = redact(f"token {gl}", only=_SECRETS, mode="pseudonym", secret=b"k" * 32)
    assert gl not in out
    assert re.search(r"<pii\.gitlab_token:[0-9a-f]{10}>", out)


def test_assignment_keeps_the_name_and_masks_the_value():
    value = _fakes()["assignment"][0]
    out, counts = redact('password = "' + value + '"', only=_SECRETS)
    assert out == 'password = "[ASSIGNMENT_1]"'
    assert counts == {"assignment": 1}


def test_token_in_assignment_keeps_the_provider_category():
    key = _fakes()["openai_key"][0]
    out, counts = redact('api_key = "' + key + '"', only=_SECRETS)
    assert out == 'api_key = "[OPENAI_KEY_1]"'
    assert counts == {"openai_key": 1}


def _mixed() -> tuple[str, list[str]]:
    f = _fakes()
    gh = f["github_token"][0]
    values = [
        f["anthropic_key"][0],
        gh,
        f["slack_token"][0],
        f["stripe_key"][0],
        f["google_api_key"][0],
        f["aws_key"][0],
    ]
    text = (
        "# deploy notes\n"
        f"ANTHROPIC_API_KEY={values[0]}\n"
        f"export GH_TOKEN={gh}\n"
        f"slack: {values[2]}, stripe: {values[3]}\n"
        f"maps key {values[4]} and aws {values[5]}\n"
        f"again {gh}\n"
    )
    return text, values


def test_mixed_document_masks_every_kind():
    text, values = _mixed()
    out, counts = redact(text, only=_SECRETS)
    for value in values:
        assert value not in out
    assert out.count("[GITHUB_TOKEN_1]") == 2
    assert counts == {
        "anthropic_key": 1,
        "github_token": 2,
        "slack_token": 1,
        "stripe_key": 1,
        "google_api_key": 1,
        "aws_key": 1,
    }
    assert "ANTHROPIC_API_KEY=[ANTHROPIC_KEY_1]" in out
    assert out.startswith("# deploy notes\n")


def test_no_token_survives_redaction():
    """Every fake of every kind in one text: the output holds none of
    them and the scanner reports no block secret on it. (The masked
    assignment still reads as one, at warn: its placeholder is quoted.)"""
    lines = [_line(cat, value) for cat, values in _fakes().items() for value in values]
    text = "\n".join(lines)
    out, _ = redact(text, only=_SECRETS)
    for values in _fakes().values():
        for value in values:
            assert value not in out
    assert [f.key for f in _scan(out) if f.severity == "block"] == []


class TestDocx:
    def test_docx_tokens_masked_in_body_and_table(self, tmp_path):
        pytest.importorskip("docx")
        from cgh_pii.redact_docx import redact_docx_file
        from docx import Document

        _text, values = _mixed()
        doc = Document()
        doc.add_paragraph("token " + values[1])
        run_split = doc.add_paragraph("key ")
        # A token split across runs is still masked: the paragraph text
        # is redacted as a whole.
        run_split.add_run(values[0][:10])
        run_split.add_run(values[0][10:])
        table = doc.add_table(rows=1, cols=2)
        table.cell(0, 0).text = values[2]
        table.cell(0, 1).text = values[1]
        src = tmp_path / "in.docx"
        doc.save(str(src))

        dst = tmp_path / "out.docx"
        counts = redact_docx_file(src, dst, only=_SECRETS)

        out = Document(str(dst))
        body = [p.text for p in out.paragraphs]
        cells = [c.text for c in out.tables[0].rows[0].cells]
        assert body == ["token [GITHUB_TOKEN_1]", "key [ANTHROPIC_KEY_1]"]
        assert cells == ["[SLACK_TOKEN_1]", "[GITHUB_TOKEN_1]"]
        assert counts == {"github_token": 2, "anthropic_key": 1, "slack_token": 1}
        everything = "\n".join(body + cells)
        for value in values[:3]:
            assert value not in everything
