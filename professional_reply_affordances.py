"""Professional Core V2 -- Reply Affordances + Entry Triage V1 (offline UX contract).

OFFLINE DOMAIN/GOVERNANCE MODULE ONLY. This module defines a deterministic,
pure, closed vocabulary and derivation rules for two related but distinct UX
concerns: a low-pressure conversation-entry triage, and mid-conversation
reply affordances attached to a trusted ProfessionalTurnPlan. It performs no
I/O of any kind: no network call, no model call, no database access, no
Telegram delivery, no environment reads, no secret access. It is not
imported by bot.py, is not wired into conversation_controller.py, and does
not render any Telegram keyboard or register any callback handler. There is
no runtime wiring in this slice -- that is explicitly future, separately
authorized work.

PURPOSE -- this is NOT an attempt to turn X20 into a questionnaire. The
purpose is narrower and entry-focused:
  1. reduce effort at conversation entry;
  2. let a user begin even when they cannot yet put a problem into words;
  3. provide explicit escape/scaffolding affordances for a real professional
     question, without coercing an answer;
  4. preserve free-text conversation as the primary interaction channel at
     all times;
  5. prevent an LLM from inventing button semantics or pressuring the user
     toward a particular selection;
  6. preserve user autonomy over both what to select and whether to select
     anything at all;
  7. prepare a trusted, closed semantic contract that a future, separately
     reviewed runtime slice can wire into Telegram delivery.

ENTRY TRIAGE IS AN ENTRY, NOT THE DEPTH CEILING -- the low-pressure entry
wording below is an ENTRY behavior only. It must not be read as making
Professional Core permanently shallow, and it is NOT the final depth of
Professional Core. Once enough material exists and the user is willing, the
professional trajectory may still progress toward: a concrete recent
episode; relevant facts; the user's interpretation/thought; emotion; bodily
experience where relevant; urge; behavior; consequence; a pattern that
repeats; a tentative working understanding; the user's own confirmation or
correction of that working understanding; a useful goal; and, later, an
appropriate governed method or intervention. None of that deeper trajectory
is implemented in this slice -- this documentation exists only so Entry
Triage is never mistaken for a permanent "just talk" ceiling.

ENTRY SELECTION PROVENANCE -- an EntryTriageCategory selection is a trusted
UI selection event, not user speech. It must never be converted into
fabricated quoted or free-text user speech (for example, turning a category
tap into an invented sentence as if the user had typed it) unless the user
literally wrote that sentence themselves. A future analysis/runtime layer
must preserve the distinction between USER_FREE_TEXT (what the user actually
typed) and ENTRY_TRIAGE_SELECTION (which button, if any, the user tapped) as
two structurally different provenance categories, so a UI selection can
never silently become fabricated evidence about what the user said. This
module does not implement persistence or Analyzer integration; it only
establishes the contract that future integration must not violate.

WHY BOTH REPLY AFFORDANCES EXIST ON A FOCUSED QUESTION -- NEEDS_SCAFFOLDING
and SKIP_CURRENT_QUESTION are accessibility/autonomy affordances, not
answer-content buttons. Neither one suggests, nor should ever be read as
suggesting, an emotion, a belief, a motive, a diagnosis, a cause, a
relationship interpretation, an action, or any other therapeutic conclusion.
Showing these two affordances alongside a trusted FOCUSED_QUESTION move does
not, by itself, turn the conversation into a multiple-choice questionnaire --
the primary channel remains the user's own free-text reply. Later product
work may refine visual presentation; this module's derivation rule stays
deterministic, and explicitly does not use an LLM-controlled "question
difficulty" heuristic of any kind.

TURN-SCOPING REQUIREMENT (future runtime invariant, not implemented here) --
a reply-affordance selection must be bound to the exact assistant
question/turn that produced it. A stale NEEDS_SCAFFOLDING or
SKIP_CURRENT_QUESTION selection must never be allowed to affect a later,
different question. This module defines no Telegram callback IDs, no
callback handlers, no persistence, no DB schema, and no runtime nonce/token
strategy -- those all belong to a future, separately authorized runtime
integration slice. This paragraph only makes the invariant explicit ahead of
that work.

SEMANTICS OF NEEDS_SCAFFOLDING -- the user is not refusing the conversation,
is not refusing the current professional target, and is not being
"resistant". The user has not substantively answered the question; they are
signaling difficulty producing an answer. The conversation continues, and
future planning should make the answering task easier while preserving the
professional target where appropriate. This action must never itself become
evidence, a belief, an emotion, a fact, an inferred diagnosis, or a
substantive answer. Future scaffolding behavior may (NOT implemented here)
simplify the wording/task, narrow the requested recall, offer at most 2-3
tentative contextual hypotheses when genuinely useful, preserve explicit
uncertainty, keep free text available, and offer a "none of these / not
quite" escape -- this slice defines only the trusted NEEDS_SCAFFOLDING
action itself, no contextual-hypothesis buttons and no additional model call.

SEMANTICS OF SKIP_CURRENT_QUESTION -- explicit refusal of THIS exact current
question only. It is not refusal of the whole conversation, not automatic
topic closure, not evidence that the topic is traumatic or that the question
was too difficult, and not permission to infer a reason for the refusal. It
is deliberately NOT the same thing as InteractionSignal.NO_QUESTIONS
(therapeutic_domain.py): that signal is an explicit, proactive, current-turn
instruction against questions in general and may arise with no prior
question at all, while SKIP_CURRENT_QUESTION only ever exists in response to
one specific already-asked question. A future planner (NOT implemented here)
must not repeat the same question verbatim, must not merely reword the same
question to force an answer, must not ask why the user declined unless the
user independently raises that issue, must not diagnose or interpret the
refusal as avoidance/resistance automatically, and must not end the
conversation automatically -- it may choose another professionally justified
move or leave the user room to continue freely.

DETERMINISTIC AVAILABILITY RULE -- reply-affordance availability is derived
exclusively from the trusted ProfessionalTurnPlan (its move and
question_allowed fields), never by inspecting rendered candidate response
text and never by letting an LLM decide whether these two affordances exist.

Only imports: __future__, dataclasses, enum,
professional_turn_planner.ProfessionalTurnPlan, and
therapeutic_domain.PrimaryResponseMove. No bot.py import, no
conversation_controller.py import, no database import, no Telegram import.

Python 3.10 target (prod 3.10.12): `str, Enum` mix-ins, not `StrEnum` (3.11+).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from professional_turn_planner import ProfessionalTurnPlan
from therapeutic_domain import PrimaryResponseMove


# ── Entry Triage ─────────────────────────────────────────────────────────────

class EntryTriageCategory(str, Enum):
    """Closed V1 entry-triage vocabulary -- user-facing entry/navigation
    hints only. A selected category is NOT a diagnosis, NOT a symptom
    diagnosis, NOT an Intent, NOT a goal, NOT consent, NOT evidence, NOT an
    established fact about the user, and NOT a treatment-method selection.
    Selecting a category means only: this is approximately the area the
    user chose to begin with. There is deliberately no automatic mapping
    from a category to therapeutic_domain.Intent and no automatic mapping
    from a category to a therapeutic method/intervention -- both remain
    exclusively future, separately governed work."""
    ANXIETY_STRESS = "ANXIETY_STRESS"
    RELATIONSHIPS_LONELINESS = "RELATIONSHIPS_LONELINESS"
    LOW_ENERGY_LOW_MOOD = "LOW_ENERGY_LOW_MOOD"
    SELF_ESTEEM_SELF_CRITICISM = "SELF_ESTEEM_SELF_CRITICISM"
    DIFFICULT_EMOTIONS = "DIFFICULT_EMOTIONS"
    UNSURE_OR_OTHER = "UNSURE_OR_OTHER"


class EntryFollowupFocus(str, Enum):
    """Closed V1 vocabulary describing what kind of first professional
    exploration may be useful after a voluntary entry selection. This is
    NOT final response text, NOT a diagnosis, does NOT assert that the
    user's underlying difficulty has been discovered, does NOT establish a
    cause or a clinical condition, and does NOT automatically select a
    treatment method. It only names a plausible next exploration focus."""
    RECENT_HIGH_INTENSITY_EPISODE = "RECENT_HIGH_INTENSITY_EPISODE"
    RECENT_RELATIONAL_EPISODE = "RECENT_RELATIONAL_EPISODE"
    RECENT_FUNCTIONING_CHANGE = "RECENT_FUNCTIONING_CHANGE"
    RECENT_SELF_CRITICISM_EPISODE = "RECENT_SELF_CRITICISM_EPISODE"
    DIFFICULT_EMOTION_AND_ANTECEDENT = "DIFFICULT_EMOTION_AND_ANTECEDENT"
    LOW_PRESSURE_OPENING = "LOW_PRESSURE_OPENING"


# The closed, deterministic V1 mapping. Every EntryTriageCategory maps to
# exactly one EntryFollowupFocus -- there is no many-to-many ambiguity and no
# category is left unmapped.
_ENTRY_FOLLOWUP_FOCUS_BY_CATEGORY: dict[EntryTriageCategory, EntryFollowupFocus] = {
    EntryTriageCategory.ANXIETY_STRESS: EntryFollowupFocus.RECENT_HIGH_INTENSITY_EPISODE,
    EntryTriageCategory.RELATIONSHIPS_LONELINESS: EntryFollowupFocus.RECENT_RELATIONAL_EPISODE,
    EntryTriageCategory.LOW_ENERGY_LOW_MOOD: EntryFollowupFocus.RECENT_FUNCTIONING_CHANGE,
    EntryTriageCategory.SELF_ESTEEM_SELF_CRITICISM: EntryFollowupFocus.RECENT_SELF_CRITICISM_EPISODE,
    EntryTriageCategory.DIFFICULT_EMOTIONS: EntryFollowupFocus.DIFFICULT_EMOTION_AND_ANTECEDENT,
    EntryTriageCategory.UNSURE_OR_OTHER: EntryFollowupFocus.LOW_PRESSURE_OPENING,
}


def followup_focus_for_category(category: EntryTriageCategory) -> EntryFollowupFocus:
    """Deterministic lookup: which EntryFollowupFocus follows a voluntary
    EntryTriageCategory selection. Fails closed on any non-member input."""
    if not isinstance(category, EntryTriageCategory):
        raise ValueError(
            "followup_focus_for_category requires an EntryTriageCategory, "
            f"got {type(category)!r}")
    return _ENTRY_FOLLOWUP_FOCUS_BY_CATEGORY[category]


# The sealed V1 Russian label for every category. This is the single source
# of truth EntryTriageOption validates against -- no other label string is a
# legal V1 option for a given category, closing the gap where a caller could
# construct a trusted-looking option carrying arbitrary model/user text.
_SEALED_ENTRY_LABEL_RU_BY_CATEGORY: dict[EntryTriageCategory, str] = {
    EntryTriageCategory.ANXIETY_STRESS: "Тревога / стресс",
    EntryTriageCategory.RELATIONSHIPS_LONELINESS: "Отношения / одиночество",
    EntryTriageCategory.LOW_ENERGY_LOW_MOOD: "Нет сил / подавленность",
    EntryTriageCategory.SELF_ESTEEM_SELF_CRITICISM: "Самооценка / самокритика",
    EntryTriageCategory.DIFFICULT_EMOTIONS: "Сильные эмоции",
    EntryTriageCategory.UNSURE_OR_OTHER: "Не знаю / другое",
}


@dataclass(frozen=True)
class EntryTriageOption:
    """One user-facing entry-triage button: a closed category plus its
    frozen V1 Russian label. Selecting this option is a trusted UI
    selection event -- see ENTRY SELECTION PROVENANCE in the module
    docstring; it is never a synthesized user utterance. label_ru is
    validated against the sealed V1 label for the given category -- no
    other text is accepted, so this trusted type can never carry arbitrary
    model/user-authored text."""
    category: EntryTriageCategory
    label_ru: str

    def __post_init__(self):
        if not isinstance(self.category, EntryTriageCategory):
            raise ValueError(
                "EntryTriageOption.category must be an EntryTriageCategory, "
                f"got {type(self.category)!r}")
        expected_label = _SEALED_ENTRY_LABEL_RU_BY_CATEGORY[self.category]
        if self.label_ru != expected_label:
            raise ValueError(
                f"EntryTriageOption.label_ru for {self.category.value} must be "
                f"exactly the sealed V1 label {expected_label!r}, got {self.label_ru!r}")


# Owner-approved V1 entry prompt (frozen, exact wording, not to be
# paraphrased or re-toned). This wording deliberately: is lower pressure
# than an earlier proposed variant; does not force the user to identify
# their "biggest" problem; communicates that an approximate selection is
# enough; explicitly permits imperfect/free-form expression; and does not
# require the user to already understand or explain a cause.
_ENTRY_PROMPT_RU = (
    "С чего тебе было бы легче начать?\n"
    "Выбери то, что сейчас ближе, или напиши как получается.\n"
    "Не обязательно сразу всё понимать и объяснять."
)

# Exact owner-approved category order. Order is part of the frozen V1
# contract -- do not reorder without a separate owner review.
_ENTRY_TRIAGE_CATEGORY_ORDER: tuple[EntryTriageCategory, ...] = (
    EntryTriageCategory.ANXIETY_STRESS,
    EntryTriageCategory.RELATIONSHIPS_LONELINESS,
    EntryTriageCategory.LOW_ENERGY_LOW_MOOD,
    EntryTriageCategory.SELF_ESTEEM_SELF_CRITICISM,
    EntryTriageCategory.DIFFICULT_EMOTIONS,
    EntryTriageCategory.UNSURE_OR_OTHER,
)

_ENTRY_TRIAGE_OPTIONS: tuple[EntryTriageOption, ...] = tuple(
    EntryTriageOption(category, _SEALED_ENTRY_LABEL_RU_BY_CATEGORY[category])
    for category in _ENTRY_TRIAGE_CATEGORY_ORDER
)


@dataclass(frozen=True)
class EntryTriageContract:
    """The frozen V1 entry-triage UX contract: the low-pressure opening
    prompt, the six ordered category options, and an explicit
    free_text_allowed flag. free_text_allowed is always True in V1: there is
    no forced-choice state, and a user may ignore every option and simply
    type -- this module never implements a forced-choice questionnaire.
    Every field is validated against the sealed V1 values -- prompt_ru must
    be exactly the owner-approved prompt, options must be exactly the six
    sealed categories once each in the sealed order (this single check
    rejects duplicates, omissions, and reordering alike), and
    free_text_allowed must be exactly True. This is a trusted contract type,
    not a free-form container a caller could repurpose with arbitrary
    text/composition."""
    prompt_ru: str
    options: tuple[EntryTriageOption, ...]
    free_text_allowed: bool

    def __post_init__(self):
        if self.prompt_ru != _ENTRY_PROMPT_RU:
            raise ValueError(
                "EntryTriageContract.prompt_ru must be exactly the sealed "
                "owner-approved V1 entry prompt")
        if not isinstance(self.options, tuple):
            raise ValueError("EntryTriageContract.options must be a tuple")
        for option in self.options:
            if not isinstance(option, EntryTriageOption):
                raise ValueError(
                    "EntryTriageContract.options entries must be "
                    f"EntryTriageOption, got {type(option)!r}")
        if tuple(option.category for option in self.options) != _ENTRY_TRIAGE_CATEGORY_ORDER:
            raise ValueError(
                "EntryTriageContract.options must contain exactly the six "
                "sealed V1 categories, each exactly once, in the sealed V1 order")
        if self.free_text_allowed is not True:
            raise ValueError("EntryTriageContract.free_text_allowed must be exactly True in V1")


ENTRY_TRIAGE_CONTRACT_V1 = EntryTriageContract(
    prompt_ru=_ENTRY_PROMPT_RU,
    options=_ENTRY_TRIAGE_OPTIONS,
    free_text_allowed=True,
)


# ── Mid-conversation reply affordances ───────────────────────────────────────

class ReplyAffordanceAction(str, Enum):
    """Closed V1 vocabulary of trusted mid-conversation autonomy actions.
    These are accessibility/autonomy affordances, never answer-content --
    see WHY BOTH REPLY AFFORDANCES EXIST in the module docstring."""
    NEEDS_SCAFFOLDING = "NEEDS_SCAFFOLDING"
    SKIP_CURRENT_QUESTION = "SKIP_CURRENT_QUESTION"


# Presentation text only -- the action identifier above is the trusted
# semantic contract a future runtime must key off of, never this label. This
# is also the single source of truth ReplyAffordanceOption validates
# against -- no other label string is a legal V1 option for a given action.
_SEALED_REPLY_AFFORDANCE_LABEL_RU_BY_ACTION: dict[ReplyAffordanceAction, str] = {
    ReplyAffordanceAction.NEEDS_SCAFFOLDING: "Не знаю",
    ReplyAffordanceAction.SKIP_CURRENT_QUESTION: "Пропустить",
}


@dataclass(frozen=True)
class ReplyAffordanceOption:
    """One mid-conversation autonomy affordance: a trusted action identifier
    plus its frozen V1 Russian presentation label. label_ru is validated
    against the sealed V1 label for the given action -- no other text is
    accepted, so this trusted type can never carry arbitrary
    model/user-authored text."""
    action: ReplyAffordanceAction
    label_ru: str

    def __post_init__(self):
        if not isinstance(self.action, ReplyAffordanceAction):
            raise ValueError(
                "ReplyAffordanceOption.action must be a ReplyAffordanceAction, "
                f"got {type(self.action)!r}")
        expected_label = _SEALED_REPLY_AFFORDANCE_LABEL_RU_BY_ACTION[self.action]
        if self.label_ru != expected_label:
            raise ValueError(
                f"ReplyAffordanceOption.label_ru for {self.action.value} must be "
                f"exactly the sealed V1 label {expected_label!r}, got {self.label_ru!r}")


# The exactly two valid V1 action sequences a ReplyAffordancePlan may carry:
# no affordances, or the full focused-question pair in the sealed order.
# Every partial, reversed, or duplicated combination is rejected.
_VALID_REPLY_AFFORDANCE_ACTION_SEQUENCES: frozenset[tuple[ReplyAffordanceAction, ...]] = frozenset({
    (),
    (ReplyAffordanceAction.NEEDS_SCAFFOLDING, ReplyAffordanceAction.SKIP_CURRENT_QUESTION),
})


@dataclass(frozen=True)
class ReplyAffordancePlan:
    """The trusted, ordered set of reply-affordance options available for
    exactly one assistant turn. Empty for every non-FOCUSED_QUESTION move in
    V1 -- see derive_reply_affordances below. There are exactly two legal V1
    shapes: no options, or exactly the two focused-question options in the
    sealed order -- every partial, reversed, or duplicated combination is
    rejected at construction, so this trusted type can never carry an
    ad-hoc affordance combination."""
    options: tuple[ReplyAffordanceOption, ...]

    def __post_init__(self):
        if not isinstance(self.options, tuple):
            raise ValueError("ReplyAffordancePlan.options must be a tuple")
        for option in self.options:
            if not isinstance(option, ReplyAffordanceOption):
                raise ValueError(
                    "ReplyAffordancePlan.options entries must be "
                    f"ReplyAffordanceOption, got {type(option)!r}")
        actions = tuple(option.action for option in self.options)
        if actions not in _VALID_REPLY_AFFORDANCE_ACTION_SEQUENCES:
            raise ValueError(
                "ReplyAffordancePlan.options must be exactly () or exactly "
                "(NEEDS_SCAFFOLDING, SKIP_CURRENT_QUESTION) in that order; got "
                f"{[a.value for a in actions]}")

    @property
    def actions(self) -> tuple[ReplyAffordanceAction, ...]:
        return tuple(option.action for option in self.options)


_NO_REPLY_AFFORDANCES = ReplyAffordancePlan(options=())

# Exact required order: NEEDS_SCAFFOLDING ("Не знаю") first, then
# SKIP_CURRENT_QUESTION ("Пропустить").
_FOCUSED_QUESTION_REPLY_AFFORDANCES = ReplyAffordancePlan(options=(
    ReplyAffordanceOption(
        ReplyAffordanceAction.NEEDS_SCAFFOLDING,
        _SEALED_REPLY_AFFORDANCE_LABEL_RU_BY_ACTION[ReplyAffordanceAction.NEEDS_SCAFFOLDING]),
    ReplyAffordanceOption(
        ReplyAffordanceAction.SKIP_CURRENT_QUESTION,
        _SEALED_REPLY_AFFORDANCE_LABEL_RU_BY_ACTION[ReplyAffordanceAction.SKIP_CURRENT_QUESTION]),
))


def derive_reply_affordances(plan: ProfessionalTurnPlan) -> ReplyAffordancePlan:
    """Deterministic V1 availability rule. IF plan.move is
    PrimaryResponseMove.FOCUSED_QUESTION AND plan.question_allowed is True,
    expose exactly NEEDS_SCAFFOLDING then SKIP_CURRENT_QUESTION, in that
    order. For every other move, expose no reply-affordance options.
    Availability is derived exclusively from the trusted plan -- never from
    inspecting rendered candidate response text, and never left to an LLM."""
    if not isinstance(plan, ProfessionalTurnPlan):
        raise ValueError(
            f"derive_reply_affordances requires a ProfessionalTurnPlan, got {type(plan)!r}")
    if plan.move is PrimaryResponseMove.FOCUSED_QUESTION and plan.question_allowed is True:
        return _FOCUSED_QUESTION_REPLY_AFFORDANCES
    return _NO_REPLY_AFFORDANCES


# Field order used for both the sealed-value lookup below and runtime
# validation -- keep in sync with ReplyAffordanceMeaning's own field order.
_REPLY_AFFORDANCE_MEANING_BOOL_FIELDS = (
    "conversation_continues", "declines_current_question",
    "declines_whole_conversation", "requests_scaffolding",
    "produces_substantive_answer", "equals_no_questions_signal",
    "becomes_evidence",
)

# The sealed, exact V1 semantics for each action -- the single source of
# truth ReplyAffordanceMeaning validates against. No other boolean
# combination is a legal V1 meaning for a given action, closing the gap
# where a caller could construct a trusted-looking meaning object carrying
# self-contradictory or otherwise arbitrary semantics.
_SEALED_REPLY_AFFORDANCE_MEANING_VALUES_BY_ACTION: dict[ReplyAffordanceAction, tuple[bool, ...]] = {
    ReplyAffordanceAction.NEEDS_SCAFFOLDING: (True, False, False, True, False, False, False),
    ReplyAffordanceAction.SKIP_CURRENT_QUESTION: (True, True, False, False, False, False, False),
}


@dataclass(frozen=True)
class ReplyAffordanceMeaning:
    """The trusted, structured semantics of one ReplyAffordanceAction -- see
    SEMANTICS OF NEEDS_SCAFFOLDING / SEMANTICS OF SKIP_CURRENT_QUESTION in
    the module docstring for the full prose contract this mirrors. Every
    boolean field must be exactly `bool` (an int 0/1 or other truthy value
    is rejected), and the full field combination must exactly equal the
    sealed V1 semantics for the given action -- no contradictory or
    otherwise arbitrary combination can be constructed."""
    action: ReplyAffordanceAction
    conversation_continues: bool
    declines_current_question: bool
    declines_whole_conversation: bool
    requests_scaffolding: bool
    produces_substantive_answer: bool
    equals_no_questions_signal: bool
    becomes_evidence: bool

    def __post_init__(self):
        if not isinstance(self.action, ReplyAffordanceAction):
            raise ValueError(
                "ReplyAffordanceMeaning.action must be a ReplyAffordanceAction, "
                f"got {type(self.action)!r}")
        for field_name in _REPLY_AFFORDANCE_MEANING_BOOL_FIELDS:
            value = getattr(self, field_name)
            if type(value) is not bool:
                raise ValueError(
                    f"ReplyAffordanceMeaning.{field_name} must be exactly bool, "
                    f"got {type(value)!r}")
        expected = _SEALED_REPLY_AFFORDANCE_MEANING_VALUES_BY_ACTION[self.action]
        actual = tuple(getattr(self, f) for f in _REPLY_AFFORDANCE_MEANING_BOOL_FIELDS)
        if actual != expected:
            raise ValueError(
                f"ReplyAffordanceMeaning for {self.action.value} does not match "
                f"the sealed V1 semantics: expected {expected}, got {actual}")


_NEEDS_SCAFFOLDING_MEANING = ReplyAffordanceMeaning(
    action=ReplyAffordanceAction.NEEDS_SCAFFOLDING,
    conversation_continues=True,
    declines_current_question=False,
    declines_whole_conversation=False,
    requests_scaffolding=True,
    produces_substantive_answer=False,
    equals_no_questions_signal=False,
    becomes_evidence=False,
)

_SKIP_CURRENT_QUESTION_MEANING = ReplyAffordanceMeaning(
    action=ReplyAffordanceAction.SKIP_CURRENT_QUESTION,
    conversation_continues=True,
    declines_current_question=True,
    declines_whole_conversation=False,
    requests_scaffolding=False,
    produces_substantive_answer=False,
    equals_no_questions_signal=False,
    becomes_evidence=False,
)

_REPLY_AFFORDANCE_MEANING_BY_ACTION: dict[ReplyAffordanceAction, ReplyAffordanceMeaning] = {
    ReplyAffordanceAction.NEEDS_SCAFFOLDING: _NEEDS_SCAFFOLDING_MEANING,
    ReplyAffordanceAction.SKIP_CURRENT_QUESTION: _SKIP_CURRENT_QUESTION_MEANING,
}


def meaning_for_action(action: ReplyAffordanceAction) -> ReplyAffordanceMeaning:
    """Deterministic lookup of the trusted, structured meaning of one
    ReplyAffordanceAction. Fails closed on any non-member input."""
    if not isinstance(action, ReplyAffordanceAction):
        raise ValueError(
            f"meaning_for_action requires a ReplyAffordanceAction, got {type(action)!r}")
    return _REPLY_AFFORDANCE_MEANING_BY_ACTION[action]
