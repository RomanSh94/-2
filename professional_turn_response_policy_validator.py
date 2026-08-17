"""Professional Core V2 -- Turn Response Policy Validator V1.

Deterministic, offline, SURFACE-proxy content-policy validator only: given
one already-trusted ProfessionalTurnPlan and one candidate reply string, it
checks a frozen, bounded set of literal-text/regex surface constructions for
two narrow classes -- unplanned action/advice content and unplanned
intervention/exercise content -- and returns PASS or one closed REJECT
reason. It performs NO semantic comprehension.

WHY these two classes exist at all: Planner V1 (professional_turn_planner.py)
has no reachable ACTION_PROPOSAL move and no reachable intervention/practice
move -- ACTION_PROPOSAL pairs only with ProfessionalObjective.OFFER_ACTION,
which is absent from professional_turn_planner._SUPPORTED_OBJECTIVES. So for
every V1-reachable ProfessionalTurnPlan, any candidate content that reads as
direct advice or as a coached intervention/exercise represents a move the
Planner never authorized for this turn -- not a clinical judgement that advice
or exercises are inherently wrong. The two reason names below are chosen to
say exactly that ("UNPLANNED_...", not e.g. "FORBIDDEN_ADVICE").

PASS does not prove: semantic objective fidelity; semantic move fidelity;
clarification-target fidelity; source-grounded reflection; absence of every
possible advice phrasing (only a small closed vocabulary is checked); absence
of every possible intervention/exercise phrasing; absence of a second
semantic move; or clinical safety. Those concerns are owned elsewhere (Fidelity
V1 for the question/list surface, safety_validator for diagnosis/dependency/
certainty/toxic-validation/risky-suggestion, or remain explicit pre-runtime
gaps with no current owner anywhere in this repository). This module does not
call any other component and does not claim to resolve any of the above.

Both policy families run for ALL FOUR V1-reachable moves (OPEN_INVITATION,
FOCUSED_QUESTION, REFLECTIVE_STATEMENT, CLOSING) UNCONDITIONALLY -- there is
no move-based bypass of either family, and question-mark presence never
disables detection (a FOCUSED_QUESTION tag-question such as "..., не так ли?"
/ "..., right?" is still evaluated exactly like any other candidate). Earlier
design drafts of this module skipped the action/advice family for
FOCUSED_QUESTION; that was rejected during contract review because Fidelity
V1 explicitly does not prove that a lone "?" represents a genuine semantic
question, so a disguised-advice tag-question would otherwise slip through
unchecked. "Move-aware" in this module means only that the bounded patterns
below are precise enough to avoid false-positiving on legitimate
FOCUSED_QUESTION/OPEN_INVITATION phrasing -- not that any move disables a
family. plan (not just candidate_text) remains an explicit required
parameter both for the frozen-move ValueError guard below and to keep this
function's signature stable if a future V1 revision needs genuine
move-conditional pattern selection; no rule body in this version branches on
plan.move's value.

Every surface rule here is a bounded, closed-vocabulary construction, never a
bare/generic trigger word standing alone:
  - "тебе нужно"/"тебе стоит"/"ты должен"/"ты должна"/"you should"/
    "you need to"/"you must" only fire when immediately (within a small,
    bounded token gap) followed by a verb from a small closed
    externally-directed action vocabulary -- never on the bare modal phrase
    itself. This is what lets "Что тебе нужно сейчас?" (modal + adverb, no
    verb) and "Ты сказал, что тебе нужно время, чтобы всё обдумать." (modal +
    noun "время", not a verb) PASS while "Тебе нужно расстаться с ним."
    (modal + listed verb) is REJECTed.
  - "попробуй"/"try" only fire when proximally followed by the same closed
    action vocabulary -- never on the bare word, so "Попробуй, если хочешь,
    рассказать чуть подробнее." (followed by "рассказать", not in the
    vocabulary) PASSes.
  - Direct imperatives ("Позвони ему.", "Leave him.") are matched only as a
    small closed set of exact imperative-verb-plus-third-party-object
    constructions -- never a bare "imperative == advice" rule. This is also
    why quoted/reported forms are naturally excluded in many cases: Russian
    true imperative morphology ("напиши") is a different word from reported/
    indicative forms ("напишешь", "написал") that a literal-text check simply
    does not match.
  - Named intervention/exercise phrases ("дыхательное упражнение", "breathing
    exercise") are bounded multi-word phrases, never bare "практик"/
    "practice"/"exercise" stems.
  - The RU breathing-imperative set ("подыши", "вдохни", "выдохни" and their
    plural/formal forms) is matched as an EXACT closed word list with word
    boundaries, not a stem ("подыш\\w*") -- this is what keeps "подышал"
    (past tense, reported) and "дышал" (a wholly different verb) from
    matching while still catching the true imperative forms on their own,
    with no additional duration/manner requirement, since these forms have
    essentially no benign non-instructional use. The EN mirror ("breathe") is
    treated more conservatively: because bare English "breathe" has genuine
    everyday idiomatic/supportive use ("just breathe") that "подыши" does
    not, it requires bounded manner-adverb or duration co-occurrence, matched
    only against the exact word "breathe" (never "breathes"/"breathed") so
    that "He breathes slowly when he is anxious." and "You said you breathed
    slowly for two minutes." both PASS.

Correction round 1 (owner-reviewed precision defects, fixed without
reopening the frozen result contract, reason vocabulary, or precedence
order):
  - DIRECTIVE POSITION: the RU/EN direct-imperative action/advice patterns
    and the EN breathe/focus intervention patterns previously searched for
    their verb ANYWHERE in the candidate, so "You said you want to leave
    him." (embedded reflection) and "I notice you breathe slowly when
    anxious." (embedded reporting) both incorrectly REJECTed. They are now
    gated to a "directive position" -- the start of the candidate, or
    immediately after a sentence-terminal punctuation mark and whitespace
    (optionally after a harmless leading word such as "please", or, for the
    breathing/focus family only, a sequencing adverb such as "first"/
    "then"/"now") -- so a true imperative opening its own sentence
    ("Leave him.", "I hear you. Leave him.", "First breathe for two
    minutes.") still REJECTs while the identical words embedded inside a
    reporting/reflective clause PASS. This is a bounded surface-position
    rule, not sentence parsing or quotation understanding -- a candidate
    that literally opens with a quoted imperative remains a documented,
    accepted residual false positive (see below).
  - EXACT WORD, NOT STEM: the RU focus instruction ("сосредоточься на
    дыхании") and the EN focus instruction ("focus on your breathing") now
    require the exact closed imperative word ("сосредоточься"/
    "сосредоточьтесь", "focus") rather than a stem ("сосредоточ\\w*") or the
    gerund ("focusing"), which previously matched reporting/past-tense forms
    ("сосредоточился", "сосредоточилась", "сосредоточивался", "You said
    focusing on your breathing helped.").
  - EXPLICIT SUGGESTION FAMILY (v1, superseded by round 2 below): a narrow
    "you could breathe ..." pattern was added alongside the
    directive-position breathing pattern so "No rush -- you could breathe
    slowly, ..." still REJECTs. This v1 pattern was NOT itself gated to any
    position -- it matched "you could breathe" ANYWHERE in the candidate,
    on the mistaken assumption that the "you could breathe" auxiliary was
    inherently a low-false-positive anchor on its own. It is not: owner
    review found this still matched embedded reporting such as "You said
    you could breathe slowly when you felt overwhelmed." Round 2 below
    corrects this.
  - MENTION IS NOT PROPOSAL (v1, refined by round 2 below): named-technique
    phrases ("дыхательное упражнение", "техника заземления", "breathing
    exercise", "grounding technique", "grounding exercise") previously
    triggered on a bare mention, so "Ты говоришь, что техника заземления
    тебе не помогла." (discussing a past technique) incorrectly REJECTed.
    They were made to require a small closed proposal/directive verb
    immediately before them within a bounded gap (RU: попробуй/попробуйте/
    сделай/сделайте/используй/используйте/примени/примените; EN:
    try/use/do) -- but that verb requirement was, in round 1, itself still
    searched anywhere in the candidate.

Correction round 2 (owner-reviewed: round 1 fixed embedded-VERB false
positives but left two related false-positive classes; fixed by extending
the SAME directive-position technique, not by adding new mechanisms):
  - "you could breathe"/"you could try/use/do <named technique>" are now
    gated to an explicit-suggestion position: candidate start, immediately
    after a sentence terminator, or immediately after the one exact frozen
    literal prefix "No rush -- "/"No rush - " (never a general em-dash or
    hyphen rule -- "You said -- you could breathe slowly." does not match,
    because the literal preceding text is "said -- ", not "No rush -- ").
    "You could breathe slowly for a minute.", "You could try a breathing
    exercise.", "No rush. You could try a grounding technique.", and "No
    rush -- you could breathe slowly, write the situation down, and then
    message someone you trust." all still REJECT; "You said you could
    breathe slowly when you felt overwhelmed." and "You said you could try
    a breathing exercise." now PASS.
  - The named-technique proposal verb (попробуй/try/etc.) and the RU
    breathing-imperative word list and RU focus imperative are now ALSO
    gated to the same directive-position anchor already used for the
    action/advice direct imperatives -- "Ты вспоминаешь, как психолог
    сказал: «Попробуй технику заземления»." and "Он сказал: «Подыши
    минуту»." (quoted, colon-preceded) no longer fire, while "Попробуй
    технику заземления.", "First try a grounding technique.", and "Сначала
    подыши две минуты, ..." still do.

Known, documented, accepted limitations (not solved by this module, and not
claimed to be):
  - Residual reflection false positive: a REFLECTIVE_STATEMENT literally quoting a
    user's own already-stated action-shaped intent (e.g. "Ты сказал, что
    тебе нужно ему позвонить.") can still match the action/advice proxy,
    because this module intentionally never receives source_text and cannot
    distinguish "bot asserts" from "bot echoes the user's own words." Adding
    source_text to resolve this is explicitly out of scope for V1.
  - Bare imperatives/hedged phrasing outside the closed vocabularies pass
    undetected (e.g. a bare "Расстанься." with no listed object structure,
    or "Возможно, стоит подумать о том, чтобы закончить эти отношения.").
  - A candidate that literally opens with a quoted imperative (e.g. a
    hypothetical candidate beginning "«Позвони ему»...") would still match
    the directive-position anchor, since this module does not attempt
    quotation understanding -- only ":"-preceded mid-text quotes are
    protected (see the correction-round note above).
  - Inline multi-step chains are only caught when one of the two existing
    bounded families independently matches somewhere in the text (see the
    module's test suite for the three worked examples); no third, dedicated
    multi-step reason exists in V1.
  - Unsupported outcome reassurance ("Всё будет хорошо."/"Everything will be
    fine.") is deliberately NOT a Policy V1 concern -- it is a different,
    named, currently-unowned pre-runtime safety gap (outcome-certainty, not
    move-fidelity), tracked outside this module and not addressed by it.
  - The action/advice modal family ("тебе нужно"/"тебе стоит"/"ты должен"/
    "ты должна"/"советую"/"рекомендую"/"you should"/"you need to"/"you
    must"/"i recommend" + a listed verb) is deliberately NOT
    directive-position-gated (see the code comment at its definition) --
    its own embedded-reporting false positive remains the documented
    residual limitation above, not a defect this correction addresses.

Two correction rounds have now closed every concrete, owner-supplied
false-positive/false-negative example against this module. This is the
final deterministic precision iteration for V1: further hypothetical
paraphrases of the same underlying question -- who is speaking, whether
text is quoted, whether a proposal is historical, whether a sentence is
reflection versus recommendation, semantic move identity, source grounding
-- are not resolvable by adding more bounded surface patterns, and are not
attempted here. They remain explicit, named, deferred requirements for a
future OFFLINE SEMANTIC EVALUATION harness (see
professional_turn_response_acceptance.py's own module docstring for the
current classification of that gap); this module does not become, and does
not claim to be moving toward becoming, a semantic NLP engine.

Precedence is fixed and first-hit-wins, exactly mirroring the fidelity
validator's convention: UNPLANNED_INTERVENTION_OR_EXERCISE_CUE is checked
before UNPLANNED_ACTION_OR_ADVICE_CUE, so a candidate matching both (e.g.
"Сделай дыхательное упражнение на пять минут.") is always reported under the
more specific intervention reason.

Only imports: __future__, re, dataclasses, enum, professional_turn_planner
(ProfessionalTurnPlan only), therapeutic_domain (PrimaryResponseMove only).
No safety_validator import, no Fidelity import, no model, no network, no DB,
no persistence, no environment/config reads, no Telegram, no runtime wiring.
Python 3.10 target (prod 3.10.12).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from professional_turn_planner import ProfessionalTurnPlan
from therapeutic_domain import PrimaryResponseMove


class PolicyStatus(str, Enum):
    PASS = "PASS"
    REJECT = "REJECT"


class PolicyRejectionReason(str, Enum):
    UNPLANNED_INTERVENTION_OR_EXERCISE_CUE = "UNPLANNED_INTERVENTION_OR_EXERCISE_CUE"
    UNPLANNED_ACTION_OR_ADVICE_CUE = "UNPLANNED_ACTION_OR_ADVICE_CUE"


@dataclass(frozen=True)
class PolicyResult:
    """status is always one of the two closed PolicyStatus members -- never
    canonicalized from an arbitrary string. reason is set if and only if
    status is REJECT."""
    status: PolicyStatus
    reason: PolicyRejectionReason | None

    def __post_init__(self):
        if not isinstance(self.status, PolicyStatus):
            raise ValueError(
                f"PolicyResult.status must be a PolicyStatus, got {type(self.status)!r}")
        if self.reason is not None and not isinstance(self.reason, PolicyRejectionReason):
            raise ValueError(
                "PolicyResult.reason must be None or a PolicyRejectionReason, "
                f"got {type(self.reason)!r}")
        if self.status is PolicyStatus.PASS:
            if self.reason is not None:
                raise ValueError("PolicyResult: status PASS requires reason=None")
        elif self.reason is None:
            raise ValueError("PolicyResult: status REJECT requires reason to be set")


# V1-supported moves only -- the exact set this module's rules are written
# against. Deliberately this module's own frozen scope declaration (matches
# professional_turn_response_fidelity_validator's identical set and identical
# fail-closed rationale: an unanticipated future Planner move raises rather
# than silently passing through unevaluated).
_SUPPORTED_POLICY_MOVES: frozenset[PrimaryResponseMove] = frozenset({
    PrimaryResponseMove.OPEN_INVITATION,
    PrimaryResponseMove.FOCUSED_QUESTION,
    PrimaryResponseMove.REFLECTIVE_STATEMENT,
    PrimaryResponseMove.CLOSING,
})


def _gap(n: int) -> str:
    """A bounded run of 0..n arbitrary whitespace-delimited filler tokens."""
    return r"(?:\S+\s+){0," + str(n) + r"}"


# -- Directive-position anchors ----------------------------------------------
# A "directive position" is the start of the candidate, or immediately after
# a sentence-terminal punctuation mark followed by whitespace (re.MULTILINE
# so a bare newline also counts as a line start). This is what lets a true
# imperative at the start of a sentence ("Leave him.", "I hear you. Leave
# him.") reject while the identical verb embedded inside a reporting/
# reflective clause ("You said you want to leave him.") passes -- the
# imperative-shaped words are simply never evaluated unless they begin their
# own sentence. Deliberately NOT anchored on ":" (would fire again on
# reported/quoted speech such as 'Он сказал: «Позвони ему».', where the
# quoted words are not this candidate's own imperative). This is a
# conservative surface rule, not quotation understanding: a candidate that
# OPENS directly with a quoted imperative is a documented, accepted residual
# false-positive, the same class of limitation as every other bounded rule
# in this module.
_RU_DIRECTIVE_START = r"(?:^|(?<=[.!?]\s))\s*(?:пожалуйста,?\s+)?"
_EN_DIRECTIVE_START = r"(?:^|(?<=[.!?]\s))\s*(?:please\s+)?"
# Breathing/focus instructions additionally tolerate a leading sequencing
# adverb ("First breathe for two minutes...") in the same directive slot --
# motivated directly by the multi-step worked examples, not invented scope.
_EN_BREATHING_DIRECTIVE_START = r"(?:^|(?<=[.!?]\s))\s*(?:please\s+|first\s+|then\s+|now\s+)?"
# RU intervention family (breathing/focus imperatives, named-technique
# proposal verbs) additionally tolerates a leading "сначала" -- same role as
# EN "first" above, motivated by the RU multi-step worked example ("Сначала
# подыши две минуты, потом ...").
_RU_INTERVENTION_DIRECTIVE_START = r"(?:^|(?<=[.!?]\s))\s*(?:пожалуйста,?\s+|сначала\s+)?"
# Explicit-suggestion position (second correction round, defect A/B): a
# candidate-level "you could ..." suggestion is only evaluated at candidate
# start, immediately after a sentence-terminal punctuation mark, or right
# after the one exact frozen low-pressure prefix "No rush -- "/"No rush - "
# (an exact literal string, never a general em-dash/hyphen rule -- see the
# correction-round docstring note below for why "You said -- you could
# breathe slowly." must NOT match this).
_EN_SUGGESTION_START = (
    r"(?:^|(?<=[.!?]\s)|(?<=No rush — )|(?<=No rush - ))\s*")


# -- Action/advice: closed externally-directed action vocabularies ----------

_RU_ACTION_VERBS = (
    "расстаться", "уйти", "написать", "позвонить", "встретиться",
    "разорвать", "поставить", "сказать", "закончить", "прекратить", "бросить",
)
_EN_ACTION_VERBS = (
    "leave", "leaving", "end", "ending", "break up", "breaking up",
    "stop", "stopping", "quit", "quitting", "cut off", "cutting off",
    "tell", "telling", "call", "calling", "message", "messaging",
    "write", "writing", "contact", "contacting",
)
_RU_ACTION_VERB_ALT = "|".join(_RU_ACTION_VERBS)
_EN_ACTION_VERB_ALT = "|".join(_EN_ACTION_VERBS)

_ACTION_OR_ADVICE_PATTERNS = [
    # RU modal + bounded verb. Not directive-position-gated: the modal
    # phrase itself ("тебе нужно" + a listed verb) is already the bounded
    # signal, and gating it on sentence-start would silently reopen the
    # documented residual reflection false positive for mid-sentence
    # reported speech in the opposite, unwanted direction (these patterns
    # were not part of the owner-reviewed blockers).
    re.compile(
        r"\b(?:тебе нужно|тебе стоит|ты должен|ты должна)\s+" + _gap(1)
        + r"(?:" + _RU_ACTION_VERB_ALT + r")\b", re.IGNORECASE),
    re.compile(
        r"\b(?:советую|рекомендую)\b(?:\s+тебе)?\s+(?:" + _RU_ACTION_VERB_ALT + r")\b",
        re.IGNORECASE),
    re.compile(
        r"\bпопробу(?:й|йте)\b\s+" + _gap(2) + r"(?:" + _RU_ACTION_VERB_ALT + r")\b",
        re.IGNORECASE),
    # RU closed direct-imperative + third-party object constructions --
    # directive-position gated (see _RU_DIRECTIVE_START above).
    re.compile(
        _RU_DIRECTIVE_START + r"\b(?:уйди|уйдите)\s+от\s+(?:него|неё|нее|них)\b",
        re.IGNORECASE | re.MULTILINE),
    re.compile(
        _RU_DIRECTIVE_START + r"\b(?:расстанься|расстаньтесь)\s+с\s+(?:ним|ней|ними)\b",
        re.IGNORECASE | re.MULTILINE),
    re.compile(
        _RU_DIRECTIVE_START
        + r"\b(?:разорви|разорвите)\s+(?:эти\s+|эту\s+)?(?:отношения|связь)\b",
        re.IGNORECASE | re.MULTILINE),
    re.compile(
        _RU_DIRECTIVE_START
        + r"\b(?:прекрати|прекратите)\s+(?:с\s+(?:ним|ней|ними)\s+)?общаться\b",
        re.IGNORECASE | re.MULTILINE),
    re.compile(
        _RU_DIRECTIVE_START + r"\b(?:позвони|позвоните)\s+(?:ему|ей|им)\b",
        re.IGNORECASE | re.MULTILINE),
    re.compile(
        _RU_DIRECTIVE_START + r"\b(?:напиши|напишите)\s+(?:ему|ей|им)\b",
        re.IGNORECASE | re.MULTILINE),
    # EN modal + bounded verb. Not directive-position-gated, same rationale
    # as the RU modal family above.
    re.compile(
        r"\byou (?:should|need to|must)\s+" + _gap(1) + r"(?:" + _EN_ACTION_VERB_ALT + r")\b",
        re.IGNORECASE),
    re.compile(
        r"\bi recommend\b(?:\s+that\s+you)?\s+" + _gap(1) + r"(?:" + _EN_ACTION_VERB_ALT + r")\b",
        re.IGNORECASE),
    re.compile(
        r"\btry\b\s+" + _gap(2) + r"(?:" + _EN_ACTION_VERB_ALT + r")\b", re.IGNORECASE),
    # EN closed direct-imperative + third-party object constructions --
    # directive-position gated (see _EN_DIRECTIVE_START above). This is the
    # fix for the owner-reviewed blocker: "You said you want to leave him."
    # no longer matches, because "leave him" does not begin its own sentence.
    re.compile(
        _EN_DIRECTIVE_START + r"\bleave\s+(?:him|her|them)\b", re.IGNORECASE | re.MULTILINE),
    re.compile(
        _EN_DIRECTIVE_START + r"\bend\s+the\s+relationship\b", re.IGNORECASE | re.MULTILINE),
    re.compile(
        _EN_DIRECTIVE_START + r"\bbreak\s+up\s+with\s+(?:him|her|them)\b",
        re.IGNORECASE | re.MULTILINE),
    re.compile(
        _EN_DIRECTIVE_START + r"\bcut\s+(?:him|her|them)\s+off\b", re.IGNORECASE | re.MULTILINE),
    re.compile(
        _EN_DIRECTIVE_START + r"\bcall\s+(?:him|her|them)\b", re.IGNORECASE | re.MULTILINE),
    re.compile(
        _EN_DIRECTIVE_START + r"\bmessage\s+(?:him|her|them)\b", re.IGNORECASE | re.MULTILINE),
]


# -- Intervention/exercise: bounded named phrases + exact imperative forms --

# RU named-technique noun phrases (case-declension-tolerant on the noun only,
# never on the verb -- see _NAMED_TECHNIQUE_PROPOSAL_PATTERNS below for why a
# bare mention of one of these is intentionally NOT sufficient on its own).
_RU_NAMED_TECHNIQUE_BODIES = (
    r"дыхательн\w*\s+упражнени\w*",
    r"техник\w*\s+заземления",
    r"упражнение\s+на\s+заземление",
)
# EN named-technique noun phrases (same role as the RU set above).
_EN_NAMED_TECHNIQUE_BODIES = (
    r"breathing\s+exercise",
    r"grounding\s+technique",
    r"grounding\s+exercise",
)

# Owner-reviewed blocker fix (round 1): mentioning/discussing a named
# technique ("Ты говоришь, что техника заземления тебе не помогла.", "You
# said the grounding technique did not help.") is not the same as proposing
# one. A bare named-technique phrase is therefore never sufficient by itself
# -- it only triggers when immediately preceded (within a small bounded gap)
# by a closed proposal/directive verb.
#
# Owner-reviewed blocker fix (round 2): that alone was still embedded-clause
# blind -- "Ты вспоминаешь, как психолог сказал: «Попробуй технику
# заземления»." and "He suggested that you try a breathing exercise." both
# contain a listed proposal verb immediately before a listed technique, but
# neither is this candidate's own proposal. The proposal verb is now ALSO
# gated to a directive position (see _RU_INTERVENTION_DIRECTIVE_START /
# _EN_BREATHING_DIRECTIVE_START above) -- quoted speech after ":" and a verb
# embedded mid-sentence ("...that you try...") no longer fire, while
# "Попробуй технику заземления.", "Please try a breathing exercise.", and
# "First try a grounding technique." still do. This does not attempt
# negation/semantic analysis or speaker attribution; it is a narrow, bounded
# surface-position requirement, same class of proxy as every other rule in
# this module.
_RU_PROPOSAL_VERB_ALT = "|".join((
    "попробуй", "попробуйте", "сделай", "сделайте",
    "используй", "используйте", "примени", "примените",
))
_EN_PROPOSAL_VERB_ALT = "|".join(("try", "use", "do"))

_NAMED_TECHNIQUE_PROPOSAL_PATTERNS = [
    re.compile(
        _RU_INTERVENTION_DIRECTIVE_START
        + r"\b(?:" + _RU_PROPOSAL_VERB_ALT + r")\b\s+" + _gap(2) + r"(?:" + body + r")",
        re.IGNORECASE | re.MULTILINE)
    for body in _RU_NAMED_TECHNIQUE_BODIES
] + [
    re.compile(
        _EN_BREATHING_DIRECTIVE_START
        + r"\b(?:" + _EN_PROPOSAL_VERB_ALT + r")\b\s+" + _gap(2) + r"(?:" + body + r")",
        re.IGNORECASE | re.MULTILINE)
    for body in _EN_NAMED_TECHNIQUE_BODIES
]

# EN explicit named-technique suggestion (family B, mirroring the "you could
# breathe" suggestion family below): "You could try a breathing exercise."
# is still a candidate-level unplanned-intervention suggestion, gated to the
# same explicit-suggestion position as "you could breathe" -- "You said you
# could try a breathing exercise." (embedded reporting) does not begin its
# own sentence at "you could", so it does not match.
_NAMED_TECHNIQUE_SUGGESTION_PATTERNS = [
    re.compile(
        _EN_SUGGESTION_START
        + r"you could\s+(?:" + _EN_PROPOSAL_VERB_ALT + r")\b\s+" + _gap(2) + r"(?:" + body + r")",
        re.IGNORECASE | re.MULTILINE)
    for body in _EN_NAMED_TECHNIQUE_BODIES
]

_INTERVENTION_OR_EXERCISE_PATTERNS = [
    *_NAMED_TECHNIQUE_PROPOSAL_PATTERNS,
    *_NAMED_TECHNIQUE_SUGGESTION_PATTERNS,
    # RU focus instruction: owner-reviewed blocker fix (round 1) -- the verb
    # must be the EXACT closed imperative word ("сосредоточься"/
    # "сосредоточьтесь"), never a stem ("сосредоточ\w*"), which is what
    # previously matched reporting/past-tense forms ("сосредоточился",
    # "сосредоточилась", "сосредоточивался"). The breathing-object noun
    # ("дыхани\w*") keeps its bounded case-declension tolerance -- that was
    # never the defect; only the verb stem was.
    # Round 2: also gated to a directive position (same technique as the
    # breathing-imperative word list below) so "Ты вспоминаешь его совет:
    # «Сосредоточься на дыхании»." (quoted, colon-preceded) no longer fires.
    re.compile(
        _RU_INTERVENTION_DIRECTIVE_START
        + r"\b(?:сосредоточься|сосредоточьтесь)\s+на\s+(?:своём\s+)?дыхани\w*",
        re.IGNORECASE | re.MULTILINE),
    # RU exact closed breathing-imperative word forms (word-bounded, not a
    # stem -- deliberately excludes "подышал"/"дышал"/other inflected
    # forms). Round 2: also gated to a directive position so "Он сказал:
    # «Подыши минуту»." (quoted, colon-preceded) no longer fires, while
    # "Подыши медленно пару минут." and "Сначала подыши две минуты, ..."
    # still do.
    re.compile(
        _RU_INTERVENTION_DIRECTIVE_START
        + r"\b(?:подыши|подышите|вдохни|вдохните|выдохни|выдохните)\b",
        re.IGNORECASE | re.MULTILINE),
    # EN focus instruction: owner-reviewed blocker fix -- exact imperative
    # word "focus" only (never "focusing", which is what previously matched
    # reporting: "You said focusing on your breathing helped."), gated to a
    # directive position so an embedded bare "focus" in a reporting clause
    # still cannot fire.
    re.compile(
        _EN_BREATHING_DIRECTIVE_START + r"\bfocus\s+on\s+(?:your\s+)?breathing\b",
        re.IGNORECASE | re.MULTILINE),
    # EN direct breathing instruction (family A, per the frozen correction):
    # exact word "breathe" (never "breathes"/"breathed"), gated to a
    # directive position, with bounded manner-adverb or duration
    # co-occurrence. This is the fix for "I notice you breathe slowly when
    # anxious." (embedded reporting, no longer matches) while "Breathe
    # slowly for a minute." and "First breathe for two minutes." (directive
    # position, optionally after "first"/"then"/"now"/"please") still
    # reject.
    re.compile(
        _EN_BREATHING_DIRECTIVE_START + r"\bbreathe\b\s+(?:slowly|deeply|calmly|gently)\b",
        re.IGNORECASE | re.MULTILINE),
    re.compile(
        _EN_BREATHING_DIRECTIVE_START + r"\bbreathe\b\s+" + _gap(4)
        + r"(?:minute|minutes|second|seconds)\b", re.IGNORECASE | re.MULTILINE),
    # EN explicit breathing suggestion (family B). Owner-reviewed blocker
    # fix (round 2): previously matched "you could breathe ..." ANYWHERE in
    # the candidate, so "You said you could breathe slowly when you felt
    # overwhelmed." (embedded reporting) incorrectly matched. Now gated to
    # the explicit-suggestion position (_EN_SUGGESTION_START) -- candidate
    # start, after a sentence terminator, or after the one exact frozen
    # "No rush -- "/"No rush - " prefix -- so "You could breathe slowly for
    # a minute." and "No rush -- you could breathe slowly, write the
    # situation down, and then message someone you trust." still REJECT
    # while the embedded-reporting form PASSes.
    re.compile(
        _EN_SUGGESTION_START + r"you could\s+breathe\b\s+(?:slowly|deeply|calmly|gently)\b",
        re.IGNORECASE | re.MULTILINE),
    re.compile(
        _EN_SUGGESTION_START + r"you could\s+breathe\b\s+" + _gap(4)
        + r"(?:minute|minutes|second|seconds)\b", re.IGNORECASE | re.MULTILINE),
]


def _matches_any(patterns, text: str) -> bool:
    return any(p.search(text) for p in patterns)


def validate_response_policy(
        plan: ProfessionalTurnPlan, candidate_text: str) -> PolicyResult:
    """Deterministic, synchronous, offline. Evaluates the frozen V1 surface
    rules in exact precedence order (intervention/exercise before
    action/advice); the first family matched determines the single returned
    reason. Both families are evaluated unconditionally for every
    V1-supported move -- there is no move-based bypass."""
    if not isinstance(plan, ProfessionalTurnPlan):
        raise ValueError(
            f"validate_response_policy: plan must be a ProfessionalTurnPlan, got {type(plan)!r}")
    if type(candidate_text) is not str:
        raise ValueError(
            f"validate_response_policy: candidate_text must be a str, got {type(candidate_text)!r}")
    if not candidate_text:
        raise ValueError("validate_response_policy: candidate_text must be non-empty")
    if not candidate_text.strip():
        raise ValueError("validate_response_policy: candidate_text must not be whitespace-only")
    if plan.move not in _SUPPORTED_POLICY_MOVES:
        raise ValueError(
            "validate_response_policy: plan.move is not a V1-supported policy "
            f"move: {plan.move!r} -- this validator requires an explicit update "
            "before it can reason about a newer Planner move")

    if _matches_any(_INTERVENTION_OR_EXERCISE_PATTERNS, candidate_text):
        return PolicyResult(
            status=PolicyStatus.REJECT,
            reason=PolicyRejectionReason.UNPLANNED_INTERVENTION_OR_EXERCISE_CUE)

    if _matches_any(_ACTION_OR_ADVICE_PATTERNS, candidate_text):
        return PolicyResult(
            status=PolicyStatus.REJECT,
            reason=PolicyRejectionReason.UNPLANNED_ACTION_OR_ADVICE_CUE)

    return PolicyResult(status=PolicyStatus.PASS, reason=None)
