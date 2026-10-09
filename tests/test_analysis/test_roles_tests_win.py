# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-09
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: A test file keeps the test role whatever directories it sits
#              in. tests/unit/handlers/test_x.py used to be classed a handler
#              from its handlers/ directory, so impact counted it as impacted
#              code and left it out of the tests to run.

from __future__ import annotations

import pytest

from codegraph.analysis.roles import classify


@pytest.mark.parametrize(
    "rel",
    [
        "tests/unit/handlers/test_reconciliation_handler.py",
        "tests/services/helpers.py",
        "app/test/managers/fixtures.py",
        "app/handlers/payment_test.py",
        "app/services/test_payment.py",
        "web/components/__tests__/Button.ts",
        "web/components/Button.spec.ts",
        "web/composables/useCart.test.ts",
        "pkg/handlers/router_test.go",
    ],
)
def test_test_files_win_over_directory_roles(tmp_path, rel):
    assert classify(tmp_path / rel, tmp_path) == ("test", "test")


@pytest.mark.parametrize(
    ("rel", "role"),
    [
        ("app/handlers/reconciliation_handler.py", "handler"),
        ("app/services/testing_service.py", "service"),
        ("app/tests_support/models/x.py", "model"),
        ("app/routers/donations.py", "router"),
    ],
)
def test_non_test_files_keep_their_role(tmp_path, rel, role):
    assert classify(tmp_path / rel, tmp_path)[0] == role


def test_a_repo_under_a_tests_directory_is_not_all_tests(tmp_path):
    root = tmp_path / "tests" / "repo"
    assert classify(root / "app/handlers/x_handler.py", root)[0] == "handler"


def test_custom_rules_still_win(tmp_path):
    from codegraph.analysis.roles import reset_rules_cache

    (tmp_path / ".codegraph").mkdir()
    (tmp_path / ".codegraph" / "config.toml").write_text(
        '[roles]\n"/tests/fixtures/" = "fixture:test"\n', encoding="utf-8"
    )
    reset_rules_cache()
    try:
        assert classify(tmp_path / "tests/fixtures/a.py", tmp_path)[0] == "fixture"
    finally:
        reset_rules_cache()
