# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-09
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The callers of `Class.method` keep only the calls that can
#              reach that class's method. A Protocol with two implementations
#              and a third-party object sharing the method names: a call on
#              a local built from one implementation, on an object a typed
#              factory returns, or on an attribute built from a library class
#              imported inside __init__ used to be listed under every method of
#              the name. A call through the Protocol stays listed with `via`,
#              and a receiver of unknown type stays listed. The filter never
#              drops on uncertainty: a factory result is typed by its return
#              annotation (imported or local), an unannotated one or a fixture
#              is kept, and a self call reaches a sibling mixin's method.

from __future__ import annotations

from pathlib import Path

import pytest

from codegraph.analysis.callers import callers_of
from codegraph.core.db import get_connection, reset_connection
from codegraph.indexer import index_repo

REPO = {
    "app/__init__.py": "",
    "app/kms.py": (
        "from typing import Protocol\n\n\n"
        "class KmsClient(Protocol):\n"
        "    async def ensure_crypto_key(self, key_id: str) -> str: ...\n"
        "    async def encrypt(self, name: str, data: bytes) -> bytes: ...\n"
        "    async def decrypt(self, name: str, data: bytes) -> bytes: ...\n"
        "    async def destroy_crypto_key(self, name: str) -> int: ...\n\n\n"
        "class InMemoryKmsClient:\n"
        "    async def ensure_crypto_key(self, key_id: str) -> str:\n"
        "        return key_id\n\n"
        "    async def encrypt(self, name: str, data: bytes) -> bytes:\n"
        "        return data\n\n"
        "    async def decrypt(self, name: str, data: bytes) -> bytes:\n"
        "        return data\n\n"
        "    async def destroy_crypto_key(self, name: str) -> int:\n"
        "        return 0\n\n\n"
        "class GoogleKmsClient:\n"
        "    def __init__(self) -> None:\n"
        "        from google.cloud import kms\n\n"
        "        self._client = kms.KeyManagementServiceClient()\n\n"
        "    async def ensure_crypto_key(self, key_id: str) -> str:\n"
        "        return key_id\n\n"
        "    async def encrypt(self, name: str, data: bytes) -> bytes:\n"
        "        def _enc() -> bytes:\n"
        "            return self._client.encrypt(request={'name': name})\n\n"
        "        return _enc()\n\n"
        "    async def decrypt(self, name: str, data: bytes) -> bytes:\n"
        "        return self._client.decrypt(request={'name': name})\n\n"
        "    async def destroy_crypto_key(self, name: str) -> int:\n"
        "        return 1\n\n\n"
        "class Cipher:\n"
        "    def __init__(self, kms_client: KmsClient) -> None:\n"
        "        self._kms = kms_client\n\n"
        "    async def destroy_org_kek(self, name: str) -> int:\n"
        "        return await self._kms.destroy_crypto_key(name)\n"
    ),
    "app/encryption.py": (
        "import functools\n\n"
        "from cryptography.fernet import Fernet, MultiFernet\n\n\n"
        "@functools.lru_cache\n"
        "def _get_fernet() -> MultiFernet:\n"
        "    return MultiFernet([Fernet(b'k')])\n\n\n"
        "def encrypt_value(plaintext: str) -> str:\n"
        "    fernet = _get_fernet()\n"
        "    return fernet.encrypt(plaintext.encode()).decode()\n\n\n"
        "def decrypt_value(stored: str) -> str:\n"
        "    fernet = _get_fernet()\n"
        "    return fernet.decrypt(stored.encode()).decode()\n"
    ),
    "app/wrapper.py": (
        "from somelib import LibBase\n\n\nclass Wrapper(LibBase):\n    pass\n"
    ),
    # Wrapper inherits destroy_crypto_key from a library: the call is on a
    # known class the repo does not define it for, so the edge falls back
    # to the methods of the name in the files the caller imports.
    "app/use_wrapper.py": (
        "from app.kms import InMemoryKmsClient\n"
        "from app.wrapper import Wrapper\n\n\n"
        "def wipe() -> None:\n"
        "    Wrapper().destroy_crypto_key('k')\n"
    ),
    "tests/__init__.py": "",
    "tests/test_kms.py": (
        "from app.kms import GoogleKmsClient, InMemoryKmsClient\n\n\n"
        "async def test_inmemory_destroy_raises_for_never_provisioned_key():\n"
        "    kms = InMemoryKmsClient()\n"
        "    await kms.destroy_crypto_key('k')\n\n\n"
        "async def test_google_ensure_key():\n"
        "    client = GoogleKmsClient()\n"
        "    await client.ensure_crypto_key('k')\n\n\n"
        "async def test_inmemory_ensure_key():\n"
        "    client = InMemoryKmsClient()\n"
        "    await client.ensure_crypto_key('k')\n\n\n"
        "async def test_unknown_receiver(client):\n"
        "    await client.destroy_crypto_key('k')\n\n\n"
        "def _google_client(fake) -> GoogleKmsClient:\n"
        "    client = GoogleKmsClient()\n"
        "    client._client = fake\n"
        "    return client\n\n\n"
        "async def test_google_helper_ensure_key():\n"
        "    await _google_client(object()).ensure_crypto_key('k')\n\n\n"
        "async def test_fixture_ensure_key(kms_fixture):\n"
        "    await kms_fixture.ensure_crypto_key('k')\n"
    ),
    "app/bus.py": (
        "class EventBus:\n"
        "    async def publish(self, name: str) -> None:\n"
        "        return None\n\n"
        "    def subscribe(self, name: str, fn) -> None:\n"
        "        return None\n\n\n"
        "class AfterCommitEventBus(EventBus):\n"
        "    async def publish(self, name: str) -> None:\n"
        "        return None\n\n\n"
        "class AuditLog:\n"
        "    async def publish(self, name: str) -> None:\n"
        "        return None\n"
    ),
    "app/deps.py": (
        "from typing import Optional\n\n"
        "from app.bus import AuditLog, EventBus\n\n\n"
        "def get_event_bus() -> EventBus:\n"
        "    return EventBus()\n\n\n"
        "def maybe_bus() -> Optional[EventBus]:\n"
        "    return None\n\n\n"
        "def get_audit_log() -> 'AuditLog':\n"
        "    return AuditLog()\n\n\n"
        "def make_anything():\n"
        "    return AuditLog()\n"
    ),
    "app/handler.py": (
        "class CheckoutMixin:\n"
        "    def _site_origins(self) -> set:\n"
        "        return set()\n\n\n"
        "class PaymentsMixin:\n"
        "    def create_portal_session(self) -> set:\n"
        "        return self._site_origins()\n\n\n"
        "class Handler(CheckoutMixin, PaymentsMixin):\n"
        "    pass\n"
    ),
    "tests/test_bus.py": (
        "from app.deps import get_audit_log, get_event_bus, make_anything, maybe_bus\n\n\n"
        "async def test_factory_publish():\n"
        "    await get_event_bus().publish('x')\n\n\n"
        "async def test_optional_factory_publish():\n"
        "    await maybe_bus().publish('x')\n\n\n"
        "async def test_audit_publish():\n"
        "    await get_audit_log().publish('x')\n\n\n"
        "async def test_unannotated_factory_publish():\n"
        "    await make_anything().publish('x')\n\n\n"
        "def test_local_import_subscribe():\n"
        "    from app.deps import get_event_bus as bus\n\n"
        "    bus().subscribe('x', print)\n"
    ),
}


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")


