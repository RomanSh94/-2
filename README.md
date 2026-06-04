# X20 — AI Emotional Support System

**Версия:** Production 2.0  
**Язык:** Python 3.12+  
**Архитектура:** Aiogram 3.7 + OpenAI GPT-4o-mini + SQLite + Flask Dashboard

---

## 📋 Описание

X20 — безопасная система AI поддержки эмоционального состояния пользователей.

**Что X20 делает:**
- 🎯 Распознаёт эмоциональное состояние (риск, паника, одиночество, выгорание)
- 🧠 Выбирает подходящий психологический сценарий (CBT, ACT, Somatic, Grounding)
- 📊 Отслеживает прогресс пользователя через до/после измерения эмоционального состояния
- 🔄 Предоставляет структурированные практики (дыхание, заземление, когнитивная работа)
- 📈 Собирает исследовательские данные для улучшения качества помощи
- 🛡️ Максимально безопасна (жесткие правила, валидация LLM ответов)

**Что X20 НЕ делает:**
- ❌ НЕ симулирует психолога или врача
- ❌ НЕ ставит диагнозы
- ❌ НЕ заменяет живое общение или профессиональную помощь
- ❌ НЕ создаёт зависимость от бота

---

## 🏗️ Архитектура

```
Сообщение пользователя
    ↓
[1. Risk Detector] — детерминированное определение рисков (explicit + implicit сигналы)
    ↓
[2. Language Detector] — определение языка (RU/EN)
    ↓
[3. Stage Detector] — определение стадии (ACUTE_DISTRESS / REFLECTION / PROBLEM_SOLVING / GROWTH)
    ↓
[4. State Engine] — отслеживание эмоциональной траектории (anxiety, panic, energy, etc.)
    ↓
[5. Readiness Engine] — оценка готовности к структурированной работе
    ↓
[6. Cognitive Capacity] — определение когнитивной нагрузки
    ↓
[7. Scenario Router] — выбор психологического сценария
    ↓
[8. Relationship Monitor] — проверка признаков зависимости от бота
    ↓
[9. Practice Selector] — выбор конкретной практики из реестра
    ↓
[10. Memory Builder] — получение контекста из истории + summary
    ↓
[11. LLM Call] — генерация ответа OpenAI (GPT-4o-mini)
    ↓
[12. Safety Validator] — проверка ответа перед отправкой
    ↓
[13. Push Notifications] — отправка alerts при критических событиях
    ↓
[14. Outcome Tracking] — просьба оценить до/после эффект
    ↓
Пользователь получает ответ
```

**Ключевой принцип:** LLM НИКОГДА не принимает решения о безопасности. Только код.

---

## 📦 Модули

### Core Risk & State Management
- **`risk_detector.py`** — детекция рисков (suicide, self-harm, panic, dependency и т.д.)
- **`stage_detector.py`** — определение стадии эмоционального процесса
- **`state_engine.py`** — отслеживание эмоциональной траектории (anxiety → energy → openness)
- **`readiness_engine.py`** — оценка готовности к структурированной работе
- **`cognitive_capacity.py`** — определение когнитивной нагрузки (overload ↔ full)

### Psychology & Practice Selection
- **`practice_registry.py`** — библиотека практик (grounding, CBT, ACT, somatic и т.д.) с версионированием
- **`relationship_monitor.py`** — обнаружение признаков зависимости от бота

### Language & Detection
- **`language_detector.py`** — определение языка (RU/EN) без зависимостей
- **`prompts.py`** — система промптов для 8 сценариев × 2 языка

### Data & Safety
- **`database.py`** — полная схема с intervention_results, adverse_events, router_decision_logs
- **`memory.py`** — rolling summary compression для длинных историй
- **`notifications.py`** — push alerts (email + webhook) при критических событиях
- **`safety_validator.py`** — валидация LLM ответов перед отправкой

### Infrastructure
- **`voice.py`** — транскрипция голосовых сообщений (Whisper)
- **`scheduler.py`** — ежедневные check-in сообщения по расписанию
- **`ab_testing.py`** — детерминированное распределение пользователей по вариантам

### Bot & Dashboard
- **`bot.py`** — основной телеграм бот (обработка сообщений, callbacks, pipeline)
- **`dashboard.py`** — Flask админ-панель с Research и A/B статистикой
- **`config.py`** — конфигурация из переменных окружения

---

## 🚀 Быстрый старт

### 1. Установка (Windows)
```powershell
# Скачайте Python 3.12: https://www.python.org/downloads/
# Откройте PowerShell в папке проекта

python -m venv venv
.\venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt

copy .env.example .env
notepad .env  # Заполните BOT_TOKEN и OPENAI_API_KEY
```

### 2. Запуск
```powershell
python bot.py
```

### 3. Доступ
- **Telegram:** Найдите вашего бота и напишите `/start`
- **Dashboard:** http://localhost:8080 (логин: admin, пароль из `.env`)

---

## ⚙️ Конфигурация

### .env переменные

```env
# Telegram
BOT_TOKEN=123456:ABC-DEF...

# OpenAI
OPENAI_API_KEY=sk-...

# Admin Dashboard
ADMIN_PASSWORD=super_secure_password
ADMIN_PORT=8080
ADMIN_USER_IDS=123456789,987654321  # Telegram IDs для admin alerts

# Email alerts (опционально)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your@gmail.com
SMTP_PASSWORD=app_specific_password
ALERT_EMAIL_TO=admin@yourdomain.com

# Webhook alerts (Slack/Discord/custom)
ALERT_WEBHOOK_URL=https://hooks.slack.com/services/...

# A/B testing
AB_VARIANTS=control,variant_a
```

