"""Single-call public-beta Therapist Core V1 request adapter.

This module performs one visible generation call and nothing else.  Access,
provider-failure finality, safety acceptance, stale suppression, delivery and
persistence remain owned by bot.py's closed turn route.
"""
from __future__ import annotations

import json

from professional_turn_conversation_context import (
    ConversationTurnRole,
    ProfessionalConversationContext,
)


THERAPIST_CORE_V1_CONSTITUTION = """You are X20 Therapist Core V1. Reply in natural {language}.
Evidence before interpretation. Model inference is not fact; clinically plausible is not confirmed
about this person. Current user evidence and corrections outrank older interpretations. Never save a
theory by reinterpreting contradictory evidence. Prior ASSISTANT text is continuity context, not
evidence about the user. Respect the current interaction contract—JUST_TALK, UNDERSTAND, or ACTION—
without treating it as a permanent trait. JUST_TALK listens and validates without hijacking;
UNDERSTAND investigates mechanism before techniques; ACTION gives practical help only when requested
and safe. Prefer one meaningful therapeutic move and do not end every reply with a question. With
sparse evidence, ask a precise question that distinguishes competing explanations; with accumulating
evidence, become more specific, synthesizing, testable, and deep: STRONGER LATER. A concrete episode is
better than abstract self-theory for investigating mechanisms. Do not prime with multiple ready-made
psychological meanings unless categories are genuinely useful. Never invent invalidation, abuse,
motives, or hidden meanings. Do not diagnose or imply medical authority. If an intervention worsened
things, do not casually recommend it again within available context. Do not become a generic wellness
catalogue or use therapist-performance jargon. You may infer more internally than you say; say only
what the evidence supports. Early turns should emphasize expert listening, synthesis, and
discriminating questions; later turns should use continuity for stronger grounded synthesis."""


def _risk_metadata(risk_result: dict) -> str:
    risk_result = risk_result or {}
    safe = {
        "level": risk_result.get("level", "low"),
        "categories": list(risk_result.get("categories") or ()),
        "ambiguous": bool(risk_result.get("ambiguous_phrases")),
    }
    return json.dumps(safe, ensure_ascii=False, separators=(",", ":"))


def build_messages(source_text: str, conversation_context: ProfessionalConversationContext,
                   risk_result: dict, lang: str) -> list[dict[str, str]]:
    """Build the exact current-message request over bounded trusted context."""
    if type(source_text) is not str or not source_text.strip():
        raise ValueError("source_text must be non-empty")
    if type(conversation_context) is not ProfessionalConversationContext:
        raise ValueError("conversation_context must be ProfessionalConversationContext")
    language = "Russian" if lang == "ru" else "English"
    messages = [{
        "role": "system",
        "content": THERAPIST_CORE_V1_CONSTITUTION.format(language=language),
    }, {
        "role": "system",
        "content": "Deterministic risk metadata (routing context, not diagnosis): "
                   + _risk_metadata(risk_result),
    }]
    for turn in conversation_context.turns:
        role = "user" if turn.role is ConversationTurnRole.USER else "assistant"
        messages.append({"role": role, "content": turn.content})
    messages.append({"role": "user", "content": source_text})
    return messages


async def generate_therapist_core_v1(*, client, model: str, source_text: str,
                                     conversation_context: ProfessionalConversationContext,
                                     risk_result: dict, lang: str,
                                     max_completion_tokens: int) -> str:
    """Make exactly one OpenAI-compatible visible generation call."""
    if type(model) is not str or not model.strip() or model != model.strip():
        raise ValueError("model must be a non-empty normalized string")
    if (type(max_completion_tokens) is not int
            or not 1 <= max_completion_tokens <= 8192):
        raise ValueError("max_completion_tokens must be an integer from 1 to 8192")
    request = dict(
        model=model,
        messages=build_messages(source_text, conversation_context, risk_result, lang),
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
    return content.strip()
