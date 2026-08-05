# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-05
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Intent-driven filtering: over the cap, keep the items
#              relevant to the focus instead of the arbitrary first N,
#              and never truncate silently (the note always explains).

from __future__ import annotations

from codegraph.server._focus import focus_filter


def _items():
    services = [{"n": f"svc{i}", "role": "service"} for i in range(10)]
    routers = [{"n": "api_a", "role": "router"}, {"n": "api_b", "role": "router"}]
    return services + routers  # routers last on purpose


def _tx(r):
    return f"{r['n']} {r['role']}"


def test_under_cap_returns_all_no_note():
    kept, note = focus_filter(_items(), "", _tx, 100)
    assert len(kept) == 12 and note is None


def test_over_cap_no_focus_notes_the_drop():
    kept, note = focus_filter(_items(), "", _tx, 5)
    assert [k["n"] for k in kept] == [f"svc{i}" for i in range(5)]
    assert "showing 5 of 12" in note and "focus=" in note


def test_focus_keeps_relevant_items_beyond_the_cap():
    """The routers sit last in raw order but win the cap under focus."""
    kept, note = focus_filter(_items(), "router", _tx, 5)
    assert {k["n"] for k in kept} == {"api_a", "api_b"}
    assert "matching focus='router'" in note


def test_multi_term_focus_ranks_by_hits():
    items = [
        {"n": "a", "role": "service", "layer": "core"},
        {"n": "b", "role": "router", "layer": "web"},
        {"n": "c", "role": "router", "layer": "core"},
    ]
    tx = lambda r: f"{r['role']} {r['layer']}"  # noqa: E731
    kept, _ = focus_filter(items, "router core", tx, 1)
    assert kept[0]["n"] == "c"  # two hits beats one


def test_focus_matching_nothing_falls_back_with_a_clear_note():
    kept, note = focus_filter(_items(), "zzz", _tx, 3)
    assert len(kept) == 3
    assert "matched none" in note