@pytest.fixture(params=["duckdb", "sqlite"])
def repo(request, tmp_path, monkeypatch):
    if request.param == "duckdb":
        pytest.importorskip("duckdb")
    monkeypatch.setenv("CGH_DB", request.param)
    reset_connection()
    root = tmp_path.resolve() / "repo"
    _write(root, REPO)
    index_repo(root, method="os_walk")
    yield root
    reset_connection()


def _rows(root: Path, name: str) -> dict[str, str | None]:
    """{caller: via} of callers_of(name)."""
    return {r["caller"]: r.get("via") for r in callers_of(get_connection(root), name)}


def test_local_of_a_sibling_implementation_is_dropped(repo):
    rows = _rows(repo, "GoogleKmsClient.destroy_crypto_key")
    assert "test_inmemory_destroy_raises_for_never_provisioned_key" not in rows
    # Through the Protocol: listed with via. Unknown receiver: kept.
    assert rows["destroy_org_kek"] == "KmsClient.destroy_crypto_key"
    assert "test_unknown_receiver" in rows
    own = _rows(repo, "InMemoryKmsClient.destroy_crypto_key")
    assert own["test_inmemory_destroy_raises_for_never_provisioned_key"] is None


def test_third_party_object_is_not_a_caller(repo):
    for name in (
        "InMemoryKmsClient.encrypt",
        "GoogleKmsClient.encrypt",
        "KmsClient.encrypt",
        "InMemoryKmsClient.decrypt",
        "KmsClient.decrypt",
    ):
        rows = _rows(repo, name)
        assert "encrypt_value" not in rows, (name, rows)
        assert "decrypt_value" not in rows, (name, rows)


def test_sdk_client_attribute_is_not_a_caller(repo):
    # GoogleKmsClient.encrypt calls self._client.encrypt on the SDK client.
    assert "encrypt" not in _rows(repo, "InMemoryKmsClient.encrypt")
    assert "decrypt" not in _rows(repo, "InMemoryKmsClient.decrypt")


