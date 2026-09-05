"""Professional Core V2 -- Runtime Context Envelope V1.

OFFLINE DOMAIN CONTRACT ONLY. This module defines the single typed context
parameter threaded through the Professional Free-Text Runtime chain (bot.py
-> run_professional_free_text_turn -> call_turn_analyzer ->
call_turn_plan_proposer -> render_turn_response). It performs no I/O of any
kind: no network call, no model call, no database access, no Telegram
delivery, no environment reads, no secret access, no filesystem access, no
time/random behavior.

WHY THIS EXISTS -- before this module, call_turn_analyzer,
call_turn_plan_proposer, and render_turn_response each accepted an optional
`conversation_context: ProfessionalConversationContext | None` directly, and
run_professional_free_text_turn accepted the same raw type as a required
parameter. That is fine for exactly one kind of context (prior conversation
turns), but a future authorized slice that adds a governed Canonical Case
Context would otherwise require touching every one of those signatures again.
ProfessionalTurnRuntimeContext is the single envelope every one of those call
sites accepts instead, so a future context kind is added as a new field on
this dataclass -- never as a new parameter on any of the functions above.

V1 SCOPE -- deliberately narrow. The envelope carries exactly one field
today, `conversation`, an unmodified ProfessionalConversationContext (see
professional_turn_conversation_context.py for that type's own bounds and
trust semantics -- nothing about that type changes here). This module does
NOT define, store, or reserve a schema for:
  - confirmed facts or corrections;
  - hypotheses or case conceptualization;
  - memory lifecycle (CANDIDATE/PROPOSED/CONFIRMED/etc.);
  - interventions or outcomes;
  - therapy lines or stages.
Any of the above is out of scope for this slice and requires its own
separately authorized owner decision and its own architecture-gate review
before a corresponding field is ever added here.

FUTURE EXTENSION POINT -- a governed Canonical Case Context, once separately
authorized, is added as a new, independently-typed field on this same
dataclass (e.g. `case_context: CanonicalCaseContext | None = None`, defaulted
so no existing constructor call anywhere breaks). It must never be merged
into `conversation.turns` or any other existing field -- the raw-conversation
trust semantics documented in professional_turn_conversation_context.py
(prior turns are transport provenance only, never confirmed fact) must stay
structurally separate from whatever higher-trust lifecycle a future case
context field carries. Only the stage(s) that a future, separately-approved
slice decides should consume that field need to change their own function
bodies to read it off the already-received envelope -- no stage signature
changes again, because every stage already receives the whole envelope.

Only imports: __future__, dataclasses, and
professional_turn_conversation_context.ProfessionalConversationContext. No
bot.py import, no database import, no Telegram import, no model/network
import. Python 3.10 target (prod 3.10.12).
"""
from __future__ import annotations

from dataclasses import dataclass

from professional_turn_conversation_context import ProfessionalConversationContext


@dataclass(frozen=True)
class ProfessionalTurnRuntimeContext:
    """The single typed context parameter threaded through the Professional
    Free-Text Runtime chain. Exactly one field in this V1 slice -- see the
    module docstring's V1 SCOPE and FUTURE EXTENSION POINT sections. Fails
    closed (raises ValueError) rather than silently coercing or wrapping a
    value of the wrong type."""
    conversation: ProfessionalConversationContext

    def __post_init__(self):
        if type(self.conversation) is not ProfessionalConversationContext:
            raise ValueError(
                "ProfessionalTurnRuntimeContext.conversation must be exactly a "
                f"ProfessionalConversationContext, got {type(self.conversation)!r}")
