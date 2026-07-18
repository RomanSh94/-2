"""First-user illustrated onboarding — localized content and step specs.

Pure data + pure functions. NO aiogram, NO database, NO Telegram calls here, so
every caption / button / callback string is unit-testable in isolation. The
Telegram-facing rendering (photo send / edit, keyboard markup) lives in
onboarding.py; the handler wiring lives in bot.py.

Design invariants enforced by tests (tests/test_first_user_onboarding.py,
tests/test_onboarding_media.py):

  * exactly 5 steps; step 5 is the privacy screen and has NO skip button;
  * every caption stays within Telegram's photo-caption limit (1024 chars);
  * every callback string stays within Telegram's 64-byte callback_data limit;
  * captions are PLAIN TEXT (the bot has no default parse_mode; the mood/crisis
    screens are plain text too) — there is no dynamic user text in any caption,
    so there is no escaping/injection surface.
"""
import pathlib

# Bump this (and add a new asset/version folder) for a genuinely new
# onboarding CONTENT revision (the 5 illustrated screens themselves) -- an
# existing completed/legacy_exempt/superseded row is keyed by (user, version)
# now (real versioning; see database.py SCHEMA). Bumping this starts the
# MANDATORY current version for every user without a row for it yet (spec
# item F: version equality is the gate -- see bot.cmd_start), including users
# who already completed an OLDER version; their old row is marked
# 'superseded' (if it was still active) and left untouched otherwise, never
# silently treated as a permanent exemption from a mandatory update.
ONBOARDING_VERSION = "v1"

# Separate axis (spec item F): the version of the PRIVACY NOTICE text shown on
# screen 5. A content-only fix to screens 1-4 does not need to bump this; a
# materially changed privacy notice should. Acknowledgement is tracked
# independently in database.user_notice_acknowledgements (NOT keyed by
# onboarding_version) -- bumping THIS constant alone reaches every settled
# user via determine_onboarding_requirement's PRIVACY_NOTICE_ONLY result,
# with no need to also bump ONBOARDING_VERSION.
PRIVACY_NOTICE_VERSION = "v1"

FIRST_STEP = 1
LAST_STEP = 5          # the privacy screen — cannot be skipped
STEPS = (1, 2, 3, 4, 5)

# Telegram hard limits (bytes for callback_data; characters for a photo caption).
TELEGRAM_CAPTION_LIMIT = 1024
TELEGRAM_CALLBACK_LIMIT = 64

# ── Callback data (compact; no user id — the requester is callback.from_user.id).
CB_PREFIX = f"onb:{ONBOARDING_VERSION}:"
CB_SKIP = f"{CB_PREFIX}skip"
CB_START = f"{CB_PREFIX}start"
CB_PRIVACY = f"{CB_PREFIX}privacy"
# Distinct callback for the PRIVACY_NOTICE_ONLY screen's "Start"/"Начать"
# button (see determine_onboarding_requirement below) -- that screen is NOT
# backed by any user_onboarding_state row, so it must never be answered by
# cb_onboarding's row-based CB_START branch. Still inside the "onb:" prefix
# (still exempt from OnboardingGateMiddleware; still rejected as a safe no-op
# by cb_onboarding for any other onboarding_version).
#
# CRITICAL: bound to the EXACT notice_version rendered on the card, not just
# ONBOARDING_VERSION -- PRIVACY_NOTICE_VERSION is an INDEPENDENT axis (see
# that constant's comment) that can bump while ONBOARDING_VERSION stays the
# same. Without embedding notice_version here, a stale v1 card left open
# across a v1->v2 bump could tap into a handler that blindly acknowledges
# whatever PRIVACY_NOTICE_VERSION is CURRENT at tap time -- i.e. the user
# would be recorded as having acknowledged v2 content they never saw. The
# handler (bot.cb_onboarding) parses the embedded version back out and
# rejects the tap as a safe no-op unless it exactly matches the CURRENT
# PRIVACY_NOTICE_VERSION.
CB_PRIVACY_ONLY_START_PREFIX = f"{CB_PREFIX}privacy_only_start:"


def cb_privacy_only_start(notice_version: str) -> str:
    return f"{CB_PRIVACY_ONLY_START_PREFIX}{notice_version}"


def cb_next(target_step: int) -> str:
    """Callback for the primary 'advance' button leading TO target_step (2..5)."""
    return f"{CB_PREFIX}next:{target_step}"


