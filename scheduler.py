"""X20 Scheduler — ежедневные check-in сообщения + кризисные follow-up'ы"""
import random
import secrets
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from functools import partial
from prompts import (
    get_checkin_msg, get_crisis_followup,
    PUSH_V1_CONTINUE_LABEL_RU, PUSH_V1_CONTINUE_LABEL_EN,
    PUSH_V1_NEW_TOPIC_LABEL_RU, PUSH_V1_NEW_TOPIC_LABEL_EN,
)
from crisis_protocol import crisis_screen
from crisis_delivery import deliver_crisis
from silence_engine import decide_push
from tz import effective_tz
from config import ADMIN_USER_IDS
import access_control
import config
import journals
import onboarding_content
from database import (
    get_checkin_users, update_last_checkin,
    get_active_crisis_events, mark_crisis_followup_sent,
    get_stage3_pending, auto_resolve_expired_crises,
    get_push_candidates, get_push_context, record_push_v1_delivery,
    get_journal_reminder_users, set_journal_settings,
    log_crisis_delivery,
    has_unresolved_crisis, get_last_assistant_message_id,
    get_user_revision, create_push_action_bindings, final_push_send_guard,
    final_push_keyboard_publish_guard,
    get_trusted_conversation_history_through_anchor,
    get_active_onboarding_state, get_onboarding_state,
    get_onboarding_eligibility, has_privacy_notice_ack,
)
from professional_turn_conversation_context import (
    build_conversation_context_from_history_rows,
)
import push_contextual_reengagement


def _minimal_reviewer_payload(uid: int, eid, note: str) -> str:
    """PR 1B-1: same minimal, no-raw-content payload as bot.py's version (kept
    duplicated, not imported from bot.py, for the same reason _send_crisis below
    doesn't import bot.py — this module must not depend on it)."""
    return f"🔔 Clinical review needed\ntester_id: {uid}\nevent_id: {eid}\nnote: {note}"


async def _send_crisis(bot: Bot, uid: int, text, kb, lang, eid, kind) -> str:
    """Scheduler-side binding of the crisis delivery ladder (same log + P0 alert
    as bot.py, but built from the Bot passed in — no import of bot.py)."""
    async def _alert(u, e, k, err):
        # PR 1B-1: routed like every other crisis alert.
        routed_kind, targets = access_control.crisis_alert_targets(u)
        if routed_kind == "owner":
            m = (f"🚨🚨 P0 CRISIS UNDELIVERED (followup) — uid={u} event={e} kind={k}\n"
                 f"err={err}")
            for admin_id in targets:
                try:
                    await bot.send_message(admin_id, m)
                except Exception:
                    pass
        elif routed_kind == "reviewer":
            payload = _minimal_reviewer_payload(u, e, "crisis followup delivery FAILED (P0)")
            for reviewer_id in targets:
                try:
                    await bot.send_message(reviewer_id, payload)
                except Exception:
                    pass
    return await deliver_crisis(partial(bot.send_message, uid), text=text, kb=kb,
                                lang=lang, uid=uid, eid=eid, kind=kind,
                                log=log_crisis_delivery, on_total_failure=_alert)

# Crisis follow-up cadence after the initial crisis message.
_CRISIS_OFFSETS = [("1h", 3600), ("24h", 86400), ("7d", 604800)]

# Bounded retries when a follow-up SCREEN fails to deliver at any ladder level
# (mirrors the stage-3 redo cap). After this, mark done and rely on backstops.
_FOLLOWUP_MAX_RETRIES = 3


def _parse_utc(ts: str) -> datetime:
    """Parse a SQLite datetime('now') string ('YYYY-MM-DD HH:MM:SS') as UTC."""
    return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


