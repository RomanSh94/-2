"""Professional Core V2 -- Canonical Case Context Foundation V1 (Phase 2A).

OFFLINE DOMAIN CONTRACT ONLY. This module defines a small, immutable,
provenance-preserving transport for already-CONFIRMED/CORRECTED longitudinal
case memory, entirely separate from both the current-turn `source_text` and
from ProfessionalConversationContext's own raw prior-turn history (see
professional_turn_conversation_context.py). It performs no I/O of any kind:
no network call, no model call, no database access, no Telegram delivery, no
environment reads, no secret access, no filesystem access, no time/random
behavior.

WHAT THIS IS NOT -- a CanonicalCaseContext is never:
  - raw conversation history (that is ProfessionalConversationContext's own,
    structurally separate concern -- the two are never merged);
  - an LLM-generated rolling summary;
  - a user profile or latent profile of any kind;
  - a diagnosis;
  - free-form model-generated case-concept text (see the governed
    working-hypothesis type in therapeutic_domain.py, which this module does
    not touch);
  - a new persistence system -- every item here is a read-only, already-
    persisted row from the existing core_memory_items table;
  - a reason to add a model/provider call -- this module makes none, and
    nothing that consumes it in this slice makes an extra one either (see
    PHASE 2A SCOPE below).

WHAT THIS IS -- a bounded, read-only view over already-confirmed longitudinal
memory (therapeutic_domain.MemoryItem rows persisted in core_memory_items,
via database's own internal write primitives -- see database.py's Phase 2B-1
Governed Canonical Memory Persistence Boundary section for the current
supported write path), restricted to exactly the two lifecycle
values that are allowed to influence a response at all
(MemoryLifecycle.influences_responses -- CONFIRMED and CORRECTED; see
therapeutic_domain.py). CANDIDATE, PROPOSED, REJECTED, HISTORICAL, and
EXPIRED items must never appear here and must never influence the
Professional Runtime.

CATEGORY IS NEVER SEMANTICALLY UPGRADED -- MemoryCategory.HYPOTHESIS is
carried through completely unchanged even when its lifecycle is CONFIRMED or
CORRECTED. A confirmed-lifecycle hypothesis is still, by its own category, a
hypothesis -- CONFIRMED here means only "this item has passed this
repository's existing confirmation gate and may influence a response," never
"this hypothesis is hereby a fact." This module never renames, recategorizes,
or reinterprets `category`; it is carried through byte-for-byte from the
already-persisted MemoryItem.

PHASE 2A SCOPE -- this is a FOUNDATION slice only. CanonicalCaseContext is
threaded into ProfessionalTurnRuntimeContext (professional_turn_runtime_
context.py) and populated by bot.py's `_run_professional_free_text_and_
deliver` from real core_memory_items rows, but no model-facing stage
(call_turn_analyzer, call_turn_plan_proposer, render_turn_response) reads it
yet -- consumer wiring is explicitly deferred to a separately authorized,
separately reviewed Phase 2B. This module does not implement: generating new
memory candidates, automatic confirmation, user confirmation/correction UX,
governed case-concept persistence integration, or any model-facing
consumption.

FROZEN V1 BOUNDS -- deliberately small, deliberately explicit, and
deliberately enforced by FAILING CLOSED (raising ValueError) rather than by
silently truncating or rewriting content:
  - MAX_CASE_ITEMS = 8
  - MAX_TOTAL_CASE_CHARS = 3000 -- a single MemoryItem.content is already
    bounded to 1000 characters by therapeutic_domain.py's own contract, so
    this total is the binding constraint well before MAX_CASE_ITEMS is (3
    items at the per-item cap already reach it).

OMISSION POLICY (mirrors professional_turn_conversation_context.py's own
policy for prior conversation turns): when eligible material exceeds these
bounds, prefer deterministic omission of the OLDEST items first (lowest
memory_item_id), always preserving the EXACT, unmodified content of whichever
newer items remain included. An item is never partially included -- either
the whole item (with its exact content and exact source_event_ids) is
included, or it is omitted entirely. Never truncates content to make it fit.

Only imports: __future__, dataclasses, and therapeutic_domain (MemoryCategory,
MemoryLifecycle, MemoryItem -- shared pure domain vocabulary, itself already
used by the live Conversation Controller path). No bot.py import, no
database import, no Telegram import, no model/network import, no config/env/
filesystem/network access of any kind. Python 3.10 target (prod 3.10.12).
"""
from __future__ import annotations

