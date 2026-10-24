# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-24
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Provider API token detection in the regex scanner: each kind
#              found at block severity with a count-only value, no match
#              inside a longer word, near misses ignored, and linear time on
#              adversarial input. Every fake token is assembled at runtime so
#              no token literal sits in the repo for secret scanners to flag.

from __future__ import annotations

import time
from pathlib import Path

import pytest

pytest.importorskip("cgh_pii")

from cgh_pii.regex_scanner import RegexPiiScanner

_ALNUM = "aB3dE5fG7hJ9kL1mN2pQ4rS6tU8vW0xYz"  # 34 characters


def _tail(n: int, alphabet: str = _ALNUM) -> str:
    return (alphabet * (n // len(alphabet) + 1))[:n]


def _fakes() -> dict[str, list[str]]:
    return {
        "secret.anthropic_key": ["sk-" + "ant-" + "api03-" + _tail(40)],
        "secret.openai_key": [
            "sk-" + "proj-" + _tail(40),
            "sk-" + "svcacct-" + _tail(40),
            "sk-" + "admin-" + _tail(40),
        ],
        "secret.github_token": [
            "gh" + "p_" + _tail(36),
            "gh" + "s_" + _tail(40),
            "github" + "_pat_" + _tail(60, _ALNUM + "_"),
        ],
        "secret.gitlab_token": ["gl" + "pat-" + _tail(20)],
        "secret.slack_token": ["xo" + "xb-" + _tail(12) + "-" + _tail(12)],
        "secret.google_api_key": ["AI" + "za" + _tail(35)],
        "secret.stripe_key": [
            "sk" + "_live_" + _tail(24),
            "rk" + "_live_" + _tail(30),
        ],
    }


def _scan(text: str):
    return RegexPiiScanner(pii=False).scan(Path("x.txt"), text, None)


def _keys(text: str) -> set[str]:
    return {f.key for f in _scan(text)}


_CASES = [(key, tok) for key, toks in _fakes().items() for tok in toks]


class TestDetected:
    @pytest.mark.parametrize(("key", "token"), _CASES)
    def test_each_kind(self, key, token):
        text = "line one\nexport VALUE " + token + " # end\n"
        found = {f.key: f for f in _scan(text)}
        assert key in found
        finding = found[key]
        assert finding.severity == "block"
        assert finding.line == 2
        # Count only: the matched token never reaches the finding.
        assert finding.value == "1"
        assert token not in finding.value

    @pytest.mark.parametrize(("key", "token"), _CASES)
    def test_quoted_and_after_equals(self, key, token):
        assert key in _keys('X="' + token + '"')
        assert key in _keys("X=" + token)

    def test_count_of_several(self):
        tok = "gh" + "p_" + _tail(36)
        found = {f.key: f for f in _scan(tok + "\n" + tok + "\n")}
        assert found["secret.github_token"].value == "2"
        assert found["secret.github_token"].line == 1

    def test_disabled_key(self):
        tok = "gl" + "pat-" + _tail(20)
        scanner = RegexPiiScanner(disabled_keys={"secret.gitlab_token"}, pii=False)
        assert scanner.scan(Path("x"), tok, None) == []


class TestNotMatched:
    @pytest.mark.parametrize(("key", "token"), _CASES)
    @pytest.mark.parametrize("prefix", ["x", "Z9", "my_", "a-"])
    def test_inside_longer_word(self, key, token, prefix):
        assert key not in _keys(prefix + token)

    @pytest.mark.parametrize(
        ("key", "text"),
        [
            # Tails one character too short.
            ("secret.anthropic_key", "sk-" + "ant-" + "api03-" + _tail(31)),
            ("secret.openai_key", "sk-" + "proj-" + _tail(31)),
            ("secret.github_token", "gh" + "p_" + _tail(35)),
            ("secret.github_token", "github" + "_pat_" + _tail(59)),
            ("secret.gitlab_token", "gl" + "pat-" + _tail(19)),
            ("secret.slack_token", "xo" + "xb-" + _tail(9)),
            ("secret.google_api_key", "AI" + "za" + _tail(34)),
            ("secret.stripe_key", "sk" + "_live_" + _tail(23)),
            # Wrong prefixes.
            ("secret.anthropic_key", "sk-" + "ant-" + "api3-" + _tail(40)),
            ("secret.anthropic_key", "sk-" + "ant-" + "API03-" + _tail(40)),
            ("secret.openai_key", "sk-" + "user-" + _tail(40)),
            ("secret.github_token", "gh" + "x_" + _tail(40)),
            ("secret.gitlab_token", "gl" + "ptt-" + _tail(30)),
            ("secret.slack_token", "xo" + "xz-" + _tail(20)),
            ("secret.google_api_key", "AI" + "zb" + _tail(35)),
            ("secret.stripe_key", "pk" + "_live_" + _tail(30)),
            ("secret.stripe_key", "sk" + "_test_" + _tail(30)),
            # A tail character outside the class cuts the run short.
            ("secret.github_token", "gh" + "p_" + _tail(20) + "-" + _tail(20)),
        ],
    )
    def test_near_miss(self, key, text):
        assert key not in _keys(text)

    def test_plain_prose(self):
        text = "Ask the sk team, the ghp group and the xoxb bot; AIza is a prefix.\n"
        assert _keys(text) == set()


class TestLinearTime:
    @pytest.mark.parametrize(
        "unit",
        [
            "sk-ant-",
            "sk-proj-",
            "ghp_",
            "github_pat_",
            "glpat-",
            "xoxb-",
            "AIza",
            "sk_live_",
        ],
    )
    def test_adversarial_repeat(self, unit):
        text = (unit * (100_000 // len(unit) + 1))[:100_000]
        start = time.perf_counter()
        _scan(text)
        assert time.perf_counter() - start < 1.0

    def test_long_near_miss_tails(self):
        # Many prefixes each followed by a tail one short of the minimum.
        chunk = "sk-" + "ant-" + "api03-" + _tail(31) + " "
        text = chunk * (100_000 // len(chunk) + 1)
        start = time.perf_counter()
        assert "secret.anthropic_key" not in _keys(text)
        assert time.perf_counter() - start < 1.0
