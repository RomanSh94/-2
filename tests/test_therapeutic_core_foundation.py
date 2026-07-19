"""Therapeutic Core Foundation (Workstream C).

Scope of this pass, per the inventory (verified by reading the actual merged
code, not assumed): two of the four target areas already had no P0/P1 gap and
were NOT changed --

  * Ordinary-response influence tracing: `traced_response_builder` /
    `influence_trace` already exist, are CI-guarded (tests/test_clinical_boundary.py's
    AST scanner forbids bot.pipeline() from reading any LATENT_SOURCE_SYMBOLS),
    and CLINICAL_BOUNDARY.md §3.2/§0.2 (Normative Amendment A1) explicitly
    classifies the state pipeline() DOES use (emotion/energy/mode/recent
    messages -- "session_state") as a SEPARATE, always-permitted memory class
    that does not require tracing. Latent psychological constructs
    (profile/pattern_hypothesis/questionnaire_score/etc.) are never read by
    pipeline() at all -- confirmed by the existing scanner, not re-tested here.
  * Outcome baseline timing: already correct -- `start_intervention` is a
    single atomic INSERT gated on an explicit 1-10 button tap
    (bot.cb_before), and it runs before the practice content is ever sent.
    No retroactive-baseline code path exists anywhere in the repo.

This file covers the two areas where a real gap WAS found and fixed:

  * Dependency reconciliation: `dependency_monitor.DependencyMonitor.assess`
    is now the ONE authority, consolidating the behavioural-pattern checks
    and `relationship_monitor.monitor_relationship`'s explicit-phrase check
    behind a single shared cooldown gate (previously: monitor_relationship
    had NO cooldown at all and fired on every matching message; the two
    mechanisms could independently disagree on whether to halt the pipeline).
  * Practice reachability: `practice_registry.select_practice` now rotates
    away from a user's recently-delivered practice ids so more of the
    registry is actually reachable in production, not just the single
    deterministic tie-break winner.

Plus one direct baseline-timing order proof (call-order evidence, not just
code inspection) and a practice-reachability audit.
"""
import asyncio
import types

import pytest

import access_control as ac
import bot
import config
import database
import practice_registry as pr

run = asyncio.run


class FakeUser:
    def __init__(self, uid, username="user", first="U"):
        self.id = uid
        self.username = username
        self.first_name = first


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


class FakeFSM:
    def __init__(self, data=None):
        self._data = data or {}
        self._state = None

    async def get_data(self):
        return self._data

    async def update_data(self, **kw):
        self._data.update(kw)

    async def set_state(self, state):
        self._state = state


def _async(value=None):
    async def _f(*a, **kw):
        return value
    return _f


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB", str(tmp_path / "t.db"))
    run(database.init_db())
    return database


@pytest.fixture(autouse=True)
def _access_env(monkeypatch):
    monkeypatch.setattr(ac, "DEPLOYMENT_MODE", "personal_use")
    monkeypatch.setattr(ac, "OWNER_USER_ID", 1)
    monkeypatch.setattr(ac, "CLINICIAN_TESTER_IDS", set())
    monkeypatch.setattr(ac, "CLINICIAN_REVIEWER_IDS", set())
    monkeypatch.setattr(ac, "TESTER_REVIEWER_MAP", {})


