"""Professional Core V2 -- Trusted UI Immediate Response V1 (offline control-response adapter).

OFFLINE DOMAIN/GOVERNANCE MODULE ONLY. This module produces the immediate,
deterministic assistant response copy for an already-trusted UI directive
(professional_turn_ui_context.TrustedEntryTriageDirective or
TrustedReplyDirective). It performs no I/O of any kind: no network call, no
model call, no database access, no Telegram delivery, no environment reads,
no secret access, no filesystem access, no time/random behavior. It is not
imported by bot.py, is not wired into conversation_controller.py, and
registers no callback handler. There is no runtime wiring in this slice.

WHY THIS SLICE EXISTS -- Trusted UI Action Context V1 established that a
button tap (ENTRY_TRIAGE_SELECTION, NEEDS_SCAFFOLDING,
SKIP_CURRENT_QUESTION) is a trusted structured UI event, never user free
text. That earlier slice did not produce an immediate assistant response to
those events. This module fills exactly that gap, and only that gap: it
does not synthesize a fake user utterance, does not feed a UI selection
through an Analyzer, does not manufacture Intent or Evidence, and does not
force a reply action into a ProfessionalTurnPlan that would not truthfully
represent it. In particular SKIP_EXACT_CURRENT_QUESTION is never
represented as InteractionSignal.NO_QUESTIONS, RepairConstraint.
QUESTION_OVERLOAD, a REPAIR objective, a CLOSE objective, fake
ESTABLISH_CONTACT semantics, or the same question repeated/paraphrased.
This is a small, deterministic control-response path; a future, separately
authorized runtime slice may route the user's next genuine free text back
into the ordinary Analyzer -> Proposer -> Planner -> Renderer -> Acceptance
pipeline, but that routing is not implemented here.

RESPONSE-PATH BOUNDARY -- these immediate UI-control responses are not
ProfessionalTurnPlan outputs, are not model-rendered candidates, and this
module never calls govern_turn_plan, render_turn_response, or
accept_professional_response. accept_professional_response validates a
response relative to a real ProfessionalTurnPlan; forcing a UI-control
response through it against a fabricated plan would be semantically wrong,
so this module deliberately does not do that. A future runtime integration
must still apply the live deterministic top-level safety/delivery gates
before any user delivery -- this slice does not implement that delivery.

OWNER-DIRECTED SEALED COPY -- the Russian response strings in this module
are the exact V1 product-voice contract for this control-response slice.
Review workflow state is intentionally not encoded here. Structural
properties are exact provenance binding, exact per-response question shape,
and no Intent/Evidence creation from UI control events.

ENTRY RESPONSE SEMANTIC LIMITS -- an entry-triage immediate response is a
low-pressure invitation, never a diagnosis, never a claim that the selected
category is established fact about the user, never a cause claim, never a
therapeutic method or exercise, never advice, and never a forced question
requiring a structured answer. It invites the user's own free text as the
next primary channel. The response text is selected exclusively from
directive.followup_focus (professional_reply_affordances.
EntryFollowupFocus) -- this module does not duplicate the category ->
followup_focus mapping; that mapping already lives in
professional_reply_affordances.followup_focus_for_category and is reused
via the already-canonicalized TrustedEntryTriageDirective.

SCAFFOLD RESPONSE -- TrustedReplyDirectiveKind.SCAFFOLD_CURRENT_TARGET means
the exact prior professional target remains available, but the user needs a
lower-effort way to respond. This module never calls the Planner, never
constructs a new ProfessionalTurnPlan, never mutates prior_plan, and never
manufactures an answer on the user's behalf. Planner V1 can only offer reply
affordances from a trusted FOCUSED_QUESTION plan, so the only prior
objectives reachable here are CLARIFY and CLARIFY_GOAL; any other prior
objective reaching this function fails closed with ValueError rather than
falling back to a generic response.

SKIP RESPONSE -- TrustedReplyDirectiveKind.SKIP_EXACT_CURRENT_QUESTION
produces one sealed response that acknowledges only the exact skipped
question. It does not ask why, does not repeat the question, does not
paraphrase the question, does not infer avoidance, does not infer trauma,
does not infer resistance, does not end the conversation, and does not
reject the broader professional target.

QUESTION SHAPE CONTRACT -- question shape belongs to the exact sealed copy,
not to a module-wide rule. Some sealed responses (the five episode-focused
entry responses, and the CLARIFY_GOAL scaffold) contain exactly one direct
"?"; the rest (LOW_PRESSURE_OPENING, the CLARIFY scaffold, and SKIP)
contain none. No sealed response ever contains "？" (fullwidth question
mark). An arbitrary caller-authored question remains impossible regardless
of this shape: TrustedUiImmediateResponse's constructor still requires
text_ru to exactly equal the sealed response for source_directive (see
FAIL-CLOSED CONSTRUCTION below), so the presence or absence of "?" is
never a caller choice. A question mark appearing in a sealed response is
only that response's copy shape -- it is not, and must never be read as,
InteractionSignal.NO_QUESTIONS, and it never triggers any Planner decision.

FAIL-CLOSED CONSTRUCTION -- TrustedUiImmediateResponse's public constructor
recomputes the exact sealed response text for the supplied source_directive
and requires text_ru to equal it exactly. A caller can therefore never
construct a trusted-looking response object carrying arbitrary copy, copy
for the wrong directive, or scaffold/skip copy swapped with each other --
every mismatch fails closed with ValueError. build_trusted_ui_immediate_
response is the only sanctioned factory, and only ever produces the
already-verified sealed pairing.

NO SYNTHESIS -- there is no API here shaped like a conversion from a UI
directive into user speech (no to_user_text, no as_user_message, no
synthetic_source_text, no category_to_text, no action_to_text). Every
output is assistant response copy only, and must never be treated as
something the user said.

Only imports: __future__, dataclasses,
professional_turn_ui_context (TrustedEntryTriageDirective,
TrustedReplyDirective, TrustedReplyDirectiveKind),
professional_reply_affordances (EntryFollowupFocus), and
therapeutic_domain (ProfessionalObjective). No bot.py import, no
conversation_controller.py import, no Analyzer/Planner/Renderer/Acceptance
import, no Telegram import, no database import.

Python 3.10 target (prod 3.10.12): `str, Enum` mix-ins, not `StrEnum` (3.11+).
"""
from __future__ import annotations

