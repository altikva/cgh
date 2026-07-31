#!/usr/bin/env python3
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-04
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: no-ai-tells guard. Fails when an em dash, en dash, or horizontal
#              bar appears in shipped prose: the codegraph package plus the
#              top-level README, CHANGELOG, and docs. Tests and dotfiles are
#              exempt. Called from CI and from the pre-commit hook, so the
#              logic stays in one place and runs the same on Linux and macOS.

from __future__ import annotations

import pathlib
import re
import sys

# U+2014 em dash, U+2013 en dash, U+2015 horizontal bar. Written as escapes
# so this guard file carries no literal dash of its own.
BANNED = re.compile("[\\u2014\\u2013\\u2015]")

ROOT_DIRS = ["codegraph", "docs"]
ROOT_FILES = ["README.md", "CHANGELOG.md"]
TEXT_SUFFIXES = {".py", ".md", ".txt", ".toml"}


def iter_files() -> "list[pathlib.Path]":
    seen: list[pathlib.Path] = []
    for name in ROOT_FILES:
        p = pathlib.Path(name)
        if p.is_file():
            seen.append(p)
    for root in ROOT_DIRS:
        base = pathlib.Path(root)
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if p.is_file() and p.suffix in TEXT_SUFFIXES:
                seen.append(p)
    return seen


def main() -> int:
    offenders: list[str] = []
    for path in iter_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if BANNED.search(line):
                offenders.append(f"{path}:{lineno}: {line.strip()}")

    if offenders:
        print("no-ai-tells: em, en, or horizontal-bar dash found in shipped prose.")
        print("Replace it with a comma, colon, period, or hyphen.\n")
        print("\n".join(offenders))
        return 1

    print("no-ai-tells: clean, no em or en dashes in shipped prose.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
