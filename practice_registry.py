"""
X20 Practice Registry — Structured, versioned practice library (Epic 4).

Each practice is a structured object (not LLM-generated). The router selects an
object, not text. Every entry carries: category, therapy approach, bilingual
name + steps, severity limits, stage contraindications, evidence level,
adverse_risk, and `user_need` (MASTER_SPEC_v2 §5) so a need-aware selector can
pick the right kind of help.

Allowed schools only (spec §11): CBT, ACT, DBT, Mindfulness, Self-Compassion,
Motivational Interviewing, Humanistic/Rogerian, Positive Psychology.
Forbidden (psychoanalysis, EMDR, hypnosis, empty-chair, pseudoscience) are NOT
present here by design.

user_need values (§5):
  NEED_HEARD      "быть услышанным"  → NO practice; only presence
  NEED_CALM       "успокоиться"      → grounding / somatic / breathing / DBT
  NEED_UNDERSTAND "понять"           → reflective / mindfulness / labeling
  NEED_SOLVE      "решить"           → CBT / MI / positive action
  NEED_BE_WITH    "пробыть в чувстве"→ ACT / mindfulness / self-compassion
"""
from typing import List, Dict, Optional

VERSION = "v1"

NEED_HEARD      = "быть услышанным"
NEED_CALM       = "успокоиться"
NEED_UNDERSTAND = "понять"
NEED_SOLVE      = "решить"
NEED_BE_WITH    = "пробыть в чувстве"
USER_NEEDS = {NEED_HEARD, NEED_CALM, NEED_UNDERSTAND, NEED_SOLVE, NEED_BE_WITH}

# Acute distress blocks cognitively demanding work (CBT/ACT/MI/reframing).
_ACUTE = ["ACUTE_DISTRESS"]


def _p(pid, category, approach, name_ru, name_en, steps_ru, steps_en,
       user_need, severity_min="low", severity_max="high",
       contraindications=None, evidence="moderate", adverse="low",
       duration=3):
    return {
        "id": pid, "version": "v1", "category": category, "approach": approach,
        "name_ru": name_ru, "name_en": name_en,
        "steps_ru": steps_ru, "steps_en": steps_en,
        "user_need": user_need,
        "severity_min": severity_min, "severity_max": severity_max,
        "contraindications": contraindications or [],
        "evidence_level": evidence, "adverse_risk": adverse,
        "duration_min": duration,
    }


