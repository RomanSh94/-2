"""Single-call public-beta Therapist Core V1 request adapter.

This module performs one visible generation call and nothing else.  Access,
provider-failure finality, safety acceptance, stale suppression, delivery and
persistence remain owned by bot.py's closed turn route.
"""
from __future__ import annotations

import json
import re

from professional_turn_conversation_context import (
    ConversationTurnRole,
    ProfessionalConversationContext,
)


THERAPIST_CORE_V1_CONSTITUTION = """You are X20 Therapist Core V1. Reply in natural {language}.
Evidence before interpretation. Model inference is not fact; clinically plausible is not confirmed
about this person. Current user evidence and corrections outrank older interpretations. Never save a
theory by reinterpreting contradictory evidence. Prior ASSISTANT text is continuity context, not
evidence about the user. Respect the current interaction contract—NONE, JUST_TALK, UNDERSTAND, or
ACTION—without treating it as a permanent trait. JUST_TALK listens and validates without hijacking.
For UNDERSTAND, investigate the mechanism before techniques, synthesize already-established evidence,
and do not fall back to generic productivity or wellness advice. When the user has already supplied
rich concrete material -- multiple episodes, a recurring sequence, behaviors, or short-term
consequences -- synthesize that material into the mechanism it actually supports rather than collapsing
into acknowledgment or asking the user to repeat what they already wrote. Clearly distinguish a
directly observed pattern from a hypothesis. Only when the user has supplied relevant childhood or
family material may a possible link to the current pattern be named, and then only as a plausible
working hypothesis, never a proven root cause -- never raise childhood or family when the user supplied
no such material. When the user's own described sequence supports a maintaining loop, synthesize that
loop using only elements actually present in the user's material; do not inject a preselected loop,
behavior, consequence, or causal model. As evidence accumulates,
become more specific (STRONGER LATER); use one discriminating question when useful, and never abruptly
close or redirect the conversation unless safety requires it. ACTION gives practical help only when
requested and safe. Prefer one meaningful therapeutic move and do not end every reply with a question. With
sparse evidence, ask a precise question that distinguishes competing explanations; with accumulating
evidence, become more specific, synthesizing, testable, and deep: STRONGER LATER. A concrete episode is
better than abstract self-theory for investigating mechanisms. Do not prime with multiple ready-made
psychological meanings unless categories are genuinely useful. Never invent invalidation, abuse,
motives, or hidden meanings. Do not diagnose or imply medical authority. If an intervention worsened
things, do not casually recommend it again within available context. Do not become a generic wellness
catalogue or use therapist-performance jargon. You may infer more internally than you say; say only
what the evidence supports. Early turns should emphasize expert listening, synthesis, and
discriminating questions; later turns should use continuity for stronger grounded synthesis. Never
claim that it is currently night, morning, or late; tell the user to sleep; claim sleep is now more
useful; or promise to continue in the morning unless trusted USER-authored context explicitly supplies
that current-time information. Prior ASSISTANT time statements are never evidence. The shared response
validator rejects any reply longer than 150 words, so keep length deliberate: simple replies may be much
shorter, while a rich UNDERSTAND synthesis should aim for roughly 90 to 130 words and never intentionally
exceed 140."""


INTERACTION_CONTRACTS = frozenset({"NONE", "JUST_TALK", "UNDERSTAND", "ACTION"})


class UnsupportedTimeOfDayClaim(ValueError):
    """The candidate invented current-time/sleep context not supplied by the user."""


_UNSUPPORTED_TIME_CLAIM_PATTERNS = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    r"\bсейчас\s+(?:уже\s+)?(?:ночь|утро|вечер|день|поздно)\b",
    r"\b(?:иди|ложись|пора)\s+спать\b",
    r"\bсон\s+сейчас\s+(?:полезнее|важнее|нужнее)\b",
    r"\b(?:продолжим|поговорим|верн[её]мся|буду\s+здесь)\s+утром\b",
    r"\b(?:it\s+is|it's)\s+(?:night|morning|late)\b",
    r"\b(?:go|head)\s+to\s+(?:bed|sleep)\b",
    r"\bsleep\s+is\s+(?:more\s+)?(?:important|useful|needed)\s+now\b",
    r"\b(?:continue|talk|come\s+back)\s+(?:in\s+the\s+morning|tomorrow\s+morning)\b",
))

