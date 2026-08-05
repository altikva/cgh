# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-05
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: fetch_and_index gated and cached: SSRF hosts refused,
#              secure mode blocks unless allow_fetch, the TTL cache
#              avoids a second network hit, and search_fetched reads the
#              indexed chunks back. Network is always mocked.

from __future__ import annotations

import pytest

from codegraph.analysis import fetch_index as fx

PAGE = (
    b"<html><head><title>Guide</title></head><body>"
    b"<h1>Setup</h1><p>Install the widget then configure the flux capacitor.</p>"
    b"<p>Run the daemon on port 8080.</p></body></html>"
)


def _repo(tmp_path):
    (tmp_path / ".codegraph").mkdir()
    return tmp_path


def _mock_fetch(monkeypatch, body=PAGE):
    """No network: the host resolves to a public IP (so the SSRF guard
    passes) and the GET returns the canned body."""
    calls = {"n": 0}
    monkeypatch.setattr(
        fx.socket,
        "getaddrinfo",
        lambda host, *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))],
    )

    def fake_get(url, repo_root):
        calls["n"] += 1
        return body

    monkeypatch.setattr(fx, "_http_get", fake_get)
    return calls


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://127.0.0.1/x",
        "http://169.254.169.254/latest/meta-data",
        "http://localhost:8080/",
        "http://10.0.0.5/internal",
        "http://2130706433/",  # decimal 127.0.0.1
        "http://0x7f000001/",  # hex 127.0.0.1
        "http://[::1]/",  # ipv6 loopback
    ],
)
def test_ssrf_and_scheme_refused(url, tmp_path):
    with pytest.raises(fx.FetchError):
        fx._guard_url(url, _repo(tmp_path))


def test_hostname_resolving_to_private_ip_refused(tmp_path, monkeypatch):
    """DNS-based SSRF: a public-looking name pointing at the LAN."""
    real = fx.socket.getaddrinfo

    def fake(host, *a, **k):
        if host == "evil.example":
            return [(2, 1, 6, "", ("10.0.0.5", 0))]
        return real(host, *a, **k)

    monkeypatch.setattr(fx.socket, "getaddrinfo", fake)
    with pytest.raises(fx.FetchError, match="non-public"):
        fx._guard_url("http://evil.example/x", _repo(tmp_path))


def test_unresolvable_host_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(
        fx.socket, "getaddrinfo", lambda *a, **k: (_ for _ in ()).throw(OSError())
    )
    with pytest.raises(fx.FetchError, match="does not resolve"):
        fx._guard_url("http://nope.invalid/x", _repo(tmp_path))


def test_public_host_allowed(tmp_path, monkeypatch):
    monkeypatch.setattr(
        fx.socket,
        "getaddrinfo",
        lambda host, *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))],
    )
    fx._guard_url("http://example.com/doc", _repo(tmp_path))  # no raise


def test_redirect_to_private_is_reguarded(tmp_path):
    """The redirect handler re-runs the guard on the new URL."""
    handler = fx._GuardedRedirect(_repo(tmp_path))
    with pytest.raises(fx.FetchError):
        handler.redirect_request(
            None, None, 302, "", {}, "http://169.254.169.254/latest"
        )


def test_secure_mode_blocks_without_allow_fetch(tmp_path, monkeypatch):
    _mock_fetch(monkeypatch)

    class _Cfg:
        mode = "secure"

    monkeypatch.setattr(
        "codegraph.core.config.load_config", lambda root: _Cfg(), raising=True
    )
    with pytest.raises(fx.FetchError, match="secure mode"):
        fx.fetch_and_index(_repo(tmp_path), "https://x.example/doc", config={})


def test_secure_mode_allows_with_flag(tmp_path, monkeypatch):
    _mock_fetch(monkeypatch)

    class _Cfg:
        mode = "secure"

    monkeypatch.setattr(
        "codegraph.core.config.load_config", lambda root: _Cfg(), raising=True
    )
    out = fx.fetch_and_index(
        _repo(tmp_path), "https://x.example/doc", config={"allow_fetch": True}
    )
    assert out["chunks"] >= 1 and out["cached"] is False


def test_fetch_index_and_search(tmp_path, monkeypatch):
    _mock_fetch(monkeypatch)
    root = _repo(tmp_path)
    out = fx.fetch_and_index(root, "https://x.example/guide", config={})
    assert out["title"] == "Guide"
    hits = fx.search_fetched(root, "flux capacitor")
    assert hits and "flux capacitor" in hits[0]["snippet"]
    assert hits[0]["url"] == "https://x.example/guide"


def test_ttl_cache_skips_the_network(tmp_path, monkeypatch):
    calls = _mock_fetch(monkeypatch)
    root = _repo(tmp_path)
    fx.fetch_and_index(root, "https://x.example/g", config={}, ttl_hours=24)
    fx.fetch_and_index(root, "https://x.example/g", config={}, ttl_hours=24)
    assert calls["n"] == 1  # second call served from cache


def test_force_refetches(tmp_path, monkeypatch):
    calls = _mock_fetch(monkeypatch)
    root = _repo(tmp_path)
    fx.fetch_and_index(root, "https://x.example/g", config={})
    fx.fetch_and_index(root, "https://x.example/g", config={}, force=True)
    assert calls["n"] == 2


def test_purge(tmp_path, monkeypatch):
    _mock_fetch(monkeypatch)
    root = _repo(tmp_path)
    fx.fetch_and_index(root, "https://x.example/g", config={})
    removed = fx.purge_fetched(root)
    assert removed >= 1
    assert fx.search_fetched(root, "flux") == []