REGISTRY: List[Dict] = [
    # ── Grounding / Mindfulness anchor (NEED_CALM) ────────────────────────────
    _p("grounding_5senses_v1", "grounding", "DBT",
       "Заземление 5-4-3-2-1", "5-4-3-2-1 Grounding",
       ["Найди 5 вещей, которые ВИДИШЬ. Назови каждую мысленно.",
        "Найди 4 вещи, которые можешь ПОТРОГАТЬ. Почувствуй текстуру.",
        "Найди 3 звука, которые СЛЫШИШЬ прямо сейчас.",
        "Найди 2 запаха или представь, что мог бы почувствовать.",
        "Найди 1 вкус во рту. Сделай медленный выдох."],
       ["Find 5 things you can SEE. Name each one mentally.",
        "Find 4 things you can TOUCH. Feel the texture.",
        "Find 3 sounds you can HEAR right now.",
        "Find 2 smells, or imagine ones you could smell.",
        "Find 1 taste in your mouth. Take a slow breath out."],
       NEED_CALM, evidence="strong"),
    _p("grounding_feet_v1", "grounding", "Mindfulness",
       "Опора на стопы", "Feet on the Ground",
       ["Поставь обе стопы на пол. Почувствуй давление пяток.",
        "Перенеси вес чуть вперёд, потом назад. Заметь опору.",
        "Сделай три медленных вдоха, ощущая пол под собой."],
       ["Place both feet on the floor. Feel the pressure in your heels.",
        "Shift your weight slightly forward, then back. Notice the support.",
        "Take three slow breaths, feeling the floor beneath you."],
       NEED_CALM, evidence="moderate", duration=2),
    _p("grounding_temperature_v1", "grounding", "DBT",
       "Якорь температуры", "Temperature Anchor",
       ["Назови, что вокруг тёплое, а что прохладное.",
        "Возьми в руки чашку или приложи ладони к щекам.",
        "Сконцентрируйся на ощущении тепла 20 секунд."],
       ["Name what around you is warm and what is cool.",
        "Hold a cup, or place your palms against your cheeks.",
        "Focus on the warmth for 20 seconds."],
       NEED_CALM, evidence="moderate", duration=2),

    # ── Breathing / Somatic (NEED_CALM) ───────────────────────────────────────
    _p("breathing_box_v1", "grounding", "Somatic",
       "Дыхание по квадрату", "Box Breathing",
       ["Вдох — медленно считай до 4.",
        "Задержка — держи 4 счёта.",
        "Выдох — медленно на 4 счёта.",
        "Пауза без воздуха — 4 счёта. Повтори 4 раза."],
       ["Inhale slowly, counting to 4.",
        "Hold for 4 counts.",
        "Exhale slowly for 4 counts.",
        "Pause without breathing for 4 counts. Repeat 4 times."],
       NEED_CALM, evidence="strong"),
    _p("breathing_478_v1", "somatic", "Somatic",
       "Дыхание 4-7-8", "4-7-8 Breathing",
       ["Вдох носом на 4 счёта.",
        "Задержи дыхание на 7 счётов.",
        "Выдох ртом со звуком на 8 счётов.",
        "Повтори цикл 3–4 раза."],
       ["Inhale through your nose for 4 counts.",
        "Hold your breath for 7 counts.",
        "Exhale through your mouth with a sound for 8 counts.",
        "Repeat the cycle 3–4 times."],
       NEED_CALM),
    _p("breathing_coherent_v1", "somatic", "Somatic",
       "Ровное дыхание", "Coherent Breathing",
       ["Вдыхай на 5 счётов — мягко, без усилия.",
        "Выдыхай на 5 счётов.",
        "Держи ровный ритм 1–2 минуты. Пусть плечи опустятся."],
       ["Breathe in for 5 counts — gently, no effort.",
        "Breathe out for 5 counts.",
        "Keep the even rhythm for 1–2 minutes. Let your shoulders drop."],
       NEED_CALM),
    _p("somatic_cold_v1", "somatic", "Somatic",
       "Быстрая регуляция (холод)", "Cold Water Regulation",
       ["Если можешь — умой лицо холодной водой.",
        "Или подержи запястья под холодной водой 30 секунд.",
        "Почувствуй ощущение — сигнал телу: ты в безопасности.",
        "Сделай медленный выдох. Повтори 3 раза."],
       ["If you can, splash cold water on your face.",
        "Or hold your wrists under cold water for 30 seconds.",
        "Notice the sensation — a signal: you are safe.",
        "Take a slow breath out. Repeat 3 times."],
       NEED_CALM),
    _p("somatic_pmr_v1", "somatic", "Somatic",
       "Прогрессивное расслабление", "Progressive Muscle Relaxation",
       ["Сожми кулаки на 5 секунд, потом резко отпусти.",
        "Напряги плечи к ушам на 5 секунд — отпусти.",
        "Напряги стопы, потом отпусти.",
        "Заметь разницу между напряжением и покоем."],
       ["Clench your fists for 5 seconds, then release.",
        "Lift your shoulders to your ears for 5 seconds — release.",
        "Tense your feet, then release.",
        "Notice the difference between tension and rest."],
       NEED_CALM, duration=4),

    # ── DBT distress tolerance / emotion regulation (NEED_CALM) ────────────────
    _p("dbt_stop_v1", "stabilization", "DBT",
       "Техника СТОП", "STOP Skill",
       ["С — СТОП. Физически остановись. Замри на секунду.",
        "Т — ТОРМОЗИ. Не действуй под эмоцией прямо сейчас.",
        "О — ОТСТУПИ. Сделай шаг назад мысленно, глубокий вдох.",
        "П — ПРИМИ решение осознанно. Что сейчас лучше всего?"],
       ["S — STOP. Physically freeze. Pause for a moment.",
        "T — TAKE a step back. Don't act on the emotion now.",
        "O — OBSERVE. Step back mentally, take a deep breath.",
        "P — PROCEED mindfully. What's the best thing to do now?"],
       NEED_CALM, evidence="strong", duration=2),
    _p("dbt_tipp_v1", "stabilization", "DBT",
       "TIPP — быстрый сброс", "TIPP for Distress",
       ["Температура: холодная вода на лицо или прохладный воздух.",
        "Интенсивное движение: 30–60 секунд активности.",
        "Медленное дыхание: выдох длиннее вдоха.",
        "Расслабь мышцы тела по очереди."],
       ["Temperature: cold water on the face or cool air.",
        "Intense movement: 30–60 seconds of activity.",
        "Paced breathing: make the exhale longer than the inhale.",
        "Relax your muscles one group at a time."],
       NEED_CALM, evidence="strong"),
    _p("dbt_self_soothe_v1", "stabilization", "DBT",
       "Самоуспокоение через чувства", "Self-Soothe with the Senses",
       ["Выбери одно чувство, которое легко порадовать сейчас.",
        "Зрение: посмотри на что-то приятное. Слух: мягкий звук/музыка.",
        "Дай себе 2 минуты просто на это ощущение."],
       ["Pick one sense that's easy to comfort right now.",
        "Sight: look at something pleasant. Sound: soft music.",
        "Give yourself 2 minutes just for that sensation."],
       NEED_CALM, duration=2),
    _p("dbt_opposite_action_v1", "stabilization", "DBT",
       "Противоположное действие", "Opposite Action",
       ["Назови эмоцию и что она «толкает» тебя сделать.",
        "Если действие не помогает тебе — выбери противоположное мягко.",
        "Сделай маленький шаг в сторону противоположного действия."],
       ["Name the emotion and the action it's pushing you toward.",
        "If that action won't help you, choose the gentle opposite.",
        "Take one small step toward the opposite action."],
       NEED_CALM, contraindications=_ACUTE),
    _p("dbt_wise_mind_v1", "stabilization", "DBT",
       "Мудрый разум", "Wise Mind",
       ["Заметь «эмоциональный ум» — что он говорит?",
        "Заметь «рациональный ум» — что говорит он?",
        "Найди точку посередине: что чувствуется верным и спокойным?"],
       ["Notice 'emotion mind' — what is it saying?",
        "Notice 'reasonable mind' — what does it say?",
        "Find the middle: what feels both true and calm?"],
       NEED_UNDERSTAND, contraindications=_ACUTE),
    _p("dbt_radical_acceptance_v1", "stabilization", "DBT",
       "Радикальное принятие", "Radical Acceptance",
       ["Назови факт, который ты не можешь изменить прямо сейчас.",
        "Скажи себе: «Это так, даже если мне это не нравится».",
        "Заметь, как борьба с фактом добавляет боли.",
        "Сделай выдох и позволь факту просто быть."],
       ["Name a fact you cannot change right now.",
        "Tell yourself: 'This is so, even if I don't like it.'",
        "Notice how fighting the fact adds suffering.",
        "Breathe out and let the fact simply be."],
       NEED_BE_WITH, contraindications=_ACUTE),

    # ── CBT (NEED_SOLVE) ───────────────────────────────────────────────────────
    _p("cbt_thought_record_v1", "cbt", "CBT",
       "Дневник мысли", "Thought Record",
       ["Какая мысль беспокоит больше всего? Запиши её.",
        "Какие факты ПОДДЕРЖИВАЮТ эту мысль?",
        "Какие факты ПРОТИВОРЕЧАТ ей?",
        "Что бы ты сказал другу с такой мыслью?",
        "Переформулируй мысль реалистичнее."],
       ["What thought bothers you most? Write it down.",
        "What facts SUPPORT this thought?",
        "What facts CONTRADICT it?",
        "What would you tell a friend with this thought?",
        "Reframe the thought more realistically."],
       NEED_SOLVE, severity_max="medium", contraindications=_ACUTE,
       evidence="strong", duration=5),
    _p("cbt_distortions_v1", "cbt", "CBT",
       "Когнитивные искажения", "Spotting Distortions",
       ["Запиши тревожную мысль.",
        "Это «всё или ничего»? Катастрофизация? Чтение мыслей?",
        "Назови искажение по имени.",
        "Скажи мысль точнее, без искажения."],
       ["Write down the anxious thought.",
        "Is it all-or-nothing? Catastrophizing? Mind-reading?",
        "Name the distortion.",
        "Restate the thought more accurately, without the distortion."],
       NEED_SOLVE, severity_max="medium", contraindications=_ACUTE),
    _p("cbt_behavioral_activation_v1", "cbt", "CBT",
       "Поведенческая активация", "Behavioral Activation",
       ["Назови одно маленькое дело, которое раньше давало смысл/радость.",
        "Сделай его ещё меньше — до 5 минут.",
        "Выбери конкретное время сегодня.",
        "После — заметь, как изменилось состояние, без оценки."],
       ["Name one small thing that used to give meaning or joy.",
        "Make it even smaller — down to 5 minutes.",
        "Pick a specific time today.",
        "Afterwards, notice any shift, without judging it."],
       NEED_SOLVE, contraindications=_ACUTE),
    _p("cbt_probability_v1", "cbt", "CBT",
       "Вероятность против катастрофы", "Probability vs Catastrophe",
       ["Назови, чего ты боишься, конкретно.",
        "Оцени реальную вероятность от 0 до 100%.",
        "Если бы это случилось — как бы ты справился?",
        "Заметь разницу между «возможно» и «точно»."],
       ["Name exactly what you fear.",
        "Rate its real probability from 0 to 100%.",
        "If it happened — how would you cope?",
        "Notice the gap between 'possible' and 'certain'."],
       NEED_SOLVE, severity_max="medium", contraindications=_ACUTE),
    _p("cbt_worry_time_v1", "cbt", "CBT",
       "Время для тревоги", "Worry Time",
       ["Запиши тревогу одной строкой.",
        "Назначь ей 10 минут позже сегодня — «время тревоги».",
        "Сейчас мягко верни внимание к текущему делу.",
        "В назначенное время вернись к списку, если ещё актуально."],
       ["Write the worry in one line.",
        "Assign it 10 minutes later today — 'worry time'.",
        "For now, gently return attention to the present task.",
        "At the set time, revisit the list if it still matters."],
       NEED_SOLVE, contraindications=_ACUTE),

    # ── ACT (NEED_BE_WITH) ─────────────────────────────────────────────────────
    _p("act_defusion_v1", "act", "ACT",
       "Разделение с мыслью", "Cognitive Defusion",
       ["Заметь тревожную мысль. Не борись с ней.",
        "Скажи: «Я замечаю, что у меня есть мысль о том, что...»",
        "Представь мысль как облако, что проплывает мимо.",
        "Мысль может быть рядом — а ты делаешь, что важно тебе."],
       ["Notice the anxious thought. Don't fight it.",
        "Say: 'I notice I'm having the thought that...'",
        "Picture the thought as a cloud drifting by.",
        "The thought can stay — and you still do what matters."],
       NEED_BE_WITH, severity_max="medium", contraindications=_ACUTE),
    _p("act_leaves_stream_v1", "act", "ACT",
       "Листья на ручье", "Leaves on a Stream",
       ["Представь ручей и листья, плывущие по нему.",
        "Каждую мысль клади на лист и отпускай по течению.",
        "Не торопи и не задерживай листья — просто наблюдай.",
        "Если отвлёкся — мягко вернись к ручью."],
       ["Imagine a stream with leaves floating on it.",
        "Place each thought on a leaf and let it drift away.",
        "Don't rush or hold the leaves — just watch.",
        "If you drift off, gently return to the stream."],
       NEED_BE_WITH, contraindications=_ACUTE, duration=4),
    _p("act_values_v1", "act", "ACT",
       "Прояснение ценностей", "Values Clarification",
       ["Назови область жизни, которая сейчас важна.",
        "Кем ты хочешь быть в ней — не что иметь, а как поступать?",
        "Какой один маленький шаг был бы в эту сторону?"],
       ["Name a life area that matters right now.",
        "Who do you want to be in it — not what to have, but how to act?",
        "What one small step would point in that direction?"],
       NEED_BE_WITH, contraindications=_ACUTE),
    _p("act_committed_action_v1", "act", "ACT",
       "Шаг по ценности", "Committed Action",
       ["Вспомни ценность, которая тебе дорога.",
        "Назови одно действие на 10 минут в её сторону.",
        "Сделай его маленьким и конкретным.",
        "Соверши шаг, даже если есть страх рядом."],
       ["Recall a value you care about.",
        "Name one 10-minute action toward it.",
        "Make it small and concrete.",
        "Take the step, even with fear nearby."],
       NEED_SOLVE, contraindications=_ACUTE),
    _p("act_self_as_context_v1", "act", "ACT",
       "Наблюдающее Я", "Self-as-Context",
       ["Заметь, что ты замечаешь свои мысли и чувства.",
        "Часть тебя наблюдает — она была с тобой всю жизнь.",
        "Из этой точки чувства можно встречать, не растворяясь в них."],
       ["Notice that you are noticing your thoughts and feelings.",
        "A part of you observes — it has been with you all your life.",
        "From there, feelings can be met without dissolving into them."],
       NEED_BE_WITH, contraindications=_ACUTE),
    _p("act_acceptance_v1", "act", "ACT",
       "Готовность чувствовать", "Acceptance & Willingness",
       ["Назови чувство, которое хочется оттолкнуть.",
        "Где оно в теле? Опиши его как погоду.",
        "Сделай вдох и дай ему место, не борясь.",
        "Заметь: ты можешь нести это и продолжать жить."],
       ["Name a feeling you want to push away.",
        "Where is it in the body? Describe it like weather.",
        "Breathe and make room for it, without fighting.",
        "Notice: you can carry this and still keep living."],
       NEED_BE_WITH, contraindications=_ACUTE),

    # ── Mindfulness (NEED_UNDERSTAND / NEED_BE_WITH) ───────────────────────────
    _p("mind_body_scan_v1", "mindfulness", "Mindfulness",
       "Сканирование тела", "Body Scan",
       ["Перенеси внимание на стопы. Что там за ощущение?",
        "Медленно поднимайся вниманием: ноги, живот, грудь, плечи.",
        "Не меняй ничего — просто замечай.",
        "Закончи вниманием на дыхании."],
       ["Bring attention to your feet. What sensation is there?",
        "Slowly move up: legs, belly, chest, shoulders.",
        "Change nothing — just notice.",
        "Finish with attention on your breath."],
       NEED_BE_WITH, duration=5),
    _p("mind_breathing_v1", "mindfulness", "Mindfulness",
       "Осознанное дыхание", "Mindful Breathing",
       ["Найди дыхание там, где оно заметнее всего.",
        "Следи за одним вдохом и выдохом полностью.",
        "Когда ум уплыл — мягко вернись к следующему вдоху."],
       ["Find the breath where it's most noticeable.",
        "Follow one full inhale and exhale.",
        "When the mind wanders, gently return to the next breath."],
       NEED_UNDERSTAND, evidence="strong"),
    _p("mind_label_emotion_v1", "mindfulness", "Mindfulness",
       "Назвать чувство", "Name the Feeling",
       ["Остановись и спроси: что я сейчас чувствую?",
        "Подбери одно-два слова: «тревога», «грусть», «злость».",
        "Назвать — значит немного приручить. Где оно в теле?"],
       ["Pause and ask: what am I feeling right now?",
        "Pick one or two words: 'anxiety', 'sadness', 'anger'.",
        "Naming it tames it a little. Where is it in the body?"],
       NEED_UNDERSTAND, evidence="strong", duration=2),
    _p("mind_open_awareness_v1", "mindfulness", "Mindfulness",
       "Открытое внимание", "Open Awareness",
       ["Сядь удобно и расширь внимание на всё вокруг.",
        "Пусть звуки, ощущения и мысли приходят и уходят.",
        "Ничего не лови и не гони — просто будь свидетелем."],
       ["Sit comfortably and widen attention to everything around.",
        "Let sounds, sensations and thoughts come and go.",
        "Catch nothing, push nothing — just witness."],
       NEED_BE_WITH, duration=4),
    _p("mind_raisin_v1", "mindfulness", "Mindfulness",
       "Минута внимательности", "One Mindful Minute",
       ["Возьми любой предмет или глоток воды.",
        "Исследуй его как впервые: вид, текстура, вкус.",
        "Дай 60 секунд полного внимания одному действию."],
       ["Take any object or a sip of water.",
        "Explore it as if for the first time: look, texture, taste.",
        "Give 60 seconds of full attention to one action."],
       NEED_UNDERSTAND, duration=1),

    # ── Self-Compassion (NEED_BE_WITH) ─────────────────────────────────────────
    _p("sc_break_v1", "self_compassion", "Self-Compassion",
       "Пауза самосострадания", "Self-Compassion Break",
       ["Скажи: «Это момент боли».",
        "Скажи: «Боль — часть жизни, я не один в этом».",
        "Положи ладонь на грудь: «Можно быть добрым к себе сейчас»."],
       ["Say: 'This is a moment of pain.'",
        "Say: 'Pain is part of life; I'm not alone in this.'",
        "Hand on your chest: 'May I be kind to myself right now.'"],
       NEED_BE_WITH, evidence="strong", duration=2),
    _p("sc_friend_v1", "self_compassion", "Self-Compassion",
       "Сострадательный друг", "Compassionate Friend",
       ["Представь, что близкий друг переживает то же самое.",
        "Что бы ты сказал ему — тёплым тоном?",
        "Теперь скажи эти же слова себе."],
       ["Imagine a close friend going through the same thing.",
        "What would you say to them, warmly?",
        "Now say those same words to yourself."],
       NEED_BE_WITH, evidence="strong"),
    _p("sc_letter_v1", "self_compassion", "Self-Compassion",
       "Письмо себе", "Letter to Yourself",
       ["Напиши пару строк себе от лица того, кто тебя принимает.",
        "Без критики — только понимание того, как тебе тяжело.",
        "Перечитай медленно."],
       ["Write a few lines to yourself from someone who accepts you.",
        "No criticism — only understanding of how hard it is.",
        "Read it back slowly."],
       NEED_BE_WITH, duration=5),
    _p("sc_soothing_touch_v1", "self_compassion", "Self-Compassion",
       "Успокаивающее касание", "Soothing Touch",
       ["Положи руку на сердце или обхвати себя за плечи.",
        "Почувствуй тепло и лёгкое давление ладони.",
        "Сделай несколько медленных вдохов в этом тепле."],
       ["Place a hand on your heart or hug your shoulders.",
        "Feel the warmth and gentle pressure of your hand.",
        "Take a few slow breaths in that warmth."],
       NEED_CALM, duration=2),

    # ── Motivational Interviewing (NEED_SOLVE) ─────────────────────────────────
    _p("mi_ruler_v1", "motivational", "MI",
       "Шкала готовности", "Readiness Ruler",
       ["По шкале 0–10: насколько ты готов к этому изменению?",
        "Почему не ниже на пару баллов? Что уже на твоей стороне?",
        "Что подняло бы оценку на один балл?"],
       ["On a 0–10 scale: how ready are you for this change?",
        "Why not a couple points lower? What's already on your side?",
        "What would raise the number by one point?"],
       NEED_SOLVE, contraindications=_ACUTE),
    _p("mi_pros_cons_v1", "motivational", "MI",
       "За и против", "Pros and Cons",
       ["Назови плюсы того, чтобы оставить как есть.",
        "Назови минусы того, чтобы оставить как есть.",
        "Назови плюсы изменения. Что перевешивает для тебя?"],
       ["Name the upsides of leaving things as they are.",
        "Name the downsides of leaving things as they are.",
        "Name the upsides of changing. What tips the balance for you?"],
       NEED_SOLVE, contraindications=_ACUTE),
    _p("mi_looking_forward_v1", "motivational", "MI",
       "Взгляд вперёд", "Looking Forward",
       ["Представь себя через год, если ничего не менять.",
        "А теперь — если маленький шаг всё же сделан.",
        "Какая картинка ближе к тому, чего ты хочешь?"],
       ["Imagine yourself a year from now if nothing changes.",
        "Now imagine it if one small step was taken.",
        "Which picture is closer to what you want?"],
       NEED_SOLVE, contraindications=_ACUTE),

    # ── Humanistic / Rogerian (NEED_UNDERSTAND / NEED_HEARD path) ──────────────
    _p("reflective_listen_v1", "reflective", "Rogerian",
       "Эмпатическое принятие", "Empathic Reflection",
       ["Опиши, что происходит — без выводов, только факты.",
        "Что ты чувствуешь при этом? Назови эмоцию.",
        "Это нормально — чувствовать именно это сейчас.",
        "Чего тебе сейчас больше всего не хватает?"],
       ["Describe what's happening — no judgments, just facts.",
        "What do you feel about it? Name the emotion.",
        "It's okay to feel exactly that right now.",
        "What do you need most right now?"],
       NEED_UNDERSTAND, evidence="strong", duration=5),
    _p("reflective_name_need_v1", "reflective", "Rogerian",
       "Назвать потребность", "Name the Need",
       ["Под этим чувством — какая потребность? Покой? Связь? Уважение?",
        "Назови её одним словом.",
        "Что было бы маленьким знаком заботы об этой потребности?"],
       ["Under this feeling — what need is there? Calm? Connection? Respect?",
        "Name it in one word.",
        "What would be a small act of care for that need?"],
       NEED_UNDERSTAND),

    # ── Positive Psychology (NEED_UNDERSTAND / NEED_SOLVE) ─────────────────────
    _p("pos_three_good_v1", "positive", "Positive Psychology",
       "Три хорошие вещи", "Three Good Things",
       ["Вспомни три вещи сегодня, что прошли хоть немного хорошо.",
        "Даже самые маленькие считаются.",
        "Для одной из них — какова была твоя роль?"],
       ["Recall three things today that went even a little well.",
        "The smallest ones count.",
        "For one of them — what was your part in it?"],
       NEED_UNDERSTAND),
    _p("pos_gratitude_v1", "positive", "Positive Psychology",
       "Благодарность", "Gratitude Note",
       ["Назови одного человека или вещь, за которые ты благодарен.",
        "Опиши конкретно — за что именно.",
        "Заметь, что меняется в теле, когда задерживаешься на этом."],
       ["Name one person or thing you're grateful for.",
        "Be specific — for what exactly.",
        "Notice what shifts in your body as you stay with it."],
       NEED_UNDERSTAND, duration=2),
    _p("pos_strengths_v1", "positive", "Positive Psychology",
       "Сильные стороны", "Signature Strengths",
       ["Назови качество, которое помогало тебе в трудный момент.",
        "Где оно проявилось недавно, даже чуть-чуть?",
        "Как можно опереться на него сегодня?"],
       ["Name a quality that has helped you in a hard moment.",
        "Where did it show up recently, even slightly?",
        "How could you lean on it today?"],
       NEED_SOLVE),
    _p("pos_best_self_v1", "positive", "Positive Psychology",
       "Лучшая версия дня", "Best Possible Day",
       ["Представь завтрашний день, прошедший по-доброму к тебе.",
        "Что в нём есть — конкретно, без идеализации?",
        "Какую одну деталь можно приблизить уже сейчас?"],
       ["Imagine tomorrow going kindly for you.",
        "What's in it — concretely, without idealizing?",
        "What one detail could you bring closer already now?"],
       NEED_SOLVE),
]

