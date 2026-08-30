"""Push V1 Contextual Continue V1 -- trusted-UI continuation generation
boundary.

WHY THIS EXISTS -- the deployed Push V1 `Продолжить` button currently
replies with a fixed, generic acknowledgement regardless of what the
resumed conversation was actually about (see bot.py's
_push_continue_reply_text). This module is the smallest dedicated
generation boundary that lets that reply be genuinely grounded in the
real, anchor-fenced prior conversation, WITHOUT reusing (and thereby
violating the provenance contract of) either professional_free_text_
runtime.run_professional_free_text_turn or therapist_core_v1.
generate_therapist_core_v1 -- both of those require a genuine CURRENT
user turn (source_text + source_message_row_id + risk_result scored
against real user-typed text), which a Push button tap structurally is
not (see bot.cb_push_action's own docstring: "a trusted UI selection,
never user free text").

CRITICAL PROVENANCE BOUNDARY -- a Push V1 Continue tap carries NO new
user-authored content. This module:
  - NEVER accepts or fabricates a `source_text` parameter;
  - NEVER places anything resembling "Продолжить"/"Continue" (or any
    other stand-in for the button tap itself) into the request as if it
    were user speech;
  - builds its request from exactly two ingredients: (1) the existing
    ProfessionalConversationContext of genuinely PRIOR, already-
    persisted, already-provenance-verified USER_AUTHORED/
    ASSISTANT_DELIVERED turns (built via professional_turn_conversation_
    context.build_conversation_context_from_history_rows over rows this
    module never fetches itself -- see the caller in bot.py), and (2) a
    fixed, deterministic steering instruction that is System-authored,
    not disguised as anything the user said.

Performs no I/O of its own except the one injected `client` chat-
completion call; no database access, no Telegram access, no environment
reads. Raises on failure (empty/no-content provider response, malformed
context) -- it is the caller's job (bot.py) to catch that and degrade to
the existing neutral fallback; this module never silently substitutes a
fallback string itself, so a caller can never mistake a real failure for
a real (if bland) generation.

Validation is deliberately NOT bundled into this module: bot.py calls
safety_validator.validate_response_without_current_user directly at the
integration site -- a response-only Safety Validator entry point (reusing
the SAME underlying deterministic checks validate_response_with_context
itself uses) added specifically because there is no genuine current user
message here for validate_response_with_context's user_last_message/
risk_result parameters to legitimately describe. This file adds no new
safety policy of its own.
"""
from __future__ import annotations

from professional_turn_conversation_context import (
    ConversationTurnRole,
    ProfessionalConversationContext,
)
from prompts import get_system_prompt

# The genuine conversational scenario for a SUCCESSFUL contextual
# continuation -- deliberately NOT database.PUSH_UI_SCENARIO. A real,
# grounded reply generated here is a genuine conversational assistant
# turn (exactly the kind of turn get_last_assistant_message_id's anchor
# selection is supposed to recognize for a FUTURE push), not a sealed,
# non-conversational UI acknowledgement -- tagging it push_ui would
# incorrectly make it permanently anchor-ineligible. The deterministic
# fallback replies (no anchor / empty context / generation or validator
# failure) remain tagged PUSH_UI_SCENARIO exactly as before, since those
# really are sealed, generic UI acknowledgements, not genuine discourse.
SCENARIO = "push_v1_contextual_continue"

# The one deterministic, System-authored steering instruction appended
# after the real prior turns, as the final request message. Never claims
# to be something the user said, and is never passed to the Safety
# Validator as a stand-in for a genuine current user message -- bot.py
# validates a Contextual Continue candidate via safety_validator.
# validate_response_without_current_user, a response-only entry point
# that takes no user-message-shaped argument at all (see that function's
# own docstring for why validate_response_with_context's
# user_last_message/risk_result parameters are NOT legitimate here, even
# though that function does not currently inspect user_last_message's
# content).
STEERING_TEXT = {
    "ru": ("Пользователь только что нажал кнопку «Продолжить», чтобы вернуться "
           "к этому разговору. Это действие не содержит новой информации от "
           "пользователя. Продолжи разговор естественно, с того места, где он "
           "остановился."),
    "en": ("The user just tapped the Continue button to return to this "
           "conversation. This action carries no new information from the "
           "user. Continue the conversation naturally from where it left off."),
}

