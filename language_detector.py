"""X20 Language Detector — встроенное определение (без зависимостей)"""
DEFAULT = "ru"
def detect_language(text: str) -> str:
    if not text or len(text.strip()) < 4: return DEFAULT
    cyrillic = sum(1 for c in text if "\u0400" <= c <= "\u04FF")
    latin = sum(1 for c in text if c.isalpha() and c.isascii())
    if cyrillic > latin: return "ru"
    if latin > cyrillic and latin > 3: return "en"
    return DEFAULT


def normalize_telegram_language_code(language_code: str | None) -> str:
    """Map a Telegram BCP-47 `language_code` (e.g. "ru", "ru-RU", "en",
    "en-US", "nl-NL", "de-DE", None) to one of the bot's two SUPPORTED
    languages. Only the primary subtag is inspected ("ru-RU" -> "ru").

    Policy (explicit, not the text-detector's DEFAULT): "ru*" -> "ru";
    EVERYTHING ELSE -- missing/empty/malformed values, and any other language
    tag such as "nl-NL", "de-DE", "uk", "fr-FR" -- falls back to "en". A
    non-Russian, non-English Telegram user (e.g. Dutch, German) must not be
    silently defaulted into Russian onboarding; English is this bot's other
    actually-supported language, so it is the honest fallback for "not
    Russian". This only ever changes which onboarding/greeting text is shown,
    never any safety/access decision. Deliberately independent of this
    module's own DEFAULT ("ru", used by the free-text detect_language above,
    an unrelated per-message signal) -- changing DEFAULT would alter existing,
    unrelated behavior outside this function's scope."""
    if not language_code:
        return "en"
    primary = language_code.strip().lower().split("-")[0]
    return "ru" if primary == "ru" else "en"
