"""Professional Core V2 -- Turn Response Fidelity Validator V1.

Deterministic, offline, SURFACE-fidelity validator only: given one already-
trusted ProfessionalTurnPlan and one candidate reply string, it checks a
finite set of frozen literal-text surface invariants and returns PASS or one
closed REJECT reason. It performs NO semantic comprehension.

PASS does not prove: semantic objective fidelity; semantic move fidelity
beyond the frozen surface proxies below; clarification-target fidelity; that
a "?" character represents a genuine semantic question; that zero "?"
characters means no implicit question/request exists in the text; source-
grounded reflection (this module never sees source_text); absence of a
second semantic move; clinical safety; absence of direct advice; absence of
an intervention; or absence of unsupported factual content. Those concerns
are either owned by separate components where explicitly defined (e.g.
objective/move/target selection and question_allowed derivation are already
owned by professional_turn_planner.govern_turn_plan), or remain explicit
pre-runtime gaps with no current owner -- semantic move fidelity beyond the
frozen surface proxies here, clarification-target fidelity, source-grounded
reflection, and absence of a second semantic move have no proving component
anywhere in this repository today. This module does not claim to resolve
any of them and does not call any other component.

Reason precedence is fixed and first-hit-wins (see validate_response_fidelity):
a candidate that violates multiple rules is reported under only the first
rule it violates in the frozen order, mirroring every other Professional
Core V2 result envelope in this codebase (single reason, never a set).

Two frozen surface limitations, by design, not oversight:
  - ASCII "?" only. Fullwidth/Arabic/other question punctuation is not
    counted in V1.
  - The list-marker check matches only a literal line-start marker pattern
    (digit/bullet + whitespace at the start of a line); inline enumeration
    ("firstly ..., secondly ...") is a known false negative, and an ordinary
    prose line that happens to start like a numbered item is a known false
    positive.

Candidate length is deliberately never re-checked here: a successful
TurnResponseRenderResult(status=CANDIDATE) already guarantees the Renderer's
own MAX_CANDIDATE_TEXT_CHARS bound. Duplicating that cap here would create a
second ownership point for the same invariant; the caller must not present
this validator with anything but an already-accepted Renderer candidate.

plan.move is checked against a frozen, explicit V1-supported move set before
any rule runs. The current ProfessionalTurnPlan constructor cannot itself
produce a move outside that set, so this can never fire against real V1
callers -- it exists purely as a fail-closed guard against a future Planner
version introducing a move this validator has not been updated to reason
about; such a mismatch is a caller/integration defect, not an ordinary
rejection, and raises ValueError rather than returning REJECT.

Only imports: __future__, re, dataclasses, enum, professional_turn_planner
(ProfessionalTurnPlan only), therapeutic_domain (PrimaryResponseMove only).
No model, no network, no DB, no persistence, no environment/config reads, no
Telegram, no runtime wiring. Python 3.10 target (prod 3.10.12).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from professional_turn_planner import ProfessionalTurnPlan
from therapeutic_domain import PrimaryResponseMove


class ResponseFidelityStatus(str, Enum):
    PASS = "PASS"
    REJECT = "REJECT"


class FidelityRejectionReason(str, Enum):
    QUESTION_MARK_NOT_ALLOWED = "QUESTION_MARK_NOT_ALLOWED"
    TOO_MANY_QUESTION_MARKS = "TOO_MANY_QUESTION_MARKS"
    FOCUSED_QUESTION_MARK_REQUIRED = "FOCUSED_QUESTION_MARK_REQUIRED"
    QUESTION_MARK_IN_NONQUESTION_MOVE = "QUESTION_MARK_IN_NONQUESTION_MOVE"
    LIST_MARKER_DETECTED = "LIST_MARKER_DETECTED"


@dataclass(frozen=True)
class ResponseFidelityResult:
    """status is always one of the two closed ResponseFidelityStatus members
    -- never canonicalized from an arbitrary string. reason is set if and
    only if status is REJECT."""
    status: ResponseFidelityStatus
    reason: FidelityRejectionReason | None

    def __post_init__(self):
        if not isinstance(self.status, ResponseFidelityStatus):
            raise ValueError(
                "ResponseFidelityResult.status must be a ResponseFidelityStatus, "
                f"got {type(self.status)!r}")
        if self.reason is not None and not isinstance(self.reason, FidelityRejectionReason):
            raise ValueError(
                "ResponseFidelityResult.reason must be None or a "
                f"FidelityRejectionReason, got {type(self.reason)!r}")
        if self.status is ResponseFidelityStatus.PASS:
            if self.reason is not None:
                raise ValueError("ResponseFidelityResult: status PASS requires reason=None")
        elif self.reason is None:
            raise ValueError("ResponseFidelityResult: status REJECT requires reason to be set")


# V1-supported moves only -- the exact set the frozen rules in
# validate_response_fidelity are written against. Deliberately NOT derived
# from PrimaryResponseMove's full membership, and deliberately not imported
# from professional_turn_planner's own private compatibility table: this is
# this module's own frozen scope declaration, independent of and narrower
# than either.
_SUPPORTED_FIDELITY_MOVES: frozenset[PrimaryResponseMove] = frozenset({
    PrimaryResponseMove.OPEN_INVITATION,
    PrimaryResponseMove.FOCUSED_QUESTION,
    PrimaryResponseMove.REFLECTIVE_STATEMENT,
    PrimaryResponseMove.CLOSING,
})

_NONQUESTION_MOVES: frozenset[PrimaryResponseMove] = frozenset({
    PrimaryResponseMove.REFLECTIVE_STATEMENT,
    PrimaryResponseMove.CLOSING,
})

# Frozen line-start structural marker only (matches the identical pattern
# already established in conversation_controller.py) -- never broadened into
# semantic list detection.
_LIST_MARKER_RE = re.compile(r"(?:^|\n)\s*(?:\d+[.)]|[-•*])\s+", re.MULTILINE)


def validate_response_fidelity(
        plan: ProfessionalTurnPlan, candidate_text: str) -> ResponseFidelityResult:
    """Deterministic, synchronous, offline. Evaluates the frozen V1 surface
    rules in exact precedence order; the first rule violated determines the
    single returned reason. Never inspects candidate_text length -- that
    bound is the Renderer's own, already-enforced responsibility."""
    if not isinstance(plan, ProfessionalTurnPlan):
        raise ValueError(
            "validate_response_fidelity: plan must be a ProfessionalTurnPlan, "
            f"got {type(plan)!r}")
    if type(candidate_text) is not str:
        raise ValueError(
            "validate_response_fidelity: candidate_text must be a str, "
            f"got {type(candidate_text)!r}")
    if not candidate_text:
        raise ValueError("validate_response_fidelity: candidate_text must be non-empty")
    if not candidate_text.strip():
        raise ValueError("validate_response_fidelity: candidate_text must not be whitespace-only")
    if plan.move not in _SUPPORTED_FIDELITY_MOVES:
        raise ValueError(
            "validate_response_fidelity: plan.move is not a V1-supported "
            f"fidelity move: {plan.move!r} -- this validator requires an "
            "explicit update before it can reason about a newer Planner move")

    qmarks = candidate_text.count("?")

    if not plan.question_allowed and qmarks > 0:
        return ResponseFidelityResult(
            status=ResponseFidelityStatus.REJECT,
            reason=FidelityRejectionReason.QUESTION_MARK_NOT_ALLOWED)

    if qmarks > 1:
        return ResponseFidelityResult(
            status=ResponseFidelityStatus.REJECT,
            reason=FidelityRejectionReason.TOO_MANY_QUESTION_MARKS)

    if plan.move is PrimaryResponseMove.FOCUSED_QUESTION and qmarks == 0:
        return ResponseFidelityResult(
            status=ResponseFidelityStatus.REJECT,
            reason=FidelityRejectionReason.FOCUSED_QUESTION_MARK_REQUIRED)

    if plan.move in _NONQUESTION_MOVES and qmarks > 0:
        return ResponseFidelityResult(
            status=ResponseFidelityStatus.REJECT,
            reason=FidelityRejectionReason.QUESTION_MARK_IN_NONQUESTION_MOVE)

    if _LIST_MARKER_RE.search(candidate_text):
        return ResponseFidelityResult(
            status=ResponseFidelityStatus.REJECT,
            reason=FidelityRejectionReason.LIST_MARKER_DETECTED)

    return ResponseFidelityResult(status=ResponseFidelityStatus.PASS, reason=None)
