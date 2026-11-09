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

import os
import secrets
import subprocess
from pathlib import Path

AUTH_KEY_FILE = "auth.key"
_CGH_DIR = ".cgh"


def ensure_locally_ignored(repo_root: str | Path, pattern: str) -> bool:
    """Ignore ``pattern`` through the repo's local ``.git/info/exclude`` (the
    shared common dir for a worktree) instead of the tracked ``.gitignore``, so
    cgh never writes an unrequested diff into a committed file. No-op outside a
    git repo, when the path is already ignored (by an existing rule), or when
    it is already in the exclude file. Returns True only when it added a line.
    Fails closed: any git or IO error leaves the repo untouched."""
    root = Path(repo_root)

    def _git(*args: str) -> subprocess.CompletedProcess[str] | None:
        try:
            return subprocess.run(
                ["git", "-C", str(root), *args],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return None

    rev = _git("rev-parse", "--git-path", "info/exclude")
    if rev is None or rev.returncode != 0:
        return False  # not a git repo (or git unavailable): nothing to do
    rel = rev.stdout.strip()
    exclude_path = Path(rel) if os.path.isabs(rel) else root / rel

    already = _git("check-ignore", "-q", pattern)
    if already is not None and already.returncode == 0:
        return False  # already ignored by .gitignore or the exclude file

    existing = (
        exclude_path.read_text(encoding="utf-8", errors="replace")
        if exclude_path.exists()
        else ""
    )
    if pattern in existing:
        return False
    try:
        exclude_path.parent.mkdir(parents=True, exist_ok=True)
        with open(exclude_path, "a", encoding="utf-8") as f:
            f.write(f"\n# cgh (local, not committed)\n{pattern}\n")
    except OSError:
        return False
    return True


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
    Keep .cgh/auth.key out of version control, through the repo's local
    .git/info/exclude rather than the tracked .gitignore (see
    ensure_locally_ignored). Returns True if a line was added.
    """
    return ensure_locally_ignored(repo_root, f"{_CGH_DIR}/{AUTH_KEY_FILE}")