from dataclasses import dataclass

from therapeutic_domain import MemoryCategory, MemoryItem, MemoryLifecycle

# -- Frozen V1 engineering bounds -- see module docstring for rationale. ---
MAX_CASE_ITEMS = 8
MAX_TOTAL_CASE_CHARS = 3000


@dataclass(frozen=True)
class CanonicalCaseItem:
    """One already-persisted, already-confirmed-or-corrected core memory
    item. Trusted only up to this repository's own existing confirmation
    gate (MemoryLifecycle.influences_responses) -- never trusted as a
    diagnosis, never trusted as more certain than its own `category` states
    (see WHAT THIS IS NOT / CATEGORY IS NEVER SEMANTICALLY UPGRADED in the
    module docstring). `memory_item_id` is the real, persisted
    core_memory_items.id -- provenance, not a display field."""
    memory_item_id: int
    category: MemoryCategory
    lifecycle: MemoryLifecycle
    content: str
    source_event_ids: tuple[int, ...]

    def __post_init__(self):
        if type(self.memory_item_id) is not int:
            raise ValueError(
                "CanonicalCaseItem.memory_item_id must be an int, got "
                f"{type(self.memory_item_id)!r}")
        if self.memory_item_id <= 0:
            raise ValueError(
                "CanonicalCaseItem.memory_item_id must be a positive int, "
                f"got {self.memory_item_id!r}")
        if type(self.category) is not MemoryCategory:
            raise ValueError(
                "CanonicalCaseItem.category must be exactly a MemoryCategory, "
                f"got {type(self.category)!r}")
        if type(self.lifecycle) is not MemoryLifecycle:
            raise ValueError(
                "CanonicalCaseItem.lifecycle must be exactly a MemoryLifecycle, "
                f"got {type(self.lifecycle)!r}")
        # Reuses the existing MemoryLifecycle.influences_responses rule
        # (therapeutic_domain.py) rather than redefining the CONFIRMED/
        # CORRECTED vocabulary a second time by hand -- a single source of
        # truth for which lifecycle values may influence a response at all.
        if not self.lifecycle.influences_responses:
            raise ValueError(
                "CanonicalCaseItem.lifecycle must be CONFIRMED or CORRECTED "
                f"(MemoryLifecycle.influences_responses), got {self.lifecycle!r}")
        if type(self.content) is not str:
            raise ValueError(
                f"CanonicalCaseItem.content must be a str, got {type(self.content)!r}")
        if not self.content or not self.content.strip():
            raise ValueError(
                "CanonicalCaseItem.content must be non-empty and not whitespace-only")
        if type(self.source_event_ids) is not tuple:
            raise ValueError(
                "CanonicalCaseItem.source_event_ids must be a tuple, got "
                f"{type(self.source_event_ids)!r}")
        for event_id in self.source_event_ids:
            if type(event_id) is not int or event_id <= 0:
                raise ValueError(
                    "CanonicalCaseItem.source_event_ids entries must all be "
                    f"positive int, got {event_id!r}")


