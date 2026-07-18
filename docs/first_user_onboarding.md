# First-user illustrated onboarding

A five-screen illustrated introduction shown to a **genuinely new authorized
user** the first time they `/start`, placed *before* the existing mood entry. It
is versioned, restart-safe, callback-idempotent, flood-safe (one card, edited in
place), gates ordinary product entry while active, and fully feature-flagged.

## Feature flag

```
FIRST_USER_ONBOARDING_ENABLED=false   # default — /start behaves exactly as before
PRIVACY_POLICY_URL=                    # optional; blank -> in-bot data/privacy summary
```

Parsed with the repo's standard boolean parser (`config.py`). **Flag off = the
old `/start` behavior, byte-for-byte** (all onboarding logic lives inside a single
`if config.FIRST_USER_ONBOARDING_ENABLED:` block in `cmd_start`, and the
mandatory ordinary-entry gate in `pipeline()`/`cb_mood` short-circuits to False
immediately when the flag is off).

`PRIVACY_POLICY_URL` is validated at config-load time (`config._validate_privacy_policy_url`):
only an absolute `http`/`https` URL with a non-empty host is accepted; anything
malformed (empty, `javascript:`, no scheme, no host) is silently normalized to
`""`. The raw env value is never logged.

## Eligibility (who sees it)

Decided in `bot.cmd_start`, **before** `upsert_user` (so the eligibility check
can see whether a `users` row already existed) and **after** the access gate (so
a blocked/unauthorized user never reaches it; onboarding can never grant access).

`database.get_onboarding_eligibility(uid)` is the single centralized source of
truth. It returns `"legacy"` if ANY of the following exist for `uid`: an
existing `users` row, or any row in `messages`, `questionnaire_sessions`,
`emotion_journal_entries`, `cbt_journal_entries`, `user_profiles`, `user_states`,
or `summaries`. It deliberately does **not** check `user_access` — a user who was
just invited but has never actually used the product still returns `"new"`.

**Version equality is the gate** (spec item F, corrected this round): a user
is "settled" for onboarding only once they have a row for the CURRENT
`ONBOARDING_VERSION` specifically. An older policy ("has this user EVER
touched onboarding, any version") made a completed old version a PERMANENT
exemption from every future MANDATORY version bump (e.g. a required new
privacy notice) — that was the bug being corrected.

| Situation (flag on) | Result |
|---|---|
| Active row, current `ONBOARDING_VERSION` | Resume at the stored `current_step`, editing the persisted card in place |
| Active row, an OLDER version (deployment bumped the version mid-flight) | Marked `superseded` (never `completed`, never `legacy_exempt` — the user did not finish it and was not exempt), then the MANDATORY current version is started immediately (step 1, fresh card) — it does **not** fall through to the ordinary greeting |
| No row for the current version; `get_onboarding_eligibility` = `"new"` | Start onboarding at screen 1 |
| No row for the current version; `get_onboarding_eligibility` = `"legacy"` | Record `legacy_exempt` for the current version, show normal greeting — **never** retro-forced |
| A row already exists for the current version (`completed`/`legacy_exempt`/`superseded`) | Settled — falls through to normal greeting |

## Language selection

A brand-new user's `/start` has no prior message to detect a language from, so
`language_detector.normalize_telegram_language_code(message.from_user.language_code)`
maps Telegram's own BCP-47 code (`"en"`, `"en-US"`, `"ru-RU"`, missing/unrecognized,
…) to one of the bot's two supported languages (English only for an `en*` primary
subtag; everything else, including missing, falls back to `"ru"`). This is passed
to `upsert_user` directly — the language is never written as an implicit `"ru"`
default and then read back.

## Mandatory onboarding gate

While a user has an ACTIVE onboarding row (any version — see `bot._onboarding_blocks_ordinary_entry`),
they cannot reach ordinary product entry:

- ordinary text (`bot.pipeline`);
- voice (`bot.handle_voice` → `bot.pipeline`);
- an old/leftover mood button (`bot.cb_mood`).

Gate ordering inside `pipeline()`:

```
deterministic risk detection
→ active-crisis handling            (unconditional — always preempts onboarding)
→ RED crisis override               (unconditional — always preempts onboarding)
→ product access gate
→ mandatory onboarding gate         (NEW — flag-gated, no-op when flag is off)
→ ordinary product persistence / scenario pipeline
```

Crisis handling is checked strictly before the onboarding gate, so an active
crisis (or a new RED-risk message) always reaches the existing crisis flow
regardless of onboarding state. When the gate blocks, it re-renders the user's
current onboarding card in place (never a new message when an edit succeeds) —
it never silently drops the message and never floods the chat. Privacy
self-service commands (`/privacy_export_all`, `/privacy_delete_all`, `/forget_all`)
are separate command handlers that never go through `pipeline()`, so they remain
available regardless of onboarding state.

## Screens

1. Welcome & boundaries (does not diagnose / does not replace a professional)
2. Crisis limitation — **corrected this round (spec item B): both RU and EN are
   now STATIC, hand-written, country-neutral text.** An earlier revision made
   RU dynamic via `crisis_protocol.get_hotline("ru")` while EN stayed static —
   that was itself the bug: it used UI language as a country proxy (a
   Russian-language screen does not mean the user is in Russia, any more than
   an English-language screen means they're in the US). Neither caption calls
   `get_hotline` or duplicates any number from `crisis_contacts.json`; both say
   only "your local emergency service" / "a local crisis service" with no
   digits at all. `onboarding_content.py` does not import `crisis_protocol`
   at all — enforced structurally by
   `tests/test_first_user_onboarding.py::test_onboarding_content_never_imports_crisis_protocol`
   (an AST-based check, not just a comment), plus a regex test asserting no
   2+ digit sequence appears in either language's screen-2 text. A specific
   number may be shown only once an approved, reliable region/location policy
   exists (not implemented here).
3. Topics the bot can help with
4. Real capabilities (verified against production code)
5. Privacy & final **Start** — **cannot be skipped**

Skip (screens 1–4) jumps straight to the privacy screen. It never completes
onboarding and never bypasses the privacy notice. Start (screen 5 only) completes
exactly once and opens the existing mood entry.

### Privacy screen wording

- The secondary button is labeled **"Privacy Policy"** only when a verified
  `PRIVACY_POLICY_URL` is configured (a real `url=` button). With no URL
  configured, it is labeled **"About data and privacy"** / **"О данных и
  приватности"** and opens a deterministic in-bot summary — never mislabeled as
  the actual Policy document.
- The final "Начать"/"Start" acknowledgment line only claims the user
  acknowledged **this notice** when no URL is configured; it additionally
  mentions the Privacy Policy only when a real link was actually shown.
  `database.complete_onboarding` records this as `privacy_notice_acknowledged_at`
  — notice acknowledgment, never framed as stronger legal consent.
- The "we do not sell data / do not use it for advertising" line has been
  **removed entirely** (spec item J, this correction round) — an earlier
  revision framed it as "по нашей политике" / "per our policy", but that still
  presented an unverifiable organizational/legal claim as fact without owner
  sign-off. Privacy copy (screen 5 and the in-bot summary) now states only
  technically verified facts: conversation history is stored; some text may
  be processed by the configured AI provider; export/delete tools exist;
  safety-audit data may be retained longer under documented exceptions. If
  the owner explicitly approves a no-sale/no-advertising statement in the
  future, it can be re-added — it must not ship un-approved.

## Real versioning

Table `user_onboarding_state` has **`PRIMARY KEY (user_id, onboarding_version)`**
— not `user_id` alone — so a user can accumulate one row per version they ever
touched (e.g. a `legacy_exempt` `v1` row and a later `v2` row) without losing
history. The actual "no double onboarding" invariant is a **partial unique index**:

```sql
CREATE UNIQUE INDEX idx_onboarding_one_active_per_user
    ON user_onboarding_state(user_id) WHERE status='active';
```

At most one row can be `active` per user, across **all** versions, enforced by
SQLite itself (an `IntegrityError` on any attempt to violate it) — not just
application-level logic. `current_step ∈ 1..5` remains a DB `CHECK` constraint.

### Status lifecycle (spec item F — an honest set, not a euphemism)

```sql
CHECK(status IN ('active', 'completed', 'legacy_exempt', 'superseded', 'cancelled'))
```

- **`active`** — in progress.
- **`completed`** — the user actually pressed Start on the final privacy step
  of THIS version. Only ever set by `complete_onboarding()`.
- **`legacy_exempt`** — the user was EXEMPTED from ever seeing this version's
  screens (prior meaningful product use predates it) — they never went
  through it. Renamed this round from the old `legacy_completed`, which
  falsely implied completion.
- **`superseded`** — this row WAS active, but a newer MANDATORY
  `onboarding_version` replaced it before the user finished — never
  "completed", the user did not complete the flow. Set by
  `supersede_onboarding_version()`; the caller (`bot.cmd_start`) then
  immediately starts the mandatory current version — a superseded row is
  never a dead end.
- **`cancelled`** — reserved for a future explicit cancel action; no code
  path produces it today.

### Two independent version axes

- **`ONBOARDING_VERSION`** (`onboarding_content.py`) — the 5 illustrated
  screens' content identity. Bump for a genuinely new screen redesign.
- **`PRIVACY_NOTICE_VERSION`** (`onboarding_content.py`) — the privacy notice
  text on screen 5. Bump when the notice materially changes, independent of
  screen content.

**Gap closed:** acknowledgement of the CURRENT privacy notice is tracked in a
dedicated table, `database.user_notice_acknowledgements`
(`user_id`, `notice_id`, `notice_version`, `acknowledged_at`; primary key
`(user_id, notice_id, notice_version)`) — never on `user_onboarding_state`
rows, and never inferred from `completed`/`legacy_exempt`/`superseded` status.
`database.has_privacy_notice_ack(uid, version)` / `record_notice_acknowledgement`
read and write this table exclusively. `bot.cmd_start` (via the pure
`onboarding_content.determine_onboarding_requirement`) decides one of three
outcomes for every request: `FULL_ONBOARDING` (genuinely new user, or an
active row to resume), `PRIVACY_NOTICE_ONLY` (the current notice is not yet
acknowledged), or `NOT_REQUIRED` (settled). The `PRIVACY_NOTICE_ONLY` screen
reuses screen 5's caption/asset but renders through a **distinct callback**,
`onb:<version>:privacy_only_start` (`onboarding_content.CB_PRIVACY_ONLY_START`),
answered in `bot.cb_onboarding` independently of the active-onboarding-row
gate — and deliberately does **not** create or touch any
`user_onboarding_state` row. This means bumping `PRIVACY_NOTICE_VERSION`
alone reaches every settled user, on the next `/start`, with **no** need to
also bump `ONBOARDING_VERSION` — the exact case the previous design could not
handle (a settled row's primary key was already taken, so a second
independent notice check had nowhere durable to live). A conservative,
proof-only backfill (`database._backfill_notice_acknowledgements`, run once
per boot inside `init_db`) seeds this table from any existing
`user_onboarding_state` row that already recorded a real
`privacy_notice_acknowledged_at` for a specific `privacy_notice_version` —
never inferred from status alone, since a `legacy_exempt` row never showed
the notice, and an old `completed` row may have acknowledged a *different*
notice version than the current one.

Because the `PRIVACY_NOTICE_ONLY` screen has no backing row, it is
intentionally **best-effort, not restart-resumable-by-edit** like full
onboarding: repeated `/start` before acknowledging sends a fresh card each
time rather than editing a remembered one (there is no `card_message_id` to
remember). This trade-off is accepted in exchange for never fabricating an
onboarding row purely to hold a card reference.

`card_chat_id` / `card_message_id` / `card_rendered_step` persist which Telegram
message is the user's current visible card and what step it actually shows —
this is what makes repeated `/start` (and the mandatory gate) resume by editing
that exact message instead of sending a new one each time. `card_rendered_step`
can trail behind `current_step`: a transition is committed to `current_step`
first, then delivery is attempted; if delivery raises a genuine network error
(not a `TelegramBadRequest`, which is caught and falls back to a fresh card),
that exception propagates rather than being swallowed, and the next `/start`
naturally retries delivering the untouched step by editing the same old card —
no separate "pending" flag needed.

`bot.cb_onboarding` is registered for the whole `"onb:"` namespace (not just the
current version), so a callback carrying an old or future version is always
answered (no hung Telegram spinner) and safely no-ops rather than being
interpreted against the wrong version's content.

Every state transition (`advance_onboarding_step`, `skip_onboarding_to_privacy`,
`complete_onboarding`, `retire_onboarding_version`) is a single atomic `UPDATE`
guarded on the expected prior state, so stale taps, double taps and concurrent
taps are no-ops — never corruption, never backward movement.

## Privacy governance

`user_onboarding_state` is registered in `privacy_registry.PRIVACY_REGISTRY`
(`category=CONSENT`, `export_policy=INCLUDE`, `delete_policy=CASCADE_DELETE`). It is
therefore covered by `/privacy_export_all`, the delete preview and
`/privacy_delete_all` / `/forget_all` automatically — generic, `WHERE user_id=?`
based, so it covers every version-row a user has, not just one. It holds no
free-text content — only a version tag, status, an integer step, a card message
reference and timestamps.

## Render/state recovery state machine (spec item G)

Five pieces of state, three of them persisted columns on `user_onboarding_state`:

| Concept | Where it lives | Meaning |
|---|---|---|
| **Logical `current_step`** | `current_step` column | The step the user's onboarding logically IS at, per the last committed transition (start/advance/skip). This is updated FIRST, before any Telegram call is attempted. |
| **Pending target step** | not persisted — a local variable in `bot.cb_onboarding` (`target`) | The step a `next:<target>` tap is trying to move TO. Only becomes the new `current_step` once `advance_onboarding_step`'s guarded UPDATE actually matches (idempotent — a stale/replayed tap's target that no longer matches `current_step` is a silent no-op, never applied). |
| **`card_rendered_step`** | `card_rendered_step` column | The step that was last **successfully delivered to Telegram** — i.e. the step the visible card in the chat actually shows right now. Can trail behind `current_step` (see recovery contract below). |
| **`card_chat_id` / `card_message_id`** | same-named columns | WHICH Telegram message is the current visible card, so any entrypoint can resume by editing that exact message. |

