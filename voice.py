"""X20 Voice — Whisper transcription with multilingual support"""
import io
from aiogram import Bot
from aiogram.types import Voice
from openai import AsyncOpenAI

LANG_MAP = {"ru": "ru", "en": "en"}

async def transcribe_voice(voice: Voice, bot: Bot, client: AsyncOpenAI, lang: str = "ru") -> str:
    """Transcribe voice message using Whisper."""
    file_info = await bot.get_file(voice.file_id)
    buf = io.BytesIO()
    await bot.download_file(file_info.file_path, destination=buf)
    buf.seek(0)
    buf.name = "voice.ogg"
    transcript = await client.audio.transcriptions.create(
        model="whisper-1",
        file=buf,
        language=LANG_MAP.get(lang, "ru"),
        response_format="text",
    )
    return transcript.strip()