from dataclasses import dataclass

from professional_reply_affordances import EntryFollowupFocus
from professional_turn_ui_context import (
    TrustedEntryTriageDirective,
    TrustedReplyDirective,
    TrustedReplyDirectiveKind,
)
from therapeutic_domain import ProfessionalObjective


# ── Sealed V1 owner-directed response copy -----------------------------------

_ENTRY_RESPONSE_TEXT_BY_FOCUS: dict[EntryFollowupFocus, str] = {
    EntryFollowupFocus.RECENT_HIGH_INTENSITY_EPISODE:
        "Вспомни последний момент, когда тревога или напряжение стали особенно сильными. Что тогда происходило?",
    EntryFollowupFocus.RECENT_RELATIONAL_EPISODE:
        "Вспомни недавний момент — в отношениях или когда особенно чувствовалось одиночество. Что произошло?",
    EntryFollowupFocus.RECENT_FUNCTIONING_CHANGE:
        "Что в последнее время стало даваться тяжелее, хотя раньше было проще?",
    EntryFollowupFocus.RECENT_SELF_CRITICISM_EPISODE:
        "Вспомни недавний момент, когда ты особенно сильно себя критиковал. Что тогда случилось?",
    EntryFollowupFocus.DIFFICULT_EMOTION_AND_ANTECEDENT:
        "Какая эмоция сейчас особенно сильная — и что происходило прямо перед ней?",
    EntryFollowupFocus.LOW_PRESSURE_OPENING:
        "Тогда тему выбирать не нужно. Напиши первую мысль, которая сейчас крутится в голове.",
}

_CLARIFY_SCAFFOLD_RESPONSE_TEXT_RU = (
    "Не ищи идеальный ответ. Напиши первую деталь, которая приходит в голову."
)

