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
parameter. ProfessionalTurnRuntimeContext is the single envelope every one of
those call sites accepts instead, so each new context kind is added as a new
field on this dataclass -- never as a new parameter on any of the functions
above.

V1 SCOPE (original slice) -- `conversation`, an unmodified
ProfessionalConversationContext (see professional_turn_conversation_context.py
for that type's own bounds and trust semantics -- nothing about that type
changes here); and `first_turn_entry_active`, a plain bool (default False) --
a governed, one-shot, current-turn-only signal meaning bot.py's existing
first-turn claim mechanism (database.claim_first_turn) succeeded for this
exact turn.

Owner-clarified semantics (Phase 1C correction pass): first_turn_entry_active
means ONLY "the one-shot First-Turn entry policy is active for this
Professional turn." It does NOT mean, and must never be treated as meaning,
that this is the user's first-ever message, that no previous conversation
exists, that no conversation history is available, or that the account/user
is new. A user may have earlier usable history and only now reach
Professional ownership (their claim is consumed on this later turn) --
`conversation` is populated from that real history exactly as on any other
turn; this field never forces it to an empty/None state and never licenses
the Renderer to assert "no earlier conversation" as fact (see
professional_turn_response_renderer.py's own first-turn entry-policy system
note). It carries no scenario, no legacy state, and no capacity/stage value.

PHASE 2A ADDITION -- `case_context`, a CanonicalCaseContext (default
EMPTY_CANONICAL_CASE_CONTEXT; see professional_case_context.py for that
type's own bounds, hard validation, and lifecycle rules). This is a
FOUNDATION slice only: the envelope now carries this field, and bot.py's
`_run_professional_free_text_and_deliver` now populates it from real,
already-CONFIRMED/CORRECTED core_memory_items rows -- but no model-facing
stage (call_turn_analyzer, call_turn_plan_proposer, render_turn_response)
reads it yet. Consumer wiring is explicitly deferred to a separately
authorized, separately reviewed Phase 2B. case_context must never be merged
into `conversation.turns` or any other field -- the raw-conversation trust
semantics documented in professional_turn_conversation_context.py (prior
turns are transport provenance only, never confirmed fact) stay structurally
separate from case_context's own, different trust level (only CONFIRMED/
CORRECTED memory, never a raw turn -- see professional_case_context.py's own
module docstring for its full trust contract, including that
MemoryCategory.HYPOTHESIS is never upgraded to a fact merely because its
lifecycle is CONFIRMED).

This module still does NOT itself generate, confirm, or persist any memory
content -- it only carries an already-built, already-validated
CanonicalCaseContext value constructed elsewhere. Out of scope here (and for
professional_case_context.py): generating new memory candidates, automatic
confirmation, user confirmation/correction UX, governed case-concept
persistence integration, and any model-facing consumption of case_context --
each requires its own separately authorized owner decision and
architecture-gate review.

This module does NOT define, store, or reserve a schema for:
  - interventions or outcomes;
  - therapy lines or stages;
  - a user profile or latent profile of any kind.
Any of the above is out of scope for this slice and requires its own
separately authorized owner decision and its own architecture-gate review
before a corresponding field is ever added here.

FUTURE EXTENSION POINT -- any further context kind is added as another new,
independently-typed, defaulted field on this same dataclass -- never as a
new parameter on any of the stage functions above, and never merged into an
existing field. Only the stage(s) that a future, separately-approved slice
decides should consume a given field need to change their own function
bodies to read it off the already-received envelope -- no stage signature
changes again, because every stage already receives the whole envelope.

Only imports: __future__, dataclasses, professional_turn_conversation_
context.ProfessionalConversationContext, and professional_case_context.
CanonicalCaseContext / EMPTY_CANONICAL_CASE_CONTEXT. No bot.py import, no
database import, no Telegram import, no model/network import. Python 3.10
target (prod 3.10.12).
"""
from __future__ import annotations

from dataclasses import dataclass

from professional_case_context import CanonicalCaseContext, EMPTY_CANONICAL_CASE_CONTEXT
from professional_turn_conversation_context import ProfessionalConversationContext


@dataclass(frozen=True)
class ProfessionalTurnRuntimeContext:
    """The single typed context parameter threaded through the Professional
    Free-Text Runtime chain. Three fields -- see the module docstring's V1
    SCOPE and PHASE 2A ADDITION sections. Fails closed (raises ValueError)
    rather than silently coercing or wrapping a value of the wrong type."""
    conversation: ProfessionalConversationContext
    first_turn_entry_active: bool = False
    case_context: CanonicalCaseContext = EMPTY_CANONICAL_CASE_CONTEXT

    def __post_init__(self):
        if type(self.conversation) is not ProfessionalConversationContext:
            raise ValueError(
                "ProfessionalTurnRuntimeContext.conversation must be exactly a "
                f"ProfessionalConversationContext, got {type(self.conversation)!r}")
        if type(self.first_turn_entry_active) is not bool:
            raise ValueError(
                "ProfessionalTurnRuntimeContext.first_turn_entry_active must be "
                f"exactly bool, got {type(self.first_turn_entry_active)!r}")
        if type(self.case_context) is not CanonicalCaseContext:
            raise ValueError(
                "ProfessionalTurnRuntimeContext.case_context must be exactly a "
                f"CanonicalCaseContext, got {type(self.case_context)!r}")
