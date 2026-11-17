# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-17
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Build the cgh graph for every pinned checkout (tier 1). Each
#              checkout gets its own .cgh/ so cgh's root-walk stops there instead
#              of climbing into an enclosing repo. Run after fetch.py.

from __future__ import annotations

import sys

from bench._util import BenchError, index_repo, repo_checkout, repos_config


def index_all() -> None:
    """Index each pinned checkout in place. Fails loudly on a missing clone."""
    for name in repos_config()["repos"]:
        checkout = repo_checkout(name)
        if not (checkout / ".git").is_dir():
            raise BenchError(
                f"{checkout} is not cloned; run `python -m bench.fetch` first."
            )
        print(f"[index] {name} -> {checkout}")
        index_repo(checkout)


def main(argv: list[str] | None = None) -> int:
    try:
        index_all()
    except BenchError as exc:
        print(f"[index] ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
