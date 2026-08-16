# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-16
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: `cgh status` shows when the index was last scanned. The
#              scan_meta indexed_at timestamp already rode in the payload;
#              here we cover the display helper that turns it into a local
#              wall-clock stamp plus a relative age, including the missing
#              and unparseable cases (it must degrade to "" so the status
#              row renders without it).

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from codegraph.cli.commands_monitor import _format_indexed_at


def test_missing_or_bad_timestamp_is_empty():
    assert _format_indexed_at(None) == ""
    assert _format_indexed_at("") == ""
    assert _format_indexed_at("not-a-date") == ""


def test_relative_age_buckets():
    now = datetime.now(UTC)

    def at(**kw):
        return _format_indexed_at((now - timedelta(**kw)).isoformat(timespec="seconds"))

    assert at(seconds=20).endswith("just now")
    assert at(minutes=5).endswith("5m ago")
    assert at(hours=3).endswith("3h ago")
    assert at(days=2).endswith("2d ago")


def test_stamp_and_age_shape():
    iso = (datetime.now(UTC) - timedelta(hours=1)).isoformat(timespec="seconds")
    out = _format_indexed_at(iso)
    # "YYYY-MM-DD HH:MM · 1h ago"
    stamp, sep, age = out.partition(" · ")
    assert sep == " · " and age == "1h ago"
    datetime.strptime(stamp, "%Y-%m-%d %H:%M")  # parses, so the shape holds


def test_naive_timestamp_is_treated_as_utc():
    # A timestamp with no offset must not raise; it is read as UTC.
    assert _format_indexed_at("2026-08-15T10:00:00").count(" · ") == 1