@dataclass(frozen=True)
class CanonicalCaseContext:
    """An ordered, bounded tuple of already-confirmed/corrected canonical
    case items only. Items must already be in strictly increasing
    memory_item_id order (this is both the ordering proof and the
    duplicate/out-of-order rejection in one check, since these are real
    autoincrement row ids -- mirrors ProfessionalConversationContext's own
    identical discipline). Fails closed on any bound violation -- never
    silently reorders, deduplicates, truncates, or drops an item to make an
    invalid batch fit; that decision belongs to whichever caller constructs
    the tuple in the first place (see OMISSION POLICY in the module
    docstring, and build_canonical_case_context_from_memory_records below)."""
    items: tuple[CanonicalCaseItem, ...]

    def __post_init__(self):
        if type(self.items) is not tuple:
            raise ValueError(
                f"CanonicalCaseContext.items must be a tuple, got {type(self.items)!r}")
        for item in self.items:
            if type(item) is not CanonicalCaseItem:
                raise ValueError(
                    "CanonicalCaseContext.items entries must be "
                    f"CanonicalCaseItem, got {type(item)!r}")
        if len(self.items) > MAX_CASE_ITEMS:
            raise ValueError(
                "CanonicalCaseContext.items exceeds the "
                f"{MAX_CASE_ITEMS}-item V1 bound (got {len(self.items)} items)")
        memory_item_ids = [item.memory_item_id for item in self.items]
        if any(memory_item_ids[i] >= memory_item_ids[i + 1]
               for i in range(len(memory_item_ids) - 1)):
            raise ValueError(
                "CanonicalCaseContext.items must be in strict chronological "
                "order (strictly increasing memory_item_id); got "
                f"{memory_item_ids!r}")
        total_chars = sum(len(item.content) for item in self.items)
        if total_chars > MAX_TOTAL_CASE_CHARS:
            raise ValueError(
                "CanonicalCaseContext total content exceeds the "
                f"{MAX_TOTAL_CASE_CHARS}-character V1 bound "
                f"(got {total_chars} characters across {len(self.items)} items)")

    @property
    def is_empty(self) -> bool:
        return len(self.items) == 0


EMPTY_CANONICAL_CASE_CONTEXT = CanonicalCaseContext(items=())


