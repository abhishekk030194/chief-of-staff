import os
import json
import logging
import re
import tempfile
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import anthropic
import whisper
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

TASKS_FILE = "tasks.json"
SHOPPING_FILE = "shopping.json"
CONVERSATION_FILE = "conversation.json"
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"
SCOPES = ["https://www.googleapis.com/auth/calendar"]

# ── Google Calendar ──────────────────────────────────────────────────────────

def get_calendar_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return build("calendar", "v3", credentials=creds)

def create_calendar_event(title, start_dt, end_dt=None, description=""):
    service = get_calendar_service()
    if end_dt is None:
        end_dt = start_dt + timedelta(hours=1)
    event = {
        "summary": title,
        "description": description,
        "start": {"dateTime": start_dt.isoformat(), "timeZone": "Asia/Kolkata"},
        "end":   {"dateTime": end_dt.isoformat(),   "timeZone": "Asia/Kolkata"},
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "popup",  "minutes": 30},
                {"method": "email",  "minutes": 60},
            ],
        },
    }
    created = service.events().insert(calendarId="primary", body=event).execute()
    return created.get("htmlLink")

def get_upcoming_events(max_results=5):
    service = get_calendar_service()
    now = datetime.utcnow().isoformat() + "Z"
    result = service.events().list(
        calendarId="primary", timeMin=now,
        maxResults=max_results, singleEvents=True,
        orderBy="startTime"
    ).execute()
    return result.get("items", [])

# ── Persistence helpers ───────────────────────────────────────────────────────

def load_tasks():
    if os.path.exists(TASKS_FILE):
        with open(TASKS_FILE) as f:
            return json.load(f)
    return []

def save_tasks(tasks):
    with open(TASKS_FILE, "w") as f:
        json.dump(tasks, f, indent=2)

def load_shopping():
    if os.path.exists(SHOPPING_FILE):
        with open(SHOPPING_FILE) as f:
            return json.load(f)
    return []

def save_shopping(items):
    with open(SHOPPING_FILE, "w") as f:
        json.dump(items, f, indent=2)

def load_conversation():
    if os.path.exists(CONVERSATION_FILE):
        with open(CONVERSATION_FILE) as f:
            return json.load(f)
    return []

def save_conversation(messages):
    if len(messages) > 20:
        messages = messages[-20:]
    with open(CONVERSATION_FILE, "w") as f:
        json.dump(messages, f, indent=2)

# ── Prompts ───────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a Chief of Staff assistant for a couple — Abhishek and his wife.
You help them manage their lives together — tasks, reminders, calendar events, decisions, shopping, finances, and more.

You are warm, organised, proactive, and concise. You speak like a trusted personal assistant.

Current date and time: {datetime}

Current task list:
{tasks}

Shopping list:
{shopping}

Upcoming calendar events:
{events}

IMPORTANT — Calendar event detection:
If the user's message contains a date, time, or scheduling intent (e.g. "tomorrow", "on Friday", "at 3pm", "next week", "schedule", "book", "appointment", "remind me on"), respond with a JSON block at the END of your reply in this exact format:
<calendar>
{{
  "title": "Event title",
  "date": "YYYY-MM-DD",
  "time": "HH:MM",
  "description": "optional description"
}}
</calendar>

Only include the <calendar> block when you are confident there is a specific event to create. Do not include it for vague reminders without a date/time.

IMPORTANT — Shopping list detection:
If the user wants to add items to the shopping list (e.g. "add milk to shopping", "we need eggs and bread", "buy tomatoes"), respond with a JSON block at the END of your reply:
<shopping>
["item 1", "item 2"]
</shopping>

Only include the <shopping> block when the user clearly wants to add grocery/shopping items.