async def _send_crisis_followups(bot: Bot) -> None:
    """Gently check in on users after a crisis, at 1h / 24h / 7d, until the
    event is resolved (user pressed 'I'm safe'). Each tag is sent at most once.
    After 7d the event is auto-resolved (lifecycle cleanup)."""
    await auto_resolve_expired_crises(7)
    now = datetime.now(timezone.utc)
    for eid, uid, lang, created_at, stage, sent in await get_active_crisis_events():
        try:
            elapsed = (now - _parse_utc(created_at)).total_seconds()
        except (ValueError, TypeError):
            continue
        for tag, secs in _CRISIS_OFFSETS:
            if elapsed >= secs and tag not in sent:
                try:
                    text, kb = crisis_screen(stage, lang, eid)
                    # DELIVERY ORDER IS INTENTIONAL — DO NOT reorder for "readability".
                    # The number-carrying SCREEN goes FIRST, the gentle intro second.
                    # On a half-failing network only one of two sends may get through
                    # (a real prod TelegramNetworkError proved the network flaps); the
                    # message that MUST arrive is the screen with the hotline, not a
                    # context-less "как ты?". intro-first would re-open the silent-
                    # delivery hole.
                    level = await _send_crisis(bot, uid, text, kb, lang, eid, "followup")
                    if level == "none":
                        # The screen reached the user at NO level (P0-alert already
                        # fired inside deliver_crisis). Do NOT mark the tag sent so the
                        # next tick retries the SCREEN — bounded by a stage-3-style cap
                        # so a long outage can't loop forever. The intro is NOT sent
                        # here, so there is nothing to spam on retry.
                        retries = [t for t in sent if t.startswith(f"{tag}_retry")]
                        if len(retries) < _FOLLOWUP_MAX_RETRIES:
                            await mark_crisis_followup_sent(eid, f"{tag}_retry{len(retries)+1}")
                        else:
                            # Retry budget spent — mark done and lean on the backstops:
                            # P0-alert, the next offset tag, and the pipeline active-gate
                            # (any user reply instantly re-shows the screen).
                            await mark_crisis_followup_sent(eid, tag)
                        continue
                    # Screen delivered → the gentle check-in may follow (best-effort,
                    # not gated; its failure doesn't undo the delivered screen).
                    await _send_crisis(bot, uid, get_crisis_followup(lang, tag), None,
                                       lang, eid, "followup_intro")
                    await mark_crisis_followup_sent(eid, tag)
                except Exception as e:
                    print(f"[scheduler] crisis followup {tag} failed {uid}: {e}")


_STAGE3_MAX_REDOS = 3


async def _send_stage3_followups(bot: Bot) -> None:
    """Stage-3 fast follow-up (5-10 min): if still unresolved, re-show the crisis
    screen + a repeat critical alert, with an antispam cap on the number of redos.
    Runs on a dedicated 3-min job so the 5-10 min window is actually honoured."""
    for eid, uid, lang, sent in await get_stage3_pending(min_minutes=5):
        redos = [t for t in sent if t.startswith("redo_")]
        if len(redos) >= _STAGE3_MAX_REDOS:
            continue
        tag = f"redo_{len(redos) + 1}"
        try:
            text, kb = crisis_screen(3, lang, eid)
            await _send_crisis(bot, uid, text, kb, lang, eid, "followup")
            # PR 1B-1: routed like every other crisis alert.
            routed_kind, targets = access_control.crisis_alert_targets(uid)
            if routed_kind == "owner":
                for admin_id in targets:
                    try:
                        await bot.send_message(
                            admin_id, f"🚨 #CRITICAL stage=3 (повтор {len(redos)+1}) "
                                      f"event_id={eid} user={uid} — событие не сведено.")
                    except Exception:
                        pass
            elif routed_kind == "reviewer":
                payload = _minimal_reviewer_payload(
                    uid, eid, f"stage3 unresolved (redo {len(redos)+1})")
                for reviewer_id in targets:
                    try:
                        await bot.send_message(reviewer_id, payload)
                    except Exception:
                        pass
            # NOTE: do NOT gate this mark on delivery (unlike _send_crisis_followups).
            # Here `redo_N` is the cap COUNTER, not a delivered-flag: the screen is
            # re-sent as redo_{N+1} on the next 3-min tick regardless of outcome, up
            # to _STAGE3_MAX_REDOS. Gating by delivery would break that retry loop.
            await mark_crisis_followup_sent(eid, tag)
        except Exception as e:
            print(f"[scheduler] stage3 followup failed {uid}: {e}")

