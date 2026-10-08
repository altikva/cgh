# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-08
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The built-in secret check: each pattern fires on a realistic
#              value, placeholders and env lookups do not, the refusal names
#              file and line without the value, and run_generation refuses
#              a reference, an extended target or a spec holding a secret,
#              for local and cloud backends and in every egress posture.

from __future__ import annotations

import pytest

pytest.importorskip("cgh_codegen")

from cgh_codegen.flow import run_generation
from cgh_codegen.picker import CodegenError
from cgh_codegen.secrets import find_secrets, secret_refusal

# Built by concatenation so this file does not itself look like it leaks.
AWS = "AKIA" + "IOSFODNN7EXAMPLQ"
GH = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
GH_PAT = "github_pat_" + "11ABCDEFG0123456789_abcdefghijklmnop"
SLACK = "xoxb-" + "1234567890-abcdefghij"
STRIPE = "sk_live_" + "4eC39HqLyjWDarjtT1zdp7dc"
PEM = "-----BEGIN " + "OPENSSH PRIVATE KEY-----"


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        (f"{PEM}\nb3BlbnNzaA==\n", "private key"),
        ("-----BEGIN " + "RSA PRIVATE KEY-----\n", "private key"),
        (f"key_id = {AWS}\n", "AWS access key id"),
        (f"TOKEN={GH}\n", "GitHub token"),
        (f"TOKEN={GH_PAT}\n", "GitHub token"),
        (f"hook = {SLACK}\n", "Slack token"),
        (f"stripe.api_key = {STRIPE}\n", "Stripe live key"),
        (
            'headers = {"Authorization": "Bearer '
            + "eyJhbGciOiJIUzI1NiJ9.abc123def456"
            + '"}\n',
            "bearer token",
        ),
        (
            '{\n  "type": "service_account",\n  "project_id": "p",\n'
            '  "private_key_id": "k",\n  "private_key": "<elided>"\n}\n',
            "GCP service account key",
        ),
        ('password = "hunter2hunter2"\n', "credential assignment"),
        ('DB_PASSWORD = "S3cure!pass"\n', "credential assignment"),
        ("SECRET_KEY = 'django-insecure-9x8y7z6w5v'\n", "credential assignment"),
        ('"api_key": "a8f3e1c9b7d5"\n', "credential assignment"),
        ("token: 'Zq81kLm09pXy'\n", "credential assignment"),
    ],
)
def test_secret_shapes_are_found(text, kind):
    assert kind in {h.kind for h in find_secrets(text)}


@pytest.mark.parametrize(
    "text",
    [
        'password = ""\n',
        'password = "changeme"\n',
        'password = "xxxxxxxxxx"\n',
        'api_key = "********"\n',
        'token = "${GITHUB_TOKEN}"\n',
        'password: "{{ vault_db_password }}"\n',
        'secret = "<your-secret-here>"\n',
        'api_key = "YOUR_API_KEY"\n',
        'password = "example-password"\n',
        "password = os.environ['DB_PASSWORD']\n",
        'token = os.getenv("TOKEN")\n',
        "token = settings.TOKEN\n",
        'if password == "hunter2hunter2":\n',
        'token_type = "bearer_access"\n',
        'msg = "the token is missing from the request"\n',
        'password = "not a secret value"\n',
        'headers = {"Authorization": f"Bearer {token}"}\n',
        'auth = "Bearer ${ACCESS_TOKEN}"\n',
        "auth = 'Bearer YOUR_ACCESS_TOKEN_HERE'\n",
        '{"type": "service_account", "client_email": "a@b.iam"}\n',
        "def add(a, b):\n    return a + b\n",
    ],
)
def test_placeholders_and_lookalikes_are_clean(text):
    assert find_secrets(text) == []


def test_refusal_names_file_and_line_not_the_value():
    text = "x = 1\n\n" + 'password = "hunter2hunter2"\n' + f"k = {STRIPE}\n"
    reason = secret_refusal("src/settings.py", text)
    assert reason is not None
    assert reason.startswith("src/settings.py:3 ")
    assert "credential assignment" in reason
    assert "and 1 more" in reason
    assert "hunter2" not in reason
    assert STRIPE not in reason and "sk_live" not in reason


def test_clean_text_has_no_refusal():
    assert secret_refusal("a.py", "def f():\n    return 1\n") is None


class FakeBackend:
    def __init__(self, *, is_local: bool) -> None:
        self.is_local = is_local
        self.name = "fake-local" if is_local else "fake-cloud"
        self.calls = 0

    def generate(self, system: str, user: str) -> tuple[str, float]:
        self.calls += 1
        return "```python\nclass UserService:\n    pass\n```", 0.0


def _repo(tmp_path, ref_body: str):
    src = tmp_path / "src"
    src.mkdir()
    (src / "order_service.py").write_text(ref_body, encoding="utf-8")
    return tmp_path


LEAKY = f'class OrderService:\n    KEY = "{AWS}"\n'


@pytest.mark.parametrize("is_local", [True, False])
@pytest.mark.parametrize("egress", [None, "open", "strict"])
def test_reference_with_secret_is_refused_everywhere(tmp_path, is_local, egress):
    root = _repo(tmp_path, LEAKY)
    backend = FakeBackend(is_local=is_local)
    config = {} if egress is None else {"egress": egress}
    with pytest.raises(CodegenError) as exc:
        run_generation(
            root,
            "spec",
            "src/user_service.py",
            "src/order_service.py",
            config=config,
            backend=backend,
        )
    msg = str(exc.value)
    assert "src/order_service.py:2" in msg and "AWS access key id" in msg
    assert AWS not in msg
    assert backend.calls == 0
    assert not (root / "src" / "user_service.py").exists()


def test_auto_pick_falls_back_past_a_secret_bearing_sibling(tmp_path):
    root = _repo(tmp_path, LEAKY)
    (root / "src" / "account_service.py").write_text(
        "class AccountService:\n    pass\n", encoding="utf-8"
    )
    backend = FakeBackend(is_local=True)
    out = run_generation(
        root, "spec", "src/user_service.py", config={}, backend=backend
    )
    assert out["reference"] == "src/account_service.py"
    assert "secret check" in out["ref_fallback"]


def test_extending_a_file_with_a_secret_is_refused(tmp_path):
    root = _repo(tmp_path, "class OrderService:\n    pass\n")
    (root / "src" / "user_service.py").write_text(
        f"import os\nTOKEN = '{GH}'\n", encoding="utf-8"
    )
    backend = FakeBackend(is_local=True)
    with pytest.raises(CodegenError, match=r"src/user_service.py:2 .*GitHub token"):
        run_generation(
            root,
            "add a helper",
            "src/user_service.py",
            config={},
            backend=backend,
            extend=True,
        )
    assert backend.calls == 0


def test_spec_with_a_secret_is_refused(tmp_path):
    root = _repo(tmp_path, "class OrderService:\n    pass\n")
    backend = FakeBackend(is_local=False)
    with pytest.raises(CodegenError, match="the spec:1") as exc:
        run_generation(
            root,
            f"call the API with {STRIPE}",
            "src/user_service.py",
            "src/order_service.py",
            config={},
            backend=backend,
        )
    assert STRIPE not in str(exc.value)
    assert backend.calls == 0