_CLARIFY_GOAL_SCAFFOLD_RESPONSE_TEXT_RU = (
    "Не обязательно сразу понимать точную цель. Что хотелось бы изменить "
    "хотя бы немного после этого разговора?"
)

_SKIP_RESPONSE_TEXT_RU = (
    "Этот вопрос пропустим. Можешь просто продолжить с того, что сейчас у "
    "тебя в голове."
)


def _expected_response_text(
        directive: TrustedEntryTriageDirective | TrustedReplyDirective) -> str:
    """The single source of truth for the sealed V1 response text belonging
    to one trusted directive. Fails closed (ValueError) on any directive
    shape this module does not have a sealed V1 response for -- there is no
    generic fallback."""
    if isinstance(directive, TrustedEntryTriageDirective):
        return _ENTRY_RESPONSE_TEXT_BY_FOCUS[directive.followup_focus]
    if isinstance(directive, TrustedReplyDirective):
        if directive.kind is TrustedReplyDirectiveKind.SKIP_EXACT_CURRENT_QUESTION:
            return _SKIP_RESPONSE_TEXT_RU
        if directive.kind is TrustedReplyDirectiveKind.SCAFFOLD_CURRENT_TARGET:
            objective = directive.prior_plan.objective
            if objective is ProfessionalObjective.CLARIFY:
                return _CLARIFY_SCAFFOLD_RESPONSE_TEXT_RU
            if objective is ProfessionalObjective.CLARIFY_GOAL:
                return _CLARIFY_GOAL_SCAFFOLD_RESPONSE_TEXT_RU
            raise ValueError(
                "no sealed V1 scaffold response for prior_plan.objective="
                f"{objective!r}")
        raise ValueError(f"no sealed V1 response for TrustedReplyDirectiveKind={directive.kind!r}")
    raise ValueError(
        "_expected_response_text requires a TrustedEntryTriageDirective or "
        f"TrustedReplyDirective, got {type(directive)!r}")


@dataclass(frozen=True)
class TrustedUiImmediateResponse:
    """The trusted, closed immediate response to one already-trusted UI
    directive. No arbitrary metadata, no reason string, no instruction
    string, no source_text, no Intent, no Evidence, no plan, no Telegram/
    callback data -- only the source directive and the sealed response
    text. The public constructor recomputes the exact expected sealed
    response for source_directive and requires text_ru to equal it exactly,
    so a caller can never construct this type carrying arbitrary,
    mismatched, or swapped copy."""
    source_directive: TrustedEntryTriageDirective | TrustedReplyDirective
    text_ru: str

    def __post_init__(self):
        if not isinstance(self.source_directive, (TrustedEntryTriageDirective, TrustedReplyDirective)):
            raise ValueError(
                "TrustedUiImmediateResponse.source_directive must be a "
                "TrustedEntryTriageDirective or TrustedReplyDirective, got "
                f"{type(self.source_directive)!r}")
        if not isinstance(self.text_ru, str) or not self.text_ru:
            raise ValueError("TrustedUiImmediateResponse.text_ru must be a non-empty str")
        expected = _expected_response_text(self.source_directive)
        if self.text_ru != expected:
            raise ValueError(
                "TrustedUiImmediateResponse.text_ru does not match the "
                "sealed V1 response for source_directive")


def build_trusted_ui_immediate_response(
        directive: TrustedEntryTriageDirective | TrustedReplyDirective,
) -> TrustedUiImmediateResponse:
    """The only sanctioned factory. Produces the sealed V1 immediate
    response for an already-trusted UI directive. Fails closed
    (ValueError) on any other input type -- an untrusted transport value,
    a raw enum member, a dict, a plain string, or None."""
    if not isinstance(directive, (TrustedEntryTriageDirective, TrustedReplyDirective)):
        raise ValueError(
            "build_trusted_ui_immediate_response requires a "
            "TrustedEntryTriageDirective or TrustedReplyDirective, got "
            f"{type(directive)!r}")
    text = _expected_response_text(directive)
    return TrustedUiImmediateResponse(source_directive=directive, text_ru=text)