async def _send_checkins(bot: Bot) -> None:
    utc_hour = datetime.now(timezone.utc).hour
    users = await get_checkin_users()
    sent = 0
    for uid, _, checkin_hour, lang, tz, tz_set in users:
        # checkin_hour is the user's LOCAL hour; compare in local time.
        if checkin_hour != (utc_hour + effective_tz(tz, tz_set, lang)) % 24:
            continue
        try:
            msg = get_checkin_msg(lang)
            await bot.send_message(uid, msg)
            await update_last_checkin(uid)
            sent += 1
        except Exception as e:
            print(f"[scheduler] checkin failed {uid}: {e}")
    if sent:
        print(f"[scheduler] Sent {sent} check-in(s) at UTC {utc_hour}:00")

async def _onboarding_blocks_push(uid: int) -> bool:
    """Scheduler-local duplicate of bot._onboarding_blocks_ordinary_entry --
    kept duplicated, not imported from bot.py, for the same reason
    _send_crisis above doesn't import bot.py — this module must not depend
    on it. Must stay in sync with bot.py's version by inspection; both call
    the SAME underlying database.py/onboarding_content primitives, so there
    is no separate decision logic to drift, only this one wrapper shape."""
    if not config.FIRST_USER_ONBOARDING_ENABLED:
        return False
    active_state = await get_active_onboarding_state(uid)
    if active_state is not None:
        return True
    current_version_row = await get_onboarding_state(uid, onboarding_content.ONBOARDING_VERSION)
    eligibility = await get_onboarding_eligibility(uid)
    notice_acked = await has_privacy_notice_ack(uid, onboarding_content.PRIVACY_NOTICE_VERSION)
    requirement = onboarding_content.determine_onboarding_requirement(
        eligibility=eligibility, has_active_state=False,
        has_current_version_row=current_version_row is not None,
        notice_acknowledged=notice_acked)
    return requirement != onboarding_content.NOT_REQUIRED


_PUSH_BINDING_LEASE_DAYS = 14
_RECORD_PUSH_MAX_ATTEMPTS = 2


def _push_binding_expiry() -> str:
    return (datetime.now(timezone.utc)
            + timedelta(days=_PUSH_BINDING_LEASE_DAYS)).strftime("%Y-%m-%d %H:%M:%S")


def _push_v1_keyboard(lang: str, tokens: dict) -> InlineKeyboardMarkup:
    """UI polish V1: one row, two buttons -- Continue then New topic. Callback
    data/token semantics are completely unchanged from the two-row layout;
    this is presentation only. `style="primary"` on Continue is an extra
    field aiogram 3.7.0's TelegramObject base class passes through
    construction and serialization untyped (Pydantic extra="allow") -- see
    the compatibility probe in the UI-polish task; New topic intentionally
    carries no style field, so Telegram renders it with default styling."""
    continue_label = PUSH_V1_CONTINUE_LABEL_EN if lang == "en" else PUSH_V1_CONTINUE_LABEL_RU
    new_topic_label = PUSH_V1_NEW_TOPIC_LABEL_EN if lang == "en" else PUSH_V1_NEW_TOPIC_LABEL_RU
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=continue_label,
                              callback_data=f"pushbtn:{tokens['push_continue']}",
                              style="primary"),
         InlineKeyboardButton(text=new_topic_label,
                              callback_data=f"pushbtn:{tokens['push_new_topic']}")],
    ])


