# Chief of Staff — AI Family Assistant (Mira)

> An AI-powered personal assistant built for couples. Manages tasks, shopping, calendar events, ideas, and more — all through a shared Telegram chat.

Built with Claude AI, Python, Telegram Bot API, Google Calendar, Notion, and faster-whisper. Deployed 24/7 on Railway cloud.

---

## What it does

Just talk to it naturally — text or voice:

> *"Remind me to call the dentist tomorrow at 10am"*
→ Creates a Google Calendar event with a reminder on your iPhone

> *"We need to buy milk, eggs and bread"*
→ Adds all three to the shared shopping list, syncs to Notion

> *"What's on our calendar this weekend?"*
→ Queries Google Calendar for the date range and lists events

> *"Remind Abhishek to pick up chocolates on his way home"*
→ Adds to shopping list AND sends Abhishek an instant Telegram DM

> *"I have an idea for a blog post about AI productivity"*
→ Saves it to the Ideas database in Notion, tagged automatically

> *"Mark all pending tasks as done"*
→ Marks everything completed, syncs to Notion

---

## Features

| Feature | Description |
|---------|-------------|
| 📋 Task Management | Add, view, and complete shared tasks. Tracks who added what. |
| 🛒 Shopping List | Add items naturally ("add milk and eggs"), tick off at the store |
| 💡 Ideas Capture | Save blog ideas, AI product ideas, and more with auto-tagging |
| 📅 Google Calendar | Create events from natural language; search by date range |
| 🔔 Cross-User Notifications | When one person assigns an action to the other, they get an instant DM |
| 🎙️ Voice Messages | Send a voice note — it transcribes and responds intelligently |
| 🖼️ Image Analysis | Send any photo — receipts, lists, documents, bills — Claude reads and acts |
| 👥 Group Chat | Works in a shared Telegram group for both partners |
| 🧠 Long-term Memory | Remembers facts about the family across all conversations (Notion-backed) |
| 🗃️ Notion Sync | All tasks, shopping, ideas, memory, cost and eval reports auto-sync |
| 📰 Morning Digest | Daily 7 AM digest with today's calendar, tasks, shopping, ideas, yesterday's cost/eval, and markets |
| 📈 Live Market Indices | Real-time prices and % change for major global and Indian indices |
| 🔍 File Search | Search and retrieve any file from your MacBook via chat |
| 🔐 Security | Prompt injection firewall, secret scanner, Telegram alerts, Notion security log |
| 📡 Observability | `/status`, `/eval`, `/costreport` — live health checks and daily reporting |
| 🟢 Status Notifications | Bot sends Telegram alerts when it goes online or offline |
| ☁️ 24/7 Cloud Hosting | Deployed on Railway — runs always, independent of your Mac |
| ⚙️ Auto-Recovery | Retry logic, graceful shutdown with Notion sync |
| ⌨️ Command Autocomplete | Type `/` to see all commands with descriptions instantly |

---

## Why I built this

Managing a household together is chaotic. Tasks fall through the cracks, shopping lists live in different places, and calendar reminders are missed. I wanted a single shared assistant that both my wife and I could talk to naturally — in text or voice — and have it actually take action.

---

## How it works

```
You (voice/text/photo) → Telegram → Bot → Claude AI → Action
                                               ↓
                         Google Calendar / Notion / Tasks / Shopping / Ideas
                                               ↓
                              Instant DM to the other partner if needed
```

1. You send a message (text, voice, or photo) in Telegram
2. Voice messages are transcribed using faster-whisper
3. The message is sent to Claude with full context (tasks, shopping, next 10 calendar events, ideas)
4. Claude understands intent and responds naturally
5. Actions are taken automatically — calendar events, task additions, Notion sync, cross-user notifications, etc.

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

## Known Limitations

- **Voice is English-only** — faster-whisper base model works best with clear English
- **File search only works locally** — the `/find` command won't work on Railway, only when running on a Mac
- **Google Calendar OAuth needs a Mac** — one-time browser auth; after that it runs fine on Railway
- **No two-way calendar sync** — Mira creates events and reads them back, but can't edit or delete events created directly in Google Calendar

