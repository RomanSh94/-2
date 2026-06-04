"""X20 Language Detector — встроенное определение (без зависимостей)"""
DEFAULT = "ru"
def detect_language(text: str) -> str:
    if not text or len(text.strip()) < 4: return DEFAULT
    cyrillic = sum(1 for c in text if "\u0400" <= c <= "\u04FF")
    latin = sum(1 for c in text if c.isalpha() and c.isascii())
    if cyrillic > latin: return "ru"
    if latin > cyrillic and latin > 3: return "en"
    return DEFAULT