# Process-local, PERMANENT-FOR-THIS-PROCESS fail-safe for the ONE window
# that cannot be made atomic across Telegram HTTP + SQLite: a CONFIRMED
# Telegram send whose record_push_v1_delivery finalization then totally
# fails (a REAL DB exception, not a lifecycle invalidation -- see below)
# leaves push_log/push_settings reflecting NOTHING about this send, so
# decide_push's own antispam (already-pushed-today, per-tier frequency,
# consecutive-unanswered) has no way to protect ANY later scheduler tick
# for this user. Correction #2 (Blocker B): a bounded-TTL version of this
# guard was tried first and rejected -- once the TTL expired, the exact
# same unrecorded delivered push would eligible the user for ANOTHER send,
# and that repeats indefinitely for as long as the write keeps failing
# (send -> suppress one tick -> TTL expires -> send again -> ...), which is
# unbounded repeated delivery, not a one-time fail-safe. The smallest safe
# fix is a PERMANENT (for the lifetime of this running process) suppression
# once total failure is confirmed: no time-based expiry, no re-enable.
# Documented residual limitation: only a process RESTART clears this set,
# so a restart occurring in the exact window after a total persistence
# failure (but before this process would otherwise have exited/restarted
# anyway) could theoretically allow one more send after restart -- this
# cannot be closed without a distributed transaction across Telegram HTTP
# and SQLite, which is out of scope.
_unrecorded_send_uids: set = set()


def _recently_sent_unrecorded(uid: int) -> bool:
    return uid in _unrecorded_send_uids


async def _finalize_push_record(uid: int, tier: str, anchor_turn_id: int,
                                 expected_last_seen: str) -> str:
    """Best-effort, bounded-retry, anchor-fenced persistence of a Telegram
    send that has ALREADY been confirmed successful by the caller (never
    called before that). Telegram delivery and this SQLite write cannot
    share one transaction, so this is the smallest safe hardening of that
    boundary: a couple of quick retries, never an unbounded loop, and
    never a retry of the Telegram send itself (which already succeeded).

    Returns one of three OUTCOMES the caller must distinguish (P1
    correction, § post-erasure recreation):

    - "recorded": push_log/push_settings were written (or correctly left
      the user's genuine re-engagement reset alone -- see
      record_push_v1_delivery). Any stale permanent-suppression entry is
      cleared.
    - "lifecycle_invalidated": record_push_v1_delivery found the captured
      anchor no longer exists -- the account's data was genuinely erased
      (GDPR delete-all) between send and this call. This is NOT a
      database error and must never be retried, must never poison
      _unrecorded_send_uids (there is nothing wrong to suppress -- the
      user intentionally erased their own lifecycle), and the caller must
      not attempt to create bindings or attach a keyboard afterward.
    - "failed": every attempt raised a real exception. uid is added to
      _unrecorded_send_uids so _recently_sent_unrecorded() permanently
      suppresses further sends to this user for the remainder of this
      process -- push_log-based antispam (once-per-day, per-tier
      frequency) cannot do this on its own, since push_log was never
      written for this send."""
    for attempt in range(1, _RECORD_PUSH_MAX_ATTEMPTS + 1):
        try:
            recorded = await record_push_v1_delivery(
                uid, tier, anchor_turn_id, expected_last_seen)
        except Exception as e:
            print(f"[scheduler] record_push_v1_delivery failed (attempt {attempt}/"
                  f"{_RECORD_PUSH_MAX_ATTEMPTS}) uid={uid}: {e}")
            continue
        if recorded:
            _unrecorded_send_uids.discard(uid)
            return "recorded"
        return "lifecycle_invalidated"
    _unrecorded_send_uids.add(uid)
    return "failed"


async def _generate_contextual_push_text(
        uid: int, lang: str, anchor_turn_id: int, model_client) -> str | None:
    """Build exact-anchor trusted context and make one minimized generation.

    No identifiers are placed in the provider request. Any DB/build/provider/
    output failure means no Push; this boundary has no neutral fallback.
    """
    if model_client is None:
        return None
    try:
        rows = await get_trusted_conversation_history_through_anchor(uid, anchor_turn_id)
        context = build_conversation_context_from_history_rows(rows)
    except Exception as e:
        print(f"[scheduler] contextual push preparation failed uid={uid}: {type(e).__name__}")
        return None
    return await push_contextual_reengagement.generate_contextual_reengagement_push(
        client=model_client,
        model="gpt-4o-mini",
        conversation_context=context,
        anchor_turn_id=anchor_turn_id,
        lang=lang,
        max_tokens=120,
    )