---

## Setup Guide

### Quick requirements

- Python 3.10+, ffmpeg (`brew install ffmpeg`)
- Telegram account + bot token (from [@BotFather](https://t.me/BotFather))
- [Anthropic API key](https://console.anthropic.com) (~$5 gets you months of family use)
- Google account with Calendar API enabled
- [Notion integration token](https://www.notion.so/my-integrations) + 3 databases (Tasks, Shopping, Ideas)
- [Railway account](https://railway.app) for cloud deployment (free $5/month credit)

### 1. Clone the repo

```bash
git clone https://github.com/abhishekk030194/chief-of-staff.git
cd chief-of-staff
```

### 2. Install dependencies

```bash
pip3 install python-telegram-bot anthropic faster-whisper \
             google-api-python-client google-auth-httplib2 google-auth-oauthlib \
             notion-client feedparser apscheduler pytz yfinance
brew install ffmpeg
```

### 3. Configure environment

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

### 4. Authorise Google Calendar (one-time, needs a browser)

```bash
python3 -c "from bot import get_calendar_service; get_calendar_service()"
```

### 5. Run locally

```bash
python3 bot.py
```

### 6. Deploy to Railway (24/7 cloud)

```bash
npm install -g @railway/cli
railway login
railway init
railway up
```

Set all env vars in Railway's dashboard. The included `Dockerfile` and `Procfile` handle everything.

---

## Bot Commands

| Command | Description |
|---------|-------------|
| `/memories` | Show everything Mira remembers about the family |
| `/remember <fact>` | Save a fact manually |
| `/forget <number>` | Remove a memory by number |
| `/digest` | Morning digest: calendar, tasks, shopping, ideas & market data |
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

## Morning Digest

Every day at **7:00 AM IST**, Mira sends a digest covering:

- 📅 Today's calendar events
- ✅ Pending tasks
- 🛒 Shopping list
- 💡 Recent ideas
- 📊 Yesterday's activity: messages, cost, actions taken, health status
- 📈 Live market indices with % change (S&P 500, Dow Jones, NASDAQ, Nifty 50, Sensex, Gold, Silver)

---

## Setting up the family group

1. Create a Telegram group with you, your partner, and the bot
2. Send `/start` in the group
3. Both partners should also DM the bot directly once (so it can send each of you private notifications)

---

## Cost

| Service | Cost |
|---------|------|
| Telegram Bot API | Free |
| Anthropic (Claude) | ~$3/month for typical family use |
| Google Calendar API | Free |
| faster-whisper (voice) | Free (runs in container) |
| Notion API | Free |
| yfinance (market data) | Free |
| Railway (24/7 hosting) | Free (within $5/month credit) |

---

## Roadmap

- [x] Shared task management with Notion sync
- [x] Google Calendar integration with iPhone reminders
- [x] Calendar date-range search ("what's on Friday?", "anything next week?")
- [x] Voice message support via faster-whisper
- [x] Live market indices in morning digest (yfinance)
- [x] MacBook file search from Telegram
- [x] Command autocomplete in Telegram
- [x] Security: prompt injection firewall, secret scanner, Telegram alerts, Notion security log
- [x] Observability: /status, /eval, /costreport with Notion sync
- [x] Auto-recovery: retry logic, graceful shutdown
- [x] Long-term memory system (Notion-backed, auto-extraction + manual /remember)
- [x] Image & photo analysis (Claude vision — receipts, documents, lists)
- [x] Status notifications: 🟢 online, 🔴 shutting down
- [x] Deploy to Railway for 24/7 cloud hosting
- [x] Cross-user notifications — instant Telegram ping when one partner assigns an action to the other
- [x] Today's calendar events in morning digest
- [ ] Gmail integration (summarise emails, draft replies)
- [ ] Weekly summary every Monday morning
- [ ] Finance & budget tracking
- [ ] Wife's Google Calendar sync (two-way)

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
