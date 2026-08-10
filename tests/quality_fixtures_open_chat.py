"""Phase 4A.1 — non-runtime, non-automated quality evaluation fixtures for the
open_chat scenario prompt (prompts.py).

NOT a pytest test module (deliberately not named test_*.py -- pytest will not
collect it). These fixtures do not assert anything and do not call the LLM;
they exist for owner/manual evaluation of real generated responses against
qualitative TARGET_CHARACTERISTICS, and to give FAILURE_TO_AVOID a concrete,
reusable checklist. No "golden response" is defined anywhere in this file --
subjective conversational quality cannot be proven by a fixed expected
string, only judged against the characteristics below.

Each fixture is a plain dict: {id, context, user_message, failure_to_avoid,
target_characteristics}. `context` is a list of (role, text) tuples for any
prior turns the scenario assumes already happened; None/[] means a
first-in-topic message with no relevant prior turn.
"""

FIXTURES = [
    {
        "id": "tired_wants_to_talk",
        "context": [],
        "user_message": "Сегодня ужасно устал. Хочу просто поговорить.",
        "failure_to_avoid": [
            "generic wellness advice (walk/music/tea/movie/breathing)",
            '"choose a topic yourself" style open question',
            "demanding a long explanation before engaging",
        ],
        "target_characteristics": [
            "no advice offered unprompted",
            "easy, low-effort entry point into the conversation",
            "grounded in the user's actual wording (tired / wanting to talk), not generic",
        ],
    },
    {
        "id": "unload_head_tonight",
        "context": [
            ("user", "Сегодня ужасно устал. Хочу просто поговорить."),
            ("assistant", "Похоже, день сильно тебя вымотал. Что из сегодняшнего до сих пор не отпускает?"),
        ],
        "user_message": "Расскажи, как мне немного разгрузить голову сегодня вечером?",
        "failure_to_avoid": [
            "five-item wellness list (walk/tea/music/creative activity/movie)",
            "ignoring that this is a continuation, not a fresh topic",
        ],
        "target_characteristics": [
            "uses the previous turn's context",
            "either clarifies what is occupying the user's mind, or gives 1-2 contextual actions",
            "if advice is given, briefly explains why it fits this situation",
        ],
    },
    {
        "id": "partner_conflict",
        "context": [],
        "user_message": (
            "Поругался с девушкой. Она сказала, что я её вообще не слушаю. "
            "Меня это до сих пор бесит."
        ),
        "failure_to_avoid": [
            "generic relationship advice",
            "treating this as a fact ('you have a listening problem')",
            "ignoring the specific phrase the user quoted",
        ],
        "target_characteristics": [
            "notices a specific detail (e.g. the phrase 'ты меня вообще не слушаешь' still carrying weight)",
            "tentative interpretation only ('похоже', 'возможно') -- never asserted as fact",
            "one useful, focused follow-up question",
        ],
    },
    {
        "id": "rumination",
        "context": [],
        "user_message": "Не могу перестать думать об одном и том же. Всё крутится в голове.",
        "failure_to_avoid": [
            "immediately prescribing breathing/distraction/a technique",
            "collapsing straight to generic 'that sounds hard'",
        ],
        "target_characteristics": [
            "helps distinguish plausible readings when useful (e.g. replaying the past vs. worrying about "
            "what's next) without forcing a taxonomy that doesn't fit",
            "seeks to understand the loop before offering any technique",
        ],
    },
    {
        "id": "loneliness_vs_boredom",
        "context": [],
        "user_message": "Весь вечер дома один. Скучно.",
        "failure_to_avoid": [
            "assuming loneliness without checking",
            "psychologizing ordinary boredom",
        ],
        "target_characteristics": [
            "allows the distinction between lack of activity and lack of human contact",
            "does not over-interpret a plain statement of boredom",
        ],
    },
    {
        "id": "low_energy_dont_know",
        "context": [
            ("assistant", "Привет. Как ты сегодня?"),
        ],
        "user_message": "не знаю",
        "failure_to_avoid": [
            '"расскажи подробнее" or any demand for a long free-form explanation',
            "treating the reply as a conversational dead end",
        ],
        "target_characteristics": [
            "reduces required effort",
            "user can continue with a very short answer (e.g. picking one of 2-3 short options)",
            "stays conversational, not a formal menu/questionnaire",
        ],
    },
    {
        "id": "explicit_advice_request",
        "context": [
            (
                "user",
                "Друг весь вечер сидел в телефоне, пока я рассказывал ему, как прошёл день. "
                "Меня это зацепило сильнее, чем я думал.",
            ),
            (
                "assistant",
                "Похоже, дело не только в телефоне — может, важнее было почувствовать, что "
                "тебя слушают именно в этот момент?",
            ),
        ],
        "user_message": "Я уже понял, почему меня это задело. Скажи лучше, что мне сейчас сделать?",
        "failure_to_avoid": [
            "answering with another question instead of advice",
            "an unfocused list of generic options",
        ],
        "target_characteristics": [
            "answers directly, no further interrogation",
            "1-2 context-specific actions, not a long list",
            "briefly explains why the action(s) fit this situation",
        ],
    },
    {
        "id": "user_rejects_advice",
        "context": [],
        "user_message": "Я не хочу сейчас советов, просто поговори со мной.",
        "failure_to_avoid": [
            "offering advice or a coping technique anyway",
            "a hidden/disguised exercise",
        ],
        "target_characteristics": [
            "respects the request immediately",
            "helps the conversation continue naturally without pivoting to fixing",
        ],
    },
    {
        "id": "ordinary_nonpathological_boredom",
        "context": [
            ("user", "Весь вечер сижу дома, скучно, даже не знаю чем себя занять."),
            (
                "assistant",
                "Похоже, вечер тянется долго. Это больше про то, что не хватает дела, или про "
                "то, что рядом сейчас никого нет?",
            ),
        ],
        "user_message": "Да нет, одному нормально. Просто делать нечего.",
        "failure_to_avoid": [
            "continuing to push the loneliness interpretation after being corrected",
            "turning plain boredom into something pathological",
        ],
        "target_characteristics": [
            "accepts the correction without resistance",
            "drops the earlier hypothesis cleanly",
        ],
    },
    {
        "id": "continuity_across_turns",
        "context": [
            (
                "user",
                "Не спал почти всю ночь, весь день на нервах из-за завтрашнего разговора с начальником.",
            ),
            (
                "assistant",
                "Похоже, ты уже много раз прокрутил этот разговор в голове. Больше напрягает "
                "сам разговор или то, чем он может закончиться?",
            ),
            (
                "user",
                "Скорее то, чем всё закончится. Не понимаю, чего от него ждать.",
            ),
            (
                "assistant",
                "Понял. Тогда больше всего давит неопределённость перед этим разговором.",
            ),
        ],
        "user_message": "Устал уже это всё крутить в голове, если честно.",
        "failure_to_avoid": [
            "responding as if this were an isolated first message",
            "inventing details never mentioned (e.g. fabricating what the boss conversation is about)",
            "unnecessarily repeating the same framing already used earlier in the conversation",
        ],
        "target_characteristics": [
            "demonstrates meaningful continuity with the specific detail from turn 1 (the boss conversation, "
            "the sleepless night)",
            "no invented memory beyond what was actually said",
        ],
    },
]
