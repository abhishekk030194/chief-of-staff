# Chief of Staff — AI Family Assistant

> An AI-powered personal assistant built for couples. Manages tasks, shopping, calendar events, and more — all through a shared Telegram chat.

Built in one evening using Claude AI, Python, Telegram Bot API, Google Calendar, and OpenAI Whisper for voice.

---

## Why I built this

Managing a household together is chaotic. Tasks fall through the cracks, shopping lists live in different places, and calendar reminders are missed. I wanted a single shared assistant that both my wife and I could talk to naturally — in text or voice — and have it actually take action.

---

## Features

| Feature | Description |
|---------|-------------|
| 📋 Task Management | Add, view, and complete shared tasks. Tracks who added what. |
| 🛒 Shopping List | Add items naturally ("add milk and eggs"), tick off at the store |
| 📅 Google Calendar | Creates real calendar events from natural language with reminders |
| 🎙️ Voice Messages | Send a voice note — it transcribes and responds intelligently |
| 👥 Group Chat | Works in a shared Telegram group for both partners |
| 🧠 Context Memory | Remembers recent conversation for natural follow-ups |

---

## How it works

```
You (voice/text) → Telegram → Bot → Claude AI → Action
                                          ↓
                              Google Calendar / Task List / Shopping List
```

1. You send a message (text or voice) in Telegram
2. Voice messages are transcribed using OpenAI Whisper
3. The message is sent to Claude with full context (tasks, shopping, calendar)
4. Claude understands intent and responds naturally
5. If a calendar event is detected, it's created automatically on Google Calendar
6. If shopping items are detected, they're added to the shared shopping list

---

## Tech Stack

- **Language:** Python 3
- **AI Brain:** Claude Sonnet (Anthropic API)
- **Interface:** Telegram Bot API (`python-telegram-bot`)
- **Voice:** OpenAI Whisper (local, runs on device)
- **Calendar:** Google Calendar API
- **Audio Processing:** ffmpeg

---

## Setup Guide

### 1. Prerequisites

- Python 3.10+
- [Telegram account](https://telegram.org)
- [Anthropic API key](https://console.anthropic.com)
- [Google Cloud project](https://console.cloud.google.com) with Calendar API enabled
- ffmpeg installed (`brew install ffmpeg` on Mac)

### 2. Clone the repo

```bash
git clone https://github.com/abhishekk030194/chief-of-staff.git
cd chief-of-staff
```

### 3. Install dependencies

```bash
pip3 install python-telegram-bot anthropic openai-whisper \
             google-api-python-client google-auth-httplib2 google-auth-oauthlib
brew install ffmpeg
```

### 4. Create your Telegram bot

1. Open Telegram → search for `@BotFather`
2. Send `/newbot` and follow the steps
3. Copy the token you receive

### 5. Set up Google Calendar API

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a new project
3. Enable the **Google Calendar API**
4. Create **OAuth 2.0 credentials** (Desktop app type)
5. Download the JSON file and save it as `credentials.json` in the project folder

### 6. Configure environment

Create a `.env` file (or set environment variables):

```bash
TELEGRAM_TOKEN=your_telegram_bot_token
ANTHROPIC_API_KEY=your_anthropic_api_key
```

### 7. Authorise Google Calendar (one-time)

```bash
ANTHROPIC_API_KEY=... python3 -c "from bot import get_calendar_service; get_calendar_service()"
```

A browser window will open — sign in and allow access.

### 8. Run the bot

```bash
TELEGRAM_TOKEN=your_token ANTHROPIC_API_KEY=your_key python3 bot.py
```

---

## Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Introduction and help |
| `/tasks` | View full task list |
| `/add <task>` | Add a task manually |
| `/done <number>` | Mark a task as completed |
| `/clear` | Remove all completed tasks |
| `/shopping` | View shopping list |
| `/bought <number>` | Mark a shopping item as bought |
| `/clearshop` | Remove all bought items |
| `/calendar` | View upcoming calendar events |

---

## Natural Language Examples

You don't need to use commands — just talk naturally:

> *"Remind me to call the dentist tomorrow at 10am"*
→ Creates a Google Calendar event with a reminder

> *"We need to buy milk, eggs and bread"*
→ Adds all three items to the shopping list

> *"What do we have to do this week?"*
→ Summarises pending tasks and upcoming events

> *"Add pay electricity bill to our tasks"*
→ Adds it to the shared task list

---

## Setting up the family group

1. Create a Telegram group with you, your partner, and the bot
2. Send `/start` in the group
3. Both of you can now chat with the bot together

The bot is smart about group chats — it only responds when addressed or when it detects relevant keywords (remind, shopping, calendar, schedule, etc.), so it won't interrupt every message between you.

---

## Cost

| Service | Cost |
|---------|------|
| Telegram Bot API | Free |
| Anthropic (Claude) | ~$2–5/month for typical family use |
| Google Calendar API | Free |
| Whisper (voice) | Free (runs locally) |
| Hosting (your Mac) | Free (runs locally) |

For 24/7 uptime without keeping your Mac on, deploy to [Railway](https://railway.app) (~$5/month).

---

## Roadmap

- [ ] Gmail integration (summarise emails, draft replies)
- [ ] Weekly digest every Monday morning
- [ ] Finance & budget tracking
- [ ] Deploy to Railway for 24/7 uptime
- [ ] Wife's Google Calendar sync

---

## Built with

- [Anthropic Claude](https://anthropic.com) — AI reasoning
- [python-telegram-bot](https://python-telegram-bot.org) — Telegram integration
- [OpenAI Whisper](https://github.com/openai/whisper) — Voice transcription
- [Google Calendar API](https://developers.google.com/calendar) — Calendar management
- [Claude Code](https://claude.ai/code) — Used to build the entire project

---

*Built by Abhishek · April 2026*
