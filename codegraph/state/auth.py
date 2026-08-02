# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: MCP auth key management: generation, storage, validation.
#
# The auth key protects the owner's loopback HTTP bridge from other local
# processes. It is the shared secret behind the Bearer-token check.
#
# Key lifecycle:
#   1. `cgh init` (or the first owner) generates the key -> .codegraph/auth.key,
#      mode 0600, gitignored.
#   2. Both the owner and every worker/CLI caller read that file via
#      ensure_auth_key() and send `Authorization: Bearer <key>`.
# The file contents are the secret; there is no env-var hand-off.

from __future__ import annotations

import secrets
from pathlib import Path

AUTH_KEY_FILE = "auth.key"
_CODEGRAPH_DIR = ".codegraph"


def generate_auth_key() -> str:
    """Generate a cryptographically secure auth key."""
    return secrets.token_urlsafe(32)


def get_auth_key_path(repo_root: str | Path) -> Path:
    """Return the path to the auth key file."""
    return Path(repo_root) / _CODEGRAPH_DIR / AUTH_KEY_FILE


def save_auth_key(repo_root: str | Path, key: str | None = None) -> str:
    """
    Generate (or save) an auth key to .codegraph/auth.key.
    Returns the key.
    """
    if key is None:
        key = generate_auth_key()

    key_path = get_auth_key_path(repo_root)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_text(key + "\n", encoding="utf-8")
    # Restrict permissions (owner read/write only)
    key_path.chmod(0o600)

    return key


def load_auth_key(repo_root: str | Path) -> str | None:
    """Load the auth key from .codegraph/auth.key. Returns None if not found."""
    key_path = get_auth_key_path(repo_root)
    if not key_path.exists():
        return None
    return key_path.read_text(encoding="utf-8").strip()


def ensure_auth_key(repo_root: str | Path) -> str:
    """Load existing key or generate a new one. Always returns a key."""
    key = load_auth_key(repo_root)
    if not key:
        key = save_auth_key(repo_root)
    return key


def ensure_gitignore_has_auth_key(repo_root: str | Path) -> bool:
    """
    Ensure .codegraph/auth.key is in .gitignore.
    Returns True if .gitignore was modified.
    """
    gitignore = Path(repo_root) / ".gitignore"
    pattern = f"{_CODEGRAPH_DIR}/{AUTH_KEY_FILE}"

    if gitignore.exists():
        content = gitignore.read_text(encoding="utf-8")
        if pattern in content:
            return False
        with open(gitignore, "a", encoding="utf-8") as f:
            f.write(f"\n# codegraph auth key (never commit)\n{pattern}\n")
        return True
    return False