# ── Illustration assets (illustration-only PNGs; NO embedded text, NO Telegram
# UI). If a file is absent/unreadable the renderer falls back to a text-only card
# with the SAME caption and keyboard (see onboarding.py) — the bot never crashes.
#
# Resolved relative to THIS MODULE's own location (not the process working
# directory) — a bot started from a different cwd (a service manager, a
# scheduler, a different shell) must still find these files. pathlib.Path
# gives an absolute path up front, so asset_path()'s return value never
# depends on os.getcwd() at call time.
_MODULE_DIR = pathlib.Path(__file__).resolve().parent
_ASSET_DIR = _MODULE_DIR / "assets" / "onboarding" / ONBOARDING_VERSION
ASSETS = {
    1: str(_ASSET_DIR / "01_welcome.png"),   # a friendly pastel flower
    2: str(_ASSET_DIR / "02_safety.png"),    # a shield, heart and phone symbol
    3: str(_ASSET_DIR / "03_topics.png"),    # a calm chat bubble / supportive character
    4: str(_ASSET_DIR / "04_features.png"),  # a notebook, checklist and subtle chart
    5: str(_ASSET_DIR / "05_privacy.png"),   # a shield, lock and privacy document
}


def asset_path(step: int) -> str:
    return ASSETS[step]


# ── Captions ────────────────────────────────────────────────────────────────
# Verbatim from the approved copy. RU is canonical; EN is a complete parallel
# translation (not a machine fallback). No unsupported modality is advertised;
# every capability claim in screen 4 is verified against production code
# (text + voice input, questionnaires, the q:m "Discuss the result" flow,
# emotion + CBT journals, summaries, privacy export/delete).

# Crisis-resource wording (spec item B, this correction round): onboarding
# NEVER selects or displays a specific emergency/hotline number in EITHER
# language, and never calls crisis_protocol.get_hotline. Onboarding runs
# before we know anything reliable about the user's actual location — the UI
# language (RU vs EN) is not a country signal, and treating it as one was
# exactly the bug being corrected here (an earlier revision called
# get_hotline("ru") merely because the screen was in Russian, which is really
# just "language en" used as a US-proxy in reverse). Both languages therefore
# use IDENTICAL neutral wording in structure: "your local emergency service"
# / "a local crisis service", no digits, no country inferred from language.
# A specific number may be shown only once an approved, reliable region/
# location policy exists (not implemented here) -- see docs/first_user_onboarding.md.
# This also means onboarding maintains NO crisis-contact data of its own to
# drift out of sync with crisis_contacts.json -- there is nothing to sync.


def _caption_ru_5(has_policy_url: bool) -> str:
    # F: the final line must NEVER claim the user read/opened a Privacy Policy
    # document that isn't actually linked anywhere -- only when a verified
    # PRIVACY_POLICY_URL is configured does "Начать" acknowledge the Policy
    # too; otherwise it acknowledges only THIS notice (matches
    # database.complete_onboarding's privacy_notice_acknowledged_at, which is
    # deliberately named/scoped as notice acknowledgment, not policy consent).
    #
    # J (this correction round): the "we do not sell data / no ads" sentence
    # was REMOVED entirely -- it is an organizational/legal claim that cannot
    # be verified from source code, and framing it as "по нашей политике"
    # (per our policy) was still presenting it as a fact to the user without
    # owner sign-off. Only technically verified statements remain: history is
    # stored; text may be processed by the AI provider; export/delete tools
    # exist; safety-audit data may be retained under documented exceptions
    # (see _PRIVACY_SUMMARY below). Do not re-add the removed claim without
    # explicit owner approval.
    ack = ("ты подтверждаешь ознакомление с этим уведомлением и Политикой "
          "конфиденциальности." if has_policy_url else
          "ты подтверждаешь, что ознакомился(-лась) с этим уведомлением.")
    return (
        "X20 сохраняет историю нашей переписки, чтобы поддерживать контекст "
        "общения, продолжать работу между разговорами и формировать доступные "
        "тебе материалы.\n\n"
        "Для генерации ответов часть текста может обрабатываться "
        "подключённым AI-провайдером — точные условия, сроки хранения и "
        "исключения описаны в Политике конфиденциальности.\n\n"
        "Ты можешь просматривать, экспортировать и удалять свои данные через "
        "инструменты приватности бота.\n\n"
        f"Нажимая «Начать», {ack}"
    )


