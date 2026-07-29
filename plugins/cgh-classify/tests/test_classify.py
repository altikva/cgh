# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: cgh-classify tests: the naive Bayes model separates two
#              vocabularies and persists, labels beat predictions, the
#              safety asymmetry (predicted-public never clears the strict
#              gate, human-public does), and the uncertain review window.

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("cgh_classify")

from cgh_classify.labels import load_labels, remove_label, set_label  # noqa: E402
from cgh_classify.model import NaiveBayesModel, model_path  # noqa: E402
from cgh_classify.scanner import ClassifyScanner  # noqa: E402
from codegraph.state import findings as store  # noqa: E402

CONFIDENTIAL_DOCS = [
    "salary payroll bank iban employee compensation bonus confidential",
    "payroll salary bonus employee iban bank account compensation",
    "confidential salary employee payroll compensation review bank",
]
PUBLIC_DOCS = [
    "readme installation guide quickstart tutorial documentation example",
    "public api documentation tutorial usage example quickstart install",
    "guide install usage documentation example tutorial public readme",
]


def _trained_model() -> NaiveBayesModel:
    docs = [(d, True) for d in CONFIDENTIAL_DOCS] + [(d, False) for d in PUBLIC_DOCS]
    return NaiveBayesModel.train(docs)


@pytest.fixture(autouse=True)
def clean_store():
    store.reset_for_tests()
    yield
    store.reset_for_tests()


@pytest.fixture
def repo(tmp_path):
    (tmp_path / ".codegraph").mkdir()
    return tmp_path


class TestModel:
    def test_learns_the_separation(self):
        model = _trained_model()
        assert model.predict("employee payroll iban salary details") > 0.7
        assert model.predict("installation tutorial for the public api") < 0.3

    def test_single_class_refuses_to_train(self):
        with pytest.raises(ValueError):
            NaiveBayesModel.train([(d, True) for d in CONFIDENTIAL_DOCS])

    def test_persistence_roundtrip(self, repo):
        model = _trained_model()
        model.save(model_path(repo))
        loaded = NaiveBayesModel.load(model_path(repo))
        assert loaded is not None
        assert loaded.trained_on == model.trained_on
        text = "salary payroll bank"
        assert abs(loaded.predict(text) - model.predict(text)) < 1e-9

    def test_corrupt_model_loads_as_none(self, repo):
        model_path(repo).write_text("not json", encoding="utf-8")
        assert NaiveBayesModel.load(model_path(repo)) is None


class TestLabels:
    def test_set_and_remove(self, repo):
        f = repo / "a.py"
        f.write_text("x")
        set_label(repo, f, True)
        assert load_labels(repo)[str(f.resolve())] is True
        set_label(repo, f, False)
        assert load_labels(repo)[str(f.resolve())] is False
        assert remove_label(repo, f) is True
        assert load_labels(repo) == {}
        assert remove_label(repo, f) is False


class TestScannerSemantics:
    def test_human_label_wins_over_model(self, repo):
        _trained_model().save(model_path(repo))
        f = repo / "payroll.txt"
        f.write_text(CONFIDENTIAL_DOCS[0])
        set_label(repo, f, False)  # human says public

        found = ClassifyScanner({}, repo).scan(f, f.read_text(), None)
        assert [(x.key, x.value) for x in found] == [("confidential", "false")]

    def test_prediction_blocks_but_never_clears(self, repo):
        _trained_model().save(model_path(repo))
        scanner = ClassifyScanner({}, repo)

        conf = scanner.scan(
            Path("/r/pay.txt"), "employee payroll iban salary bank bonus", None
        )
        assert any(
            f.key == "confidential" and f.value == "true" and f.severity == "block"
            for f in conf
        )

        pub = scanner.scan(
            Path("/r/doc.txt"), "installation tutorial documentation example", None
        )
        keys = {f.key for f in pub}
        assert "confidential" not in keys
        assert "confidential.predicted" in keys

    def test_uncertain_window(self, repo):
        _trained_model().save(model_path(repo))
        scanner = ClassifyScanner(
            {"uncertain_low": 0.0, "uncertain_high": 1.0, "threshold": 1.1}, repo
        )
        found = scanner.scan(Path("/r/any.txt"), "salary tutorial", None)
        assert [f.key for f in found] == ["confidential.uncertain"]

    def test_no_model_no_label_no_findings(self, repo):
        assert ClassifyScanner({}, repo).scan(Path("/r/a.py"), "text", None) == []


class TestGateInterplay:
    """The safety asymmetry, checked against the real gate from
    cgh-summarize when it is installed alongside."""

    def test_predicted_public_does_not_clear_strict_gate(self, repo):
        gate = pytest.importorskip("cgh_summarize.gate")
        (repo / ".codegraph" / "config.toml").write_text(
            '[codegraph]\nmode = "secure"\n', encoding="utf-8"
        )
        _trained_model().save(model_path(repo))
        scanner = ClassifyScanner({}, repo)

        # Model says public: strict gate still refuses.
        pub = scanner.scan(Path("/r/doc.txt"), PUBLIC_DOCS[0], None)
        store.record_findings(repo, "/r/doc.txt", scanner.name, pub)
        assert not gate.cloud_allowed(repo, "/r/doc.txt", {})[0]

        # Human says public: strict gate clears.
        f = repo / "doc.txt"
        f.write_text(PUBLIC_DOCS[0])
        set_label(repo, f, False)
        human = scanner.scan(f, PUBLIC_DOCS[0], None)
        store.record_findings(repo, str(f), scanner.name, human)
        assert gate.cloud_allowed(repo, str(f), {})[0]