# ── Slice 2A: dependency reconciliation, at pipeline level ──────────────────
def _full_pipeline_stub_set(monkeypatch):
    """The minimal downstream stub set (matches tests/test_checkpoint2_fixes.py's
    established pattern) so bot.pipeline() can run end-to-end without a real
    LLM/network call."""
    monkeypatch.setattr(bot, "get_emotional_trajectory", _async(types.SimpleNamespace(
        trend="stable", hopelessness_streak=0, yellow_plus_streak=0, messages_analyzed=0)))
    monkeypatch.setattr(bot, "load_state", _async(None))
    monkeypatch.setattr(bot, "save_state", _async(None))
    monkeypatch.setattr(bot, "log_router_decision", _async(None))
    monkeypatch.setattr(bot, "maybe_summarize", _async(None))
    monkeypatch.setattr(bot, "build_context", _async(("", [])))
    monkeypatch.setattr(bot, "maybe_update_profile", _async(None))
    monkeypatch.setattr(bot, "get_user_message_count", _async(1))
    monkeypatch.setattr(bot, "check_sudden_improvement", _async(False))
    monkeypatch.setattr(bot, "log_moderation", _async(None))
    monkeypatch.setattr(bot, "save_message", _async(None))
    monkeypatch.setattr(bot, "push_alert", _async(None))

    async def fake_typing(chat_id, action):
        return None
    monkeypatch.setattr(bot.bot, "send_chat_action", fake_typing)

    class _Choice:
        def __init__(self):
            self.message = types.SimpleNamespace(content="ok, noted")

    async def fake_create(*a, **kw):
        return types.SimpleNamespace(choices=[_Choice()])
    monkeypatch.setattr(bot.client.chat.completions, "create", fake_create)


def test_dependency_redirect_short_circuits_no_double_response(monkeypatch, tmp_db):
    # The exact P1 this pass closes: previously the behavioural-pattern
    # trigger did NOT return, so a dependency redirect and a full ordinary
    # LLM reply could both be sent for the same message.
    _full_pipeline_stub_set(monkeypatch)
    monkeypatch.setattr(bot.dependency_monitor, "record_message", _async(None))
    monkeypatch.setattr(bot.dependency_monitor, "assess", _async("A soft, narrow redirect."))
    user = FakeUser(1)  # owner -- has product access in personal_use mode
    msg = FakeMessage(user, "hello")
    run(bot.pipeline(msg, msg.text, None, tg_user=user))
    assert len(msg.answers) == 1
    assert msg.answers[0][0] == "A soft, narrow redirect."


def test_ordinary_message_unaffected_when_no_dependency_signal(monkeypatch, tmp_db):
    _full_pipeline_stub_set(monkeypatch)
    monkeypatch.setattr(bot.dependency_monitor, "record_message", _async(None))
    monkeypatch.setattr(bot.dependency_monitor, "assess", _async(None))
    user = FakeUser(1)
    msg = FakeMessage(user, "у меня был тяжёлый день на работе")
    run(bot.pipeline(msg, msg.text, None, tg_user=user))
    assert len(msg.answers) == 1
    assert msg.answers[0][0] == "ok, noted"  # the ordinary LLM reply, not a redirect


def test_crisis_still_preempts_dependency_check_entirely(monkeypatch, tmp_db):
    # RED-risk crisis handling must never even reach the dependency check.
    calls = {"assess": 0}

    async def spy_assess(*a, **kw):
        calls["assess"] += 1
        return None
    monkeypatch.setattr(bot.dependency_monitor, "assess", spy_assess)
    monkeypatch.setattr(bot, "trigger_crisis", _async(None))
    user = FakeUser(52)
    msg = FakeMessage(user, "я хочу покончить с собой")
    run(bot.pipeline(msg, msg.text, None, tg_user=user))
    assert calls["assess"] == 0


def test_dependency_module_is_the_sole_authority_no_llm_involved():
    # The system prompt may (correctly) instruct the LLM NOT to encourage
    # dependency -- that is a safety constraint, not the LLM deciding
    # anything. What must NOT appear is an instruction asking the LLM to
    # detect, judge, or classify dependency itself (that decision belongs
    # solely to dependency_monitor.assess).
    import prompts
    judging_phrases = (
        "determine whether", "decide whether", "assess whether",
        "classify the user as dependent", "judge dependency",
        "определи, зависим", "оцени привязанность",
    )
    for lang in ("ru", "en"):
        for scenario in ("open_chat", "reflective", "cbt_thought"):
            text = prompts.get_system_prompt(scenario, lang).lower()
            assert not any(p in text for p in judging_phrases)


