"""Therapeutic Conversation Core — pure domain vocabulary (Phase 1).

Deliberately named *_domain*, not *_core*: this module is ONLY typed
vocabulary — dataclasses and enums — for the governed Core (master prompt
§15). It contains NO I/O, NO database access, NO LLM calls, NO Telegram
delivery, NO feature-flag reads, and NO pipeline integration. Its only
imports are `__future__`, `dataclasses`, `enum`, and `typing` (enforced by
tests/test_therapeutic_domain_purity.py's import-allowlist check).

This is why it is safe to sit on tests/test_clinical_boundary.py's
LATENT_ALLOWED_FILES: it *defines* the `Formulation` shape, it never *reads*
one as a live control signal. A future module that adds runtime behavior
(session controller, method selector, LLM calls, delivery) MUST live in a
differently-named file and MUST NOT be added here or folded into the
allowlist entry for this file — see that test file's purity check, which
would fail the moment this module imports anything beyond the four stdlib
names above.

database.py serializes these models into the additive core_* tables from
Phase 1 onward. As of Phase 3, bot.pipeline() reads/writes SessionState
through database.py's accessors (never this module directly) -- the
Conversation Controller runtime lives in conversation_controller.py, not
here; this module still stays pure vocabulary only.

Two hard rules from the spec are enforced here:

  * Session **phase** and session **lifecycle status** are SEPARATE axes
    (§15.3) — a session in phase INTERVENE can be OPEN or PAUSED; do not
    collapse them.
  * Unknown enum values FAIL validation (§15.2). Deserialising malformed
    persisted/model state raises `ValueError`; the deterministic safe fallback
    lives at the call boundary, never by silently coercing a bad value.

Python 3.10 target (prod 3.10.12): `str, Enum` mix-ins, not `StrEnum` (3.11+).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── Enumerated vocabularies ──────────────────────────────────────────────────
# All are `(str, Enum)` so their `.value` is JSON-native and `Cls(bad)` raises
# ValueError — the validation hook we rely on for persisted/LLM-sourced state.

class SessionPhase(str, Enum):
    OPENING = "OPENING"
    UNDERSTAND = "UNDERSTAND"
    FORMULATE = "FORMULATE"
    CONFIRM_FORMULATION = "CONFIRM_FORMULATION"
    SET_GOAL = "SET_GOAL"
    STABILIZE = "STABILIZE"
    INTERVENE = "INTERVENE"
    REVIEW_OUTCOME = "REVIEW_OUTCOME"
    INTEGRATE = "INTEGRATE"
    ACTION_PLAN = "ACTION_PLAN"
    REPAIR = "REPAIR"
    CLOSE = "CLOSE"


class LifecycleStatus(str, Enum):
    OPEN = "OPEN"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class Intent(str, Enum):
    """Why the user wrote *this* turn. Covers §10.1's full request list plus
    the continuity paths named across §7/§14/Phase-9's canary script
    (questionnaire discussion, journal work, problem solving, resuming
    unfinished work, relationship support, closure) — not just the Phase 3
    minimum 7. This is a versioned, extensible vocabulary, not a permanent
    boundary: a later phase adding a new continuity path adds a member here,
    it does not fork a second Intent-like enum. UNKNOWN is the explicit
    safe-fallback sentinel — never guessed silently by a model; crisis intent
    is deliberately absent because the deterministic safety layer (CLAUDE.md
    pipeline stage 3) resolves and stops the pipeline before the Core ever
    sees the turn."""
    VENT = "VENT"
    EXPLAIN = "EXPLAIN"
    ACTION = "ACTION"
    CHANGE_PATTERN = "CHANGE_PATTERN"
    DECISION_SUPPORT = "DECISION_SUPPORT"
    PRACTICE = "PRACTICE"
    REPAIR = "REPAIR"
    PROBLEM_SOLVING = "PROBLEM_SOLVING"
    RELATIONSHIP_SUPPORT = "RELATIONSHIP_SUPPORT"
    JOURNAL_WORK = "JOURNAL_WORK"
    QUESTIONNAIRE_DISCUSSION = "QUESTIONNAIRE_DISCUSSION"
    CONTINUE_PREVIOUS_WORK = "CONTINUE_PREVIOUS_WORK"
    CLOSE_CONVERSATION = "CLOSE_CONVERSATION"
    UNKNOWN = "UNKNOWN"


class ConsentState(str, Enum):
    """Consent for a structured intervention (§10.7 / §15.6). Default ABSENT —
    a structured intervention requires GRANTED, checked deterministically."""
    ABSENT = "ABSENT"
    PENDING = "PENDING"
    GRANTED = "GRANTED"
    DECLINED = "DECLINED"
    WITHDRAWN = "WITHDRAWN"


class RepairConstraint(str, Enum):
    """Persist across several turns after user criticism (§16)."""
    BOT_REPEATS = "BOT_REPEATS"
    ADVICE_REJECTED = "ADVICE_REJECTED"
    QUESTION_OVERLOAD = "QUESTION_OVERLOAD"
    MISSED_EXPLANATION = "MISSED_EXPLANATION"
    EXERCISE_REJECTED = "EXERCISE_REJECTED"
    TOPIC_REJECTED = "TOPIC_REJECTED"


class PracticeProposalStatus(str, Enum):
    """Hardening §3 -- consent applies to one exact, persisted proposal, not
    to the session in general."""
    PROPOSED = "PROPOSED"
    PENDING = "PENDING"
    GRANTED = "GRANTED"
    DECLINED = "DECLINED"
    # PR #73 request-changes §3: a real delivery-claim state -- GRANTED means
    # "the user said yes", DELIVERING means "this callback invocation has
    # claimed the exclusive right to attempt the Telegram send and has
    # rechecked safety immediately before doing so". Closes the race where a
    # crisis/​start beginning between GRANTED and the actual send would
    # otherwise let practice steps go out to a user now in crisis.
    DELIVERING = "DELIVERING"
    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    WITHDRAWN = "WITHDRAWN"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"
    DELIVERY_FAILED = "DELIVERY_FAILED"


# A proposal is still actionable (a consent tap can still mean something) only
# in these two statuses -- every other status is a terminal or invalidated
# state (hardening §4).
ACTIONABLE_PROPOSAL_STATUSES = (PracticeProposalStatus.PROPOSED, PracticeProposalStatus.PENDING)

# A STARTED proposal (steps successfully delivered) is still actionable for
# the post-practice outcome buttons.
STARTED_ACTIONABLE_STATUS = PracticeProposalStatus.STARTED


class PracticeOutcome(str, Enum):
    """Phase 3 final closure §5 -- a purely qualitative, explicit,
    user-reported outcome. Deliberately has NO before/after numeric scores:
    a Controller-owned practice never collected a before score, so there is
    nothing safe to compute a delta from -- fabricating or estimating one is
    explicitly forbidden. This is NOT OutcomeMeasurement/MetricKind (Phase 1,
    for method-level scored interventions with a real before/after pair);
    reusing that machinery here would mean inventing the missing before
    value, which is exactly what is forbidden."""
    HELPED = "HELPED"
    PARTLY_HELPED = "PARTLY_HELPED"
    NO_CHANGE = "NO_CHANGE"
    WORSE = "WORSE"


class CapabilityLevel(str, Enum):
    """What the system may do with a method WITHOUT a human clinician (§12).
    Ordered by escalating restriction; `rank` supports 'no lower-capability
    path exists' checks."""
    AUTONOMOUS = "AUTONOMOUS"
    ASSESSMENT_AND_CONSENT = "ASSESSMENT_AND_CONSENT"
    CLINICIAN_SUPPORTED = "CLINICIAN_SUPPORTED"
    CRISIS_OR_MEDICAL = "CRISIS_OR_MEDICAL"

    @property
    def rank(self) -> int:
        return _CAPABILITY_RANK[self]


