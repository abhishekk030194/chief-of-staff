"""
Shared test configuration and fixtures.

bot.py imports several heavy external packages (Telegram, Anthropic, Whisper,
Notion, Google APIs) at module level. We mock these before importing bot so
tests run without any real credentials or network access.
"""
import sys
from unittest.mock import MagicMock

# Stub every external dependency that bot.py imports at the top level.
_EXTERNAL_MODULES = [
    "feedparser",
    "telegram",
    "telegram.ext",
    "anthropic",
    "whisper",
    "notion_client",
    "google.oauth2.credentials",
    "google_auth_oauthlib.flow",
    "google.auth.transport.requests",
    "googleapiclient.discovery",
    "yfinance",
]
for _mod in _EXTERNAL_MODULES:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

import pytest  # noqa: E402 — must come after stubs