# Scenario → primary category (used by the legacy scenario-based selector).
CATEGORY_MAP = {
    "crisis": "grounding", "grounding": "grounding",
    "stabilization": "stabilization", "cbt_thought": "cbt",
    "act_acceptance": "act", "reflective": "reflective",
    "somatic": "somatic", "open_chat": "reflective",
}

# ── Canonical production practice catalog (Therapeutic Core Foundation) ────
# Every scenario in CATEGORY_MAP already had exactly one safe, evidence-
# ranked practice reachable through select_practice() -- an empirically
# verified, emergent property of the evidence-rank/id tie-break in _best().
# These 7 ids are that exact reachable set, now made an EXPLICIT, enforced
# allowlist instead of an emergent property: select_practice() filters to
# this set before ranking, and get_production_practice_by_id() is the only
# lookup a Telegram callback (untrusted input) may use -- a forged, stale,
# or catalog-only id fails closed (returns None) rather than being served.
# The remaining 36 REGISTRY entries are real, safe, approved-school
# definitions (CATALOG_ONLY status) -- kept for deliberate future wiring,
# never silently selectable. No entry in REGISTRY uses a prohibited modality
# (psychoanalysis/EMDR/IFS/schema therapy/trauma excavation/dream
# interpretation/diagnosis/medical treatment) -- verified directly against
# every entry's `approach`/name fields, not merely assumed.
PRODUCTION_PRACTICE_IDS = frozenset({
    "breathing_box_v1", "dbt_stop_v1", "cbt_thought_record_v1",
    "cbt_behavioral_activation_v1", "act_acceptance_v1",
    "reflective_listen_v1", "breathing_478_v1",
})

_SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2}
_EVIDENCE_RANK = {"strong": 0, "moderate": 1, "weak": 2}


def is_production_practice(practice_id: str) -> bool:
    return practice_id in PRODUCTION_PRACTICE_IDS


def practice_status(practice_id: str) -> str:
    """PRODUCTION | CATALOG_ONLY | UNKNOWN (id not in REGISTRY at all).
    No entry is currently OBSOLETE or PROHIBITED -- see PRODUCTION_PRACTICE_IDS
    docstring."""
    if practice_id in PRODUCTION_PRACTICE_IDS:
        return "PRODUCTION"
    if any(p["id"] == practice_id for p in REGISTRY):
        return "CATALOG_ONLY"
    return "UNKNOWN"


def _fits(p: Dict, stage: str, user_sev: int) -> bool:
    return (stage not in p.get("contraindications", [])
            and _SEVERITY_ORDER.get(p["severity_min"], 0) <= user_sev
            and user_sev <= _SEVERITY_ORDER.get(p["severity_max"], 2))


def _best(candidates: List[Dict]) -> Dict:
    """Deterministic pick: strongest evidence first, then stable by id."""
    return sorted(candidates,
                  key=lambda p: (_EVIDENCE_RANK.get(p["evidence_level"], 1), p["id"]))[0]


