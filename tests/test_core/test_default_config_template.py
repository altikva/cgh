# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The config.toml written by cgh init is the reference the
#              user edits: it must stay valid TOML, keep being valid
#              when the commented options are uncommented, and mention
#              every section the loader actually reads.

from __future__ import annotations

import re
import tomllib

from codegraph.core.config import generate_default_config


def test_template_is_valid_toml():
    data = tomllib.loads(generate_default_config())
    assert set(data) >= {"codegraph", "parsers", "mcp", "plugins", "paths", "roles"}
    assert data["codegraph"]["max_file_size_kb"] == 500
    assert data["mcp"]["auto_watch"] is True


def test_template_mentions_every_optional_surface():
    text = generate_default_config()
    for needle in (
        "# mode =",
        "# precise_calls =",
        "# subrepos =",
        "# federate_auto_up =",
        "# log_max_mb =",
        "# [plugin.summarize]",
        "# [plugin.classify]",
        "# backend =",
    ):
        assert needle in text, f"template lost the {needle!r} guidance"


def test_uncommenting_every_option_still_parses():
    """Uncomment each `# key = value` and `# [section]` line the way a
    user would and check the result is still valid TOML with sane
    values. Guards against a stale example drifting into a syntax
    error nobody notices until a user hits it."""
    lines = []
    for line in generate_default_config().splitlines():
        if re.match(r"^# (\[[a-z.]+\]|[a-z_]+ = )", line):
            # Strip the marker plus any trailing explanation comment
            # that would otherwise merge into the value.
            uncommented = line[2:]
            lines.append(uncommented)
        else:
            lines.append(line)
    data = tomllib.loads("\n".join(lines))

    cg = data["codegraph"]
    assert cg["mode"] == "assist"
    assert cg["federate_auto_up"] is True
    assert cg["log_max_mb"] == 5
    summarize = data["plugin"]["summarize"]
    assert summarize["backend"] == "auto"
    assert summarize["min_kb"] == 4
    assert data["plugin"]["classify"]["threshold"] == 0.7
