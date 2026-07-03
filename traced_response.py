"""A1 / PR 0 — the single traced path for latent-influenced replies.

Governing invariant (supersedes the old "profile never influences" rule for the
v1 personal-use bot; see CLINICAL_BOUNDARY.md Appendix A):

  A latent profile / pattern_hypothesis / questionnaire_score / confirmed_episode /
  schema_theme / mode / formulation MAY influence the bot's reply — ONLY when that
  influence is explicit, logged, inspectable, attributable, CONTENT-fully recorded,
  and impossible without a durable trace record.

This module is the ONLY place a latent-influenced reply is assembled. The rules it
enforces:

1. CONTENT-FUL TRACE (bidirectional). To send a latent reply you MUST declare its
   real influences (non-empty type + source_id + a human_readable line that names
   the source_id). A formal/empty trace (e.g. "influence: none") while a real
   source drove the reply is a contract breach → TraceIntegrityError, nothing sent.
   A test that feeds real influence but an empty/placeholder trace goes red.

2. FAIL-CLOSED ORDER. The influence_trace is persisted (with a real response_id)
   BEFORE the reply is sent. If persist fails, the latent reply is NOT sent — we
   degrade to a non-latent neutral fallback. Never send-then-log: that order can
   never fail closed. An orphan trace (persisted, reply not sent) is acceptable and
   safer than a delivered reply with no trace.

3. NOT A CRISIS PATH. Fail-closed hangs ONLY on the latent path. Crisis/safety
   delivery has its own deterministic guaranteed-delivery path and is NEVER routed
   through here.

The module is aiogram-free and dependency-injected (persist/send/fallback/build are
passed in), so the invariant is unit-testable; `persist_influence_trace` is the real
DB binding used in production.
"""
import uuid


class TraceIntegrityError(Exception):
    """A latent reply was requested without a content-ful influence declaration.
    The reply is NOT sent."""


class Influence:
    """One declared latent influence behind a reply."""
    __slots__ = ("influence_type", "source_id", "human_readable")

    def __init__(self, influence_type: str, source_id, human_readable: str):
        self.influence_type = (influence_type or "").strip()
        self.source_id = str(source_id if source_id is not None else "").strip()
        self.human_readable = (human_readable or "").strip()


# Strings that mean "no real influence recorded" — a trace made of these is a lie
# when a latent source actually drove the reply.
_PLACEHOLDERS = {
    "", "none", "n/a", "na", "null", "nil", "placeholder", "influence: none",
    "no influence", "-", "todo", "tbd", "unknown",
}


def _is_content_ful(inf: "Influence") -> bool:
    if not inf.influence_type or not inf.source_id or not inf.human_readable:
        return False
    if inf.source_id.lower() in _PLACEHOLDERS:
        return False
    if inf.human_readable.strip().lower() in _PLACEHOLDERS:
        return False
    # Bidirectional: the human-readable line must actually NAME the source, so a
    # generic "reply used a pattern" that doesn't reference source_id X fails.
    if inf.source_id not in inf.human_readable:
        return False
    return True


def content_ful(influences) -> bool:
    """True only if there is at least one influence and EVERY one names its real
    source. This is what makes the trace inspectable by a human psychologist."""
    influences = list(influences or [])
    return bool(influences) and all(_is_content_ful(i) for i in influences)


def new_response_id() -> str:
    """Stable, inspectable trace_group_id joining all influence rows of one reply.
    Never nullable, never a placeholder.

    SCOPE (no overclaim): this is ONLY a joining key for influence_trace rows. PR 0
    does not link it to the actual user message / sent bot-response text, and does
    not build a psychologist_review_pack — that aggregation is future work (PR 1A
    structure/privacy, product PRs for content)."""
    return uuid.uuid4().hex


async def persist_influence_trace(response_id: str, user_id, rows) -> None:
    """Real DB binding (RAISES on failure, so the builder can fail closed)."""
    import database
    await database.log_influence_trace(response_id, user_id, rows)


async def traced_response_builder(*, user_id, influences, build_response, send,
                                  persist_trace, neutral_fallback,
                                  requester_uid,
                                  response_id: str | None = None):
    """Assemble and deliver a latent-influenced reply through the trace guard.

    Args (all callables are async):
      influences:      list[Influence] — the real sources; MUST be content-ful.
      build_response:  () -> str       — builds the latent reply; called ONLY after
                                         the trace is durably persisted.
      send:            (text) -> None  — delivers the latent reply.
      persist_trace:   (response_id, user_id, rows) -> None — RAISES on failure.
      neutral_fallback:() -> None      — sends a NON-latent reply if trace can't
                                         persist (fail-closed degradation).
      requester_uid:   REQUIRED (PR 1B-1, no default — Python itself refuses a
                       call that omits it). Checked against access_control BEFORE
                       the trace-integrity check below, so an unauthorized caller
                       is rejected before we even inspect the influences. This is
                       a SEPARATE, earlier gate than the fail-closed-on-persist
                       guard from PR 0 — both apply, neither replaces the other.
    Returns the response_id on success, or None if it failed closed (persist
    failure only — an access-control denial RAISES instead, see below).
    """
    # PR 1B-1 role/mode gate — earlier and independent of the trace-integrity and
    # persist-fail-closed checks below. Raises access_control.A1NotAllowed; the
    # caller must not treat this as a soft failure — nothing is built or sent.
    import access_control
    await access_control.assert_a1_allowed(requester_uid)

    # Own-context enforcement (checkpoint item 4): a role/mode-allowed requester
    # may still only build a latent reply for THEMSELVES. Without this, an
    # allowed OWNER/TESTER/REVIEWER could accidentally construct a traced reply
    # using someone else's user_id context — a cross-user latent-influence leak
    # that role/mode checks alone do not prevent (they only ask "is this
    # requester allowed A1 at all", not "for whom").
    if requester_uid != user_id:
        raise access_control.A1NotAllowed(
            "A1 requester/user context mismatch: a traced reply may only be "
            "built for the requester's own data")

    if not content_ful(influences):
        # Contract breach: refuse to send a latent reply without a real trace.
        raise TraceIntegrityError(
            "traced_response_builder needs content-ful influences (type + source_id "
            "+ a human_readable line naming the source); a formal/empty trace is a "
            "lie the psychologist can't review. Nothing sent.")

    rid = response_id or new_response_id()
    rows = [(i.influence_type, i.source_id, i.human_readable) for i in influences]

    # FAIL-CLOSED: persist the trace BEFORE building/sending the latent reply.
    try:
        await persist_trace(rid, user_id, rows)
    except Exception as e:  # noqa: BLE001 — any persist failure blocks the latent reply
        print(f"[influence-trace] persist FAILED rid={rid} uid={user_id}: {e}")
        await neutral_fallback()          # non-latent; latent context never used
        return None

    text = await build_response()          # latent context used ONLY now (trace durable)
    await send(text)
    return rid
