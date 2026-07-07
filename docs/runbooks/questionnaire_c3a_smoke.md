# C3a — Questionnaire interpretation controlled-flow smoke

This runbook is for the owner only.

This does NOT enable `QUESTIONNAIRE_INTERPRETATION_ENABLED` in production.

This does NOT satisfy or replace the §8 crisis-delivery live-smoke prerequisite / PR 1B-1.

This only checks the already-merged questionnaire interpretation flow (PRs A, B, C1, C1.1, C2, C2.1) in a test bot process.

## What C3a can and cannot prove

C3a can add confidence in:
- real Telegram rendering (emoji, markup, message layout);
- real button/callback behavior (actual `callback_data` round-trips through Telegram);
- real event-loop/process behavior (this is the first time this flow runs outside `pytest`/`FakeCallback`).

C3a does not reliably prove:
- validator rejection fallback;
- LLM-call failure fallback.

Those two failure paths are exercised via mocking, not by triggering the real conditions live, and remain covered by automated tests instead:
- `tests/test_questionnaire_discuss.py::test_discuss_output_rejected_never_sends_latent_reply`
- `tests/test_questionnaire_discuss.py::test_discuss_llm_call_failure_never_sends_latent_reply`

C3a does not authorize production enable.

## Environment

Use the test bot only:

- launcher: `run_test_bot.py` (repo root)
- env file: `.env.test` (local only — gitignored, never committed; copy `.env.test.example` and fill in a **separate test bot token from @BotFather**, never the production token)
- DB: `x20_test.db` (forced by `run_test_bot.py`, independent of production `x20.db`)
- production bot token: forbidden
- production DB: forbidden

Before running, the startup banner (added in this PR) must show:

```text
🧪 X20 TEST bot
DB=x20_test.db
QUESTIONNAIRE_INTERPRETATION_ENABLED=True
port=8081
```

If the banner is absent, or shows `DB=x20.db`, or shows `QUESTIONNAIRE_INTERPRETATION_ENABLED=False`, **stop** — you are not looking at the test process/config you intend to smoke.

Note: `config.QUESTIONNAIRE_INTERPRETATION_ENABLED` is read once, at Python import time, from the `QUESTIONNAIRE_INTERPRETATION_ENABLED` environment variable. Editing `.env.test` while `run_test_bot.py` is already running has **no effect** — stop the process, edit `.env.test`, then start it again.

## Setup

1. Create/update your local `.env.test` only (never commit it):

```dotenv
BOT_TOKEN=<your separate test-bot token from @BotFather>
QUESTIONNAIRE_INTERPRETATION_ENABLED=true
# OPENAI_API_KEY / ADMIN_PASSWORD / ADMIN_USER_IDS as needed, see .env.test.example
```

2. Confirm `.env.test` is not tracked:

```bash
cd "C:\Users\Я\Desktop\x20_production_final"
git status --short
git check-ignore .env.test || true
```

`git check-ignore .env.test` should print `.env.test`, confirming it's ignored.

3. Start the test bot:

```bash
python run_test_bot.py
```

4. Confirm the banner shows the flag as `True`, `DB=x20_test.db`, and that a synthetic eligible questionnaire exists (e.g. the `demo_result_eligible_v1` fixture-equivalent definition under your test instance's `private_questionnaires/` directory, `legal_status: synthetic`, `result_policy: user_visible_full`, `status: active`). Do not use real STAI/GAD-7/PHQ/etc. content for this test.

## Happy-path walkthrough

| # | Action | Expected |
|---|--------|----------|
| 1 | `/menu` → `📊 Тесты` (or equivalent questionnaire category entry) | Category/list appears |
| 2 | Start the synthetic eligible questionnaire and answer through completion | Result screen appears with score, color intensity bar, intensity label; no diagnosis wording |
| 3 | Tap `📊 Расчёты` | Raw calculation shown; no diagnosis/intervention language |
| 4 | Back, tap `🧠 Что значат шкалы` | Plain-language explanation; original wording; no manual paraphrase |
| 5 | Back, tap `🧾 Отчёт специалисту` | Report with answers + a score line (flag is on and definition is eligible) |
| 6 | Back, tap `💬 Обсудить результат` | Deterministic discuss menu appears; no LLM topic reply yet |
| 7 | Tap `Почему так вышло?` | One bounded, non-diagnostic reply |
| 8 | Tap `Что можно сделать дальше?` | One bounded, non-diagnostic reply |
| 9 | Tap `Вопросы специалисту` | One bounded, non-diagnostic reply |

## Best-effort failure-path walkthrough

| # | Action | Expected |
|----|--------|----------|
| 10 | From a second test account not permitted by `access_control.assert_a1_allowed` (e.g. an unmapped/ineligible role in `controlled_clinical_test` mode), tap a discuss topic button | Neutral not-available message; no crash; no raw uid shown anywhere (logs or chat) |
| 11 | If any discuss reply looks diagnosis-shaped, disorder-naming, or otherwise unsafe, record the exact text and stop | Do not treat a clean pass here as proof the validator-rejection fallback works — that path is proven only by the automated tests listed above, not by getting lucky with one live LLM call |

## After the walkthrough

1. Stop the test bot (`Ctrl+C`).
2. Do not copy any `.env.test` settings into production `.env`.
3. Record pass/fail per row above.
4. Bring results back for review before any C3b / production-enable decision.

## Still blocked after C3a

Even if every row above passes:

- §8 crisis-delivery live smoke remains not passed.
- PR 1B-1 remains not replaced or satisfied.
- `QUESTIONNAIRE_INTERPRETATION_ENABLED` production enable remains blocked, pending either (1) PR 1B-1 passing, or (2) a separate, explicit future governance decision.
