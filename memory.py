"""X20 Memory — rolling summary compression."""
from openai import AsyncOpenAI
from database import get_unsummarized_messages, mark_summarized, save_summary, get_latest_summary, get_recent_messages

THRESHOLD = 20
KEEP = 8

SUMMARY_PROMPT = """\
Сожми этот диалог в краткое резюме (3–5 предложений).
Сохрани: ключевые темы, эмоциональное состояние, важные детали о пользователе.
Пиши от третьего лица: "Пользователь говорил о...".

{existing}Диалог:
{conv}

Резюме:"""

async def maybe_summarize(user_id: int, client: AsyncOpenAI) -> None:
    rows = await get_unsummarized_messages(user_id)
    if len(rows) <= THRESHOLD:
        return
    to_sum = rows[:-KEEP]
    ids = [r[0] for r in to_sum]
    conv = "\n".join(f"{r[1].upper()}: {r[2]}" for r in to_sum)
    existing = await get_latest_summary(user_id)
    eb = f"Предыдущее резюме:\n{existing}\n\n" if existing else ""
    resp = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role":"user","content":SUMMARY_PROMPT.format(existing=eb,conv=conv)}],
        max_tokens=400, temperature=0.3,
    )
    await save_summary(user_id, resp.choices[0].message.content)
    await mark_summarized(ids)

async def build_context(user_id: int) -> tuple:
    summary = await get_latest_summary(user_id)
    recent  = await get_recent_messages(user_id, KEEP)
    return summary, recent
