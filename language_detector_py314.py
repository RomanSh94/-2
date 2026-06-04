"""
X20 Language Detector — встроенная версия без зависимостей

Для Python 3.14: langdetect может не устанавливаться.
Эта версия использует только встроенный Python.
Определяет RU vs EN по кириллице/латинице.
"""

DEFAULT = "ru"

def detect_language(text: str) -> str:
    """
    Определяет язык по количеству кириллицы/латиницы.
    Не требует внешних зависимостей.
    
    Returns: 'ru' или 'en'
    """
    if not text or len(text.strip()) < 4:
        return DEFAULT

    cyrillic = sum(1 for c in text if "\u0400" <= c <= "\u04FF")
    latin    = sum(1 for c in text if c.isalpha() and c.isascii())

    # Простое правило: где больше букв, тот язык
    if cyrillic > latin:
        return "ru"
    if latin > cyrillic and latin > 3:
        return "en"

    # Для смешанных текстов — отстаёт Russian по умолчанию
    return DEFAULT