def test_constructor_of_another_implementation_is_dropped(repo):
    rows = _rows(repo, "InMemoryKmsClient.ensure_crypto_key")
    assert "test_google_ensure_key" not in rows
    assert rows["test_inmemory_ensure_key"] is None
    assert "test_inmemory_ensure_key" not in _rows(
        repo, "GoogleKmsClient.ensure_crypto_key"
    )
    assert _rows(repo, "GoogleKmsClient.ensure_crypto_key") == {
        "test_google_ensure_key": None,
        "test_google_helper_ensure_key": None,
        "test_fixture_ensure_key": None,
    }


def test_test_helper_returning_an_implementation_types_the_receiver(repo):
    # _google_client(...) is annotated -> GoogleKmsClient in the test file.
    assert "test_google_helper_ensure_key" in _rows(
        repo, "GoogleKmsClient.ensure_crypto_key"
    )
    assert "test_google_helper_ensure_key" not in _rows(
        repo, "InMemoryKmsClient.ensure_crypto_key"
    )


def test_fixture_receiver_is_kept(repo):
    for name in (
        "InMemoryKmsClient.ensure_crypto_key",
        "GoogleKmsClient.ensure_crypto_key",
        "KmsClient.ensure_crypto_key",
    ):
        assert "test_fixture_ensure_key" in _rows(repo, name), name


def test_imported_factory_return_annotation_types_the_receiver(repo):
    # get_event_bus() -> EventBus, maybe_bus() -> Optional[EventBus].
    for name in ("EventBus.publish", "AfterCommitEventBus.publish"):
        rows = _rows(repo, name)
        assert "test_factory_publish" in rows, (name, rows)
        assert "test_optional_factory_publish" in rows, (name, rows)
        assert "test_audit_publish" not in rows, (name, rows)
    audit = _rows(repo, "AuditLog.publish")
    assert "test_audit_publish" in audit
    assert "test_factory_publish" not in audit
    assert "test_optional_factory_publish" not in audit
    # Imported inside the test body, under an alias.
    assert "test_local_import_subscribe" in _rows(repo, "EventBus.subscribe")


def test_unannotated_factory_receiver_is_kept(repo):
    for name in ("EventBus.publish", "AfterCommitEventBus.publish", "AuditLog.publish"):
        assert "test_unannotated_factory_publish" in _rows(repo, name), name


def test_self_call_reaches_a_sibling_mixin(repo):
    # Handler(CheckoutMixin, PaymentsMixin): PaymentsMixin's self call
    # lands on CheckoutMixin's method in every Handler.
    rows = _rows(repo, "CheckoutMixin._site_origins")
    assert "create_portal_session" in rows


def test_known_unrelated_receiver_class_is_dropped_at_query_time(repo):
    conn = get_connection(repo)
    edges = conn.find_neighbors("CALLS", src_where={"name": "wipe"}, return_dst=["id"])
    # The stored edge is still a guess by name...
    assert any(
        e["dst_id"].endswith("InMemoryKmsClient.destroy_crypto_key") for e in edges
    )
    # ...which the class-qualified query does not trust.
    assert "wipe" not in _rows(repo, "InMemoryKmsClient.destroy_crypto_key")


def test_bare_name_lists_every_name_match(repo):
    rows = callers_of(get_connection(repo), "destroy_crypto_key")
    assert {r["caller"] for r in rows} == {
        "destroy_org_kek",
        "test_inmemory_destroy_raises_for_never_provisioned_key",
        "test_unknown_receiver",
        "wipe",
    }
    assert not any("via" in r for r in rows)


def test_parser_types_only_unambiguous_locals(tmp_path):
    from codegraph.parsers.python import PythonParser

    path = tmp_path / "m.py"
    path.write_text(
        "from app.kms import GoogleKmsClient, InMemoryKmsClient\n\n\n"
        "def f(flag, c):\n"
        "    a = InMemoryKmsClient()\n"
        "    b = InMemoryKmsClient()\n"
        "    if flag:\n"
        "        b = GoogleKmsClient()\n"
        "    for d in []:\n"
        "        pass\n"
        "    d = InMemoryKmsClient()\n"
        "    c = InMemoryKmsClient()\n"
        "    with open('x') as e:\n"
        "        e = InMemoryKmsClient()\n"
        "    a.go()\n"
        "    b.go()\n"
        "    c.go()\n"
        "    d.go()\n"
        "    e.go()\n",
        encoding="utf-8",
    )
    [fn] = PythonParser().parse(path).functions
    receivers = {r.receiver for r in fn.call_refs if r.name == "go"}
    # Only `a` has one known class; the others are also bound another way.
    assert receivers == {"InMemoryKmsClient()", "b", "c", "d", "e"}