def _caption_en_5(has_policy_url: bool) -> str:
    # J: see the RU variant's comment -- the unverified "we do not sell data /
    # no ads" claim was removed entirely, not merely reframed.
    ack = ("you acknowledge that you have read this notice and the Privacy "
          "Policy." if has_policy_url else
          "you acknowledge that you have read this notice.")
    return (
        "X20 stores the history of our conversation to maintain context, "
        "continue work between conversations and create materials available "
        "to you.\n\n"
        "To generate replies, some text may be processed by the configured AI "
        "provider. Exact conditions, retention periods and exceptions are "
        "described in the Privacy Policy.\n\n"
        "You can view, export and delete your data through the bot’s "
        "privacy tools.\n\n"
        f"By pressing “Start”, {ack}"
    )


_CAPTIONS = {
    "ru": {
        1: (
            "Привет! Я X20 — ИИ-помощник для психологической поддержки.\n\n"
            "Я помогаю бережно разбирать мысли, чувства и сложные ситуации, "
            "используя структурированные навыки самопомощи и научно обоснованные "
            "подходы.\n\n"
            "Я не заменяю психолога, психотерапевта или врача и не ставлю диагнозы."
        ),
        3: (
            "Ко мне можно обратиться, чтобы:\n\n"
            "• справиться с тревогой и стрессом;\n"
            "• разобрать конкретную ситуацию;\n"
            "• понять, что поддерживает тяжёлое или подавленное состояние;\n"
            "• бережнее относиться к себе;\n"
            "• выстраивать личные границы;\n"
            "• улучшать отношения с близкими;\n"
            "• выбрать один реалистичный следующий шаг.\n\n"
            "Здесь нет «глупых» вопросов. Можно начать с того, что сейчас "
            "беспокоит больше всего."
        ),
        4: (
            "Я умею:\n\n"
            "• поддерживать разговор текстом и принимать голосовые сообщения;\n"
            "• проводить доступные опросы и показывать их результаты;\n"
            "• помогать обсуждать результат через кнопку «Обсудить результат»;\n"
            "• вести дневник эмоций и КПТ-дневник;\n"
            "• показывать доступные сводки о состоянии;\n"
            "• выгружать и удалять сохранённые данные.\n\n"
            "При необходимости выгруженные материалы можно показать специалисту.\n\n"
            "В разговоре мы не будем делать всё сразу — выберем одну задачу и "
            "один понятный следующий шаг."
        ),
    },
    "en": {
        1: (
            "Hi! I’m X20, an AI assistant for psychological support.\n\n"
            "I can help you gently explore thoughts, feelings and difficult "
            "situations using structured self-help skills and evidence-informed "
            "approaches.\n\n"
            "I do not replace a psychologist, psychotherapist or doctor, and I do "
            "not diagnose."
        ),
        3: (
            "You can talk to me to:\n\n"
            "• cope with anxiety and stress;\n"
            "• work through a specific situation;\n"
            "• understand what may be maintaining a difficult or low state;\n"
            "• relate to yourself more kindly;\n"
            "• build personal boundaries;\n"
            "• improve relationships with people close to you;\n"
            "• choose one realistic next step.\n\n"
            "There are no “stupid” questions here. You can begin with whatever is "
            "bothering you most right now."
        ),
        4: (
            "I can:\n\n"
            "• support text conversations and accept voice messages;\n"
            "• provide available questionnaires and show their results;\n"
            "• help discuss a result through the “Discuss the result” button;\n"
            "• provide an emotion journal and a CBT journal;\n"
            "• show the summaries currently available to you;\n"
            "• export and delete stored data.\n\n"
            "Exported materials can be shared with a professional when useful.\n\n"
            "We will not try to do everything at once — we will choose one task "
            "and one clear next step."
        ),
    },
}

# Screen 2 (crisis limitation): IDENTICAL neutral structure in both languages,
# no specific number in either. Onboarding language (RU vs EN) is a UI/text
# preference, not a country signal — treating it as one (in either direction)
# was the exact bug corrected here. Neither caption calls
# crisis_protocol.get_hotline or duplicates any number from crisis_contacts.json.
_CAPTION_RU_2 = (
    "Если тебе очень плохо или небезопасно прямо сейчас, пожалуйста, "
    "не оставайся с этим в одиночку.\n\n"
    "X20 не подходит для экстренных ситуаций. Если есть непосредственный "
    "риск причинить вред себе или другому человеку — позвони в местную "
    "экстренную службу или обратись в местную кризисную службу.\n\n"
    "По возможности свяжись с человеком, которому доверяешь."
)
_CAPTION_EN_2 = (
    "If you feel unsafe or at immediate risk right now, please do not face "
    "it alone.\n\n"
    "X20 is not suitable for emergencies. If there is an immediate risk of "
    "harm to yourself or someone else, call your local emergency number or "
    "contact a local crisis service.\n\n"
    "When possible, contact someone you trust."
)