async def _send_silence_pushes(bot: Bot, model_client=None) -> None:
    """Re-engagement pushes (Push V1). Antispam cadence still lives entirely
    in decide_push() (unchanged tiers/limits/quiet-hours/mute); this
    function additionally, in order: (1) vetoes early for the user's whole
    unresolved-crisis lifecycle (has_unresolved_crisis, not just 24h) for
    efficiency; (2) rechecks product access and mandatory-onboarding state
    fresh, since get_push_candidates() only filters inactivity + permanent
    mute -- both access checks use access_control.proactive_push_eligible(),
    not has_full_access(): a temp-test-only wall-clock lease can expire with
    no DB signal the revision-bound final guard below could ever observe,
    so it is never sufficient grounds for Push V1 to proactively contact
    someone (see access_control.proactive_push_eligible); (3) acquires the
    two remaining send prerequisites -- a REAL prior conversation anchor
    (never sends the "after our conversation" card to a user with none, and
    never lets a previous Push V1 UI reply count as that anchor) and the
    current user_interaction_revision; (4) performs a SECOND, fresh
    proactive_push_eligible() recheck -- P1 correction: an access
    revocation (database.block_user_access) that happens during the
    anchor/revision prerequisite awaits above changes no signal the
    EARLIER access check (step 2) already observed, so that earlier check
    alone cannot catch it; this fresh recheck, positioned AFTER those awaits,
    can; (5) runs ONE authoritative, last-possible-moment guard
    (final_push_send_guard) that re-reads, from a single connection,
    last_seen-still-matches AND no-unresolved-crisis AND
    user_interaction_revision-still-matches AND the anchor still exists --
    this is deliberately the LAST awaited call before the Telegram send
    (Correction #2, Blocker A; P1 correction, § access revocation): every
    earlier prerequisite await (access, onboarding, anchor, revision, the
    fresh access recheck itself) can take an arbitrary amount of wall-clock
    time, during which the user could re-engage, a crisis could start, or
    access could be revoked (revocation is caught here via the revision
    match, since block_user_access bumps the same counter on every genuine
    active->blocked transition), so ANY check performed BEFORE those awaits
    would leave exactly that gap unguarded; (6) suppresses a send if a prior
    tick's Telegram send succeeded but its finalization totally failed (see
    _recently_sent_unrecorded -- a purely synchronous, in-memory check, so
    it may run between the guard and the send without reopening the race).
    NO other database/network await runs between a True result from
    final_push_send_guard and bot.send_message(...). After a successful
    send, _finalize_push_record's anchor-fenced write (P1 correction, §
    post-erasure recreation) either records the delivery or -- if the
    user's account lifecycle was erased in the exact window after send --
    reports "lifecycle_invalidated", in which case no binding is created
    and no keyboard is ever attached. Otherwise sends one short contextual
    Push grounded in bounded trusted history through the exact anchor, and —
    only if its two-button binding is durably created
    AND final_push_keyboard_publish_guard (P1 correction, § delete-all-
    between-binding-and-publication) confirms, in the LAST awaited DB read
    before the edit call, that both bindings are still open and the anchor
    still exists — attaches the Continue/New-topic keyboard. SQLite and the
    Telegram API cannot be made globally atomic: this only guarantees a
    deletion that committed before that guard's read is caught; a deletion
    beginning after it succeeds, or during the edit call itself, is an
    unavoidable cross-system residual that cb_push_action's own
    consumption-time checks still fail closed against."""
    now = datetime.now(timezone.utc)
    for uid, last_seen, lang, tz, tz_set in await get_push_candidates():
        try:
            last_activity = _parse_utc(last_seen)
        except (ValueError, TypeError):
            continue
        # Quiet hours evaluated in the user's effective LOCAL time, so the +3
        # default shift never pushes a notification into someone's night.
        local_now = now + timedelta(hours=effective_tz(tz, tz_set, lang))
        ctx = await get_push_context(uid)
        muted_until = None
        if ctx["mute_mode"] == "until" and ctx["mute_until"]:
            try:
                muted_until = _parse_utc(ctx["mute_until"])
            except (ValueError, TypeError):
                muted_until = None
        last_crisis_at = None
        if ctx["last_crisis_at"]:
            try:
                last_crisis_at = _parse_utc(ctx["last_crisis_at"])
            except (ValueError, TypeError):
                last_crisis_at = None
        tier_push_times: dict = {}
        for tier, ts in ctx["push_log"]:
            try:
                tier_push_times.setdefault(tier, []).append(_parse_utc(ts))
            except (ValueError, TypeError):
                continue

        # P0: deterministic, lifecycle-length unresolved-crisis veto — fail
        # CLOSED (treat as unresolved / block) on lookup failure.
        try:
            unresolved_crisis = await has_unresolved_crisis(uid)
        except Exception as e:
            print(f"[scheduler] push crisis-check failed {uid}: {e}")
            continue

        tier = decide_push(
            now, last_activity,
            muted_until=muted_until,
            last_crisis_at=last_crisis_at,
            has_unresolved_crisis=unresolved_crisis,
            consecutive_unanswered=ctx["consecutive_unanswered"],
            tier_push_times=tier_push_times,
            quiet_now=local_now,
        )
        if not tier:
            continue

        # P1 §6: get_push_candidates() only checked inactivity + permanent
        # mute — re-verify ordinary product state fresh, right before send.
        # Uses proactive_push_eligible(), not has_full_access(): a temp-test-
        # only wall-clock lease can expire with no DB signal Push V1's
        # revision-bound final guard could ever observe (see
        # access_control.proactive_push_eligible), so Push V1 must never
        # treat it as sufficient grounds to proactively contact someone,
        # even though an ordinary in-session reply still may. Fail CLOSED
        # (no push) on any lookup failure.
        try:
            has_access = await access_control.proactive_push_eligible(uid)
        except Exception as e:
            print(f"[scheduler] push access-check failed {uid}: {e}")
            has_access = False
        if not has_access:
            continue
        try:
            onboarding_blocks = await _onboarding_blocks_push(uid)
        except Exception as e:
            print(f"[scheduler] push onboarding-check failed {uid}: {e}")
            onboarding_blocks = True
        if onboarding_blocks:
            continue

        # Product-contract prerequisite: Push V1's copy explicitly says
        # "after our conversation" -- it must never be sent to a user with
        # no real conversation anchor (get_last_assistant_message_id
        # already excludes a prior Push V1 UI reply from counting as one).
        # Fail CLOSED (no push) on lookup failure. This and the revision
        # fetch below are both PREREQUISITES for the send, acquired BEFORE
        # the final guard -- not final-guard checks themselves -- per
        # Correction #2, Blocker A.
        try:
            anchor_turn_id = await get_last_assistant_message_id(uid)
        except Exception as e:
            print(f"[scheduler] push anchor-check failed {uid}: {e}")
            continue
        if anchor_turn_id is None:
            continue

        try:
            revision = await get_user_revision(uid)
        except Exception as e:
            print(f"[scheduler] push revision-fetch failed {uid}: {e}")
            continue

        # Contextual Re-engagement Push V1: exact-anchor history recovery and
        # the single provider call happen before the existing fresh access
        # recheck and final lifecycle guard. Insufficient USER_AUTHORED
        # evidence, provider failure, or deterministic output rejection means
        # no Push; the former fixed neutral card is deliberately not used.
        push_text = await _generate_contextual_push_text(
            uid, lang or "ru", anchor_turn_id, model_client)
        if push_text is None:
            continue

        # P1 correction, § access revocation: a SECOND, fresh access check
        # positioned AFTER the anchor/revision prerequisite awaits -- the
        # EARLIER check (above, before those awaits) cannot by itself catch
        # a block_user_access() that happens during them. Same
        # proactive_push_eligible() helper as the earlier check, for the
        # same temp-test-lease reason. Fail CLOSED.
        try:
            fresh_access = await access_control.proactive_push_eligible(uid)
        except Exception as e:
            print(f"[scheduler] push final access-check failed {uid}: {e}")
            fresh_access = False
        if not fresh_access:
            continue

        # THE final, authoritative, last-possible-moment guard -- every
        # prerequisite await above (access, onboarding, anchor, revision,
        # the fresh access recheck itself) can take arbitrary wall-clock
        # time, during which the user could re-engage, a crisis could
        # start, or access could be revoked; this is why the freshness/
        # crisis/revision/anchor check must run HERE, as the very last
        # awaited call, not earlier. Fail CLOSED on lookup failure.
        try:
            guard_ok = await final_push_send_guard(uid, last_seen, revision, anchor_turn_id)
        except Exception as e:
            print(f"[scheduler] push final guard failed {uid}: {e}")
            continue
        if not guard_ok:
            continue

        # Synchronous, in-memory only -- safe to run here without
        # reopening the awaited-gap race the guard above just closed (see
        # Correction #2, Blocker B): a prior tick's Telegram send may have
        # succeeded while its finalization totally failed.
        if _recently_sent_unrecorded(uid):
            continue

        try:
            sent = await bot.send_message(uid, push_text)
        except Exception as e:
            print(f"[scheduler] push {tier} send failed {uid}: {e}")
            continue

        outcome = await _finalize_push_record(uid, tier, anchor_turn_id, last_seen)
        if outcome == "lifecycle_invalidated":
            # The account's data was genuinely erased (GDPR delete-all) in
            # the window after send -- not a DB failure. Never recreate
            # bindings, never attach a keyboard, never poison
            # _unrecorded_send_uids for an erasure the user intentionally
            # requested.
            continue

        # Publication order (§9): plain text is already delivered above; the
        # two-button keyboard is only ever attached AFTER its bindings are
        # durably written, so a button can never be visible before it is
        # actionable. If binding creation fails, the push simply stays
        # plain text — no dead buttons, no partial bindings.
        try:
            expires_at = _push_binding_expiry()
            tokens = {"push_continue": secrets.token_urlsafe(9),
                      "push_new_topic": secrets.token_urlsafe(9)}
            rows = [{"token": tokens[action], "action": action, "expires_at": expires_at}
                    for action in ("push_continue", "push_new_topic")]
            batch_ok = await create_push_action_bindings(
                uid, sent.chat.id, sent.message_id, revision, anchor_turn_id, rows)
        except Exception as e:
            print(f"[scheduler] push binding create failed {uid}: {e}")
            batch_ok = False
        if not batch_ok:
            continue

        # P1 correction, § delete-all-between-binding-and-publication: a
        # GDPR delete-all (or any other lifecycle-invalidating event) can
        # commit in the gap between the durable binding write above and the
        # keyboard publication below -- without this guard, a keyboard
        # would be attached whose tokens no longer resolve to anything.
        # This is the LAST awaited DB read before the edit call, mirroring
        # final_push_send_guard's discipline before bot.send_message: no
        # other database/network await runs between a True result here and
        # bot.edit_message_reply_markup(...) itself. SQLite and the
        # Telegram API cannot be made globally atomic -- this guard only
        # guarantees a deletion that COMMITTED before this read is caught;
        # a deletion beginning after this guard succeeds, or during the
        # edit call itself, is an unavoidable cross-system residual, not
        # something this guard claims to close (cb_push_action's own
        # token/revision checks at consumption time remain the backstop for
        # that narrow window -- a stale keyboard can only ever fail closed
        # when tapped, never resurrect erased state).
        try:
            publishable = await final_push_keyboard_publish_guard(
                uid, sent.chat.id, sent.message_id, revision, anchor_turn_id, tokens)
        except Exception as e:
            print(f"[scheduler] push keyboard publish guard failed {uid}: {e}")
            publishable = False
        if not publishable:
            continue

        try:
            await bot.edit_message_reply_markup(
                chat_id=sent.chat.id, message_id=sent.message_id,
                reply_markup=_push_v1_keyboard(lang or "ru", tokens))
        except Exception as e:
            print(f"[scheduler] push keyboard attach failed {uid}: {e}")