_CONTEXTUAL_CONTINUE_RULES = {
    "ru": (
        "Особые правила для этого продолжения разговора:\n"
        "1. Пользователь явно выбрал продолжить предыдущий разговор.\n"
        "2. Нажатие кнопки не содержит новой психологической или фактической информации.\n"
        "3. Прошлые реплики с ролью user означают только: «пользователь раньше сказал это».\n"
        "4. Прошлые реплики с ролью assistant означают только: «ассистент раньше сказал/спросил это» "
        "— это НЕ подтверждённый факт о пользователе, даже если ассистент предполагал что-то о нём.\n"
        "5. Никогда не превращай прошлую догадку или интерпретацию ассистента в факт о пользователе.\n"
        "6. Если более поздняя реплика пользователя противоречит более ранней или уточняет её — "
        "побеждает более поздняя реплика.\n"
        "7. Никогда не придумывай отсутствующий контекст — тему, факт, мотив, диагноз, отношения, "
        "событие, чувство или намерение, которые не подтверждены репликами пользователя.\n"
        "8. Продолжай с самой релевантной недавней нерешённой точки разговора.\n"
        "9. Не пересказывай весь разговор без необходимости.\n"
        "10. Не утверждай, что пользователь сказал что-то, если это не подтверждено его репликами.\n"
        "11. Никакого диагноза.\n"
        "12. Никаких придуманных мотивов.\n"
        "13. Никакой ложной уверенности.\n"
        "14. Сделай один осмысленный разговорный шаг.\n"
        "15. Обычно задавай не больше одного вопроса.\n"
        "16. Спокойный, некатегоричный тон — это стиль подачи, а не повод дать пустой общий ответ.\n"
        "Никогда не упоминай слова «анкор», «контекстное окно», «сохранённая история», "
        "«Push V1» или «X20» пользователю."
    ),
    "en": (
        "Special rules for this conversation continuation:\n"
        "1. The user explicitly chose to continue the prior conversation.\n"
        "2. The button tap contains no new psychological or factual information.\n"
        "3. Prior user-role turns mean only: \"the user previously said this.\"\n"
        "4. Prior assistant-role turns mean only: \"the assistant previously said/asked this\" "
        "-- this is NOT a confirmed fact about the user, even if the assistant guessed something.\n"
        "5. Never promote a previous assistant guess or interpretation into a user fact.\n"
        "6. If a later user turn corrects or contradicts an earlier one, the later turn wins.\n"
        "7. Never invent missing context -- a topic, fact, motive, diagnosis, relationship, event, "
        "feeling, or intention not actually supported by the user's own turns.\n"
        "8. Continue from the most relevant recent unresolved point in the conversation.\n"
        "9. Do not unnecessarily summarize the entire conversation.\n"
        "10. Do not say the user said something unless their own turns actually support it.\n"
        "11. No diagnosis.\n"
        "12. No invented motives.\n"
        "13. No false certainty.\n"
        "14. Make one meaningful conversational move.\n"
        "15. Normally ask at most one question.\n"
        "16. A low-pressure tone is an entry style, not an excuse for an empty generic reply.\n"
        "Never mention the words \"anchor\", \"context window\", \"stored history\", "
        "\"Push V1\", or \"X20\" to the user."
    ),
}


def build_messages(conversation_context: ProfessionalConversationContext, lang: str) -> list:
    """Build the exact request messages for a Contextual Continue V1 call.
    Raises ValueError on a malformed or empty context -- callers must
    never call this (or generate_push_contextual_continue) with an empty
    context; the empty-context fallback decision belongs entirely to the
    caller (bot.py), before this function is ever reached."""
    if type(conversation_context) is not ProfessionalConversationContext:
        raise ValueError(
            f"build_messages: conversation_context must be a "
            f"ProfessionalConversationContext, got {type(conversation_context)!r}")
    if conversation_context.is_empty:
        raise ValueError("build_messages: conversation_context must not be empty")
    if lang not in ("ru", "en"):
        raise ValueError(f"build_messages: lang must be 'ru' or 'en', got {lang!r}")

    messages = [
        {"role": "system", "content": get_system_prompt("open_chat", lang)},
        {"role": "system", "content": _CONTEXTUAL_CONTINUE_RULES[lang]},
    ]
    for turn in conversation_context.turns:
        role = "user" if turn.role is ConversationTurnRole.USER else "assistant"
        messages.append({"role": role, "content": turn.content})
    messages.append({"role": "system", "content": STEERING_TEXT[lang]})
    return messages


async def generate_push_contextual_continue(
        *, client, model: str, conversation_context: ProfessionalConversationContext,
        lang: str, max_tokens: int) -> str:
    """Make exactly one OpenAI-compatible generation call over the trusted
    anchor-fenced context. Raises ValueError on a malformed context, a
    provider failure, or a provider response with no usable content --
    never returns a fallback string itself. `client` is injected (never
    constructed here), matching the convention every other generation
    call site in this repository already uses."""
    if (type(max_completion_tokens := max_tokens) is not int
            or not 1 <= max_completion_tokens <= 4096):
        raise ValueError("max_tokens must be an integer from 1 to 4096")
    messages = build_messages(conversation_context, lang)
    response = await client.chat.completions.create(
        model=model, messages=messages, n=1, temperature=0.65,
        max_tokens=max_completion_tokens,
    )
    choices = getattr(response, "choices", None)
    if not choices:
        raise ValueError("provider response has no choices")
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None)
    if type(content) is not str or not content.strip():
        raise ValueError("provider response has no usable content")
    return content.strip()
