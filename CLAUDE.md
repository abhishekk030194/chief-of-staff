# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Chief of Staff (Mira)** is a single-file Python Telegram bot serving as an AI-powered family assistant. It uses Claude Sonnet 4.6 as its reasoning engine to manage tasks, shopping lists, calendar events, and ideas for a household via natural language (text and voice).

## Running the Bot

```bash
# Install dependencies
pip3 install python-telegram-bot anthropic faster-whisper \
             google-api-python-client google-auth-httplib2 google-auth-oauthlib \
             notion-client feedparser apscheduler pytz yfinance
brew install ffmpeg  # required for voice message processing

# One-time Google Calendar OAuth (opens browser)
python3 -c "from bot import get_calendar_service; get_calendar_service()"

# Run
python3 bot.py
```

The bot is also deployed to **Railway** (24/7 cloud). The `Dockerfile` and `Procfile` handle the cloud build. Cloud-specific env vars bootstrap credential files on first boot via `_bootstrap_cloud_files()` at module load time.

All required environment variables (see `.env.example`):
- `TELEGRAM_TOKEN`, `ANTHROPIC_API_KEY`
- `NOTION_TOKEN`, `NOTION_TASKS_DB`, `NOTION_SHOPPING_DB`, `NOTION_IDEAS_DB`
- `ALLOWED_USERS` — comma-separated Telegram user IDs allowed to use the bot

There is no test suite, build step, linter, or CI pipeline.

## Architecture

The entire application lives in `bot.py` (~1900 lines). There is no module structure.

### Core Message Flow

```
Telegram message (text or voice)
  → block_unauthorised()         — allowlist check, logs security events to Notion
  → check_prompt_injection()     — 12 forbidden phrase patterns blocked at ingestion
  → build context (tasks + shopping + calendar + ideas) injected into SYSTEM_PROMPT
  → client.messages.create()     — Claude Sonnet 4.6, max_tokens=1024
  → scan_for_secrets()           — redacts key-looking strings from reply
  → regex parse XML-like tags    — structured action extraction
  → side effects: JSON writes, Notion API, Google Calendar API
  → reply_text() to Telegram
```

### Claude as Action Router

Claude's response may contain XML-like action blocks that the bot parses with `re.search()` and `json.loads()`. These are the available tags and what triggers them (defined in `SYSTEM_PROMPT`):

| Tag | Action |
|-----|--------|
| `<calendar>{...}</calendar>` | Creates Google Calendar event |
| `<shopping>[...]</shopping>` | Adds items to shopping list + Notion sync |
| `<filesearch>{"query": ...}</filesearch>` | Searches Mac filesystem, sends files via Telegram |
| `<taskdone>{"match": ...}</taskdone>` | Marks a specific task done by number or keyword |
| `<markalldone></markalldone>` | Marks all tasks done |
| `<shoppingdone>{"match": ...}</shoppingdone>` | Marks a shopping item as bought |
| `<markallbought></markallbought>` | Marks all shopping bought |
| `<ideadone>{"match": ...}</ideadone>` | Removes an idea |
| `<idea>{"title": ..., "type": ...}</idea>` | Saves idea to local JSON + Notion |
| `<notify_other>message</notify_other>` | Sends an instant Telegram message to the other user (e.g. Alekya → Abhishek) |

Action blocks are stripped from the reply before sending to the user.

### Dual Storage Pattern

All data lives in local JSON files (fast, in-process) with async Notion as backup:

| Local file | Contents |
|------------|----------|
| `tasks.json` | Task list |
| `shopping.json` | Shopping items |
| `ideas.json` | Captured ideas |
| `conversation.json` | Last 20 messages (conversation memory) |
| `daily_stats.json` | Token usage and activity metrics per day |
| `chat_ids.json` | Registered chat IDs for scheduled digest |

Notion databases are written to immediately on add/complete. The `/notion` command and graceful shutdown trigger a full deduplicated sync. Deduplication is done by lowercase name comparison.

Auto-created Notion databases (IDs persisted to `.txt` files after first creation): `cost_db_id.txt`, `eval_db_id.txt`, `security_db_id.txt`.

### Retry and Error Handling

`with_retry(fn, retries=3, delay=2)` wraps all Claude API and Notion calls with exponential backoff. Returns `None` on exhausted retries — callers must handle `None`.

### Scheduling

APScheduler runs two jobs:
- `send_daily_digest` — morning digest at 7:00 AM IST to all registered chat IDs
- `heartbeat_job` — fires every 60 seconds, writes a timestamp to `last_heartbeat.txt`; if the gap since last heartbeat exceeds 5 minutes it sends a "woke from sleep" notification (detects Mac sleep/wake on local runs)

`post_init` (ApplicationBuilder hook) sends a 🟢 online notification to all chat IDs on startup and seeds the heartbeat file. `post_shutdown` (hook) sends a 🔴 shutting down notification and triggers a Notion sync using `urllib` directly (the async HTTP client is already closed at that point).

### Voice Transcription

Voice messages are downloaded as `.ogg` files and transcribed via `faster_whisper.WhisperModel("base", device="cpu", compute_type="int8")`. The model is lazy-loaded on first voice message (`get_whisper_model()`). Transcription: `segments, _ = model.transcribe(path)` → join segment texts. Do not switch back to `openai-whisper` — it cannot be built reliably in Docker on Python 3.11-slim.

### Security Layers

1. **Allowlist**: `ALLOWED_USERS` env var; every handler calls `await block_unauthorised()` as its first line.
2. **Injection firewall**: `check_prompt_injection()` checks against `INJECTION_PATTERNS` list before sending to Claude.
3. **Secret scanner**: `scan_for_secrets()` runs regex over Claude's reply to redact credential-like strings.

Security events are logged to Notion and sent as Telegram alerts to `OWNER_ID` (the smallest user ID in `ALLOWED_USERS`).

### Group Chat Behavior

In groups/supergroups, the bot only responds when `@bot_name` is mentioned or the message matches one of the `trigger_words` list. The `@bot_name` mention is stripped before processing.

### Cost Tracking

Claude Sonnet 4.6 pricing constants at top of file (`COST_INPUT_PER_MTK`, `COST_OUTPUT_PER_MTK`). Token counts from every API response are accumulated in `daily_stats.json` via `increment_stat()`. Update these constants if the model or pricing changes.
