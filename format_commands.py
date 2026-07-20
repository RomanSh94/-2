"""X20 Format Commands — bounded, deterministic meta-command parser for
adaptive response delivery (Voice and Adaptive Response UX).

Pure substring/word-level pattern matching, no LLM call. Runs AFTER
crisis/dependency handling and BEFORE ordinary therapeutic routing (see
bot.pipeline) — a false match here can only ever affect HOW the eventual
answer is delivered, never whether risk/crisis/dependency handling ran,
since those all happen earlier and unconditionally.

A MIXED message ("Мне тревожно, и ответь голосом") still matches — this
module never decides the message is "only" a format request; that
judgment (is_pure_format_command) is separate and deliberately
conservative, so a genuine emotional disclosure riding along with a
format request always still reaches the ordinary pipeline.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class FormatCommand:
    kind: str   # voice_oneshot | voice_persistent | text_persistent |
                # concise_oneshot | concise_persistent | detailed_oneshot
    persistent: bool


_PERSISTENT_MARKERS = {
    "ru": ("всегда", "теперь отвечай", "по умолчанию", "с этого момента"),
    "en": ("always reply", "always respond", "from now on", "by default"),
}

# Unambiguous voice-action phrases -- explicitly about how the BOT should
# respond, safe to match anywhere in a longer, mixed message.
_VOICE_PATTERNS = {
    "ru": (
        "ответь голосом", "отвечай голосом", "отвечай мне голосом",
        "скажи голосом", "озвучь ответ", "озвучь это",
        "озвучь последнее", "озвучь предыдущий ответ",
        "запиши голосовое", "можешь сказать это голосом",
        "прочитай мне это", "прочитай это",
    ),
    "en": (
        "reply with voice", "answer with voice", "say it out loud",
        "read the last reply", "voice the last answer",
        "read this to me", "read it to me",
    ),
}

# Ambiguous out of context -- "много текста"/"лень читать" could just as
# easily describe unrelated content (a book, someone else's messages) as a
# request about the BOT's own reply. These only count as a format command
# when they make up (almost) the WHOLE message -- never as a substring
# buried inside an unrelated sentence (see _is_standalone).
_AMBIGUOUS_VOICE_PATTERNS = {
    "ru": ("мне лень читать", "лень читать", "много текста", "надоело читать"),
    "en": ("too much text", "too much to read", "don't feel like reading"),
}

_TEXT_PATTERNS = {
    "ru": ("не присылай голосовые", "отвечай текстом", "пиши текстом",
           "без голосовых"),
    "en": ("no voice messages", "reply with text", "just text",
           "no voice replies"),
}

_CONCISE_PATTERNS = {
    # "слишком много текста" deliberately excluded here -- it overlaps
    # "много текста" in _AMBIGUOUS_VOICE_PATTERNS, which already covers this
    # concept under the standalone-only rule (see the book-comment false
    # positive test). Keeping it here too would let it bypass that guard.
    "ru": ("короче", "можно кратко",
           "без длинного объяснения", "сократи ответ", "покороче"),
    "en": ("keep it short", "make it brief", "too long", "shorter please",
           "keep it brief"),
}

_DETAILED_PATTERNS = {
    "ru": ("можно подробнее", "объясни подробнее", "раскрой мысль",
           "расскажи подробнее"),
    "en": ("more detail", "explain in more detail", "elaborate", "go deeper"),
}

# Connective/filler words that don't count as "substantial remaining
# content" when deciding whether a message is a PURE format command.
_CONNECTIVES = {
    "ru": ("и", "но", "а", "также", "просто"),
    "en": ("and", "but", "also", "just"),
}

_WORD_RE = re.compile(r"[\w']+", re.UNICODE)


def _has_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(p in text for p in phrases)


def _first_match(text: str, phrases: tuple[str, ...]) -> Optional[str]:
    for p in phrases:
        if p in text:
            return p
    return None


def _words(s: str) -> set:
    return set(_WORD_RE.findall(s.lower()))


def _leftover_word_count(text: str, matched_phrase: str, lang: str) -> int:
    """How many words remain in `text` once the matched command phrase, any
    persistent marker, and bare connectives are accounted for. Zero means
    "nothing else here" -- the message is (functionally) just the command."""
    lang = lang if lang in _PERSISTENT_MARKERS else "ru"
    persistent_marker = _first_match(text, _PERSISTENT_MARKERS[lang]) or ""
    consumed = _words(matched_phrase) | _words(persistent_marker) | set(_CONNECTIVES[lang])
    all_words = _words(text)
    return len(all_words - consumed)


def _is_standalone(text: str, matched_phrase: str, lang: str) -> bool:
    return _leftover_word_count(text, matched_phrase, lang) == 0


def parse_format_command(user_text: str, lang: str = "ru") -> Optional[FormatCommand]:
    """None if user_text contains no recognizable format/meta command."""
    text = (user_text or "").strip().lower()
    if not text:
        return None
    lang = lang if lang in _VOICE_PATTERNS else "ru"
    persistent = _has_any(text, _PERSISTENT_MARKERS[lang])

    # Order matters: TEXT preference phrasing is checked before VOICE so
    # "не присылай голосовые" is never mistaken for a voice request.
    if _has_any(text, _TEXT_PATTERNS[lang]):
        return FormatCommand(kind="text_persistent", persistent=True)
    if _has_any(text, _VOICE_PATTERNS[lang]):
        return FormatCommand(kind="voice_persistent" if persistent else "voice_oneshot",
                              persistent=persistent)
    ambiguous = _first_match(text, _AMBIGUOUS_VOICE_PATTERNS[lang])
    if ambiguous and _is_standalone(text, ambiguous, lang):
        return FormatCommand(kind="voice_persistent" if persistent else "voice_oneshot",
                              persistent=persistent)
    if _has_any(text, _CONCISE_PATTERNS[lang]):
        return FormatCommand(kind="concise_persistent" if persistent else "concise_oneshot",
                              persistent=persistent)
    if _has_any(text, _DETAILED_PATTERNS[lang]):
        return FormatCommand(kind="detailed_oneshot", persistent=False)
    return None


def is_pure_format_command(user_text: str, lang: str = "ru") -> bool:
    """True only when the message is (functionally) just the format command
    — no separate substantive clause riding along with it. Used to decide
    whether to skip ordinary therapeutic routing entirely. Deliberately
    conservative: any leftover word beyond the matched phrase, its
    persistent marker, and bare connectives keeps this False, so a real
    disclosure alongside a format request always still reaches the
    pipeline (e.g. "Мне одиноко, но покороче")."""
    cmd = parse_format_command(user_text, lang)
    if cmd is None:
        return False
    text = (user_text or "").strip().lower()
    lang = lang if lang in _VOICE_PATTERNS else "ru"

    matched = (_first_match(text, _TEXT_PATTERNS[lang])
               or _first_match(text, _VOICE_PATTERNS[lang])
               or _first_match(text, _AMBIGUOUS_VOICE_PATTERNS[lang])
               or _first_match(text, _CONCISE_PATTERNS[lang])
               or _first_match(text, _DETAILED_PATTERNS[lang])
               or "")
    return _leftover_word_count(text, matched, lang) == 0
