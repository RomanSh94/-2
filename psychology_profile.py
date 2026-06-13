"""
X20 Psychology Profile (§5) — DETERMINISTIC, no LLM, no diagnoses.

Every dimension is an aggregate of risk signals the pipeline ALREADY computed
(stored per-message on the messages table) plus a handful of extra keyword
signals (sleep, future-orientation, themes, coping). There is no model call
anywhere in this module — `test_no_llm_called_during_profile_compute` pins that.

Each dimension is a (value, confidence) pair in [0,1]. Confidence grows with the
number of observations; the UI must show "(мало данных)" below ~0.3 so we never
assert "одиночество 80%" off two messages.

Bilingual keyword lists (ru+en) per the project convention.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Tuple


# ── Extra keyword signals (not risk categories, profile-only) ──────────────────
FUTURE_POSITIVE_PATTERNS = [
    "хочу попробовать", "планирую", "собираюсь", "буду", "когда вырасту",
    "в следующем месяце", "через год", "моя цель", "мечтаю", "хочу научиться",
    "i want to try", "i plan", "i'm going to", "my goal", "i dream of",
]
FUTURE_NEGATIVE_PATTERNS = [
    "не вижу смысла планировать", "зачем что-то планировать",
    "у меня нет будущего", "будущего нет", "нет никакого будущего",
    "no future", "no point planning", "why plan",
]
SLEEP_PROBLEM_PATTERNS = [
    "не могу уснуть", "не сплю", "плохо сплю", "бессонница", "кошмары",
    "просыпаюсь по ночам", "недосып", "не выспался", "не выспалась",
    "can't sleep", "insomnia", "nightmares", "no sleep", "didn't sleep",
]
THEME_KEYWORDS = {
    "работа": ["работ", "начальник", "коллег", "проект", "дедлайн", "увольнен",
               "офис", "work", "boss", "deadline", "colleague"],
    "семья": ["мама", "папа", "мать", "отец", "родител", "сестра", "брат",
              "семья", "family", "mom", "dad", "parents"],
    "отношения": ["парень", "девушка", "муж", "жена", "отношени", "расставан",
                  "развод", "любовь", "relationship", "boyfriend", "girlfriend",
                  "breakup", "divorce"],
    "здоровье": ["болезн", "врач", "диагноз", "лечени", "больниц", "симптом",
                 "illness", "doctor", "diagnosis", "hospital"],
    "деньги": ["деньги", "долг", "кредит", "финанс", "зарплат", "ипотек",
               "money", "debt", "loan", "salary", "rent"],
    "учёба": ["учёб", "учеб", "универ", "школа", "экзамен", "сесси", "препод",
              "study", "university", "school", "exam"],
    "самооценка": ["я никчёмн", "я никчемн", "ничего не умею", "ничтожеств",
                   "неудачник", "worthless", "loser", "useless"],
    "одиночество": ["одинок", "никого нет", "не с кем", "никто не понимает",
                    "lonely", "alone", "no one understands"],
}
COPING_PATTERNS = [
    "помогло", "сработало", "стало легче", "почувствовал себя лучше",
    "почувствовала себя лучше", "это помогает",
    "helped", "worked", "feel better", "it helps",
]


def _frac(numer: int, denom: int) -> float:
    return min(1.0, numer / denom) if denom else 0.0


def compute_future_orientation(messages: List[dict]) -> Tuple[float, float]:
    pos = sum(1 for m in messages
              if any(p in m["content"].lower() for p in FUTURE_POSITIVE_PATTERNS))
    neg = sum(1 for m in messages
              if any(p in m["content"].lower() for p in FUTURE_NEGATIVE_PATTERNS))
    if pos + neg == 0:
        return 0.5, 0.0
    return round(pos / (pos + neg), 3), round(min(1.0, (pos + neg) / 5), 3)


def compute_sleep_problems(messages: List[dict]) -> Tuple[float, float]:
    hits = sum(1 for m in messages
               if any(p in m["content"].lower() for p in SLEEP_PROBLEM_PATTERNS))
    value = min(1.0, hits / max(len(messages), 1) * 3)
    return round(value, 3), round(min(1.0, len(messages) / 10), 3)


def extract_themes(messages: List[dict]) -> List[str]:
    counts = {t: 0 for t in THEME_KEYWORDS}
    for m in messages:
        low = m["content"].lower()
        for theme, kws in THEME_KEYWORDS.items():
            if any(kw in low for kw in kws):
                counts[theme] += 1
    return [t for t, c in sorted(counts.items(), key=lambda x: -x[1]) if c >= 2][:5]


def extract_coping(messages: List[dict]) -> List[str]:
    out = []
    for m in messages:
        low = m["content"].lower()
        if any(p in low for p in COPING_PATTERNS):
            out.append(m["content"][:100])
    return out[-5:]


@dataclass
class PsychologyProfile:
    user_id: int
    loneliness: Tuple[float, float] = (0.0, 0.0)
    hopelessness: Tuple[float, float] = (0.0, 0.0)
    self_criticism: Tuple[float, float] = (0.0, 0.0)
    anxiety_level: Tuple[float, float] = (0.0, 0.0)
    social_support: Tuple[float, float] = (0.5, 0.0)
    future_orientation: Tuple[float, float] = (0.5, 0.0)
    energy_level: Tuple[float, float] = (0.5, 0.0)
    sleep_problems: Tuple[float, float] = (0.0, 0.0)
    crisis_risk: float = 0.0
    mood_trend: str = "stable"
    main_themes: List[str] = field(default_factory=list)
    coping_strategies_used: List[str] = field(default_factory=list)
    messages_analyzed: int = 0
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def empty(cls, user_id: int) -> "PsychologyProfile":
        return cls(user_id=user_id)

    def to_db_fields(self) -> dict:
        import json
        return {
            "loneliness_value": self.loneliness[0], "loneliness_confidence": self.loneliness[1],
            "hopelessness_value": self.hopelessness[0], "hopelessness_confidence": self.hopelessness[1],
            "self_criticism_value": self.self_criticism[0], "self_criticism_confidence": self.self_criticism[1],
            "anxiety_value": self.anxiety_level[0], "anxiety_confidence": self.anxiety_level[1],
            "social_support_value": self.social_support[0], "social_support_confidence": self.social_support[1],
            "future_orientation_value": self.future_orientation[0],
            "future_orientation_confidence": self.future_orientation[1],
            "energy_value": self.energy_level[0], "energy_confidence": self.energy_level[1],
            "sleep_problems_value": self.sleep_problems[0], "sleep_problems_confidence": self.sleep_problems[1],
            "crisis_risk": self.crisis_risk, "mood_trend": self.mood_trend,
            "main_themes": json.dumps(self.main_themes, ensure_ascii=False),
            "coping_strategies_used": json.dumps(self.coping_strategies_used, ensure_ascii=False),
            "messages_analyzed": self.messages_analyzed,
            "last_updated": self.last_updated.strftime("%Y-%m-%d %H:%M:%S"),
        }


async def compute_profile(user_id: int, window_days: int = 7) -> PsychologyProfile:
    """Build the profile from already-logged per-message risk snapshots.

    Fully deterministic: NO LLM, NO extra API calls."""
    from database import get_user_messages_with_risk, load_state
    from state_engine import get_emotional_trajectory

    msgs = await get_user_messages_with_risk(user_id, window_hours=window_days * 24)
    if not msgs:
        return PsychologyProfile.empty(user_id)

    total = len(msgs)
    counts: dict = {}
    for m in msgs:
        for c in m["risk_categories"]:
            counts[c] = counts.get(c, 0) + 1

    conf = round(min(1.0, total / 10), 3)   # shared count-based confidence

    loneliness = (round(_frac(counts.get("loneliness", 0), total), 3), conf)
    hopelessness = (round(_frac(counts.get("hopelessness", 0), total), 3), conf)
    self_criticism = (round(_frac(counts.get("self_blame", 0), total), 3), conf)
    anxiety = (round(_frac(counts.get("panic", 0) + counts.get("dissociation", 0), total), 3), conf)
    social_neg = counts.get("loneliness", 0) + counts.get("dependency", 0)
    social_support = (round(max(0.0, 1.0 - (social_neg / max(total, 1)) * 2), 3), conf)

    future = compute_future_orientation(msgs)
    sleep = compute_sleep_problems(msgs)

    state = await load_state(user_id)
    energy = (round(state.get("energy", 0.5), 3) if state else 0.5, 0.7 if state else 0.0)

    crisis_risk = round(min(1.0, (msgs[-1]["risk_score"] or 0) / 100), 3)
    trajectory = await get_emotional_trajectory(user_id, window_hours=window_days * 24)

    return PsychologyProfile(
        user_id=user_id,
        loneliness=loneliness, hopelessness=hopelessness, self_criticism=self_criticism,
        anxiety_level=anxiety, social_support=social_support, future_orientation=future,
        energy_level=energy, sleep_problems=sleep, crisis_risk=crisis_risk,
        mood_trend=trajectory.trend, main_themes=extract_themes(msgs),
        coping_strategies_used=extract_coping(msgs), messages_analyzed=total,
    )


async def maybe_update_profile(user_id: int, message_count: int, force: bool = False) -> None:
    """Recompute & persist on every 5th user message (or when forced, e.g. after
    a crisis event). Cheap: pure SQL aggregation, no model call."""
    if not force and message_count % 5 != 0:
        return
    from database import save_profile
    profile = await compute_profile(user_id)
    await save_profile(user_id, profile.to_db_fields())


# ── User-facing formatting (plain language, no jargon/diagnoses) ───────────────

def _bar(value: float, confidence: float) -> str:
    filled = "▓" * round(value * 5)
    empty = "░" * (5 - round(value * 5))
    suffix = " (мало данных)" if confidence < 0.3 else ""
    return f"{filled}{empty}{suffix}"


def _translate_trend(trend: str) -> str:
    return {
        "deteriorating": "📉 кажется, становится тяжелее — стоит обратить внимание",
        "stable": "➖ примерно как было",
        "improving": "📈 кажется, становится немного легче",
    }.get(trend, "пока непонятно")


def format_profile_for_user(p: dict) -> str:
    """`p` is the DB-row dict from database.get_profile()."""
    import json
    themes = json.loads(p.get("main_themes") or "[]")
    return (
        "Вот что я заметил за наши разговоры (это не диагнозы — просто то, что слышу в твоих словах):\n\n"
        f"🌿 Частые темы: {', '.join(themes) if themes else 'пока не выделил'}\n\n"
        f"Одиночество:      {_bar(p['loneliness_value'], p['loneliness_confidence'])}\n"
        f"Безнадёжность:    {_bar(p['hopelessness_value'], p['hopelessness_confidence'])}\n"
        f"Тревога:          {_bar(p['anxiety_value'], p['anxiety_confidence'])}\n"
        f"Самокритика:      {_bar(p['self_criticism_value'], p['self_criticism_confidence'])}\n"
        f"Опора на близких: {_bar(p['social_support_value'], p['social_support_confidence'])}\n"
        f"Планы на будущее: {_bar(p['future_orientation_value'], p['future_orientation_confidence'])}\n"
        f"Сон:              {_bar(p['sleep_problems_value'], p['sleep_problems_confidence'])}\n\n"
        f"📊 Динамика: {_translate_trend(p.get('mood_trend', 'stable'))}\n\n"
        f"Это на основе {p.get('messages_analyzed', 0)} сообщений. "
        f"Я не врач, это не медицинская оценка. Хочешь — сотру всё, командой /profile_reset."
    )
