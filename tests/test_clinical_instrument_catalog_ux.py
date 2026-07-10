"""Manifest-driven professional questionnaire catalog UX.

Covers: catalog generated from the governance manifest (no hardcoded
instrument list in bot.py), honest availability states, the availability
double-gate for FUTURE activation (never fires in this PR), JAPS/STAS hidden,
the "Для специалиста" -> "Отчёт для консультации" reframe, gate-order
preservation, RU+EN copy, and the guarantee that catalog info paths create no
session / write no DB rows / make no LLM call.

Handler-level tests run against the REAL bot.py handlers and a REAL tmp sqlite
DB, same pattern as tests/test_questionnaire_command_flow.py. No real clinical
instrument is activated and no real question/answer/scoring content exists
anywhere here -- manifest entries render as INFO screens only, and the one
positive double-gate test uses a fully synthetic manifest item + a fake
registry, never real instrument content.
"""
import asyncio
import pathlib
import types

import pytest

import bot
import database
import questionnaires
import questionnaire_ux
import clinical_instrument_catalog as cat
import access_control as ac

REPO_ROOT = pathlib.Path(__file__).parent.parent
MANIFEST_PATH = REPO_ROOT / "clinical_instruments_manifest.json"
FIXTURE_DIR = pathlib.Path(__file__).parent / "fixtures" / "registry"


def _document():
    return cat.load_instrument_manifest(MANIFEST_PATH)


class FakeUser:
    def __init__(self, uid, username="user"):
        self.id = uid
        self.username = username


class FakeMessage:
    def __init__(self, user, text=""):
        self.from_user = user
        self.text = text
        self.chat = types.SimpleNamespace(id=user.id)
        self.answers = []

    async def answer(self, text, **kw):
        self.answers.append((text, kw))

    async def edit_reply_markup(self, **kw):
        pass


class FakeCallback:
    def __init__(self, user, message, data=""):
        self.from_user = user
        self.message = message
        self.data = data
        self.answered = 0

    async def answer(self, *a, **kw):
        self.answered += 1


def _async(value=None):
    async def _f(*a, **kw):
        return value
    return _f


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB", str(tmp_path / "t.db"))
    asyncio.run(database.init_db())
    return database


@pytest.fixture(autouse=True)
def _common(monkeypatch, tmp_db):
    monkeypatch.setattr(bot, "get_user_language", _async("ru"))
    monkeypatch.setattr(bot, "get_active_crisis", _async(None))
    monkeypatch.setattr(bot, "log_crisis_delivery", _async(None))
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "personal_use")
    monkeypatch.setattr(ac, "OWNER_USER_ID", 1)
    monkeypatch.setattr(ac, "CLINICIAN_TESTER_IDS", set())
    monkeypatch.setattr(ac, "CLINICIAN_REVIEWER_IDS", set())
    monkeypatch.setattr(ac, "TESTER_REVIEWER_MAP", {})
    monkeypatch.setattr(bot, "CallbackQuery", FakeCallback)
    monkeypatch.setattr(bot, "_load_registry_fresh",
                        lambda: questionnaires.load_registry(FIXTURE_DIR))


def _sessions_for(uid):
    import sqlite3
    con = sqlite3.connect(database.DB)
    rows = con.execute(
        "SELECT id, questionnaire_id, questionnaire_version, status, current_index "
        "FROM questionnaire_sessions WHERE user_id=?", (uid,)).fetchall()
    con.close()
    return rows


def _buttons(kw):
    kb = kw["reply_markup"]
    return [(btn.text, btn.callback_data) for row in kb.inline_keyboard for btn in row]


def _press_info(uid, instrument_id):
    user = FakeUser(uid)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data=f"q:i:{instrument_id}")
    asyncio.run(bot.cb_questionnaire_info(cb))
    return msg


