# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: MCP auth key management: generation, storage, validation.
#
# The auth key protects the owner's loopback HTTP bridge from other local
# processes. It is the shared secret behind the Bearer-token check.
#
# Key lifecycle:
#   1. `cgh init` (or the first owner) generates the key -> .cgh/auth.key,
#      mode 0600, gitignored.
#   2. Both the owner and every worker/CLI caller read that file via
#      ensure_auth_key() and send `Authorization: Bearer <key>`.
# The file contents are the secret; there is no env-var hand-off.

from __future__ import annotations

import secrets
from pathlib import Path

AUTH_KEY_FILE = "auth.key"
_CGH_DIR = ".cgh"


def ensure_state_dir_ignored(repo_root: str | Path) -> bool:
    """Keep the whole ``.cgh/`` state dir (the auth key included) out of
    version control with a self-contained ``.cgh/.gitignore`` of ``*``.

    The file lives inside cgh's own directory, so it protects the credential
    whether or not a git repo exists yet, from any subdirectory, however git
    is configured, and it never writes a diff into the repo's tracked
    ``.gitignore`` (which would block tearing down an agent worktree). This is
    the only protection the secret needs, so it does not depend on a git
    command succeeding. Returns True when it wrote the file."""
    ignore = Path(repo_root) / _CGH_DIR / ".gitignore"
    try:
        if ignore.exists():
            lines = ignore.read_text(encoding="utf-8", errors="replace").splitlines()
            if any(ln.strip() == "*" for ln in lines):
                return False
        ignore.parent.mkdir(parents=True, exist_ok=True)
        ignore.write_text("*\n", encoding="utf-8")
        return True
    except OSError:
        return False


def generate_auth_key() -> str:
    """Generate a cryptographically secure auth key."""
    return secrets.token_urlsafe(32)


def get_auth_key_path(repo_root: str | Path) -> Path:
    """Return the path to the auth key file."""
    return Path(repo_root) / _CGH_DIR / AUTH_KEY_FILE


def save_auth_key(repo_root: str | Path, key: str | None = None) -> str:
    """
    Generate (or save) an auth key to .cgh/auth.key.
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
    """Load the auth key from .cgh/auth.key. Returns None if not found."""
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
    Keep .cgh/auth.key out of version control. The whole .cgh/ dir is ignored
    by a self-contained .cgh/.gitignore (see ensure_state_dir_ignored), which
    covers the auth key. Returns True if that file was written.
    """
    return ensure_state_dir_ignored(repo_root)
