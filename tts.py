"""X20 TTS — bounded ElevenLabs speech-synthesis adapter.

Only already approved final response text may reach this module. The external
transport is independently default-off, never truncates text, and converts any
failure into ``TTSError`` so the caller can deliver the complete text instead.
"""
from __future__ import annotations

import os
import tempfile

import httpx

import config

_ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"

# Conservative compressed-audio ceiling used as a dependency-free corruption /
# runaway-response check. Audio is never truncated.
_BYTES_PER_SECOND_CEILING = 4000


class TTSError(Exception):
    """A synthesis failure that requires complete-text fallback."""


async def synthesize_speech(_client: object, text: str, language: str = "ru") -> str:
    """Return a temporary Ogg/Opus file path owned by the caller.

    ``_client`` is retained only for call-site compatibility with the existing
    delivery layer. ElevenLabs uses its own HTTPS interface. ``language`` is
    likewise retained for compatibility; the configured multilingual model
    handles Russian and English without a language parameter.
    """
    if not config.ELEVENLABS_TTS_ENABLED:
        raise TTSError("ElevenLabs TTS is disabled")
    if not config.ELEVENLABS_API_KEY:
        raise TTSError("ElevenLabs API key is not configured")

    approved_text = (text or "").strip()
    if not approved_text:
        raise TTSError("empty text")
    if len(approved_text) > config.TTS_MAX_INPUT_CHARS:
        raise TTSError("input exceeds configured maximum; use complete-text fallback")

    url = _ELEVENLABS_TTS_URL.format(voice_id=config.ELEVENLABS_VOICE_ID)
    try:
        async with httpx.AsyncClient(timeout=config.TTS_TIMEOUT_SECONDS) as transport:
            response = await transport.post(
                url,
                params={
                    "output_format": config.ELEVENLABS_OUTPUT_FORMAT,
                    # Zero Retention Mode is plan-dependent. Request it
                    # explicitly; unsupported accounts fail closed to text.
                    "enable_logging": "false",
                },
                headers={
                    "xi-api-key": config.ELEVENLABS_API_KEY,
                    "accept": "audio/ogg",
                    "content-type": "application/json",
                },
                json={
                    "text": approved_text,
                    "model_id": config.ELEVENLABS_MODEL_ID,
                },
            )
            response.raise_for_status()
            audio = response.content
    except httpx.TimeoutException as exc:
        raise TTSError("timeout") from exc
    except Exception as exc:
        raise TTSError(f"provider error: {type(exc).__name__}") from exc

    max_bytes = config.TTS_MAX_AUDIO_SECONDS * _BYTES_PER_SECOND_CEILING
    if not audio.startswith(b"OggS"):
        raise TTSError("provider returned invalid Ogg audio")
    if len(audio) > max_bytes:
        raise TTSError("output exceeds configured maximum size/duration")

    fd, path = tempfile.mkstemp(suffix=".ogg")
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(audio)
    except Exception as exc:
        _silent_remove(path)
        raise TTSError(f"write failed: {type(exc).__name__}") from exc
    return path


def _silent_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