_CAPABILITY_RANK = {
    CapabilityLevel.AUTONOMOUS: 0,
    CapabilityLevel.ASSESSMENT_AND_CONSENT: 1,
    CapabilityLevel.CLINICIAN_SUPPORTED: 2,
    CapabilityLevel.CRISIS_OR_MEDICAL: 3,
}


class InterventionStatus(str, Enum):
    """§15.6. NOT_COMPLETED is a neutral fact, never read as 'resistance'."""
    PROPOSED = "PROPOSED"
    ACCEPTED = "ACCEPTED"
    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    NOT_COMPLETED = "NOT_COMPLETED"
    CANCELLED_BY_USER = "CANCELLED_BY_USER"
    SUPERSEDED = "SUPERSEDED"
    ADVERSE = "ADVERSE"


class MemoryLifecycle(str, Enum):
    """§14.9. An LLM inference is CANDIDATE — it never becomes CONFIRMED
    automatically, and REJECTED/EXPIRED items must not influence responses."""
    CANDIDATE = "CANDIDATE"
    PROPOSED = "PROPOSED"
    CONFIRMED = "CONFIRMED"
    CORRECTED = "CORRECTED"
    REJECTED = "REJECTED"
    HISTORICAL = "HISTORICAL"
    EXPIRED = "EXPIRED"

    @property
    def influences_responses(self) -> bool:
        """Only settled, still-valid items may shape a reply (§14.12)."""
        return self in (MemoryLifecycle.CONFIRMED, MemoryLifecycle.CORRECTED)


