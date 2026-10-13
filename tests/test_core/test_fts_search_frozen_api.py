"""fts_search is a frozen internal API.

An external script owned by another team imports it directly with
``from codegraph.core.fts import fts_search`` and passes these parameters
by name. Renaming, removing or reordering one breaks that script, so a
change here needs that team's sign-off first.
"""

from __future__ import annotations

import inspect


def test_fts_search_import_path_and_signature():
    from codegraph.core.fts import fts_search

    params = list(inspect.signature(fts_search).parameters)
    assert params == ["conn", "query", "limit", "kind_filter", "prose"]


def test_fts_search_defaults():
    from codegraph.core.fts import fts_search

    params = inspect.signature(fts_search).parameters
    assert params["limit"].default == 15
    assert params["kind_filter"].default is None
    assert params["prose"].default is False