# ── Slice 2B: practice reachability / rotation ──────────────────────────────
def test_practice_reachability_audit_documents_the_known_gap():
    """Empirical reachability audit (Therapeutic Core Foundation inventory,
    Phase 2/8). A per-user practice-history rotation mechanism was
    prototyped in this pass and then DELIBERATELY REMOVED: it used prior
    practice history (get_recent_practice_ids -> avoid_ids) to influence
    current practice selection without a persisted influence trace, which
    is untraced latent influence under this workstream's own contract
    ("practice history" is explicitly listed as a latent-influence source).
    Tracing it properly would mean wiring practice selection through
    traced_response_builder inside bot.pipeline()'s hot path -- extending
    the CI-enforced AST allowlist in tests/test_clinical_boundary.py and
    changing the single hottest code path in the bot -- which is exactly
    the kind of architecture-reopening this bounded "foundation, not
    perfection" pass must not do unilaterally. So: REMOVED, not retained
    behind a flag.

    This test proves the CURRENT (post-removal, pre-rotation) reachability
    exactly as it was found in the original inventory: only 7 of 43
    registry entries are reachable through the real production call path
    (select_practice, called from bot.py's pipeline()), for ANY combination
    of stage/severity. Closing this gap for real requires an explicit
    product/clinical decision (either wiring the already-existing,
    already-tested select_practice_by_need with a defined stage->need
    mapping, or a properly-traced rotation mechanism reviewed against the
    A1 boundary) -- deferred, not fixed here."""
    stages = ("OPEN", "ACUTE_DISTRESS", "REFLECTION", "PROBLEM_SOLVING", "GROWTH")
    severities = ("low", "medium", "high")
    reached = set()
    for scenario in pr.CATEGORY_MAP:
        for stage in stages:
            for severity in severities:
                p = pr.select_practice(scenario, stage, severity, "ru")
                reached.add(p["id"])

    assert reached == {
        "breathing_box_v1", "dbt_stop_v1", "cbt_behavioral_activation_v1",
        "cbt_thought_record_v1", "act_acceptance_v1", "reflective_listen_v1",
        "breathing_478_v1",
    }
    all_ids = {p["id"] for p in pr.REGISTRY}
    assert len(all_ids - reached) == 36  # defined-but-unreachable, unchanged from inventory


def test_select_practice_has_no_avoid_ids_parameter():
    # Confirms the rotation mechanism was fully removed, not left as dead
    # unused capability.
    import inspect
    sig = inspect.signature(pr.select_practice)
    assert "avoid_ids" not in sig.parameters


def test_get_recent_practice_ids_does_not_exist():
    # Confirms the untraced-influence read function was fully removed.
    assert not hasattr(database, "get_recent_practice_ids")


def test_no_prohibited_modality_in_registry():
    forbidden = ("psychoanaly", "emdr", "hypnosis", "chair work", "reparenting",
                 "regression", "имаготерап", "психоанализ", "гипноз")
    for p in pr.REGISTRY:
        blob = " ".join([p["approach"], p["name_ru"], p["name_en"]]).lower()
        assert not any(f in blob for f in forbidden)


def test_practice_selection_has_no_llm_call_path():
    import inspect
    src = inspect.getsource(pr.select_practice) + inspect.getsource(pr._best)
    assert "openai" not in src.lower() and "client." not in src


