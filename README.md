# Chief of Staff — AI Family Assistant (Mira)

> An AI-powered personal assistant built for couples. Manages tasks, shopping, calendar events, ideas, and more — all through a shared Telegram chat.

Built using Claude AI, Python, Telegram Bot API, Google Calendar, Notion, and faster-whisper for voice. Deployed 24/7 on Railway cloud.

---

## Why I built this

Managing a household together is chaotic. Tasks fall through the cracks, shopping lists live in different places, and calendar reminders are missed. I wanted a single shared assistant that both my wife and I could talk to naturally — in text or voice — and have it actually take action.

---

## Features

| Feature | Description |
|---------|-------------|
| 📋 Task Management | Add, view, and complete shared tasks. Tracks who added what. |
| 🛒 Shopping List | Add items naturally ("add milk and eggs"), tick off at the store |
| 💡 Ideas Capture | Save blog ideas, AI product ideas, and more with auto-tagging |
| 📅 Google Calendar | Creates real calendar events from natural language with reminders |
| 🎙️ Voice Messages | Send a voice note — it transcribes and responds intelligently |
| 🖼️ Image Analysis | Send any photo — receipts, lists, documents, bills — Claude reads and acts on them |
| 👥 Group Chat | Works in a shared Telegram group for both partners |
| 🧠 Long-term Memory | Remembers facts about the family across all conversations (Notion-backed) |
| 🗃️ Notion Sync | All tasks, shopping, ideas, memory, cost and eval reports auto-sync to Notion |
| 📰 Morning Digest | Daily 7 AM digest with tasks, shopping, ideas, and live market data |
| 📈 Live Market Indices | Real-time prices and % change for major global and Indian indices |
| 🔍 File Search | Search and retrieve any file from your MacBook via chat |
| 🔐 Security | Prompt injection firewall, secret scanner, Telegram alerts, Notion security log |
| 📡 Observability | `/status`, `/eval`, `/costreport` — live health checks and daily reporting |
| 🔔 Cross-User Notifications | When one person gives Mira an action item for the other, they get an instant Telegram message |
| 🟢 Status Notifications | Bot sends Telegram alerts when it goes online, offline, or wakes from sleep |
| ☁️ 24/7 Cloud Hosting | Deployed on Railway — runs always, independent of your Mac |
| ⚙️ Auto-Recovery | Retry logic, graceful shutdown with Notion sync |
| ⌨️ Command Autocomplete | Type `/` to see all commands with descriptions instantly |

---

## How it works

```
You (voice/text) → Telegram → Bot → Claude AI → Action
                                          ↓
                    Google Calendar / Notion / Task List / Shopping / Ideas
```

1. You send a message (text or voice) in Telegram
2. Voice messages are transcribed using faster-whisper
3. The message is sent to Claude with full context (tasks, shopping, calendar, ideas)
4. Claude understands intent and responds naturally
5. Actions are taken automatically — calendar events, task additions, Notion sync, etc.

---

## Tech Stack

- **Language:** Python 3
- **AI Brain:** Claude Sonnet 4.6 (Anthropic API)
- **Interface:** Telegram Bot API (`python-telegram-bot`)
- **Voice:** faster-whisper (runs in cloud container)
- **Calendar:** Google Calendar API
- **Database:** Notion API (tasks, shopping, ideas)
- **Market Data:** yfinance (real-time stock/commodity prices)
- **Scheduling:** APScheduler (daily digest at 7 AM IST)
- **Audio Processing:** ffmpeg
- **Hosting:** Railway (24/7 cloud deployment, ~$0–1/month)

---

## Setup Guide

### 1. Prerequisites