# ── manifest rendering ───────────────────────────────────────────────────────
def test_catalog_is_generated_from_manifest():
    instruments = cat.public_catalog_instruments(_document())
    ids = {ci.instrument_id for ci in instruments}
    # Exactly the 6 public-visible governance instruments, straight from the
    # manifest -- not a hand-maintained list.
    assert ids == {"bdi_ii", "hdrs", "zung_sas", "zung_sds", "epds", "dass"}


def test_no_duplicate_manual_instrument_list_in_bot():
    src = (REPO_ROOT / "bot.py").read_text(encoding="utf-8")
    # The catalog must come from the service layer; no hardcoded instrument
    # display names or an inline BDI/HDRS/DASS list may live in bot.py.
    for name in ("Шкала депрессии Бека", "Шкала депрессии Гамильтона",
                 "Шкалы депрессии, тревоги и стресса",
                 "Beck Depression Inventory", "Hamilton Depression Rating Scale"):
        assert name not in src, f"hardcoded instrument name {name!r} in bot.py"


def test_hidden_identity_incomplete_instruments_not_rendered():
    ids = {ci.instrument_id for ci in cat.public_catalog_instruments(_document())}
    assert "japs" not in ids   # identity family-only, occupational, hidden
    assert "stas" not in ids   # identity conflict, hidden


def test_bdi_rendered_as_requires_license():
    ci = cat.get_catalog_instrument(_document(), "bdi_ii")
    assert ci is not None
    assert ci.availability == "requires_license"
    assert ci.category_id == "depression_mood_energy"


def test_hdrs_rendered_as_clinician_rated_information_only():
    ci = cat.get_catalog_instrument(_document(), "hdrs")
    assert ci.administration_mode == "clinician_rated"
    assert ci.availability == "information_only"
    txt = questionnaire_ux.instrument_info_text(ci, "ru")
    assert "специалист" in txt.lower()
    assert "недоступно" in txt


def test_zung_sas_rendered_under_anxiety():
    ci = cat.get_catalog_instrument(_document(), "zung_sas")
    assert ci.category_id == "anxiety"
    assert ci in cat.catalog_instruments_by_category(_document(), "anxiety")


def test_zung_sds_rendered_under_depression():
    ci = cat.get_catalog_instrument(_document(), "zung_sds")
    assert ci.category_id == "depression_mood_energy"


def test_epds_rendered_as_specialized_perinatal():
    ci = cat.get_catalog_instrument(_document(), "epds")
    assert ci.category_id == "specialized"
    assert ci.population_note_ru is not None
    assert ci in cat.catalog_instruments_by_category(_document(), "specialized")


def test_dass_version_incomplete_not_available():
    ci = cat.get_catalog_instrument(_document(), "dass")
    assert ci.availability != "available"
    assert ci.availability == "version_under_review"
    assert ci.category_id == "stress"


# ── availability double-gate (future activation, never fires now) ────────────
class _FakeRegistry:
    def __init__(self, startable):
        self._startable = set(startable)

    def can_start(self, qid):
        return qid in self._startable

    def get(self, qid):
        return {"id": qid, "version": "v1"}


def _ready_item(**over):
    """Fully synthetic, fully-cleared manifest entry (never a real
    instrument). Passes can_activate_instrument -- used ONLY to exercise the
    dormant activation path."""
    item = {
        "instrument_id": "synthetic_ready",
        "display_name_ru": "Синтетическая методика",
        "display_name_en": "Synthetic Instrument",
        "catalog_category_id": "anxiety",
        "abbreviation": "SYN",
        "version": "v1",
        "identity_status": "verified",
        "domain": "anxiety",
        "administration_mode": "self_report",
        "population": ["adult"],
        "activation_status": "ready",
        "public_catalog_visible": True,
        "risk_item_metadata_status": "verified",
        "evidence": [{"kind": "primary_source", "title": "x", "url": None,
                      "accessed_at": "2026-07-10", "supports": ["identity"]}],
        "rights": {k: {"status": "allowed", "evidence": [
            {"kind": "license_terms", "title": "x", "url": None,
             "accessed_at": "2026-07-10", "supports": [k]}]}
            for k in ("digital_reproduction", "commercial_use", "translation_use")},
        "blockers": [],
    }
    item.update(over)
    return item