def _localize(practice: Dict, lang: str) -> Dict:
    result = dict(practice)
    result["steps"] = practice.get(f"steps_{lang}", practice.get("steps_ru", []))
    result["name"] = practice.get(f"name_{lang}", practice.get("name_ru", practice["id"]))
    return result


def select_practice(scenario: str, stage: str = "OPEN",
                    severity: str = "medium", lang: str = "ru") -> Optional[Dict]:
    """Legacy scenario-based selection (kept for the current pipeline).

    Respects contraindications and severity limits; among valid candidates picks
    the strongest-evidence one deterministically. Falls back to grounding.
    ENFORCED (not just emergent): candidates are filtered to
    PRODUCTION_PRACTICE_IDS before ranking, so a future CATALOG_ONLY addition
    with stronger evidence can never become silently selectable."""
    category = CATEGORY_MAP.get(scenario, "grounding")
    user_sev = _SEVERITY_ORDER.get(severity, 1)
    candidates = [p for p in REGISTRY if p["category"] == category
                  and p["id"] in PRODUCTION_PRACTICE_IDS and _fits(p, stage, user_sev)]
    if not candidates:
        candidates = [p for p in REGISTRY if p["category"] == "grounding"
                      and p["id"] in PRODUCTION_PRACTICE_IDS]
    return _localize(_best(candidates), lang)