# ── Button labels ─────────────────────────────────────────────────────────────
_PRIMARY = {
    "ru": {
        1: "Продолжить",
        2: "С чем ко мне можно обратиться?",
        3: "Что ты умеешь?",
        4: "А в безопасности ли мои данные?",
        5: "Начать",
    },
    "en": {
        1: "Continue",
        2: "What can I talk to you about?",
        3: "What can you do?",
        4: "Is my data safe?",
        5: "Start",
    },
}
_SKIP_LABEL = {"ru": "Пропустить знакомство", "en": "Skip introduction"}
# Two DISTINCT labels (spec item F): _PRIVACY_LABEL is only ever used for a
# REAL url= button (a verified PRIVACY_POLICY_URL is configured) -- it is
# truthful to call that "the Privacy Policy" because it links straight to it.
# _ABOUT_DATA_LABEL is used for the in-bot-summary callback button (no URL
# configured) -- calling THAT "the Privacy Policy" would misrepresent a short
# bot-generated summary as the actual legal document, so it gets a distinct,
# honest label.
_PRIVACY_LABEL = {"ru": "Политика конфиденциальности", "en": "Privacy Policy"}
_ABOUT_DATA_LABEL = {"ru": "О данных и приватности", "en": "About data and privacy"}


def _lang(lang: str) -> str:
    return "en" if lang == "en" else "ru"


def caption(step: int, lang: str = "ru", privacy_policy_url: str = "") -> str:
    """`privacy_policy_url` only affects step 5's final acknowledgment line
    (see _caption_ru_5/_caption_en_5) -- whether "Начать"/"Start" acknowledges
    the notice alone, or the notice AND the real Privacy Policy."""
    L = _lang(lang)
    if step == 2:
        return _CAPTION_RU_2 if L == "ru" else _CAPTION_EN_2
    if step == LAST_STEP:
        has_url = bool(privacy_policy_url)
        return _caption_ru_5(has_url) if L == "ru" else _caption_en_5(has_url)
    return _CAPTIONS[L][step]


def button_spec(step: int, lang: str = "ru", privacy_policy_url: str = "") -> list[list[dict]]:
    """Return the inline-keyboard layout for a step as a pure spec: a list of
    rows, each a list of button dicts. A button is either a callback button
    ({"text","cb"}) or a URL button ({"text","url"}). onboarding.py turns this
    into an aiogram InlineKeyboardMarkup — kept out of here so the layout is
    testable without aiogram.

    Steps 1–4: primary 'advance' + 'Skip introduction'.
    Step 5 (privacy): 'Start' + a data/privacy button. NO skip button — the
    privacy notice can never be skipped. That second button is a real URL
    button labeled "Privacy Policy" ONLY when a verified PRIVACY_POLICY_URL is
    configured; otherwise it is a callback labeled "About data and privacy"
    that shows a deterministic in-bot summary (never a dead/invented link, and
    never mislabeled as the actual Policy document).
    """
    L = _lang(lang)
    if step == LAST_STEP:
        privacy_btn = ({"text": _PRIVACY_LABEL[L], "url": privacy_policy_url}
                       if privacy_policy_url else
                       {"text": _ABOUT_DATA_LABEL[L], "cb": CB_PRIVACY})
        return [
            [{"text": _PRIMARY[L][5], "cb": CB_START}],
            [privacy_btn],
        ]
    # informational steps 1..4
    return [
        [{"text": _PRIMARY[L][step], "cb": cb_next(step + 1)}],
        [{"text": _SKIP_LABEL[L], "cb": CB_SKIP}],
    ]