def test_ready_manifest_without_definition_does_not_start():
    item = _ready_item()
    assert cat.can_activate_instrument(item) is True   # manifest side is clear
    # ...but no matching registry definition -> must NOT start.
    assert cat.catalog_activation_ready(item, _FakeRegistry(set()), "synthetic_ready") is False


def test_valid_definition_without_ready_manifest_does_not_start():
    blocked = _ready_item(activation_status="blocked")
    assert cat.can_activate_instrument(blocked) is False
    # Registry would happily start it, but the manifest gate is closed.
    assert cat.catalog_activation_ready(
        blocked, _FakeRegistry({"synthetic_ready"}), "synthetic_ready") is False


def test_start_requires_manifest_and_definition_both_ready():
    item = _ready_item()
    assert cat.catalog_activation_ready(
        item, _FakeRegistry({"synthetic_ready"}), "synthetic_ready") is True


# ── blocked / info-only instruments create no session ────────────────────────
def test_blocked_instrument_does_not_create_session():
    msg = _press_info(1, "bdi_ii")
    assert _sessions_for(1) == []
    assert questionnaire_ux.instrument_info_text(
        cat.get_catalog_instrument(_document(), "bdi_ii"), "ru").split("\n")[0] \
        in msg.answers[-1][0]


def test_information_only_instrument_does_not_create_session():
    _press_info(1, "hdrs")
    assert _sessions_for(1) == []


def test_metadata_only_instrument_does_not_create_session():
    # zung_sds is version_under_review (identity/version incomplete) -- pure
    # metadata, never startable.
    _press_info(1, "zung_sds")
    assert _sessions_for(1) == []


# ── UX ───────────────────────────────────────────────────────────────────────
def test_root_uses_professional_category_names():
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cmd_questionnaire(msg))
    text, kw = msg.answers[0]
    datas = [d for _, d in _buttons(kw)]
    for cid in ("depression_mood_energy", "anxiety", "stress", "specialized",
                "self_observation", "consultation_report"):
        assert f"q:c:{cid}" in datas
    # Old low-trust symptom labels are gone from the root.
    assert "Сон / стресс" not in text
    assert "Для специалиста" not in text


def test_catalog_never_shows_bare_empty_category_dead_end(monkeypatch):
    # Force an empty manifest so a manifest category is genuinely empty.
    monkeypatch.setattr(bot, "_load_catalog_document",
                        lambda: {"schema_version": 2, "instruments": []})
    user = FakeUser(1)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data="q:c:anxiety")
    # catalog_instruments_by_category on an empty doc -> no instruments; the
    # handler must still attach navigation, never a dead end.
    asyncio.run(bot.cb_questionnaire_category(cb))
    text, kw = msg.answers[-1]
    assert "нет доступных опросников" not in text   # old dead-end string gone
    datas = [d for _, d in _buttons(kw)]
    assert "q:l" in datas and "menu:back" in datas


def test_catalog_buttons_one_per_row():
    user = FakeUser(1)
    msg = FakeMessage(user)
    asyncio.run(bot.cmd_questionnaire(msg))
    for row in msg.answers[0][1]["reply_markup"].inline_keyboard:
        assert len(row) == 1
    # category (manifest) screen
    cb = FakeCallback(user, msg, data="q:c:depression_mood_energy")
    asyncio.run(bot.cb_questionnaire_category(cb))
    for row in msg.answers[-1][1]["reply_markup"].inline_keyboard:
        assert len(row) == 1
    # info screen
    msg2 = _press_info(1, "bdi_ii")
    for row in msg2.answers[-1][1]["reply_markup"].inline_keyboard:
        assert len(row) == 1


