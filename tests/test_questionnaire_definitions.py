"""Questionnaire Core PR #1 — definition loader, validation, and fail-closed
rejection of risk-bearing definitions.

No real/licensed questionnaire text is used anywhere here -- only the
synthetic fixture at tests/fixtures/synthetic_questionnaire.json (invented,
non-clinical items) and small ad-hoc risk-bearing definitions constructed
inline for the negative-control tests.
"""
import json
import pathlib

import pytest

import questionnaires as q

FIXTURE_PATH = pathlib.Path(__file__).parent / "fixtures" / "synthetic_questionnaire.json"


def _load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


# ── licensing / gitignore hygiene ───────────────────────────────────────────────
def test_private_questionnaires_dir_is_gitignored():
    gitignore = (pathlib.Path(__file__).resolve().parent.parent / ".gitignore").read_text(encoding="utf-8")
    assert "private_questionnaires/" in gitignore


def test_synthetic_questionnaire_contains_no_clinical_instrument_text():
    src = FIXTURE_PATH.read_text(encoding="utf-8").lower()
    # Fragments associated with well-known licensed instruments -- none of
    # these should ever appear in a fixture meant to be synthetic/invented.
    forbidden_fragments = (
        "phq-9", "phq9", "little interest or pleasure",
        "beck depression", "bdi-ii", "gad-7", "gad7",
        "feeling nervous, anxious",
    )
    for frag in forbidden_fragments:
        assert frag not in src


# ── validation: the good path ───────────────────────────────────────────────────
def test_synthetic_fixture_validates_successfully():
    d = _load_fixture()
    q._validate_definition(d)   # must not raise