# ── Deterministic in-bot privacy summary (used by the Privacy Policy button when
# no verified public URL is configured). Points at the REAL existing privacy
# self-service commands; invents no URL and makes no unsupported claim.
# J (this correction round): the "we do not sell data / no ads" bullet was
# REMOVED entirely (not reframed) -- an organizational/legal claim with no
# owner sign-off does not belong in bot copy, verified or not. Only
# technically verified statements remain here.
_PRIVACY_SUMMARY = {
    "ru": (
        "Коротко о данных:\n\n"
        "• X20 сохраняет историю переписки, чтобы поддерживать контекст и "
        "продолжать работу между разговорами.\n"
        "• Для генерации ответов часть текста обрабатывается подключённым "
        "AI-провайдером.\n"
        "• Записи, связанные с безопасностью, могут сохраняться дольше по "
        "политике безопасности.\n\n"
        "Твои данные под твоим контролем:\n"
        "• /privacy_export_all — выгрузить все свои данные;\n"
        "• /privacy_delete_all — удалить свои данные."
    ),
    "en": (
        "About your data, in short:\n\n"
        "• X20 stores the conversation history to keep context and continue work "
        "between conversations.\n"
        "• To generate replies, some text is processed by the configured AI "
        "provider.\n"
        "• Safety-related records may be kept longer under the safety policy.\n\n"
        "Your data stays under your control:\n"
        "• /privacy_export_all — export all your data;\n"
        "• /privacy_delete_all — delete your data."
    ),
}


def privacy_summary(lang: str = "ru") -> str:
    return _PRIVACY_SUMMARY[_lang(lang)]


def button_spec_privacy_only(notice_version: str, lang: str = "ru",
                             privacy_policy_url: str = "") -> list[list[dict]]:
    """Same layout as button_spec(LAST_STEP, ...) -- identical caption/data
    button -- but the primary button's callback is cb_privacy_only_start(...)
    instead of CB_START, since this screen is not backed by any onboarding
    row (see determine_onboarding_requirement's PRIVACY_NOTICE_ONLY result).
    `notice_version` MUST be the exact version being rendered right now (the
    caller's current PRIVACY_NOTICE_VERSION) -- it is baked into the
    callback so a stale card cannot later acknowledge a different version."""
    L = _lang(lang)
    spec = button_spec(LAST_STEP, lang, privacy_policy_url)
    spec[0] = [{"text": _PRIMARY[L][LAST_STEP], "cb": cb_privacy_only_start(notice_version)}]
    return spec


# ── Onboarding requirement decision (spec item F correction) ────────────────
# Pure, side-effect-free: no Telegram I/O, no DB access -- the caller gathers
# all evidence (eligibility, whether an active row exists, whether a row for
# the CURRENT onboarding_version exists, whether the CURRENT privacy notice
# is already acknowledged) and passes it in explicitly. Directly unit-tested
# against the full decision matrix (tests/test_onboarding_schema_migration.py
# / tests/test_first_user_onboarding.py).
FULL_ONBOARDING = "FULL_ONBOARDING"
PRIVACY_NOTICE_ONLY = "PRIVACY_NOTICE_ONLY"
NOT_REQUIRED = "NOT_REQUIRED"


def determine_onboarding_requirement(
    *, eligibility: str, has_active_state: bool,
    has_current_version_row: bool, notice_acknowledged: bool,
) -> str:
    """
    has_active_state       -- True iff an ACTIVE user_onboarding_state row
                              exists (any version) -- always resumes/starts
                              full onboarding; the caller decides the exact
                              mechanics (resume in place vs. supersede+start
                              a mandatory newer version).
    has_current_version_row -- True iff a (non-active) row already exists for
                              the CURRENT ONBOARDING_VERSION specifically
                              (completed/legacy_exempt/superseded). Ignored
                              when has_active_state is True.
    eligibility             -- "new" or "legacy" (database.get_onboarding_eligibility).
    notice_acknowledged     -- database.has_privacy_notice_ack(uid, PRIVACY_NOTICE_VERSION),
                              independent of any onboarding row.

    A genuinely new user (no current-version row, eligibility == "new") always
    gets FULL_ONBOARDING regardless of notice_acknowledged (a truly new user
    cannot have acknowledged anything yet; this branch never actually
    contradicts that, but is deliberately checked first so eligibility, not
    notice state, decides who sees the illustrated screens). Every other case
    reduces to the one real question this fixes: has the CURRENT privacy
    notice been acknowledged, independent of onboarding-version history? If
    not: PRIVACY_NOTICE_ONLY, and a future PRIVACY_NOTICE_VERSION bump reaches
    that user even if ONBOARDING_VERSION never changes.
    """
    if has_active_state:
        return FULL_ONBOARDING
    if eligibility == "new" and not has_current_version_row:
        return FULL_ONBOARDING
    return NOT_REQUIRED if notice_acknowledged else PRIVACY_NOTICE_ONLY
