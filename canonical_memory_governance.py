"""Canonical Memory Governance V1 (Phase 2B-1) -- the closed, pure policy
that decides what a future canonical-memory writer is allowed to do.

OFFLINE POLICY CONTRACT ONLY. This module owns exactly three closed
decisions -- which MemoryCategory values a generic candidate writer may
create, which MemoryLifecycle transitions are legal, and what shape raw
candidate content/provenance must already have before persistence -- and
nothing else. It performs no I/O of any kind: no network call, no model
call, no database access, no Telegram delivery, no environment reads, no
secret access, no filesystem access, no time/random behavior.

WHAT THIS IS NOT:
  - a persistence layer -- it decides policy only; database.py's governed
    functions (add_governed_core_memory_candidate,
    apply_governed_core_memory_lifecycle_transition) are the only code
    that may act on this policy against core_memory_items;
  - a memory extractor -- it does not read conversations, does not call a
    model, and does not decide WHAT a candidate should say; it only
    decides whether a proposed category/transition/shape is even legal.
    Building the actual extractor is Phase 2B-2, not this module;
  - an epistemic upgrade mechanism -- it never turns a hypothesis into a
    fact, never auto-confirms a CANDIDATE, and never raises confidence.
    MemoryCategory.HYPOTHESIS stays HYPOTHESIS regardless of lifecycle;
    this module has no code path that changes a category during a
    lifecycle transition at all.

Reuses MemoryCategory/MemoryLifecycle from therapeutic_domain.py (the
existing pure domain vocabulary) rather than defining a second one.

Only imports: __future__ and therapeutic_domain. Python 3.10 target (prod
3.10.12)."""
from __future__ import annotations

from therapeutic_domain import MemoryCategory, MemoryLifecycle

# ── Candidate-writable categories (Phase 2B-1 V1) ───────────────────────────
# A future automatic candidate extractor (Phase 2B-2, NOT this slice) may
# only create CANDIDATE rows in these five categories. CONFIRMED_PATTERN,
# INTERVENTION, OUTCOME, and PLAN are deliberately excluded from *generic*
# candidate creation -- they require their own specialized evidence/outcome
# contracts (a pattern needs cross-episode confirmation evidence, an
# intervention/outcome pair already has the dedicated core_interventions/
# core_outcomes machinery, a plan needs its own governance) that this
# generic boundary does not implement. This does NOT remove those values
# from MemoryCategory -- they remain legal enum members for OTHER, more
# specialized write paths that do not exist yet; this module only forbids
# them at THIS generic candidate-write boundary.
GENERIC_CANDIDATE_WRITABLE_CATEGORIES: frozenset[MemoryCategory] = frozenset({
    MemoryCategory.EXPLICIT_FACT,
    MemoryCategory.PREFERENCE,
    MemoryCategory.GOAL,
    MemoryCategory.EPISODE_MAP,
    MemoryCategory.HYPOTHESIS,
})


def is_generic_candidate_writable_category(category: MemoryCategory) -> bool:
    """True iff a generic candidate writer may create a CANDIDATE row in
    this category. Raises ValueError (fail closed) if `category` is not
    exactly a MemoryCategory member -- never silently treated as False for
    a malformed input, since that would look identical to a legal,
    deliberate rejection."""
    if type(category) is not MemoryCategory:
        raise ValueError(
            "is_generic_candidate_writable_category: category must be "
            f"exactly a MemoryCategory, got {type(category)!r}")
    return category in GENERIC_CANDIDATE_WRITABLE_CATEGORIES


# ── Lifecycle transition graph (Phase 2B-1 V1) ──────────────────────────────
# The ONLY legal in-place lifecycle transitions this slice allows. Every
# edge is a (current, target) pair; anything not listed here is illegal,
# including every same-state "transition" (X -> X is never in this set,
# even for a non-terminal lifecycle -- there is no arbitrary same-state
# update pretending to be a transition).
#
# CORRECTED is deliberately never a transition TARGET here -- a corrected
# item will later be created as a brand-new, append-only row by a
# dedicated correction transaction in a LATER Phase 2B slice, never by
# mutating an existing row's lifecycle in place. Implementing that
# transaction is explicitly out of scope for this slice.
ALLOWED_LIFECYCLE_TRANSITIONS: frozenset[tuple[MemoryLifecycle, MemoryLifecycle]] = frozenset({
    (MemoryLifecycle.CANDIDATE, MemoryLifecycle.PROPOSED),
    (MemoryLifecycle.CANDIDATE, MemoryLifecycle.EXPIRED),
    (MemoryLifecycle.PROPOSED, MemoryLifecycle.CONFIRMED),
    (MemoryLifecycle.PROPOSED, MemoryLifecycle.REJECTED),
    (MemoryLifecycle.PROPOSED, MemoryLifecycle.EXPIRED),
    (MemoryLifecycle.CONFIRMED, MemoryLifecycle.HISTORICAL),
    (MemoryLifecycle.CONFIRMED, MemoryLifecycle.REJECTED),
    (MemoryLifecycle.CORRECTED, MemoryLifecycle.HISTORICAL),
    (MemoryLifecycle.CORRECTED, MemoryLifecycle.REJECTED),
})

