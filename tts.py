"""X20 TTS — bounded speech synthesis adapter (Voice and Adaptive Response UX).

The single point where already Safety-Validator-approved text becomes audio.
Reuses the same AsyncOpenAI client convention as voice.py / bot.py — no
separate API client is created here.
"""
from __future__ import annotations

import asyncio
import os
import tempfile

from openai import AsyncOpenAI

import config

# Rough, conservative bytes/second ceiling for compressed voice audio
# (opus voice is typically far below this) — used only as a practical,
# dependency-free sanity check since no audio-duration decoder is a
# dependency of this repo. An oversized file fails closed (raises), it is
# never truncated (a truncated compressed-audio file can be corrupt/garbled).
_BYTES_PER_SECOND_CEILING = 4000


class TTSError(Exception):
    """Any TTS failure — timeout, provider error, oversized input/output, or
    a write failure. Callers must catch this and fall back to text; it must
    never propagate to the user or crash the bot."""


async def synthesize_speech(client: AsyncOpenAI, text: str, language: str = "ru") -> str:
    """Returns a path to a TEMPORARY audio file the caller owns and MUST
    delete (see bot.py's `finally: os.remove(...)`). Raises TTSError on any
    failure — never returns a partial or corrupt file path."""
    text = (text or "").strip()
    if not text:
        raise TTSError("empty text")
    if len(text) > config.TTS_MAX_INPUT_CHARS:
        text = text[: config.TTS_MAX_INPUT_CHARS].rstrip()
    voice = config.TTS_VOICE_EN if language == "en" else config.TTS_VOICE_RU

    try:
        response = await asyncio.wait_for(
            client.audio.speech.create(
                model=config.TTS_MODEL,
                voice=voice,
                input=text,
                response_format=config.TTS_RESPONSE_FORMAT,
            ),
            timeout=config.TTS_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as e:
        raise TTSError("timeout") from e
    except Exception as e:
        raise TTSError(f"provider error: {type(e).__name__}") from e

    fd, path = tempfile.mkstemp(suffix=f".{config.TTS_RESPONSE_FORMAT}")
    os.close(fd)
    try:
        if hasattr(response, "write_to_file"):
            response.write_to_file(path)
        else:
            with open(path, "wb") as f:
                f.write(response.content)
        max_bytes = config.TTS_MAX_AUDIO_SECONDS * _BYTES_PER_SECOND_CEILING
        if os.path.getsize(path) > max_bytes:
            raise TTSError("output exceeds configured maximum size/duration")
    except TTSError:
        _silent_remove(path)
        raise
    except Exception as e:
        _silent_remove(path)
        raise TTSError(f"write failed: {type(e).__name__}") from e
    return path


def _silent_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