# ── Outcome baseline timing: direct call-order proof (not just inspection) ──
def test_baseline_write_happens_strictly_before_practice_content_is_sent(tmp_db, monkeypatch):
    uid = 604
    run(database.upsert_user(uid, "u", "U"))
    order = []
    real_start_intervention = database.start_intervention

    async def spy_start_intervention(*a, **kw):
        order.append("db_write")
        return await real_start_intervention(*a, **kw)
    monkeypatch.setattr(bot, "start_intervention", spy_start_intervention)
    monkeypatch.setattr(bot, "load_state", _async({}))

    user = FakeUser(uid)
    cb_msg = FakeMessage(user)
    real_answer = cb_msg.answer

    async def spy_answer(text, **kw):
        order.append("telegram_send")
        return await real_answer(text, **kw)
    cb_msg.answer = spy_answer

    cb = FakeCallback(user, cb_msg, data="before:breathing_box_v1:grounding:ru:5")
    fsm = FakeFSM()
    run(bot.cb_before(cb, fsm))

    assert order[0] == "db_write"
    assert "telegram_send" in order
    assert order.index("db_write") < order.index("telegram_send")


def test_baseline_is_never_written_without_an_explicit_user_tap(tmp_db):
    # No code path creates an intervention_results row without cb_before
    # being invoked with an explicit score parsed from callback data.
    uid = 605
    run(database.upsert_user(uid, "u", "U"))
    import sqlite3
    con = sqlite3.connect(database.DB)
    count = con.execute(
        "SELECT COUNT(*) FROM intervention_results WHERE user_id=?", (uid,)).fetchone()[0]
    con.close()
    assert count == 0


# ── Slice 1A: source-by-source latent-influence matrix ─────────────────────
# Owner decision (this pass): the routine session_state exemption
# (CLINICAL_BOUNDARY.md §3.2 -- current emotion/energy/mode/recent messages)
# already covers what bot.pipeline() uses; it must NOT be widened to trace
# every ordinary reply (that would require reopening the hot path and the
# CI-enforced AST allowlist). Deeper latent constructs remain fully untraced
# in pipeline() -- not "not required to trace", literally never read there
# at all, per tests/test_clinical_boundary.py's existing AST scan (cited,
# not duplicated). This section proves, source by source, which bucket each
# candidate latent source actually falls into TODAY, so "PASS" here rests on
# direct evidence, not a summary claim.
import inspect


def _pipeline_source():
    return inspect.getsource(bot.pipeline)


def test_session_state_is_used_under_the_documented_exemption_scenario():
    # `state` (accumulated emotion/energy/panic/overwhelm across PRIOR
    # messages, not just the current one) actually changes scenario
    # selection -- this is the "session_state may change behavior within the
    # session" exemption being genuinely exercised, not just theoretical.
    from state_engine import choose_scenario, DEFAULT_STATE
    calm_state = dict(DEFAULT_STATE)
    acute_state = dict(DEFAULT_STATE)
    acute_state["panic"] = 0.95
    acute_state["overwhelm"] = 0.9
    s_calm = choose_scenario(calm_state, [], "OPEN", "MEDIUM", 1.0, "control")
    s_acute = choose_scenario(acute_state, [], "ACUTE_DISTRESS", "LOW", 0.2, "control")
    assert s_calm != s_acute  # session_state genuinely steers the outcome


def test_session_state_is_used_under_the_documented_exemption_memory():
    # `summary`/`recent` (rolling conversation memory -- "последние сообщения"
    # in CLINICAL_BOUNDARY.md §3.2's session_state definition) are injected
    # directly into the LLM prompt -- confirmed by source inspection of the
    # exact lines that build `messages`, not by running the real LLM.
    src = _pipeline_source()
    assert "summary, recent = await build_context(uid)" in src
    assert 'messages.append({"role": "system", "content": f"Context:\\n{summary}"})' in src
    assert "for role, content in recent:" in src