def get_production_practice_by_id(practice_id: str, lang: str = "ru") -> Optional[Dict]:
    """The ONLY practice lookup a Telegram callback (untrusted input) may
    use. Fails closed (returns None) for any non-production id -- forged,
    stale-version, catalog-only, or simply nonexistent -- rather than
    serving it. get_practice_by_id (below) has no such guard and must never
    be called directly with callback-supplied data."""
    if practice_id not in PRODUCTION_PRACTICE_IDS:
        return None
    return get_practice_by_id(practice_id, lang)


def select_practice_by_need(user_need: str, stage: str = "OPEN",
                            severity: str = "medium",
                            lang: str = "ru") -> Optional[Dict]:
    """Need-aware selection (MASTER_SPEC_v2 §5).

    `NEED_HEARD` returns None on purpose — the user needs presence, not a
    practice. Otherwise picks the strongest-evidence practice tagged for that
    need that fits the stage/severity, falling back to grounding."""
    if user_need == NEED_HEARD:
        return None
    user_sev = _SEVERITY_ORDER.get(severity, 1)
    candidates = [p for p in REGISTRY
                  if p.get("user_need") == user_need and _fits(p, stage, user_sev)]
    if not candidates:
        candidates = [p for p in REGISTRY
                      if p["category"] == "grounding" and _fits(p, stage, user_sev)]
    if not candidates:
        return None
    return _localize(_best(candidates), lang)


def get_practice_by_id(practice_id: str, lang: str = "ru") -> Optional[Dict]:
    for p in REGISTRY:
        if p["id"] == practice_id:
            return _localize(p, lang)
    return None