**The invariant the database must never violate:** `current_step` is committed
by a state-transition function (`start_or_get_onboarding`,
`advance_onboarding_step`, `skip_onboarding_to_privacy`,
`supersede_onboarding_version`, `complete_onboarding`) — a plain, fast,
guarded `UPDATE`/`INSERT` with no Telegram call inside it. Only AFTER that
commits does `bot._render_onboarding_card` attempt delivery via
`onboarding.send_or_edit_onboarding_card`, and only after DELIVERY succeeds
does it call `database.set_onboarding_card_ref` to advance
`card_rendered_step`/`card_chat_id`/`card_message_id` to match. **The database
never claims a screen was shown (`card_rendered_step`) until Telegram
delivery actually succeeded** — a delivery failure (or a persistence failure
of the ref itself) leaves `card_rendered_step` and the card reference stale,
pointing at whatever was last ACTUALLY delivered, while `current_step`
reflects the true, committed logical state. The next `/start` (or gate hit)
naturally reconciles the two by rendering `current_step` again, editing
whatever card `card_message_id` still points at (or sending fresh if that
fails) — no separate "pending" flag is needed; the gap between the two
columns IS the pending-delivery signal.

**Exception contract** (`onboarding.send_or_edit_onboarding_card`) — only
intentional Telegram/transport exceptions are ever caught, never a blanket
`except Exception`:
- `TelegramBadRequest` on an edit (stale card ID, deleted card, "message
  can't be edited", text↔media shape mismatch) → recoverable → send ONE
  fresh replacement card.
- `TelegramForbiddenError` (the user blocked the bot) on EITHER the edit or
  any send → NOT recoverable by retrying (a replacement send would fail
  identically) → return `None` (no card delivered, no ref persisted), never
  raise.
- Anything else (`TelegramNetworkError`, `TelegramRetryAfter`, a programmer
  error, or a failure in `set_onboarding_card_ref` itself after a
  *successful* Telegram call) → propagates uncaught. This is deliberate: the
  logical `current_step` is already durably committed by this point, so a
  propagated failure here is a safely recoverable "card is stale", not data
  corruption — and hiding it behind a blanket `except` would turn a visible,
  retriable failure into an invisible one.

Concurrency: `advance_onboarding_step`'s guarded `UPDATE ... WHERE
current_step=?` and `start_or_get_onboarding`'s `INSERT OR IGNORE` make
concurrent `/start`s and concurrent double-taps resolve to exactly one
winner at the database level, with no application-level lock.

## Media / assets

Illustration-only PNGs (no embedded text, no Telegram UI), resolved **relative to
`onboarding_content.py`'s own module location** (`pathlib.Path(__file__).resolve().parent`),
not the process working directory — the bot finds them regardless of where it
was launched from:

```
assets/onboarding/v1/01_welcome.png
assets/onboarding/v1/02_safety.png
assets/onboarding/v1/03_topics.png
assets/onboarding/v1/04_features.png
assets/onboarding/v1/05_privacy.png
```

The renderer (`onboarding.send_or_edit_onboarding_card`) is addressed by
`(chat_id, message_id)` through a `bot`-like object (aiogram's real
`send_photo`/`send_message`/`edit_message_media`/`edit_message_text` surface) —
not a specific `Message`/`CallbackQuery` object — so any entrypoint (a fresh
`/start` after a restart, the ordinary-entry gate, the callback handler) can
resume/edit the SAME persisted card. It sends one photo card the first time and
**edits that same message** for every subsequent step. See the state machine
above for the full exception contract. **If an asset file is missing/unreadable
it falls back to a text-only card with the same caption and keyboard** — the
bot never crashes. The five files are **not yet committed**; until they are,
onboarding runs in text-only mode.

## Rollout & rollback

- Stage 0: flag off, full tests.
- Stage 1: owner-only smoke.
- Stage 2: one invited test account.
- Stage 3: small invited cohort.

**Rollback:** set `FIRST_USER_ONBOARDING_ENABLED=false`. This immediately restores
the old `/start` behavior. It does **not** delete onboarding metadata and does
**not** affect questionnaires or the crisis pipeline.

## Known gap (NOT fixed here, NOT "by design"): DASS-21 completions do not show "💬 Обсудить результат"

The owner requirement is that a valid, authorized DASS-21 result shows
**"💬 Обсудить результат" / "💬 Discuss the result"** via the existing
`q:m:<session_id>` namespace, same as any other eligible questionnaire. This
is **not currently true**, confirmed with a real, authorized, full 21-answer
DASS-21 completion test
(`tests/test_dass21_flow.py::test_dass21_completion_keyboard_missing_qm_button_tracked_gap`).
This is a real product gap, tracked here, not something this PR is closing.

### Why it can't be safely closed inside this PR

1. **DASS-21 never reaches the keyboard that has `q:m`.** Completion always
   renders through `bot._send_dass21_result`, which uses
   `bot._questionnaire_completion_keyboard` (specialist report + navigation
   only). The generic PR-B result path that adds `q:m`
   (`bot._questionnaire_result_keyboard`) is never reached for DASS by design
   of the existing completion branch (`_send_questionnaire_step` routes DASS
   definitions to `_send_dass21_result` before the generic path even runs).
2. **The `q:m` gate itself rejects DASS.** `_discuss_gate_and_load` step 6
   calls `questionnaires.is_result_eligible(definition)`, which requires
   `legal_status == "synthetic"` and `scoring.type == "sum"`. The real DASS-21
   definition has `legal_status == "public_domain"` and `result_policy ==
   "no_score"` — it fails this check by construction, every time.
3. **Even bypassing (2), the score computation is wrong for DASS.**
   `_discuss_gate_and_load` calls `questionnaires.compute_sum_score`, a single
   overall sum — DASS-21 deliberately has **three independent subscales and no
   overall total** (see `CLAUDE.md`, `dass21_scorer.Dass21Scorer`, and this
   file's other tests asserting no "Итог"/"Общий"/severity wording anywhere in
   a DASS result). A single sum score across depression+anxiety+stress items
   would be a clinically meaningless — and clinically unsafe — number.
4. **The discuss topics themselves assume a single score+intensity
   narrative** ("why did this come out this way", tied to one
   `intensity_label`) — not applicable unmodified to three subscales.
5. **DASS-specific integrity checks are missing from the q:m path entirely.**
   `_send_dass21_result` re-authorizes via `dass21_access.authorize_dass21_user`
   and re-validates the DASS runtime/version before rendering a result; the
   generic `q:m` gate chain has no equivalent DASS-aware recheck.

Adding a bare button without addressing all five would produce a button that
either (a) is a dead end (fails closed to `not_available_text` the instant
it's pressed), or (b) — if `is_result_eligible`/`compute_sum_score` were
loosened just to make it "work" — silently computes and could leak a
clinically meaningless combined score for a licensed instrument that
deliberately forbids one. Neither is acceptable, so this PR does **not** add
the button, does **not** touch DASS scoring, `is_result_eligible`, or
`_discuss_gate_and_load`, and does **not** create a second discuss namespace
(`q:discuss`) to route around the problem.

### Minimal follow-up implementation plan (separate PR, not this one)

1. Add a DASS-aware branch inside `_discuss_gate_and_load` (or a sibling
   function reused only for DASS) that recognizes a DASS-21 definition via
   `dass21_runtime.is_dass21_definition` (the same check `_send_questionnaire_step`
   already uses) and, instead of `compute_sum_score`, recomputes the three
   subscale values through `dass21_scorer.Dass21Scorer` — the SAME validated
   path `_send_dass21_result` already uses — never a new scorer.
2. Re-run `dass21_access.authorize_dass21_user` and the DASS runtime/version
   integrity check inside that branch, matching `_send_dass21_result`'s own
   gate order exactly, so `q:m` for DASS is never less strict than the direct
   result screen.
3. Write DASS-specific discuss-topic copy that references the three subscales
   without inventing a combined score, a cutoff, or a severity/diagnosis label.
4. Reuse the existing `q:m:<session_id>` / `q:m:<session_id>:<topic>` callback
   namespace exactly as-is — no `q:discuss`, no second engine — only the
   internal eligibility/scoring branch changes.
5. Add a completion-to-result-to-`q:m` regression proving: the button appears
   on an authorized DASS-21 result; pressing it re-validates ownership and
   DASS authorization; the LLM payload (if any) receives only the three
   subscale values, never raw item text, answer labels, or a combined score.

Until that follow-up lands, DASS-21 results correctly show specialist-report
and navigation only, and this is the accurate, verified state of the product
today — not a design choice being defended.