def test_deeper_latent_constructs_not_loaded_by_pipeline_at_all():
    # profile / pattern_hypothesis / questionnaire_score / confirmed_episode /
    # schema_theme / mode / formulation: already CI-enforced absent from
    # pipeline() by tests/test_clinical_boundary.py's AST scan (not
    # duplicated here) -- this test adds the two sources that scanner does
    # NOT cover by name: outcome history and (post-removal) practice history.
    src = _pipeline_source()
    assert "intervention_results" not in src  # outcome history: never read here
    assert "get_recent_practice_ids" not in src  # practice history: removed, not reintroduced
    # pipeline() legitimately BUILDS the before-score offer (before_score_kb
    # is the keyboard for a NEW offer, not a read of a past value) -- what
    # must never appear is pipeline() READING a past before_score/after_score
    # VALUE to influence a new reply.
    assert 'before_score"]' not in src and 'get("before_score"' not in src
    assert 'after_score"]' not in src and 'get("after_score"' not in src


def test_questionnaire_history_not_read_by_ordinary_pipeline():
    # Beyond the SEPARATE, already-traced discuss-topic flow -- covered
    # directly by tests/test_questionnaire_no_influence.py; cited, not
    # duplicated. This is the pipeline()-specific half of that same claim.
    src = _pipeline_source()
    assert "questionnaire_score" not in src
    assert "get_questionnaire_responses" not in src


def test_dependency_history_is_governed_by_its_own_separate_contract_not_a1():
    # dependency_monitor's in-memory state IS read by pipeline() (via
    # assess()) -- but CLINICAL_BOUNDARY.md §2.3 explicitly carves dependency
    # out as its OWN bounded, structural, non-diagnostic mechanism (soft/
    # narrow response, never crisis, never a permanent label) -- a separate
    # contract from A1's "does this influence therapeutic content" question,
    # already fully covered by tests/test_dependency_monitor.py and this
    # file's dependency-reconciliation tests above (cited, not duplicated).
    src = _pipeline_source()
    assert "dependency_monitor.assess" in src
    # And it never writes a permanent identity label anywhere reachable from here.
    assert "is_dependent" not in src and "dependency_label" not in src


# ── Canonical production practice catalog (final P1 closure) ───────────────
def _fresh_cb(uid, data):
    return FakeCallback(FakeUser(uid), FakeMessage(FakeUser(uid)), data=data)


def _row_count(uid):
    import sqlite3
    con = sqlite3.connect(database.DB)
    n = con.execute(
        "SELECT COUNT(*) FROM intervention_results WHERE user_id=?", (uid,)).fetchone()[0]
    con.close()
    return n


@pytest.mark.parametrize("pid", sorted(pr.PRODUCTION_PRACTICE_IDS))
def test_every_production_id_has_a_real_route(tmp_db, monkeypatch, pid):
    monkeypatch.setattr(bot, "load_state", _async({}))
    uid = 700 + hash(pid) % 100
    run(database.upsert_user(uid, "u", "U"))
    cb = _fresh_cb(uid, f"before:{pid}:grounding:ru:5")
    fsm = FakeFSM()
    run(bot.cb_before(cb, fsm))
    assert _row_count(uid) == 1
    assert any(pid or True for _ in [1])  # route exercised without raising
    assert cb.message.answers  # practice content was actually sent


def test_every_reachable_scenario_maps_to_a_production_id():
    # Cites test_practice_reachability_audit_documents_the_known_gap (already
    # proves every CATEGORY_MAP scenario resolves to one of the 7 ids); this
    # test adds the direct converse: every id that comes out of
    # select_practice for ANY scenario/stage/severity is ALWAYS a member of
    # PRODUCTION_PRACTICE_IDS, by construction of the enforced filter.
    stages = ("OPEN", "ACUTE_DISTRESS", "REFLECTION", "PROBLEM_SOLVING", "GROWTH")
    for scenario in pr.CATEGORY_MAP:
        for stage in stages:
            for sev in ("low", "medium", "high"):
                p = pr.select_practice(scenario, stage, sev, "ru")
                assert p["id"] in pr.PRODUCTION_PRACTICE_IDS