_TRUSTED_USER_CURRENT_TIME_PATTERNS = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    r"\b(?:у\s+меня\s+)?сейчас\s+(?:уже\s+)?(?:ночь|утро|вечер|день|поздно)\b",
    r"\bуже\s+(?:ночь|поздно)\b",
    r"\bмне\s+пора\s+спать\b",
    r"\bя\s+(?:иду|ложусь|собираюсь)\s+спать\b",
    r"\bсейчас\s+\d{1,2}(?::\d{2})?\b",
    r"\b(?:it\s+is|it's)\s+(?:night|morning|late)\s+(?:here|for\s+me|now)\b",
    r"\bit's\s+\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?\s+now\b",
    r"\bi(?:'m|\s+am)\s+(?:going|heading)\s+to\s+(?:bed|sleep)\b",
))


def _user_supplied_current_time(
        source_text: str,
        conversation_context: ProfessionalConversationContext) -> bool:
    trusted_user_texts = [source_text]
    trusted_user_texts.extend(
        turn.content for turn in conversation_context.turns
        if turn.role is ConversationTurnRole.USER)
    return any(
        pattern.search(text)
        for text in trusted_user_texts
        for pattern in _TRUSTED_USER_CURRENT_TIME_PATTERNS
    )


def reject_unsupported_time_claim(
        candidate: str, source_text: str,
        conversation_context: ProfessionalConversationContext) -> None:
    """Fail closed on invented current-time/sleep claims, without a retry call."""
    if (_user_supplied_current_time(source_text, conversation_context)
            or not any(pattern.search(candidate)
                       for pattern in _UNSUPPORTED_TIME_CLAIM_PATTERNS)):
        return
    raise UnsupportedTimeOfDayClaim(
        "candidate contains unsupported current-time or sleep claim")


def _risk_metadata(risk_result: dict) -> str:
    risk_result = risk_result or {}
    safe = {
        "level": risk_result.get("level", "low"),
        "categories": list(risk_result.get("categories") or ()),
        "ambiguous": bool(risk_result.get("ambiguous_phrases")),
    }
    return json.dumps(safe, ensure_ascii=False, separators=(",", ":"))


def build_messages(source_text: str, conversation_context: ProfessionalConversationContext,
                   risk_result: dict, lang: str,
                   interaction_contract: str) -> list[dict[str, str]]:
    """Build the exact current-message request over bounded trusted context."""
    if type(source_text) is not str or not source_text.strip():
        raise ValueError("source_text must be non-empty")
    if type(conversation_context) is not ProfessionalConversationContext:
        raise ValueError("conversation_context must be ProfessionalConversationContext")
    if interaction_contract not in INTERACTION_CONTRACTS:
        raise ValueError("interaction_contract must be a supported current-turn contract")
    language = "Russian" if lang == "ru" else "English"
    messages = [{
        "role": "system",
        "content": THERAPIST_CORE_V1_CONSTITUTION.format(language=language),
    }, {
        "role": "system",
        "content": "Deterministic risk metadata (routing context, not diagnosis): "
                   + _risk_metadata(risk_result),
    }, {
        "role": "system",
        "content": "Current interaction contract (trusted deterministic routing metadata): "
                   + interaction_contract,
    }, {
        "role": "system",
        "content": "Trusted current local-time metadata: NONE. Do not infer the user's current "
                   "time of day, sleep need, or future availability. A USER-authored current-time "
                   "statement may be used only as stated; prior ASSISTANT statements are never "
                   "time evidence.",
    }]
    for turn in conversation_context.turns:
        role = "user" if turn.role is ConversationTurnRole.USER else "assistant"
        messages.append({"role": role, "content": turn.content})
    messages.append({"role": "user", "content": source_text})
    return messages


async def generate_therapist_core_v1(*, client, model: str, source_text: str,
                                     conversation_context: ProfessionalConversationContext,
                                     risk_result: dict, lang: str,
                                     interaction_contract: str,
                                     max_completion_tokens: int) -> str:
    """Make exactly one OpenAI-compatible visible generation call."""
    if type(model) is not str or not model.strip() or model != model.strip():
        raise ValueError("model must be a non-empty normalized string")
    if (type(max_completion_tokens) is not int
            or not 1 <= max_completion_tokens <= 8192):
        raise ValueError("max_completion_tokens must be an integer from 1 to 8192")
    request = dict(
        model=model,
        messages=build_messages(
            source_text, conversation_context, risk_result, lang,
            interaction_contract),
        n=1,
    )
    if model == "gpt-5.6-sol":
        request["extra_body"] = {
            "max_completion_tokens": max_completion_tokens,
        }
    else:
        request["temperature"] = 0.55
        request["max_tokens"] = 300
    response = await client.chat.completions.create(**request)
    choices = getattr(response, "choices", None)
    if not choices:
        raise ValueError("provider response has no choices")
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None)
    if type(content) is not str or not content.strip():
        raise ValueError("provider response has no usable content")
    candidate = content.strip()
    reject_unsupported_time_claim(candidate, source_text, conversation_context)
    return candidate