# Terminal in THIS slice -- no edge in ALLOWED_LIFECYCLE_TRANSITIONS ever has
# one of these as its `current` (source) element, so nothing can transition
# OUT of them here (no resurrection from REJECTED/EXPIRED/HISTORICAL).
# Exposed separately (rather than only implicitly, via
# ALLOWED_LIFECYCLE_TRANSITIONS) so a caller can ask "is this lifecycle
# terminal" without enumerating the whole edge set.
TERMINAL_LIFECYCLES: frozenset[MemoryLifecycle] = frozenset({
    MemoryLifecycle.HISTORICAL,
    MemoryLifecycle.REJECTED,
    MemoryLifecycle.EXPIRED,
})


def is_allowed_lifecycle_transition(current: MemoryLifecycle, target: MemoryLifecycle) -> bool:
    """True iff (current -> target) is exactly one of the closed edges in
    ALLOWED_LIFECYCLE_TRANSITIONS. Raises ValueError (fail closed) if
    either argument is not exactly a MemoryLifecycle member."""
    if type(current) is not MemoryLifecycle:
        raise ValueError(
            "is_allowed_lifecycle_transition: current must be exactly a "
            f"MemoryLifecycle, got {type(current)!r}")
    if type(target) is not MemoryLifecycle:
        raise ValueError(
            "is_allowed_lifecycle_transition: target must be exactly a "
            f"MemoryLifecycle, got {type(target)!r}")
    return (current, target) in ALLOWED_LIFECYCLE_TRANSITIONS


# ── Raw candidate content boundary (Phase 2B-1 V1) ──────────────────────────
# Mirrors therapeutic_domain.MemoryItem's own content bound (its
# __post_init__ silently strips/clips to this many characters via _clip).
# This module's validator runs BEFORE a MemoryItem is ever constructed and
# REJECTS anything __post_init__ would otherwise have to alter -- it never
# relies on that silent clipping/stripping to make unsafe input safe. The
# governed database layer additionally re-verifies, after construction,
# that the built MemoryItem.content is byte-for-byte identical to the raw
# input accepted here, as a second, independent defense-in-depth layer --
# so even a future drift between this number and MemoryItem's own internal
# bound would surface as a rejection, never as silent truncation.
MAX_NEW_CANDIDATE_CONTENT_CHARS = 1000


def validate_new_candidate_content(content: str) -> None:
    """Raises ValueError (fail closed) unless `content` is exactly a str,
    non-empty, not whitespace-only, already normalized with respect to
    surrounding whitespace (content == content.strip()), and at most
    MAX_NEW_CANDIDATE_CONTENT_CHARS characters. Returns None on success --
    this is a pure validator, never a transform; it never returns a
    stripped/clipped copy of `content`. Never echoes the content itself in
    an exception message (canonical memory is psychological profile
    data)."""
    if type(content) is not str:
        raise ValueError(
            f"validate_new_candidate_content: content must be a str, got {type(content)!r}")
    if not content:
        raise ValueError("validate_new_candidate_content: content must be non-empty")
    if not content.strip():
        raise ValueError("validate_new_candidate_content: content must not be whitespace-only")
    if content != content.strip():
        raise ValueError(
            "validate_new_candidate_content: content must already be normalized "
            "(content == content.strip()); no silent stripping is performed")
    if len(content) > MAX_NEW_CANDIDATE_CONTENT_CHARS:
        raise ValueError(
            "validate_new_candidate_content: content exceeds "
            f"{MAX_NEW_CANDIDATE_CONTENT_CHARS} characters "
            f"(got {len(content)}); no silent truncation is performed")


# ── Raw candidate provenance shape (Phase 2B-1 V1) ──────────────────────────
# Structural validation ONLY -- whether each id actually exists, belongs to
# this user, and is genuinely user-authored requires a database read and is
# therefore NOT this pure module's job; see
# database.add_governed_core_memory_candidate for that check.
def validate_new_candidate_provenance_shape(source_event_ids) -> None:
    """Raises ValueError (fail closed) unless `source_event_ids` is exactly
    a list, non-empty, and every entry is exactly a positive int (bool
    rejected even though bool is an int subclass) with no duplicates.
    Returns None on success."""
    if type(source_event_ids) is not list:
        raise ValueError(
            "validate_new_candidate_provenance_shape: source_event_ids must "
            f"be a list, got {type(source_event_ids)!r}")
    if not source_event_ids:
        raise ValueError(
            "validate_new_candidate_provenance_shape: source_event_ids must "
            "be non-empty -- every new governed candidate requires provenance")
    for event_id in source_event_ids:
        if type(event_id) is not int or event_id <= 0:
            raise ValueError(
                "validate_new_candidate_provenance_shape: every source_event_ids "
                f"entry must be a positive int, got {event_id!r}")
    if len(set(source_event_ids)) != len(source_event_ids):
        raise ValueError(
            "validate_new_candidate_provenance_shape: source_event_ids must "
            f"not contain duplicates, got {source_event_ids!r}")