- Python 3.10+
- [Telegram account](https://telegram.org)
- [Anthropic API key](https://console.anthropic.com)
- [Google Cloud project](https://console.cloud.google.com) with Calendar API enabled
- [Notion integration token](https://www.notion.so/my-integrations)
- ffmpeg installed (`brew install ffmpeg` on Mac)

### 2. Clone the repo

```bash
git clone https://github.com/abhishekk030194/chief-of-staff.git
cd chief-of-staff
```

### 3. Install dependencies

```bash
pip3 install python-telegram-bot anthropic faster-whisper \
             google-api-python-client google-auth-httplib2 google-auth-oauthlib \
             notion-client feedparser apscheduler pytz yfinance
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

### 6. Set up Notion

1. Go to [Notion Integrations](https://www.notion.so/my-integrations) and create a new integration
2. Create three databases in Notion: **Tasks**, **Shopping**, **Ideas**
3. Share each database with your integration
4. Copy the database IDs from each database URL

### 7. Configure environment

Create a `.env` file:

```bash
TELEGRAM_TOKEN=your_telegram_bot_token
ANTHROPIC_API_KEY=your_anthropic_api_key
NOTION_TOKEN=your_notion_integration_token
NOTION_TASKS_DB=your_tasks_database_id
NOTION_SHOPPING_DB=your_shopping_database_id
NOTION_IDEAS_DB=your_ideas_database_id
ALLOWED_USERS=your_telegram_user_id,partner_telegram_user_id
```

> To find your Telegram user ID, message the bot and use `/myid`

### 8. Authorise Google Calendar (one-time)

```bash
python3 -c "from bot import get_calendar_service; get_calendar_service()"
```

A browser window will open — sign in and allow access.

### 9. Run the bot

```bash
TELEGRAM_TOKEN=your_token ANTHROPIC_API_KEY=your_key \
NOTION_TOKEN=your_token NOTION_TASKS_DB=... \
NOTION_SHOPPING_DB=... NOTION_IDEAS_DB=... \
python3 bot.py
```

### 10. Deploy to Railway (24/7 cloud)

```bash
npm install -g @railway/cli
railway login
railway init
railway up
```

Set all env vars in Railway's dashboard. The included `Dockerfile` and `Procfile` handle everything.

---

## Bot Commands

Type `/` in Telegram to see all commands with auto-suggestions.

| Command | Description |
|---------|-------------|
| `/memories` | Show everything Mira remembers about the family |
| `/remember <fact>` | Save a fact manually — e.g. `/remember Alekya is allergic to shellfish` |
| `/forget <number>` | Remove a memory by number |
| `/digest` | Morning digest: tasks, shopping, ideas & live market data |
| `/tasks` | View all pending tasks |
| `/add <task>` | Add a task manually |
| `/done <number>` | Mark a task as completed |
| `/clear` | Remove all completed tasks |
| `/shopping` | View shopping list |
| `/bought <number>` | Mark a shopping item as bought |
| `/clearshop` | Remove all bought items |
| `/calendar` | View upcoming calendar events |
| `/find <keywords>` | Search for a file on your MacBook |
| `/costreport` | Today's Claude API cost, token usage, and monthly projection |
| `/eval` | Today's activity report and health check |
| `/status` | Live status: uptime, integrations, today's activity |
| `/notion` | Force sync tasks/shopping/ideas to Notion |
| `/myid` | Show your Telegram user ID |

---

## Natural Language Examples

You don't need to use commands — just talk naturally:

> *"Remind me to call the dentist tomorrow at 10am"*
→ Creates a Google Calendar event with a reminder

> *"We need to buy milk, eggs and bread"*
→ Adds all three items to the shopping list

> *"What do we have to do this week?"*
→ Summarises pending tasks and upcoming events

> *"I have an idea for a blog post about AI productivity"*
→ Saves it to the Ideas database in Notion

> *"Mark all pending tasks as done"*
→ Marks everything completed

---

## Morning Digest

Every day at **7:00 AM IST**, Mira sends a digest covering:

- ✅ Pending tasks
- 🛒 Shopping list
- 💡 Recent ideas
- 📈 Live market indices with % change:
  - S&P 500, Dow Jones, NASDAQ
  - Nifty 50, Sensex
  - Gold, Silver

---

## Setting up the family group

1. Create a Telegram group with you, your partner, and the bot
2. Send `/start` in the group
3. Both of you can now chat with Mira together

---

## Cost

| Service | Cost |
|---------|------|
| Telegram Bot API | Free |
| Anthropic (Claude) | ~$2–5/month for typical family use |
| Google Calendar API | Free |
| faster-whisper (voice) | Free (runs in container) |
| Notion API | Free |
| yfinance (market data) | Free |
| Railway (24/7 hosting) | Free (within $5/month credit) |

---

## Roadmap

- [x] Shared task management with Notion sync
- [x] Google Calendar integration with iPhone reminders
- [x] Voice message support via faster-whisper
- [x] Live market indices in morning digest (yfinance)
- [x] MacBook file search from Telegram
- [x] Command autocomplete in Telegram
- [x] Security: prompt injection firewall, secret scanner, Telegram alerts, Notion security log
- [x] Observability: /status, /eval, /costreport with Notion sync
- [x] Auto-recovery: retry logic, graceful shutdown
- [x] Long-term memory system (Notion-backed, auto-extraction + manual /remember)
- [x] Image & photo analysis (Claude vision — receipts, documents, lists)
- [x] Status notifications: 🟢 online, 🔴 shutting down, 😴 woke from sleep
- [x] Deploy to Railway for 24/7 cloud hosting
- [x] Wife's access (Alekya added to ALLOWED_USERS)
- [x] Cross-user notifications — instant Telegram ping when one person assigns an action to the other
- [ ] Gmail integration (summarise emails, draft replies)
- [ ] Weekly summary every Monday morning
- [ ] Finance & budget tracking
- [ ] Wife's Google Calendar sync

---

## Built with

- [Anthropic Claude](https://anthropic.com) — AI reasoning
- [python-telegram-bot](https://python-telegram-bot.org) — Telegram integration
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — Voice transcription
- [Google Calendar API](https://developers.google.com/calendar) — Calendar management
- [Notion API](https://developers.notion.com) — Database and knowledge management
- [yfinance](https://github.com/ranaroussi/yfinance) — Real-time market data
- [Railway](https://railway.app) — Cloud hosting
- [Claude Code](https://claude.ai/code) — Used to build the entire project

---

*Built by Abhishek · April 2026*