Group chat rules:
- Both Abhishek and his wife share the same task list and calendar
- Address people by their first name when relevant
- Keep responses short and use bullet points when listing things."""

# ── Command handlers ──────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Hi! I'm your Chief of Staff.\n\n"
        "I can help both of you with:\n"
        "• 📋 Tasks & to-dos\n"
        "• 📅 Google Calendar events & reminders\n"
        "• 🛒 Shopping lists\n"
        "• 🧠 Decisions & research\n\n"
        "Just chat naturally! Try:\n"
        "_'Remind me to call the dentist tomorrow at 10am'_\n"
        "_'We need to buy groceries and pay rent'_\n"
        "_'What's on our calendar this week?'_",
        parse_mode="Markdown"
    )

async def tasks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tasks = load_tasks()
    if not tasks:
        await update.message.reply_text("No tasks yet! Just tell me what needs to be done.")
        return
    pending = [t for t in tasks if not t.get("done")]
    done    = [t for t in tasks if t.get("done")]
    msg = "📋 *Task List:*\n\n"
    if pending:
        msg += "*Pending:*\n"
        for i, t in enumerate(pending, 1):
            by = f" _(by {t.get('added_by','?')})_" if t.get("added_by") else ""
            msg += f"{i}. ⬜ {t['text']}{by}\n"
    if done:
        msg += "\n*Done:*\n"
        for t in done:
            msg += f"• ✅ {t['text']}\n"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def calendar_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        events = get_upcoming_events(5)
        if not events:
            await update.message.reply_text("📅 No upcoming events on your calendar.")
            return
        msg = "📅 *Upcoming Events:*\n\n"
        for e in events:
            start = e["start"].get("dateTime", e["start"].get("date", ""))
            if "T" in start:
                dt = datetime.fromisoformat(start[:19])
                start_str = dt.strftime("%a %b %d at %I:%M %p")
            else:
                start_str = start
            msg += f"• *{e.get('summary','No title')}* — {start_str}\n"
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not fetch calendar: {str(e)}")

async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /done <task number>\nUse /tasks to see numbers.")
        return
    tasks = load_tasks()
    pending = [t for t in tasks if not t.get("done")]
    try:
        idx = int(args[0]) - 1
        task_text = pending[idx]["text"]
        for t in tasks:
            if t["text"] == task_text:
                t["done"] = True
        save_tasks(tasks)
        user_name = update.message.from_user.first_name
        await update.message.reply_text(f"✅ *{task_text}* marked done by {user_name}!", parse_mode="Markdown")
    except (IndexError, ValueError):
        await update.message.reply_text("Invalid number. Use /tasks to see the list.")

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tasks = load_tasks()
    removed = len([t for t in tasks if t.get("done")])
    tasks = [t for t in tasks if not t.get("done")]
    save_tasks(tasks)
    await update.message.reply_text(f"🗑️ Cleared {removed} completed task(s)!")

async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /add <task>\nExample: /add Call the dentist")
        return
    task_text = " ".join(context.args)
    user_name = update.message.from_user.first_name
    tasks = load_tasks()
    tasks.append({"text": task_text, "done": False, "added_by": user_name, "date": datetime.now().isoformat()})
    save_tasks(tasks)
    await update.message.reply_text(f"✅ Added: *{task_text}*", parse_mode="Markdown")

async def shopping_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = load_shopping()
    pending = [i for i in items if not i.get("bought")]
    bought  = [i for i in items if i.get("bought")]
    if not items:
        await update.message.reply_text("🛒 Shopping list is empty!\n\nSay _'add milk and eggs to the shopping list'_ to add items.", parse_mode="Markdown")
        return
    msg = "🛒 *Shopping List:*\n\n"
    if pending:
        msg += "*Still needed:*\n"
        for i, item in enumerate(pending, 1):
            by = f" _(by {item.get('added_by','?')})_" if item.get("added_by") else ""
            msg += f"{i}. ⬜ {item['name']}{by}\n"
    if bought:
        msg += "\n*Already in basket:*\n"
        for item in bought:
            msg += f"• ✅ {item['name']}\n"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def bought_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /bought <item number>\nUse /shopping to see numbers.")
        return
    items = load_shopping()
    pending = [i for i in items if not i.get("bought")]
    try:
        idx = int(args[0]) - 1
        item_name = pending[idx]["name"]
        for i in items:
            if i["name"] == item_name:
                i["bought"] = True
        save_shopping(items)
        user_name = update.message.from_user.first_name
        await update.message.reply_text(f"✅ *{item_name}* marked as bought by {user_name}!", parse_mode="Markdown")
    except (IndexError, ValueError):
        await update.message.reply_text("Invalid number. Use /shopping to see the list.")

async def clearshop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = load_shopping()
    removed = len([i for i in items if i.get("bought")])
    items = [i for i in items if not i.get("bought")]
    save_shopping(items)
    await update.message.reply_text(f"🗑️ Cleared {removed} bought item(s) from shopping list!")

# ── Voice message handler ─────────────────────────────────────────────────────

whisper_model = None

def get_whisper_model():
    global whisper_model
    if whisper_model is None:
        whisper_model = whisper.load_model("base")
    return whisper_model

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎙️ Got your voice message, transcribing...")
    try:
        voice = update.message.voice or update.message.audio
        file = await context.bot.get_file(voice.file_id)

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp_path = tmp.name
        await file.download_to_drive(tmp_path)

        model = get_whisper_model()
        result = model.transcribe(tmp_path)
        transcription = result["text"].strip()
        os.unlink(tmp_path)

        if not transcription:
            await update.message.reply_text("⚠️ Couldn't understand the audio. Please try again.")
            return

        await update.message.reply_text(f"📝 I heard: _{transcription}_", parse_mode="Markdown")

        # Process transcription through the main handler
        await process_text_message(update, context, transcription)

    except Exception as e:
        logging.error(f"Voice error: {e}")
        await update.message.reply_text("⚠️ Couldn't process the voice message. Please try again.")

# ── Main message handler ──────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_type = update.message.chat.type
    user_message = update.message.text
    bot_username = context.bot.username

    if chat_type in ["group", "supergroup"]:
        bot_mentioned = f"@{bot_username}" in user_message
        trigger_words = ["remind", "add task", "what do we", "can you", "please", "help",
                         "schedule", "need to", "have to", "don't forget", "todo", "to do",
                         "summarize", "summary", "shopping", "budget", "finance", "plan",
                         "what's on", "whats on", "calendar", "appointment", "book", "meeting",
                         "tomorrow", "tonight", "next week", "at ", "pm", "am"]
        triggered = any(w in user_message.lower() for w in trigger_words)
        if not bot_mentioned and not triggered:
            return
        user_message = user_message.replace(f"@{bot_username}", "").strip()

    await process_text_message(update, context, user_message)

async def process_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE, user_message: str):
    """Core logic: takes a text string and generates a response (used by both text and voice handlers)."""
    user_name = update.message.from_user.first_name

    # Build context for Claude
    tasks = load_tasks()
    task_text = "\n".join(
        [f"- {'[done]' if t.get('done') else '[pending]'} {t['text']} (by {t.get('added_by','?')})"
         for t in tasks]
    ) if tasks else "No tasks yet."

    shopping = load_shopping()
    pending_shopping = [i for i in shopping if not i.get("bought")]
    shopping_text = "\n".join([f"- {i['name']}" for i in pending_shopping]) if pending_shopping else "Empty."

    try:
        events = get_upcoming_events(3)
        event_text = "\n".join(
            [f"- {e.get('summary','?')} on {e['start'].get('dateTime', e['start'].get('date','?'))[:16]}"
             for e in events]
        ) if events else "No upcoming events."
    except Exception:
        event_text = "Calendar not available."

    system = SYSTEM_PROMPT.format(
        datetime=datetime.now().strftime("%A, %B %d %Y at %I:%M %p"),
        tasks=task_text,
        shopping=shopping_text,
        events=event_text
    )

    conversation = load_conversation()
    conversation.append({"role": "user", "content": f"{user_name}: {user_message}"})

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=system,
        messages=conversation
    )
    reply = response.content[0].text

    conversation.append({"role": "assistant", "content": reply})
    save_conversation(conversation)

    # Check if Claude wants to add shopping items
    shopping_match = re.search(r"<shopping>(.*?)</shopping>", reply, re.DOTALL)
    if shopping_match:
        try:
            new_items = json.loads(shopping_match.group(1).strip())
            shopping = load_shopping()
            existing = [i["name"].lower() for i in shopping]
            added = []
            for item in new_items:
                if item.lower() not in existing:
                    shopping.append({"name": item, "bought": False, "added_by": user_name, "date": datetime.now().isoformat()})
                    added.append(item)
            save_shopping(shopping)
        except Exception as e:
            logging.error(f"Shopping error: {e}")

    # Check if Claude wants to create a calendar event
    calendar_match = re.search(r"<calendar>(.*?)</calendar>", reply, re.DOTALL)
    clean_reply = re.sub(r"<shopping>.*?</shopping>", "", reply, flags=re.DOTALL)
    clean_reply = re.sub(r"<calendar>.*?</calendar>", "", clean_reply, flags=re.DOTALL).strip()

    if calendar_match:
        try:
            event_data = json.loads(calendar_match.group(1).strip())
            title = event_data.get("title", "Reminder")
            date_str = event_data.get("date", datetime.now().strftime("%Y-%m-%d"))
            time_str = event_data.get("time", "09:00")
            description = event_data.get("description", "")

            start_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
            link = create_calendar_event(title, start_dt, description=description)
            clean_reply += f"\n\n📅 *Calendar event created:* [{title}]({link})"
        except Exception as e:
            logging.error(f"Calendar error: {e}")
            clean_reply += "\n\n⚠️ Couldn't create the calendar event. Try /calendar to check."

    # Auto-save tasks mentioned in message
    task_keywords = ["need to", "have to", "must", "don't forget", "todo", "to do"]
    if any(kw in user_message.lower() for kw in task_keywords):
        for line in user_message.split('\n'):
            line = line.strip().lstrip('-•*123456789. ')
            if len(line) > 5 and line not in [t['text'] for t in tasks]:
                tasks.append({"text": line, "done": False, "added_by": user_name,
                               "date": datetime.now().isoformat()})
        save_tasks(tasks)

    await update.message.reply_text(clean_reply, parse_mode="Markdown")

# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",    start))
    app.add_handler(CommandHandler("tasks",    tasks_command))
    app.add_handler(CommandHandler("calendar", calendar_command))
    app.add_handler(CommandHandler("done",     done_command))
    app.add_handler(CommandHandler("clear",    clear_command))
    app.add_handler(CommandHandler("add",       add_command))
    app.add_handler(CommandHandler("shopping",  shopping_command))
    app.add_handler(CommandHandler("bought",    bought_command))
    app.add_handler(CommandHandler("clearshop", clearshop_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    print("🤖 Chief of Staff bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