def build_canonical_case_context_from_memory_records(records) -> CanonicalCaseContext:
    """Pure, synchronous builder: turns already-fetched (memory_item_id,
    MemoryItem) pairs into a CanonicalCaseContext by applying the OMISSION
    POLICY (module docstring) and the FROZEN V1 BOUNDS. No I/O of any kind --
    the caller (e.g. database.list_core_memory_item_records(uid,
    influencing_only=True)) is responsible for the actual DB read; this
    function trusts none of that filtering blindly (see DEFENSE IN DEPTH
    below).

    `records` -- any iterable of (memory_item_id: int, MemoryItem) pairs, in
    ascending memory_item_id order (real core_memory_items.id provenance,
    paired with the already-deserialized MemoryItem it identifies).

    DEFENSE IN DEPTH -- every record is independently re-checked against
    MemoryItem.influences_responses here, even though the caller is already
    expected to have filtered to CONFIRMED/CORRECTED only. This reuses the
    existing lifecycle rule (therapeutic_domain.py) rather than redefining
    it a second time by hand, so there is exactly one place that decides
    which lifecycle values may influence a response.

    FAIL-CLOSED ON STRUCTURAL MALFORMATION (Phase 2A correction, P2-2) --
    this function distinguishes two situations that must never be
    conflated:
      - a STRUCTURALLY VALID record whose lifecycle simply does not
        influence responses (CANDIDATE, PROPOSED, REJECTED, HISTORICAL,
        EXPIRED) -- this is expected, ordinary filtering: the record is
        deterministically OMITTED (skipped), never an error.
      - a STRUCTURALLY MALFORMED record -- `memory_item_id` not exactly a
        positive int (a bool is never accepted here either, despite bool
        being an int subclass), an item that is not exactly a MemoryItem,
        or malformed provenance (see PROVENANCE VALIDATED BEFORE LIFECYCLE
        FILTER below) -- this is NEVER silently filtered out. It raises
        ValueError immediately, failing the ENTIRE batch rather than
        silently preserving only the valid subset (a mix of one malformed
        and several otherwise-valid records raises just the same as an
        all-malformed batch). The caller (bot.py's isolated case-context
        boundary) substitutes EMPTY_CANONICAL_CASE_CONTEXT for the whole
        turn on any such failure -- a partially trusted case context must
        never reach the Professional Runtime. The exception message never
        includes record content.

    PROVENANCE VALIDATED BEFORE LIFECYCLE FILTER (Phase 2A correction,
    P2) -- `item.source_event_ids` is checked for every structurally
    supplied MemoryItem record (type is exactly `list`, every entry is
    exactly a positive `int`, bool rejected the same way memory_item_id
    rejects it) BEFORE this function ever looks at whether that item's
    lifecycle influences responses. This ordering matters: a
    non-influencing record (CANDIDATE/PROPOSED/REJECTED/HISTORICAL/
    EXPIRED) with malformed provenance must still fail the batch, not be
    silently skipped by the lifecycle check before its own malformation is
    ever noticed -- otherwise a valid CONFIRMED/CORRECTED record elsewhere
    in the same batch could wrongly survive as a partially trusted result
    while a structurally broken sibling record was quietly dropped instead
    of raised. CanonicalCaseItem's own __post_init__ (final tuple
    construction below) re-validates the now-tuple-converted
    source_event_ids as a second, later defense-in-depth layer -- it is
    not the sole place this is ever caught.

    category is carried through completely unchanged (see CATEGORY IS NEVER
    SEMANTICALLY UPGRADED in the module docstring) -- this function never
    inspects or rewrites it.

    Never truncates, never merges items, never reorders input -- if the
    surviving records are not already in strictly increasing memory_item_id
    order (or contain a duplicate id), CanonicalCaseContext's own
    __post_init__ fails closed with ValueError; this function does not
    pre-sort or de-duplicate to paper over that.

    No model call, no source_text parameter -- this function only ever sees
    already-persisted, already-confirmed/corrected memory rows."""
    eligible = []
    for memory_item_id, item in records:
        if type(memory_item_id) is not int or memory_item_id <= 0:
            raise ValueError(
                "build_canonical_case_context_from_memory_records: "
                "structurally malformed memory_item_id")
        if type(item) is not MemoryItem:
            raise ValueError(
                "build_canonical_case_context_from_memory_records: "
                "structurally malformed record -- item is not a MemoryItem")
        # Provenance structure validated BEFORE the lifecycle filter below
        # -- see PROVENANCE VALIDATED BEFORE LIFECYCLE FILTER above. A
        # non-influencing record with malformed provenance must still fail
        # the whole batch, never be silently dropped by the lifecycle
        # check first.
        if type(item.source_event_ids) is not list:
            raise ValueError(
                "build_canonical_case_context_from_memory_records: "
                "structurally malformed provenance -- source_event_ids is not a list")
        for event_id in item.source_event_ids:
            if type(event_id) is not int or event_id <= 0:
                raise ValueError(
                    "build_canonical_case_context_from_memory_records: "
                    "structurally malformed provenance -- source_event_ids "
                    "entry must be a positive int")
        if not item.influences_responses:
            continue  # structurally valid, non-influencing lifecycle -- expected omission
        eligible.append((memory_item_id, item))

    if len(eligible) > MAX_CASE_ITEMS:
        eligible = eligible[-MAX_CASE_ITEMS:]

    total_chars = sum(len(item.content) for _memory_item_id, item in eligible)
    while total_chars > MAX_TOTAL_CASE_CHARS and eligible:
        _dropped_id, dropped_item = eligible.pop(0)
        total_chars -= len(dropped_item.content)

    items = tuple(
        CanonicalCaseItem(
            memory_item_id=memory_item_id,
            category=item.category,
            lifecycle=item.lifecycle,
            content=item.content,
            source_event_ids=tuple(item.source_event_ids))
        for memory_item_id, item in eligible)
    return CanonicalCaseContext(items=items)