def _checkin_kb(kind: str, options) -> InlineKeyboardMarkup:
    rows, row = [], []
    for value, label in options:
        row.append(InlineKeyboardButton(text=label, callback_data=f"checkin:{kind}:{value}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _muted_or_post_crisis(uid: int, now: datetime) -> bool:
    """Reuse Silence-Engine antispam: respect mute and the 24h post-crisis
    cooldown so journal reminders never pile onto a fragile moment."""
    ctx = await get_push_context(uid)
    if ctx["mute_mode"] == "forever":
        return True
    if ctx["mute_mode"] == "until" and ctx["mute_until"]:
        try:
            if now < _parse_utc(ctx["mute_until"]):
                return True
        except (ValueError, TypeError):
            pass
    if ctx["last_crisis_at"]:
        try:
            if (now - _parse_utc(ctx["last_crisis_at"])) < timedelta(hours=24):
                return True
        except (ValueError, TypeError):
            pass
    return False


async def _send_journal_checkins(bot: Bot) -> None:
    """Morning/evening journal check-ins in the user's LOCAL time (tz_offset).
    Opt-in only, once per slot per local day, never during mute / post-crisis."""
    now = datetime.now(timezone.utc)
    for u in await get_journal_reminder_users():
        tz = effective_tz(u["tz_offset"], u["tz_set"], u["lang"])
        local = now + timedelta(hours=tz)
        local_hour = local.hour
        local_date = local.strftime("%Y-%m-%d")
        try:
            if await _muted_or_post_crisis(u["user_id"], now):
                continue
            if u["morning_enabled"] and local_hour == u["morning_hour"] \
                    and u["last_morning"] != local_date:
                await bot.send_message(u["user_id"], "Доброе утро. Как ты сейчас?",
                                       reply_markup=_checkin_kb("morning", journals.MORNING_OPTIONS))
                await set_journal_settings(u["user_id"], last_morning=local_date)
            elif u["evening_enabled"] and local_hour == u["evening_hour"] \
                    and u["last_evening"] != local_date:
                await bot.send_message(u["user_id"], "Вечер. Хочешь что-то записать?",
                                       reply_markup=_checkin_kb("evening", journals.EVENING_OPTIONS))
                await set_journal_settings(u["user_id"], last_evening=local_date)
        except Exception as e:
            print(f"[journal-checkin] {u['user_id']}: {type(e).__name__}: {e}")


def setup_scheduler(bot: Bot, model_client=None) -> AsyncIOScheduler:
    s = AsyncIOScheduler()
    s.add_job(_send_checkins, "cron", minute=0, args=[bot],
              id="checkins", replace_existing=True, misfire_grace_time=300)
    s.add_job(_send_crisis_followups, "interval", minutes=15, args=[bot],
              id="crisis_followups", replace_existing=True, misfire_grace_time=600)
    s.add_job(_send_stage3_followups, "interval", minutes=3, args=[bot],
              id="stage3_followups", replace_existing=True, misfire_grace_time=120)
    s.add_job(_send_silence_pushes, "interval", minutes=30, args=[bot, model_client],
              id="silence_pushes", replace_existing=True, misfire_grace_time=600)
    s.add_job(_send_journal_checkins, "cron", minute=0, args=[bot],
              id="journal_checkins", replace_existing=True, misfire_grace_time=300)
    return s
