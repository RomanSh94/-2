"""Phase 4A.1 -- deterministic tests for the open_chat quality-contract rewrite.

These tests are objective (string/hash/signature checks) and explicitly do
NOT attempt to prove subjective conversational quality -- that is the job of
tests/quality_fixtures_open_chat.py plus manual owner evaluation, never a
pytest assertion. Hashes below were captured immediately after the Phase
4A.1 open_chat rewrite landed and pin content this slice must not touch;
a future PR that legitimately needs to change one of these frozen constants
should update the corresponding hash consciously, not silently.
"""
import hashlib
import inspect

import prompts
import conversation_controller
import database


def _h(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# ── 1. RU open_chat includes the new quality rules ─────────────────────────

def test_open_chat_ru_includes_new_quality_rules():
    text = prompts.PROMPTS["open_chat"]["ru"]
    markers = [
        "Не терапевт, не психолог, не врач",           # role boundary
        "реальный контекст разговора",                  # read the conversation
        "Сначала пойми, что происходит, потом советуй", # understand before advising
        "не выдумывай факты",                            # no invented facts
        "Избегай дежурных фраз-открывашек",              # anti-filler empathy openers
        "автоматической, пустой формы",                  # anti-filler: automatic empty form
        "только как гипотезу",                           # tentative formulation
        "различай близкие по смыслу состояния",          # psychological precision
        "задай ровно один",                              # one focused question
        "не отказ говорить",                             # low-energy signal handling
        "не перекладывай на пользователя",               # just-talk: don't hand topic back
        "Сам создай один естественный, конкретный и лёгкий вход",  # just-talk: bot creates entry point
        "не объясняй, что берёшь инициативу",            # just-talk: implicit, don't announce it
        "Не предлагай по умолчанию",                     # just-talk: no default distraction/mood-lift
        "выполнить технику",                              # just-talk: no unsolicited technique
        "Если подходящей детали",                        # zero-context fallback trigger condition
        "2–3 коротких, конкретных и обычных варианта",   # zero-context fallback: 2-3 low-effort options
        "Не оформляй это как список, меню или анкету",   # zero-context fallback: not a questionnaire
        "не предполагай автоматически одиночество",      # zero-context fallback: no assumed distress
        "Если пользователь явно просит совет",           # explicit advice request
        "Не переигрывай в терапию",                      # don't over-therapize
        "«Это нормально»",                                # Phase 4A.1c: extended filler-opener list
        "Тепло должно идти от внимания к",                # Phase 4A.1c: warmth-from-attention, not phrase
        "Не приписывай пользователю тип личности",       # Phase 4A.1c: no personality/identity inference
        "Максимум один смысловой вопрос за ответ",        # Phase 4A.1c: question economy
        "это фактически два вопроса или меню",           # Phase 4A.1c: compound/menu question ban
        "Ответ без единого вопроса — нормальный",        # Phase 4A.1c: zero-question responses encouraged
        "не переключайся в шаблонный режим «слушателя»", # Phase 4A.1c: just-talk, no canned listening mode
        "избегай механических «ты говорил»",             # Phase 4A.1c: no mechanical memory-signaling
        "Не превращай ответ в каталог из нескольких обычных вариантов",  # Phase 4A.1c: no advice menu
        "закрывающий вопрос-выбор вроде «Что тебе ближе?»",  # Phase 4A.1c: no forced choice-closer
        "психообразовательных фраз вроде «Усталость может накапливаться»",  # Phase 4A.1d: no generic psychoed openers
        "мысль → эмоция → импульс → поведение → последствие",  # Phase 4A.1d: professional-depth chain
        "не означает навсегда оставаться поверхностным",   # Phase 4A.1d: JUST_TALK != permanently superficial
        "не достраивай историю за",                        # Phase 4A.1d: low-evidence no-story-completion
        "неизвестное должно оставаться неизвестным",       # Phase 4A.1d: unknown stays unknown
        "один разговорный ход",                            # Phase 4A.1d: one conversational move
        "не заполняй отсутствующие эмоции, импульсы",      # Phase 4A.1d: missing-link guard
    ]
    for m in markers:
        assert m in text, f"missing quality-contract marker: {m!r}"


# ── 2. EN open_chat provides semantic parity ────────────────────────────────

def test_open_chat_en_provides_semantic_parity():
    text = prompts.PROMPTS["open_chat"]["en"]
    markers = [
        "Not a therapist, psychologist, or doctor",
        "Use the actual conversation",
        "Understand before advising",
        "never invent facts",
        "Avoid rote opener phrases",
        "automatic, empty form",
        "only as a hypothesis",
        "distinguish between close-but-different states",
        "ask exactly one",
        "is a signal, not a refusal to talk",
        "don't hand the work of inventing the topic back to the user",
        "Create one natural, concrete",
        "don't explain that you're taking initiative",
        "Don't default to distraction",
        "a technique unless the user asks",
        "If there genuinely isn't a usable detail",       # zero-context fallback trigger condition
        "2-3 short, concrete, ordinary ways to start",    # zero-context fallback: 2-3 low-effort options
        "Do not format this as a list, menu, or questionnaire",  # zero-context fallback: not a questionnaire
        "do not automatically assume loneliness",         # zero-context fallback: no assumed distress
        "If the user explicitly asks for advice",
        "Don't over-therapize",
        "\"That's normal\"",                              # Phase 4A.1c: extended filler-opener list
        "Warmth should come from attention",              # Phase 4A.1c: warmth-from-attention, not phrase
        "Don't characterize the user's personality",      # Phase 4A.1c: no personality/identity inference
        "At most one semantic question per response",     # Phase 4A.1c: question economy
        "effectively two questions or a menu of choices", # Phase 4A.1c: compound/menu question ban
        "A reply with zero questions is a normal, often better outcome",  # Phase 4A.1c: zero-question ok
        "don't switch into a canned \"listening mode\"",  # Phase 4A.1c: just-talk, no canned listening mode
        "avoid mechanical phrases like \"you said\"",     # Phase 4A.1c: no mechanical memory-signaling
        "Don't turn the reply into a catalogue of ordinary options",  # Phase 4A.1c: no advice menu
        "a closing choice-question like \"which one sounds better?\"",  # Phase 4A.1c: no forced closer
        "generic psychoeducational lines like",           # Phase 4A.1d: no generic psychoed openers
        "thought -> emotion -> impulse -> behavior -> consequence",  # Phase 4A.1d: professional-depth chain
        "doesn't mean staying superficial forever",        # Phase 4A.1d: JUST_TALK != permanently superficial
        "don't complete the story for them",               # Phase 4A.1d: low-evidence no-story-completion
        "unknown information should stay unknown",         # Phase 4A.1d: unknown stays unknown
        "one conversational move",                          # Phase 4A.1d: one conversational move
        "don't fill in a missing emotion, impulse",        # Phase 4A.1d: missing-link guard
    ]
    for m in markers:
        assert m in text, f"missing quality-contract marker: {m!r}"


# ── 3-7. Frozen constants this slice must not touch ─────────────────────────

def test_first_turn_contract_unchanged():
    assert _h(prompts.FIRST_TURN_CONTRACT_TEXT_RU) == (
        "30ee316cc7a8efeab839a87cabeb7534a3fd21372399682f290b0ab54234767a")
    assert _h(prompts.FIRST_TURN_CONTRACT_TEXT_EN) == (
        "4fce4613a09355ad1001f66a1dd3f888498330471783049911b8612952a3d4c5")


def test_first_turn_buttons_unchanged():
    assert _h(repr(prompts.UNIVERSAL_CONTINUATION_BUTTONS_RU)) == (
        "c0afa4d7e94f0c03d46bc39ab50d4fe8bb24f3bcbcf95d4f94cd775e304dd313")
    assert _h(repr(prompts.UNIVERSAL_CONTINUATION_BUTTONS_EN)) == (
        "c4c7dd3bcd02bc00ac3e5ce4c9031b784c3409d48a9a2f8cebd10170af6ba137")


def test_crisis_texts_unchanged():
    assert _h(prompts.CRISIS_TEXT_RU) == (
        "fb58d87cb16801fe1c5b3ed7f886c2a5dcff33939896be6eb6f75d278d4d5ed1")
    assert _h(prompts.CRISIS_TEXT_EN) == (
        "271d366297d445e24f5934ce4bef3139f339df2abe4faf2de52edd4ca241d531")


def test_dependency_texts_unchanged():
    assert _h(prompts.DEPENDENCY_TEXT_RU) == (
        "9cf4b6b77264b275b8dad76470f4d38b50a7174a2c260d1c888ff07d7f5b18b2")
    assert _h(prompts.DEPENDENCY_TEXT_EN) == (
        "22d76ee23f2306a3e90cf7994a07f4c43f96e437cfa6c6e2f50ed2ba9d9f490f")


def test_base_rules_unchanged():
    assert _h(prompts.BASE_RULES_RU) == (
        "c1d317f3e1d3952ec827d224ee1e944ff2b34cf5c8453206a6cabd0b9073bfa4")
    assert _h(prompts.BASE_RULES_EN) == (
        "bbe52b7eb7937e0708b0427b53a41e4fd3f7b074d2c9d6601a12c6dad71f4487")


def test_other_scenario_prompts_unchanged():
    """Only open_chat was in scope for this slice -- every other scenario
    prompt must be byte-identical to before the rewrite."""
    expected = {
        ("crisis", "ru"): "ae173ec8e4c1201a0134cfeac4b490cc757956861af7b54512e5e4a03529e6f8",
        ("crisis", "en"): "488d5ea620cf3b546e16240e09a542d44fe2d36f1c9207b612465d6a4f66ee37",
        ("grounding", "ru"): "72ad4e0aadbb8abcbc33e4696e8e22a3464dc9c55008ec21e57a3a3e22461113",
        ("grounding", "en"): "c623b270db3070009857ada8e90802fe33109c0c890707a310ff1621c040787e",
        ("stabilization", "ru"): "57272d1de306d60eb213a1bd631f82bb78ad49eb13f8572e405fe1cf1200e2ac",
        ("stabilization", "en"): "3c4772f7f4f4ecc4d827c3d793a30b36b70853181ca5376f708904d81aec43f5",
        ("cbt_thought", "ru"): "7b81073e6e8551cc487c738266c5bf9b71b2d950e6e9a5e9085d7871c1b61336",
        ("cbt_thought", "en"): "985bf764c3d2ab070081cd2f8e5aaf74ef1533a6ecbffc61900b1362b35ee809",
        ("act_acceptance", "ru"): "5f94156c0177e77c371c5d9793905654395eff2760065e37fd6ebdabb6047c69",
        ("act_acceptance", "en"): "81cc3642fe4726d21d389110e72333958e9c478d6c49d57294e36e0f76a293a8",
        ("reflective", "ru"): "5ee5f968e9f8281e4920d2f995e1996e05b2e41a1c084d788e4c0098e7012b0d",
        ("reflective", "en"): "6e53bb42f34b4a96ee26ee7f7d9cf635d65aea5d00f2171eded3920dad3e976b",
        ("somatic", "ru"): "5bf0555482e7850058ccb7d922e76e379f56174f0526d259738d1e1c01f1827d",
        ("somatic", "en"): "6673c423df6b1e06dfd97f9c925634ab1ea84422567719a11d4f35ef559285ea",
    }
    for (scenario, lang), digest in expected.items():
        assert _h(prompts.PROMPTS[scenario][lang]) == digest, f"{scenario}/{lang} changed unexpectedly"


def test_controller_prompts_unchanged():
    assert _h(conversation_controller._BASE_INSTRUCTIONS_RU) == (
        "730eb128bfe58c57ff351cb3dcc0815141ff8fac6f06f0d89e0c4d666fca7492")
    assert _h(conversation_controller._BASE_INSTRUCTIONS_EN) == (
        "3ecd288170dca68ce49be3e6c63bd9c2f1ec33ca76e36af3006400b71e2ac8b7")


# ── 8. No state_engine context injection was introduced ────────────────────

def test_no_state_engine_labels_injected_into_open_chat():
    """The audited-and-rejected approach: injecting raw internal heuristic
    labels (anxiety=..., energy=..., etc.) into the prompt. Confirms none of
    state_engine's internal state-dimension names leaked into the rewrite as
    literal injected values, and that get_system_prompt's signature was not
    widened to accept a state argument."""
    text = (prompts.PROMPTS["open_chat"]["ru"] + prompts.PROMPTS["open_chat"]["en"]).lower()
    for label in ("anxiety=", "loneliness=", "energy=", "overwhelm=", "panic=",
                  "hopelessness=", "openness=", "dissociation=",
                  "тревога=", "одиночество=", "энергия="):
        assert label not in text

    sig = inspect.signature(prompts.get_system_prompt)
    assert list(sig.parameters) == ["scenario", "lang"]


# ── 9. No DB/schema changes were introduced ─────────────────────────────────

def test_no_db_schema_changes():
    assert _h(database.SCHEMA) == (
        "1d76fefe6a13834504be69dc20c0bbfa6c0e9bdeecf18f1fd768242ef3866336")


# ── Fixture-file sanity (structural only -- never asserts response quality) ─

def test_quality_fixtures_load_and_are_well_formed():
    from tests.quality_fixtures_open_chat import FIXTURES
    assert len(FIXTURES) == 15
    required_keys = {"id", "context", "user_message", "failure_to_avoid", "target_characteristics"}
    ids = set()
    for fx in FIXTURES:
        assert required_keys.issubset(fx.keys())
        assert isinstance(fx["user_message"], str) and fx["user_message"]
        assert isinstance(fx["failure_to_avoid"], list) and fx["failure_to_avoid"]
        assert isinstance(fx["target_characteristics"], list) and fx["target_characteristics"]
        ids.add(fx["id"])
    assert len(ids) == 15  # all ids unique


def test_quality_fixtures_structural_integrity():
    """Permanent structural-hygiene guard for quality_fixtures_open_chat.py --
    catches the exact contamination classes found during Phase 4A.1 manual
    audit (placeholder bracket text, the current turn duplicated as the last
    context entry, malformed role sequences). Deliberately does NOT judge
    dialogue quality or content -- shape only."""
    from tests.quality_fixtures_open_chat import FIXTURES

    assert len(FIXTURES) == 15

    seen_ids = set()
    for fx in FIXTURES:
        assert isinstance(fx, dict)

        cid = fx.get("id")
        assert isinstance(cid, str) and cid.strip()
        assert cid not in seen_ids, f"duplicate fixture id: {cid}"
        seen_ids.add(cid)

        user_message = fx.get("user_message")
        assert isinstance(user_message, str) and user_message.strip()
        assert "<" not in user_message and ">" not in user_message, (
            f"{cid}: placeholder bracket text in user_message")

        context = fx.get("context")
        assert isinstance(context, list)

        context_texts = []
        prior_role = None
        for i, turn in enumerate(context):
            assert isinstance(turn, tuple) and len(turn) == 2, (
                f"{cid}: context turn {i} is not a 2-item tuple")
            role, text = turn
            assert role in ("user", "assistant"), (
                f"{cid}: context turn {i} has invalid role {role!r}")
            assert isinstance(text, str) and text.strip(), (
                f"{cid}: context turn {i} has empty/non-string text")
            assert "<" not in text and ">" not in text, (
                f"{cid}: placeholder bracket text in context turn {i}")
            assert role != prior_role, (
                f"{cid}: consecutive {role} turns at context index {i-1},{i}")
            prior_role = role
            context_texts.append(text)

        assert user_message not in context_texts, (
            f"{cid}: user_message duplicated inside context")

        if context:
            assert context[-1][0] != "user", (
                f"{cid}: context ends with a user turn -- appending user_message "
                "would create consecutive user turns")
