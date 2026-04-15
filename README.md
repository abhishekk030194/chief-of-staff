# Chief of Staff — AI Family Assistant

A personal AI agent that acts as a Chief of Staff for a couple. Built with Claude AI, Telegram, and Google Calendar.

## Features
- 📋 Shared task management
- 🛒 Shopping list (real-time sync between both phones)
- 📅 Google Calendar integration (creates events from natural language)
- 🎙️ Voice message support
- 👥 Group chat support

## Setup

1. Clone the repo
2. Install dependencies:
   ```
   pip3 install python-telegram-bot anthropic openai-whisper google-api-python-client google-auth-httplib2 google-auth-oauthlib
   brew install ffmpeg
   ```
3. Create a `.env` file with your keys (see `.env.example`)
4. Run: `TELEGRAM_TOKEN=... ANTHROPIC_API_KEY=... python3 bot.py`

## Commands
| Command | Description |
|---------|-------------|
| `/tasks` | View task list |
| `/add <task>` | Add a task |
| `/done <number>` | Mark task as done |
| `/clear` | Remove completed tasks |
| `/shopping` | View shopping list |
| `/bought <number>` | Mark item as bought |
| `/clearshop` | Remove bought items |
| `/calendar` | View upcoming events |