def test_information_screen_has_non_diagnostic_disclaimer():
    for iid in ("bdi_ii", "hdrs", "epds", "dass", "zung_sas", "zung_sds"):
        ci = cat.get_catalog_instrument(_document(), iid)
        for lang in ("ru", "en"):
            txt = questionnaire_ux.instrument_info_text(ci, lang)
            assert ("не диагноз" in txt) or ("not a diagnosis" in txt)
            # never a score / cutoff / diagnosis claim
            for forbidden in ("баллов", "cutoff", "диагноз:", "норма", "percentile"):
                assert forbidden not in txt


def test_report_label_is_consultation_report():
    datas_root = questionnaire_ux.CATALOG_CATEGORIES
    labels_ru = [ru for _, ru, _ in datas_root]
    assert "Отчёт для консультации" in labels_ru
    assert "Для специалиста" not in labels_ru
    txt = questionnaire_ux.consultation_report_text("ru")
    assert "Отчёт для консультации" in txt


def test_report_text_says_no_automatic_third_party_send():
    txt = questionnaire_ux.consultation_report_text("ru")
    assert "Ты сам решаешь, кому показать отчёт." in txt
    assert "никому не отправляет его автоматически" in txt


def test_english_copy_exists():
    assert "Screening scales" in questionnaire_ux.list_text("en")
    assert "Consultation report" in questionnaire_ux.consultation_report_text("en")
    ci = cat.get_catalog_instrument(_document(), "hdrs")
    assert "clinician-rated" in questionnaire_ux.instrument_info_text(ci, "en")


# ── gates ──────────────────────────────────────────────────────────────────
def test_catalog_root_crisis_gate_first(monkeypatch):
    monkeypatch.setattr(bot, "get_active_crisis", _async((7, 0, "ru")))
    user = FakeUser(1)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data="q:l")
    asyncio.run(bot.cb_questionnaire_list(cb))
    from crisis_protocol import get_hotline
    assert get_hotline("ru")["primary"] in msg.answers[-1][0]
    assert "Скрининговые шкалы" not in msg.answers[-1][0]


def test_catalog_category_crisis_gate_first(monkeypatch):
    monkeypatch.setattr(bot, "get_active_crisis", _async((7, 0, "ru")))
    user = FakeUser(1)
    msg = FakeMessage(user)
    cb = FakeCallback(user, msg, data="q:c:anxiety")
    asyncio.run(bot.cb_questionnaire_category(cb))
    from crisis_protocol import get_hotline
    assert get_hotline("ru")["primary"] in msg.answers[-1][0]


def test_catalog_info_crisis_gate_first(monkeypatch):
    monkeypatch.setattr(bot, "get_active_crisis", _async((7, 0, "ru")))
    msg = _press_info(1, "bdi_ii")
    from crisis_protocol import get_hotline
    assert get_hotline("ru")["primary"] in msg.answers[-1][0]
    # Instrument info never rendered when a crisis is active.
    assert "требуется лицензированная версия" not in msg.answers[-1][0]


def test_catalog_requires_product_access():
    # UNKNOWN user under personal_use (OWNER_USER_ID=1) -> blocked, no info.
    msg = _press_info(424242, "bdi_ii")
    assert msg.answers
    assert "требуется лицензированная версия" not in msg.answers[0][0]
    assert _sessions_for(424242) == []


def test_catalog_info_has_no_llm_call(monkeypatch):
    def _boom(*a, **kw):
        raise AssertionError("catalog info path must never call the LLM/trace builder")
    monkeypatch.setattr(bot, "traced_response_builder", _boom)
    monkeypatch.setattr(bot, "persist_influence_trace", _boom)
    msg = _press_info(1, "bdi_ii")
    assert "недоступно" in msg.answers[-1][0]


def test_catalog_info_has_no_db_write():
    uid = 1
    _press_info(uid, "hdrs")
    assert _sessions_for(uid) == []
    data = asyncio.run(database.export_all_personal_data(uid))
    assert data["questionnaire_responses"] == []