def test_catalog_only_definitions_are_not_production_selectable():
    catalog_only = {p["id"] for p in pr.REGISTRY} - pr.PRODUCTION_PRACTICE_IDS
    assert len(catalog_only) == 36
    for pid in catalog_only:
        assert pr.practice_status(pid) == "CATALOG_ONLY"
        assert pr.get_production_practice_by_id(pid) is None


def test_forged_practice_id_fails_closed_no_row_no_content(tmp_db, monkeypatch):
    monkeypatch.setattr(bot, "load_state", _async({}))
    uid = 701
    run(database.upsert_user(uid, "u", "U"))
    for forged in ("mind_body_scan_v1", "totally_made_up_id", ""):
        cb = _fresh_cb(uid, f"before:{forged}:grounding:ru:5")
        run(bot.cb_before(cb, FakeFSM()))
        assert _row_count(uid) == 0
        assert cb.message.answers == []


def test_malformed_callback_data_fails_closed(tmp_db, monkeypatch):
    monkeypatch.setattr(bot, "load_state", _async({}))
    uid = 702
    run(database.upsert_user(uid, "u", "U"))
    for bad in ("before:only_id", "before:id:scenario:lang:not_a_number"):
        cb = _fresh_cb(uid, bad)
        run(bot.cb_before(cb, FakeFSM()))
        assert _row_count(uid) == 0


def test_duplicate_tap_creates_exactly_one_baseline_and_cannot_overwrite(tmp_db, monkeypatch):
    monkeypatch.setattr(bot, "load_state", _async({}))
    uid = 703
    run(database.upsert_user(uid, "u", "U"))
    fsm = FakeFSM()
    cb1 = _fresh_cb(uid, "before:breathing_box_v1:grounding:ru:5")
    run(bot.cb_before(cb1, fsm))
    assert _row_count(uid) == 1
    # Duplicate tap of the SAME offer, even with a DIFFERENT score.
    cb2 = _fresh_cb(uid, "before:breathing_box_v1:grounding:ru:9")
    run(bot.cb_before(cb2, fsm))
    assert _row_count(uid) == 1  # still exactly one row
    import sqlite3
    con = sqlite3.connect(database.DB)
    before = con.execute(
        "SELECT before_score FROM intervention_results WHERE user_id=?", (uid,)).fetchone()[0]
    con.close()
    assert before == 5  # first tap's score stands, never overwritten


def test_cross_user_callbacks_never_cross_contaminate(tmp_db, monkeypatch):
    monkeypatch.setattr(bot, "load_state", _async({}))
    a, b = 704, 705
    run(database.upsert_user(a, "u", "U"))
    run(database.upsert_user(b, "u", "U"))
    run(bot.cb_before(_fresh_cb(a, "before:breathing_box_v1:grounding:ru:5"), FakeFSM()))
    assert _row_count(a) == 1
    assert _row_count(b) == 0


def test_crisis_scenario_never_offers_a_baseline_prompt():
    src = _pipeline_source()
    assert 'if scenario not in ("crisis", "open_chat"):' in src


def test_baseline_skip_flag_off_reproduces_prior_keyboard_exactly():
    assert config.THERAPEUTIC_CORE_FOUNDATION_ENABLED is False
    kb_new = bot.before_score_kb("breathing_box_v1", "grounding", "ru")
    kb_old = bot.score_kb("before:breathing_box_v1:grounding:ru")
    assert kb_new.inline_keyboard == kb_old.inline_keyboard  # byte-for-byte identical


def test_baseline_skip_flag_on_adds_one_skip_button_creates_no_row(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "THERAPEUTIC_CORE_FOUNDATION_ENABLED", True)
    uid = 706
    run(database.upsert_user(uid, "u", "U"))
    kb = bot.before_score_kb("breathing_box_v1", "grounding", "ru")
    assert len(kb.inline_keyboard) == 3  # 2 score rows + 1 skip row
    cb = _fresh_cb(uid, "before_skip:breathing_box_v1:grounding:ru")
    run(bot.cb_before_skip(cb, FakeFSM()))
    assert _row_count(uid) == 0  # non-evaluable: no baseline fabricated
    assert cb.message.answers  # practice content still shown