def test_get_validated_definition_returns_fixture_when_present(tmp_path):
    (tmp_path / "demo.json").write_text(FIXTURE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    definition, error = q.get_validated_definition(tmp_path)
    assert error is None
    assert definition["id"] == "synthetic_demo_v1"


# ── not_configured vs invalid distinction ───────────────────────────────────────
def test_no_private_definitions_reports_not_configured(tmp_path):
    definition, error = q.get_validated_definition(tmp_path)   # empty dir
    assert definition is None
    assert error == "not_configured"


def test_missing_directory_reports_not_configured(tmp_path):
    missing = tmp_path / "does_not_exist"
    definition, error = q.get_validated_definition(missing)
    assert definition is None
    assert error == "not_configured"


# ── fail-closed: risk-bearing definitions ───────────────────────────────────────
def test_definition_with_contains_risk_items_true_refuses_to_start(tmp_path):
    d = _load_fixture()
    d["contains_risk_items"] = True
    (tmp_path / "risky.json").write_text(json.dumps(d), encoding="utf-8")
    definition, error = q.get_validated_definition(tmp_path)
    assert definition is None
    assert error == "invalid"


def test_definition_with_item_risk_flag_refuses_to_start(tmp_path):
    d = _load_fixture()
    d["items"][0]["risk_flag"] = True
    (tmp_path / "risky.json").write_text(json.dumps(d), encoding="utf-8")
    definition, error = q.get_validated_definition(tmp_path)
    assert definition is None
    assert error == "invalid"


def test_definition_with_option_risk_flag_refuses_to_start(tmp_path):
    d = _load_fixture()
    d["items"][0]["options"][0]["risk_flag"] = True
    (tmp_path / "risky.json").write_text(json.dumps(d), encoding="utf-8")
    definition, error = q.get_validated_definition(tmp_path)
    assert definition is None
    assert error == "invalid"


def test_invalid_definition_missing_required_fields_rejected(tmp_path):
    (tmp_path / "broken.json").write_text(json.dumps({"id": "x"}), encoding="utf-8")
    definition, error = q.get_validated_definition(tmp_path)
    assert definition is None
    assert error == "invalid"


def test_definition_with_empty_items_rejected(tmp_path):
    d = _load_fixture()
    d["items"] = []
    (tmp_path / "empty.json").write_text(json.dumps(d), encoding="utf-8")
    definition, error = q.get_validated_definition(tmp_path)
    assert definition is None
    assert error == "invalid"


def test_option_without_id_or_value_rejected(tmp_path):
    d = _load_fixture()
    del d["items"][0]["options"][0]["value"]
    (tmp_path / "broken_option.json").write_text(json.dumps(d), encoding="utf-8")
    definition, error = q.get_validated_definition(tmp_path)
    assert definition is None
    assert error == "invalid"


# ── fail-closed: malformed / multiple files ──────────────────────────────────────
def test_malformed_private_definition_reports_invalid(tmp_path):
    (tmp_path / "broken.json").write_text("{not valid json", encoding="utf-8")
    definition, error = q.get_validated_definition(tmp_path)
    assert definition is None
    assert error == "invalid"


def test_multiple_private_definitions_report_invalid(tmp_path):
    # Two DIFFERENT, otherwise-valid definitions -- proves the loader refuses
    # outright on ambiguity rather than picking one (e.g. alphabetically
    # first) and silently ignoring the other.
    d1 = _load_fixture()
    d1["id"] = "demo_one"
    d2 = _load_fixture()
    d2["id"] = "demo_two"
    (tmp_path / "a_first.json").write_text(json.dumps(d1), encoding="utf-8")
    (tmp_path / "b_second.json").write_text(json.dumps(d2), encoding="utf-8")

    definition, error = q.get_validated_definition(tmp_path)

    assert definition is None
    assert error == "invalid"


def test_valid_plus_malformed_private_definition_reports_invalid(tmp_path):
    # One valid file + one malformed file present together -- still "invalid"
    # as a WHOLE (two files present at all is the ambiguity condition; the
    # loader must not attempt to salvage the valid one).
    (tmp_path / "a_valid.json").write_text(FIXTURE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "b_broken.json").write_text("{not valid json", encoding="utf-8")
    definition, error = q.get_validated_definition(tmp_path)
    assert definition is None
    assert error == "invalid"


# ── strengthened schema validation ───────────────────────────────────────────────
def test_missing_item_text_rejected(tmp_path):
    d = _load_fixture()
    del d["items"][0]["text"]
    (tmp_path / "broken.json").write_text(json.dumps(d), encoding="utf-8")
    definition, error = q.get_validated_definition(tmp_path)
    assert definition is None
    assert error == "invalid"


def test_missing_option_label_rejected(tmp_path):
    d = _load_fixture()
    del d["items"][0]["options"][0]["label"]
    (tmp_path / "broken.json").write_text(json.dumps(d), encoding="utf-8")
    definition, error = q.get_validated_definition(tmp_path)
    assert definition is None
    assert error == "invalid"


def test_option_id_too_long_rejected(tmp_path):
    d = _load_fixture()
    d["items"][0]["options"][0]["id"] = "x" * (q.MAX_ANSWER_ID_LEN + 1)
    (tmp_path / "broken.json").write_text(json.dumps(d), encoding="utf-8")
    definition, error = q.get_validated_definition(tmp_path)
    assert definition is None
    assert error == "invalid"


def test_option_id_with_colon_rejected(tmp_path):
    d = _load_fixture()
    d["items"][0]["options"][0]["id"] = "bad:id"
    (tmp_path / "broken.json").write_text(json.dumps(d), encoding="utf-8")
    definition, error = q.get_validated_definition(tmp_path)
    assert definition is None
    assert error == "invalid"


def test_option_id_with_space_rejected(tmp_path):
    d = _load_fixture()
    d["items"][0]["options"][0]["id"] = "bad id"
    (tmp_path / "broken.json").write_text(json.dumps(d), encoding="utf-8")
    definition, error = q.get_validated_definition(tmp_path)
    assert definition is None
    assert error == "invalid"


# ── helper functions ─────────────────────────────────────────────────────────
def test_get_item_by_index():
    d = _load_fixture()
    assert q.get_item(d, 0)["id"] == "energy"
    assert q.get_item(d, 1)["id"] == "focus"
    assert q.get_item(d, 99) is None


def test_find_option_by_id():
    d = _load_fixture()
    item = q.get_item(d, 0)
    assert q.find_option(item, "mid")["value"] == "2"
    assert q.find_option(item, "does_not_exist") is None