---

## 📊 Dashboard Features

### Tabs
- **Overview** — статистика (пользователи, сообщения, риск-события)
- **Research** — effectiveness per scenario, A/B test результаты
- **Users** — список пользователей + история диалогов
- **Moderation** — логи риск-событий
- **Adverse Events** — побочные эффекты от практик
- **Validator** — заблокированные LLM ответы
- **Export** — CSV экспорт для анализа

### Key Metrics
- **Avg Improvement** — (before_score - after_score) за сессию
- **% Helped** — процент сессий с positive delta
- **Confidence Score** — уверенность системы в качестве ответа
- **Adverse Rate** — процент сессий где состояние ухудшилось
- **A/B Winner** — какой вариант даёт лучший результат

---

## 🎯 Scenario Types

1. **Crisis** — suicide/self-harm detected → stabilization override
2. **Grounding** — panic/dissociation → 5-senses, breathing exercises
3. **Stabilization** — overwhelm → short validation + one action
4. **CBT Thought** — anxiety + capacity → examine beliefs gently
5. **ACT Acceptance** — hopelessness + openness → defusion from thoughts
6. **Reflective** — loneliness → Rogerian client-centered listening
7. **Somatic** — low energy → body regulation, minimal talking
8. **Open Chat** — default supportive conversation

---

## 🔬 Research Data Collection

Система собирает данные для анализа эффективности:

- **Intervention Results** — what practice, who, when, before/after score, confidence
- **Router Decision Logs** — snapshot состояния и почему выбран сценарий
- **Weekly Progress** — агрегированная прогресс по неделям
- **Practice Versions** — отслеживание effectiveness по версии практики
- **Adverse Events** — когда состояние ухудшилось + почему
- **Response Quality** — user 👍/👎 feedback на ответы (после сессии)
- **A/B Results** — effectiveness контрола vs вариантов

---

## 🛡️ Safety Guardrails

### Hard Rules
- ❌ Никаких диагнозов ("у тебя депрессия")
- ❌ Никакой имитации привязанности ("я люблю тебя")
- ❌ Никакого поощрения зависимости ("тебе нужен только я")
- ❌ Никакой принудительной травма-работы
- ❌ Никаких философских обсуждений суицида

### Crisis Override
Если detected `suicide` или `self_harm`:
1. Normal pipeline ОСТАНАВЛИВАЕТСЯ
2. Отправляется детерминированный crisis text
3. Admin получает push alert
4. Система логирует event

### Validator Layer
Каждый ответ GPT проверяется на:
- Forbidden phrases (диагнозы, манипуляция)
- Длину (>150 слов → блокировка)
- Certainty claims (это точно/явно)

---

## 📚 Practice Library

В `practice_registry.py` хранятся структурированные практики:

```python
{
    "id": "grounding_5senses_v1",
    "version": "v1",
    "category": "grounding",
    "name_ru": "Заземление 5-4-3-2-1",
    "name_en": "5-4-3-2-1 Grounding",
    "duration_min": 3,
    "evidence_level": "strong",
    "severity_min": "low",
    "severity_max": "high",
    "contraindications": [],
    "adverse_risk": "low",
    "steps_ru": [...],
    "steps_en": [...],
}
```

**Никакой GPT не генерирует практики.** Все шаги предопределены и версионированы.

---

## 🌍 Multilingual Support

Полная поддержка RU + EN:
- Risk patterns для обоих языков
- Prompts для обоих языков
- Practice steps для обоих языков
- Auto-detection языка пользователя

---

## 📝 Logging & Analytics

Все события логируются:
- `moderation_logs` — риск-события
- `intervention_results` — сессии (то что рекомендовалось, результат)
- `adverse_events` — когда something went wrong
- `router_decision_logs` — snapshot состояния в момент выбора сценария
- `response_quality` — user feedback (👍/👎)

---

## 🔐 Deployment

### Production Checklist
- [ ] Use Python 3.12 or 3.13
- [ ] Create `.env` с правильными токенами
- [ ] Используйте SMTP для email alerts
- [ ] Настройте webhook для Slack/Discord alerts
- [ ] Включите A/B тестирование (AB_VARIANTS)
- [ ] Регулярно проверяйте adverse_events
- [ ] Мониторьте research metrics в dashboard

### Rate Limits
- OpenAI: 3 requests/min для gpt-4o-mini (настроиться под вашу подписку)
- Telegram: обработка 1 сообщение = ~1-2 сек
- Dashboard: работает в отдельном потоке (не блокирует бота)

---

## 🐛 Troubleshooting

### "Бот не отвечает"
1. Проверьте BOT_TOKEN в `.env`
2. Убедитесь, что бот добавлен в Telegram
3. Посмотрите логи в консоли

### "Ошибка при установке пакетов"
Используйте Python 3.12 вместо 3.14. См. `INSTALL_WINDOWS.md`.

### "Dashboard не запустился"
```
http://localhost:8080
Пароль из ADMIN_PASSWORD в .env
```

### "OpenAI API error"
Проверьте:
- OPENAI_API_KEY правильный
- Баланс в аккаунте OpenAI
- Квота на API requests

---

## 📞 Support & Contributing

Для вопросов и предложений:
1. Проверьте логи в консоли
2. Прочитайте комментарии в коде
3. Обратитесь к документации в каждом модуле

---

**Made with care for emotional safety.** 💙

*Last updated: May 2026*
