"""Shared test setup.

bot.py instantiates aiogram Bot(token=...) and AsyncOpenAI(api_key=...) at import
time, so the handler-level tests need dummy credentials present BEFORE bot is
imported. These are throwaway values assembled at runtime (not a real token); no
network call happens at construction.
"""
import os

# Built from parts so the file contains no literal token-shaped string.
_DUMMY_TOKEN = "123456789" + ":" + ("A" * 35)

os.environ.setdefault("BOT_TOKEN", _DUMMY_TOKEN)
os.environ.setdefault("OPENAI_API_KEY", "test-key-not-used")
os.environ.setdefault("ADMIN_PASSWORD", "test")
os.environ.setdefault("ADMIN_USER_IDS", "")