class MemoryCategory(str, Enum):
    EXPLICIT_FACT = "EXPLICIT_FACT"
    PREFERENCE = "PREFERENCE"
    GOAL = "GOAL"
    EPISODE_MAP = "EPISODE_MAP"
    HYPOTHESIS = "HYPOTHESIS"
    CONFIRMED_PATTERN = "CONFIRMED_PATTERN"
    INTERVENTION = "INTERVENTION"
    OUTCOME = "OUTCOME"
    PLAN = "PLAN"


class ConfirmationStatus(str, Enum):
    UNCONFIRMED = "UNCONFIRMED"
    CONFIRMED = "CONFIRMED"
    PARTIALLY_CONFIRMED = "PARTIALLY_CONFIRMED"
    CORRECTED = "CORRECTED"
    REJECTED = "REJECTED"


class MetricKind(str, Enum):
    """Outcome metrics are NOT interchangeable — never compare across kinds
    (§15.9). For Behavioural Activation, ACTION_COMPLETION can be a real win
    even when MOOD/DISTRESS is unchanged (§ Phase 6)."""
    DISTRESS = "DISTRESS"
    MOOD = "MOOD"
    UNDERSTANDING = "UNDERSTANDING"
    ABILITY_TO_ACT = "ABILITY_TO_ACT"
    ACTION_COMPLETION = "ACTION_COMPLETION"


class MetricDirection(str, Enum):
    HIGHER_IS_BETTER = "HIGHER_IS_BETTER"
    LOWER_IS_BETTER = "LOWER_IS_BETTER"


class OutcomeClass(str, Enum):
    IMPROVED = "IMPROVED"
    UNCHANGED = "UNCHANGED"
    WORSENED = "WORSENED"
    INCOMPLETE = "INCOMPLETE"


# ── Enum (de)serialisation helper ────────────────────────────────────────────
def as_enum(cls: type, value: Any):
    """Coerce a stored/model value into `cls`, raising a clear ValueError on an
    unknown member (§15.2). Accepts an existing member or its string value."""
    if isinstance(value, cls):
        return value
    try:
        return cls(value)
    except ValueError as exc:
        raise ValueError(
            f"{cls.__name__}: unknown value {value!r}; "
            f"allowed={[m.value for m in cls]}") from exc


def _clip(text: str | None, limit: int) -> str | None:
    """Bounded free text — no unbounded model output becomes canonical state."""
    if text is None:
        return None
    text = str(text).strip()
    return text[:limit] if text else None