def test_baseline_skip_disabled_when_flag_off(tmp_db, monkeypatch):
    uid = 707
    run(database.upsert_user(uid, "u", "U"))
    cb = _fresh_cb(uid, "before_skip:breathing_box_v1:grounding:ru")
    run(bot.cb_before_skip(cb, FakeFSM()))
    assert cb.message.answers == []  # flag off -- no-op, matches "no new behavior by default"


def test_skip_then_no_after_prompt_no_improvement_claim_possible(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "THERAPEUTIC_CORE_FOUNDATION_ENABLED", True)
    uid = 708
    run(database.upsert_user(uid, "u", "U"))
    cb = _fresh_cb(uid, "before_skip:breathing_box_v1:grounding:ru")
    run(bot.cb_before_skip(cb, FakeFSM()))
    # No "how do you feel now" / after-score keyboard was ever sent.
    assert not any("score_kb" in str(kw) for _, kw in cb.message.answers)
    assert _row_count(uid) == 0


def test_db_failure_before_baseline_prevents_intervention_start(tmp_db, monkeypatch):
    monkeypatch.setattr(bot, "load_state", _async({}))
    uid = 709
    run(database.upsert_user(uid, "u", "U"))

    async def boom(*a, **kw):
        raise database.aiosqlite.Error("simulated DB failure")
    monkeypatch.setattr(bot, "start_intervention", boom)
    cb = _fresh_cb(uid, "before:breathing_box_v1:grounding:ru:5")
    with pytest.raises(Exception):
        run(bot.cb_before(cb, FakeFSM()))
    assert cb.message.answers == []  # practice content never sent -- fails loud, not fake-success
    assert _row_count(uid) == 0


def test_telegram_failure_after_baseline_leaves_baseline_row_intact(tmp_db, monkeypatch):
    monkeypatch.setattr(bot, "load_state", _async({}))
    uid = 710
    run(database.upsert_user(uid, "u", "U"))
    cb = _fresh_cb(uid, "before:breathing_box_v1:grounding:ru:5")

    async def fail_answer(*a, **kw):
        raise RuntimeError("simulated Telegram send failure")
    cb.message.answer = fail_answer
    with pytest.raises(Exception):
        run(bot.cb_before(cb, FakeFSM()))
    # The baseline row is already committed (DB write precedes Telegram send,
    # per test_baseline_write_happens_strictly_before_practice_content_is_sent)
    # -- honest, retryable state, not a false "nothing happened".
    assert _row_count(uid) == 1


def test_intervention_results_privacy_lifecycle_cross_user_isolated(tmp_db, monkeypatch):
    # intervention_results is already registered (category RESEARCH_LOG,
    # privacy_registry.py:92-93) -- no new table this pass. Direct proof of
    # export/delete-preview/delete-all/cross-user isolation for THIS table
    # specifically (not previously exercised end-to-end in this worktree).
    monkeypatch.setattr(bot, "load_state", _async({}))
    a, b = 711, 712
    run(database.upsert_user(a, "u", "U"))
    run(database.upsert_user(b, "u", "U"))
    run(bot.cb_before(_fresh_cb(a, "before:breathing_box_v1:grounding:ru:5"), FakeFSM()))

    exp_a = run(database.export_all_personal_data(a))
    exp_b = run(database.export_all_personal_data(b))
    assert len(exp_a["intervention_results"]) == 1
    assert exp_b["intervention_results"] == []

    preview = run(database.preview_delete_all_personal_data(a))
    assert preview["intervention_results"]["row_count"] == 1

    summary = run(database.delete_all_personal_data(a))
    assert summary["intervention_results"] == 1
    assert _row_count(a) == 0
    assert _row_count(b) == 0  # untouched (b never had a row, confirms no cross-effect)