# ── Value models ─────────────────────────────────────────────────────────────
# Regular (mutable) dataclasses with __post_init__ validation. `to_dict` emits
# JSON-native primitives (enum -> .value, set -> sorted list); `from_dict` is
# the strict inverse used when reloading canonical state after a restart.

@dataclass
class Formulation:
    """A working, user-confirmable hypothesis — NEVER a diagnosis or a
    certainty-declared cause (§15.4)."""
    trigger: str | None = None
    facts: str | None = None
    thought: str | None = None
    emotion: str | None = None
    body: str | None = None
    urge: str | None = None
    behavior: str | None = None
    avoidance: str | None = None
    short_term_effect: str | None = None
    long_term_consequence: str | None = None
    need_or_value: str | None = None
    alternative_explanation: str | None = None
    confidence: float = 0.0
    confirmation: ConfirmationStatus = ConfirmationStatus.UNCONFIRMED
    source_event_ids: list[int] = field(default_factory=list)

    def __post_init__(self):
        self.confirmation = as_enum(ConfirmationStatus, self.confirmation)
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"Formulation.confidence out of [0,1]: {self.confidence}")
        for f in ("trigger", "facts", "thought", "emotion", "body", "urge",
                  "behavior", "avoidance", "short_term_effect",
                  "long_term_consequence", "need_or_value", "alternative_explanation"):
            setattr(self, f, _clip(getattr(self, f), 500))

    def to_dict(self) -> dict:
        d = {k: getattr(self, k) for k in (
            "trigger", "facts", "thought", "emotion", "body", "urge", "behavior",
            "avoidance", "short_term_effect", "long_term_consequence",
            "need_or_value", "alternative_explanation", "confidence")}
        d["confirmation"] = self.confirmation.value
        d["source_event_ids"] = list(self.source_event_ids)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Formulation":
        return cls(**d)


@dataclass
class Intervention:
    """One governed, versioned intervention. At most one is ACTIVE per session
    (§15.6); a structured intervention needs consent GRANTED (checked by the
    controller, not here)."""
    method_id: str
    version: str
    capability_level: CapabilityLevel
    purpose: str
    status: InterventionStatus = InterventionStatus.PROPOSED
    consent: ConsentState = ConsentState.ABSENT
    primary_metric_kind: MetricKind | None = None

    ACTIVE = (InterventionStatus.ACCEPTED, InterventionStatus.STARTED)

    def __post_init__(self):
        if not str(self.method_id).strip():
            raise ValueError("Intervention.method_id must be non-empty")
        self.capability_level = as_enum(CapabilityLevel, self.capability_level)
        self.status = as_enum(InterventionStatus, self.status)
        self.consent = as_enum(ConsentState, self.consent)
        if self.primary_metric_kind is not None:
            self.primary_metric_kind = as_enum(MetricKind, self.primary_metric_kind)
        self.purpose = _clip(self.purpose, 300) or ""

    @property
    def is_active(self) -> bool:
        return self.status in self.ACTIVE

    def to_dict(self) -> dict:
        return {
            "method_id": self.method_id,
            "version": self.version,
            "capability_level": self.capability_level.value,
            "purpose": self.purpose,
            "status": self.status.value,
            "consent": self.consent.value,
            "primary_metric_kind": (
                self.primary_metric_kind.value if self.primary_metric_kind else None),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Intervention":
        return cls(**d)


@dataclass
class OutcomeMeasurement:
    """A single measured point on ONE metric (§15.9). `classify` compares only
    within this measurement's own kind/direction — never across kinds."""
    metric_kind: MetricKind
    direction: MetricDirection
    scale_min: int
    scale_max: int
    prompt_version: str
    before: int | None = None
    after: int | None = None
    delayed: int | None = None
    completed: bool = False
    adverse: bool = False

    def __post_init__(self):
        self.metric_kind = as_enum(MetricKind, self.metric_kind)
        self.direction = as_enum(MetricDirection, self.direction)
        if self.scale_min >= self.scale_max:
            raise ValueError("OutcomeMeasurement: scale_min must be < scale_max")
        for f in ("before", "after", "delayed"):
            v = getattr(self, f)
            if v is not None and not self.scale_min <= v <= self.scale_max:
                raise ValueError(f"OutcomeMeasurement.{f}={v} outside "
                                 f"[{self.scale_min},{self.scale_max}]")

    def classify(self) -> OutcomeClass:
        if not self.completed:
            return OutcomeClass.INCOMPLETE
        if self.before is None or self.after is None:
            return OutcomeClass.INCOMPLETE
        delta = self.after - self.before
        if self.direction is MetricDirection.LOWER_IS_BETTER:
            delta = -delta
        if delta > 0:
            return OutcomeClass.IMPROVED
        if delta < 0:
            return OutcomeClass.WORSENED
        return OutcomeClass.UNCHANGED

    def to_dict(self) -> dict:
        return {
            "metric_kind": self.metric_kind.value,
            "direction": self.direction.value,
            "scale_min": self.scale_min,
            "scale_max": self.scale_max,
            "prompt_version": self.prompt_version,
            "before": self.before,
            "after": self.after,
            "delayed": self.delayed,
            "completed": self.completed,
            "adverse": self.adverse,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "OutcomeMeasurement":
        return cls(**d)


@dataclass
class MemoryItem:
    """A confirmable memory record (§14). Provenance is required; a CANDIDATE
    from an LLM inference does not influence replies until CONFIRMED/CORRECTED."""
    category: MemoryCategory
    lifecycle: MemoryLifecycle
    content: str
    confidence: float = 0.0
    source_event_ids: list[int] = field(default_factory=list)

    def __post_init__(self):
        self.category = as_enum(MemoryCategory, self.category)
        self.lifecycle = as_enum(MemoryLifecycle, self.lifecycle)
        self.content = _clip(self.content, 1000) or ""
        if not self.content:
            raise ValueError("MemoryItem.content must be non-empty")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"MemoryItem.confidence out of [0,1]: {self.confidence}")

    @property
    def influences_responses(self) -> bool:
        return self.lifecycle.influences_responses

    def to_dict(self) -> dict:
        return {
            "category": self.category.value,
            "lifecycle": self.lifecycle.value,
            "content": self.content,
            "confidence": self.confidence,
            "source_event_ids": list(self.source_event_ids),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MemoryItem":
        return cls(**d)


# Upper bound accepted by RepairRecord.__post_init__ -- a validation ceiling,
# not the operational window (conversation_controller.REPAIR_WINDOW_TURNS is
# the actual value new signals are set to). Keeps a corrupted/adversarial
# persisted value from creating an effectively-permanent constraint.
MAX_REPAIR_TTL_TURNS = 10


@dataclass
class RepairRecord:
    """Hardening §7 -- one independently-timed repair constraint. Replaces
    the single shared `repair_turns_remaining` int: a fresh BOT_REPEATS
    signal must not reset an unrelated ADVICE_REJECTED record's countdown."""
    constraint: RepairConstraint
    source_turn_id: int | None
    created_at: str
    remaining_turns: int
    clearing_reason: str | None = None

    def __post_init__(self):
        self.constraint = as_enum(RepairConstraint, self.constraint)
        if not 0 <= self.remaining_turns <= MAX_REPAIR_TTL_TURNS:
            raise ValueError(
                f"RepairRecord.remaining_turns out of [0,{MAX_REPAIR_TTL_TURNS}]: "
                f"{self.remaining_turns}")
        self.clearing_reason = _clip(self.clearing_reason, 100)

    @property
    def active(self) -> bool:
        return self.remaining_turns > 0

    def to_dict(self) -> dict:
        return {
            "constraint": self.constraint.value,
            "source_turn_id": self.source_turn_id,
            "created_at": self.created_at,
            "remaining_turns": self.remaining_turns,
            "clearing_reason": self.clearing_reason,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RepairRecord":
        return cls(**d)


@dataclass
class PracticeProposal:
    """Hardening §3 -- a bounded, persisted, exact practice proposal. Consent
    applies to THIS proposal, never to "whatever practice the session is
    currently about" -- closes the gap where the LLM could describe one
    practice while a stale callback delivers a different hard-coded one."""
    proposal_id: str
    user_id: int
    session_id: str
    practice_id: str
    practice_version: str
    purpose: str
    expected_duration: str
    status: PracticeProposalStatus = PracticeProposalStatus.PROPOSED
    proposal_message_id: int | None = None
    created_at: str = ""
    expires_at: str = ""
    delivered_at: str | None = None
    superseded_reason: str | None = None
    outcome: "PracticeOutcome | None" = None
    outcome_recorded_at: str | None = None
    # PR #73 request-changes §6: restart-safe delivery tracking for the two
    # post-practice follow-up UI prompts, distinct from proposal_message_id/
    # delivered_at above (which track the PRACTICE STEPS message only).
    outcome_prompt_message_id: int | None = None
    outcome_prompt_delivered_at: str | None = None
    outcome_prompt_status: str | None = None
    helped_prompt_message_id: int | None = None
    helped_prompt_delivered_at: str | None = None
    helped_prompt_status: str | None = None

    def __post_init__(self):
        if not str(self.proposal_id).strip():
            raise ValueError("PracticeProposal.proposal_id must be non-empty")
        if not isinstance(self.user_id, int):
            raise ValueError("PracticeProposal.user_id must be an int (owner key)")
        if not str(self.practice_id).strip():
            raise ValueError("PracticeProposal.practice_id must be non-empty")
        self.status = as_enum(PracticeProposalStatus, self.status)
        self.purpose = _clip(self.purpose, 300) or ""
        self.expected_duration = _clip(self.expected_duration, 50) or ""
        self.superseded_reason = _clip(self.superseded_reason, 100)
        if self.outcome is not None:
            self.outcome = as_enum(PracticeOutcome, self.outcome)
        for field_name in ("outcome_prompt_status", "helped_prompt_status"):
            value = getattr(self, field_name)
            if value is not None and value not in ("FAILED", "DELIVERED"):
                raise ValueError(f"PracticeProposal.{field_name} must be None/FAILED/DELIVERED, got {value!r}")

    @property
    def is_actionable(self) -> bool:
        return self.status in ACTIONABLE_PROPOSAL_STATUSES

    def to_dict(self) -> dict:
        return {
            "proposal_id": self.proposal_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "practice_id": self.practice_id,
            "practice_version": self.practice_version,
            "purpose": self.purpose,
            "expected_duration": self.expected_duration,
            "status": self.status.value,
            "proposal_message_id": self.proposal_message_id,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "delivered_at": self.delivered_at,
            "superseded_reason": self.superseded_reason,
            "outcome": self.outcome.value if self.outcome else None,
            "outcome_recorded_at": self.outcome_recorded_at,
            "outcome_prompt_message_id": self.outcome_prompt_message_id,
            "outcome_prompt_delivered_at": self.outcome_prompt_delivered_at,
            "outcome_prompt_status": self.outcome_prompt_status,
            "helped_prompt_message_id": self.helped_prompt_message_id,
            "helped_prompt_delivered_at": self.helped_prompt_delivered_at,
            "helped_prompt_status": self.helped_prompt_status,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PracticeProposal":
        return cls(**d)


@dataclass
class SessionState:
    """Canonical, restart-safe session state (§15.3). Phase and lifecycle are
    independent axes. This is a value snapshot; the DB is the source of truth
    (Phase 1 storage layer) — never a process-local dict."""
    session_id: str
    user_id: int
    intent: Intent = Intent.UNKNOWN
    phase: SessionPhase = SessionPhase.OPENING
    lifecycle_status: LifecycleStatus = LifecycleStatus.OPEN
    consent: ConsentState = ConsentState.ABSENT
    active_goal: str | None = None
    active_intervention_id: str | None = None
    pending_outcome: bool = False
    # Hardening §6/§7: `intent` is ALWAYS the base/governing intent -- REPAIR
    # is an overlay, never persisted here (see conversation_controller and
    # bot._controller_claim_turn). Each RepairConstraint gets its own
    # independently-timed record instead of one shared countdown.
    repair_records: list[RepairRecord] = field(default_factory=list)
    # Phase 3: reference to the Phase 2 depression_disclosure_flows row this
    # session was seeded from (claim_disclosure_handoff's flow id), so a
    # session can be traced back to its originating handoff without
    # duplicating that data here. None for a session not seeded from a handoff.
    handoff_flow_id: str | None = None

    def __post_init__(self):
        if not str(self.session_id).strip():
            raise ValueError("SessionState.session_id must be non-empty")
        if not isinstance(self.user_id, int):
            raise ValueError("SessionState.user_id must be an int (owner key)")
        self.intent = as_enum(Intent, self.intent)
        self.phase = as_enum(SessionPhase, self.phase)
        self.lifecycle_status = as_enum(LifecycleStatus, self.lifecycle_status)
        self.consent = as_enum(ConsentState, self.consent)
        self.active_goal = _clip(self.active_goal, 300)
        self.repair_records = [
            r if isinstance(r, RepairRecord) else RepairRecord.from_dict(r)
            for r in self.repair_records]

    @property
    def is_active(self) -> bool:
        return self.lifecycle_status in (LifecycleStatus.OPEN, LifecycleStatus.PAUSED)

    @property
    def active_repair_constraints(self) -> set[RepairConstraint]:
        """The read-only view every caller outside this class should use --
        only constraints with a still-live record are active (§7)."""
        return {r.constraint for r in self.repair_records if r.active}

    def add_repair_signal(self, constraints: set[RepairConstraint],
                          source_turn_id: int | None, created_at: str,
                          window_turns: int) -> None:
        """A fresh repair signal refreshes ONLY the constraints it names --
        an unrelated already-active constraint's own countdown is untouched
        (§7's core requirement)."""
        by_constraint = {r.constraint: r for r in self.repair_records}
        for c in constraints:
            existing = by_constraint.get(c)
            if existing is not None:
                existing.remaining_turns = window_turns
                existing.source_turn_id = source_turn_id
                existing.created_at = created_at
                existing.clearing_reason = None
            else:
                self.repair_records.append(RepairRecord(
                    constraint=c, source_turn_id=source_turn_id,
                    created_at=created_at, remaining_turns=window_turns))

    def decay_repair_turns(self) -> None:
        """Called once per controller turn that is NOT itself a fresh repair
        signal -- every still-active record's independent countdown ticks
        down by one; reaching 0 clears that record only."""
        for r in self.repair_records:
            if r.remaining_turns > 0:
                r.remaining_turns -= 1
                if r.remaining_turns == 0 and r.clearing_reason is None:
                    r.clearing_reason = "expired"

    def clear_repair_constraint(self, constraint: RepairConstraint, reason: str) -> None:
        """An explicit override (e.g. an explicit advice request clearing
        ADVICE_REJECTED) -- clears only the named constraint's record."""
        for r in self.repair_records:
            if r.constraint is constraint and r.active:
                r.remaining_turns = 0
                r.clearing_reason = reason

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "intent": self.intent.value,
            "phase": self.phase.value,
            "lifecycle_status": self.lifecycle_status.value,
            "consent": self.consent.value,
            "active_goal": self.active_goal,
            "active_intervention_id": self.active_intervention_id,
            "pending_outcome": self.pending_outcome,
            "repair_records": [r.to_dict() for r in self.repair_records],
            "handoff_flow_id": self.handoff_flow_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SessionState":
        d = dict(d)
        # Backward compatibility: pre-hardening persisted JSON has
        # `repair_constraints` (a list of constraint values) + a single
        # shared `repair_turns_remaining` int instead of `repair_records`.
        # Migrate it into one record per constraint rather than raising --
        # a restart must not lose an in-flight repair window.
        if "repair_records" not in d:
            old_constraints = d.pop("repair_constraints", [])
            old_remaining = d.pop("repair_turns_remaining", 0)
            d["repair_records"] = [
                {"constraint": c, "source_turn_id": None, "created_at": "",
                 "remaining_turns": old_remaining, "clearing_reason": None}
                for c in old_constraints]
        else:
            d.pop("repair_constraints", None)
            d.pop("repair_turns_remaining", None)
        return cls(**d)


@dataclass
class ResponsePlan:
    """Phase 3 §8 — the validated, bounded contract a controller-generated
    response must satisfy. Built deterministically from intent + repair
    constraints BEFORE the LLM call; the Controller Fidelity Validator
    (conversation_controller.py) checks the actual generated text against it
    after the call. Every field is a closed type -- no unrestricted string
    fields that could carry an instruction."""
    intent: Intent
    listening_only: bool = False
    advice_allowed: bool = True
    intervention_allowed: bool = True
    explanation_required: bool = False
    direct_answer_required: bool = False
    consent_required: bool = False
    question_allowed: bool = True
    max_questions: int = 1
    max_actions: int = 1
    max_interventions: int = 1
    repair_constraints: set[RepairConstraint] = field(default_factory=set)

    def __post_init__(self):
        self.intent = as_enum(Intent, self.intent)
        self.repair_constraints = {
            as_enum(RepairConstraint, c) for c in self.repair_constraints}
        if not 0 <= self.max_questions <= 1:
            raise ValueError("ResponsePlan.max_questions must be 0 or 1 (SS9: "
                             "normally no more than one main question)")
        if not 0 <= self.max_actions <= 1:
            raise ValueError("ResponsePlan.max_actions must be 0 or 1 (SS9: "
                             "no more than one requested action)")
        if not 0 <= self.max_interventions <= 1:
            raise ValueError("ResponsePlan.max_interventions must be 0 or 1 "
                             "(SS9: no more than one primary intervention)")
        if RepairConstraint.QUESTION_OVERLOAD in self.repair_constraints:
            self.question_allowed = False
            self.max_questions = 0
        if RepairConstraint.ADVICE_REJECTED in self.repair_constraints:
            self.advice_allowed = False
        if RepairConstraint.EXERCISE_REJECTED in self.repair_constraints:
            self.intervention_allowed = False
        # Phase 3 correction §9 -- per-intent cross-field invariants FAIL
        # CLOSED (raise) rather than being silently coerced into something
        # permissive. This makes an inconsistent plan for these four intents
        # impossible to construct, not just discouraged by convention.
        if self.question_allowed is False and self.max_questions != 0:
            raise ValueError("ResponsePlan: question_allowed=False requires max_questions=0")
        if self.intent is Intent.VENT:
            if not (self.listening_only and not self.advice_allowed
                    and not self.intervention_allowed):
                raise ValueError(
                    "ResponsePlan: VENT requires listening_only=True, "
                    "advice_allowed=False, intervention_allowed=False")
        if self.intent is Intent.EXPLAIN:
            if not (self.explanation_required and self.direct_answer_required):
                raise ValueError(
                    "ResponsePlan: EXPLAIN requires explanation_required=True "
                    "and direct_answer_required=True")
        if self.intent is Intent.ACTION:
            if self.max_actions != 1 or self.question_allowed:
                raise ValueError(
                    "ResponsePlan: ACTION requires max_actions=1 and "
                    "question_allowed=False (the bot chooses, it does not ask)")
        if self.intent is Intent.PRACTICE and not self.consent_required:
            raise ValueError("ResponsePlan: PRACTICE requires consent_required=True")

    def to_dict(self) -> dict:
        return {
            "intent": self.intent.value,
            "listening_only": self.listening_only,
            "advice_allowed": self.advice_allowed,
            "intervention_allowed": self.intervention_allowed,
            "explanation_required": self.explanation_required,
            "direct_answer_required": self.direct_answer_required,
            "consent_required": self.consent_required,
            "question_allowed": self.question_allowed,
            "max_questions": self.max_questions,
            "max_actions": self.max_actions,
            "max_interventions": self.max_interventions,
            "repair_constraints": sorted(c.value for c in self.repair_constraints),
        }
