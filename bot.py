import os
import json
import logging
import logging.handlers
import re
import signal
import sys
import tempfile
import time
import fnmatch
import urllib.request
import urllib.error
import urllib.parse
import feedparser
import pytz
from datetime import datetime, timedelta
from pathlib import Path
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import anthropic
from faster_whisper import WhisperModel
from notion_client import Client as NotionClient
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# ── Structured JSON logging ───────────────────────────────────────────────────

class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({
            "time":    datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S IST"),
            "level":   record.levelname,
            "message": record.getMessage(),
        })

_log_handler = logging.handlers.RotatingFileHandler(
    "mira.log", maxBytes=2 * 1024 * 1024, backupCount=3  # 2MB, keep 3 files
)
_log_handler.setFormatter(JsonFormatter())
logging.basicConfig(level=logging.INFO, handlers=[_log_handler, logging.StreamHandler()])
# Suppress httpx request logs — they print full URLs which contain the Telegram bot token
logging.getLogger("httpx").setLevel(logging.WARNING)

# ── Cloud bootstrap: write credential files from env vars if present ──────────
def _bootstrap_cloud_files():
    """On Railway (or any cloud), write credential/data files from env vars."""
    import json as _json
    # Google OAuth files
    _token = os.getenv("GOOGLE_TOKEN_JSON")
    if _token and not os.path.exists("token.json"):
        with open("token.json", "w") as f: f.write(_token)
    _creds = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if _creds and not os.path.exists("credentials.json"):
        with open("credentials.json", "w") as f: f.write(_creds)
    # Notion DB ID files
    for _env, _file in [
        ("NOTION_COST_DB_ID",     "cost_db_id.txt"),
        ("NOTION_EVAL_DB_ID",     "eval_db_id.txt"),
        ("NOTION_SECURITY_DB_ID", "security_db_id.txt"),
        ("NOTION_MEMORY_DB_ID",   "memory_db_id.txt"),
    ]:
        _val = os.getenv(_env)
        if _val and not os.path.exists(_file):
            with open(_file, "w") as f: f.write(_val)
    # Chat IDs
    _chat_ids = os.getenv("CHAT_IDS")
    if _chat_ids and not os.path.exists("chat_ids.json"):
        ids = [int(x.strip()) for x in _chat_ids.split(",") if x.strip()]
        with open("chat_ids.json", "w") as f: _json.dump(ids, f)

_bootstrap_cloud_files()

# ── Uptime tracking ───────────────────────────────────────────────────────────
BOT_START_TIME = datetime.now(pytz.timezone("Asia/Kolkata"))

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_TASKS_DB = os.getenv("NOTION_TASKS_DB")
NOTION_SHOPPING_DB = os.getenv("NOTION_SHOPPING_DB")
NOTION_IDEAS_DB = os.getenv("NOTION_IDEAS_DB")

IDEAS_FILE = "ideas.json"
CHAT_IDS_FILE = "chat_ids.json"
HEARTBEAT_FILE = "last_heartbeat.txt"
IST = pytz.timezone("Asia/Kolkata")

notion = NotionClient(auth=NOTION_TOKEN) if NOTION_TOKEN else None

# Only these Telegram user IDs can use Mira. Add yours and your wife's.
ALLOWED_USERS_RAW = os.getenv("ALLOWED_USERS", "")
ALLOWED_USERS = set(int(uid.strip()) for uid in ALLOWED_USERS_RAW.split(",") if uid.strip().isdigit())
# First user in the list is the owner who receives security alerts
OWNER_ID = min(ALLOWED_USERS) if ALLOWED_USERS else None

def is_allowed(update) -> bool:
    """Return True if the sender is on the allowlist (or allowlist is empty)."""
    if not ALLOWED_USERS:
        return True  # not configured yet — open access
    return update.effective_user.id in ALLOWED_USERS

async def block_unauthorised(update, context):
    """Call this instead of is_allowed() to block AND alert in async handlers."""
    if is_allowed(update):
        return False  # not blocked
    uid  = update.effective_user.id
    name = update.effective_user.first_name or "Unknown"
    details = f"Tried to use Mira but is not on the allowlist."
    log_security_event("Unauthorised Access", uid, name, details)
    await send_security_alert(context.bot, "Unauthorised Access", uid, name, details)
    await update.message.reply_text("⛔ Sorry, you're not authorised to use Mira.")
    return True  # was blocked

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

TASKS_FILE = "tasks.json"
SHOPPING_FILE = "shopping.json"
CONVERSATION_FILE = "conversation.json"
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"
STATS_FILE = "daily_stats.json"
COST_DB_FILE   = "cost_db_id.txt"
EVAL_DB_FILE   = "eval_db_id.txt"
MEMORY_DB_FILE = "memory_db_id.txt"
SCOPES = ["https://www.googleapis.com/auth/calendar"]

# Claude Sonnet 4.6 pricing (USD per million tokens)
COST_INPUT_PER_MTK  = 3.00
COST_OUTPUT_PER_MTK = 15.00

# ── Daily stats tracking ──────────────────────────────────────────────────────

def _today() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")

def load_stats() -> dict:
    if os.path.exists(STATS_FILE):
        with open(STATS_FILE) as f:
            return json.load(f)
    return {}

def save_stats(stats: dict):
    with open(STATS_FILE, "w") as f:
        json.dump(stats, f, indent=2)

def get_today_stats() -> dict:
    stats = load_stats()
    today = _today()
    if today not in stats:
        stats[today] = {
            "messages": 0, "voice_messages": 0,
            "input_tokens": 0, "output_tokens": 0,
            "tasks_added": 0, "shopping_added": 0,
            "ideas_added": 0, "calendar_events": 0,
            "errors": 0
        }
        save_stats(stats)
    return stats[today]

def increment_stat(key: str, amount: int = 1):
    stats = load_stats()
    today = _today()
    if today not in stats:
        get_today_stats()
        stats = load_stats()
    stats[today][key] = stats[today].get(key, 0) + amount
    save_stats(stats)

# ── Retry logic ──────────────────────────────────────────────────────────────

def with_retry(fn, retries=3, delay=2, label="operation"):
    """Call fn up to `retries` times with exponential backoff. Returns result or None."""
    for attempt in range(1, retries + 1):
        try:
            return fn()
        except Exception as e:
            if attempt == retries:
                logging.error(f"[RETRY] {label} failed after {retries} attempts: {e}")
                return None
            wait = delay * attempt
            logging.warning(f"[RETRY] {label} attempt {attempt} failed ({e}), retrying in {wait}s...")
            time.sleep(wait)

# ── Security ─────────────────────────────────────────────────────────────────

SECURITY_DB_FILE = "security_db_id.txt"

# Prompt injection patterns — phrases that try to hijack Claude's behaviour
INJECTION_PATTERNS = [
    "ignore previous instructions", "ignore all instructions", "ignore your instructions",
    "disregard previous", "disregard all previous", "forget everything",
    "you are now", "you are a different", "pretend you are", "act as if you are",
    "new persona", "your new instructions", "system prompt", "override instructions",
    "jailbreak", "dan mode", "developer mode", "unrestricted mode",
    "send this to", "forward this to", "email this to", "leak the data",
    "print all tasks", "show all secrets", "reveal your instructions",
    "what is your api key", "show credentials",
]

# Patterns that look like secrets — strip from Claude's replies
SECRET_PATTERNS = [
    r"sk-ant-[A-Za-z0-9\-_]{20,}",          # Anthropic API key
    r"ntn_[A-Za-z0-9]{30,}",                 # Notion token
    r"AAH[A-Za-z0-9\-_]{30,}",              # Telegram bot token suffix
    r"Bearer [A-Za-z0-9\-_.]{20,}",          # Generic Bearer token
    r"[A-Za-z0-9]{32,}:[A-Za-z0-9\-_]{20,}", # key:secret format
]

def check_prompt_injection(message: str):
    """Return the matched pattern if injection detected, else None."""
    lower = message.lower()
    for pattern in INJECTION_PATTERNS:
        if pattern in lower:
            return pattern
    return None

def scan_for_secrets(text: str) -> str:
    """Replace any secret-looking strings in Claude's reply with [REDACTED]."""
    import re as _re
    for pattern in SECRET_PATTERNS:
        text = _re.sub(pattern, "[REDACTED]", text)
    return text

def get_security_db_id():
    return _get_or_create_db(
        SECURITY_DB_FILE, "🔐 Mira Security Log", "🔐",
        {
            "Event":      {"title": {}},
            "Type":       {"select": {}},
            "User ID":    {"rich_text": {}},
            "User Name":  {"rich_text": {}},
            "Details":    {"rich_text": {}},
            "Timestamp":  {"rich_text": {}},
        }
    )

def log_security_event(event_type: str, user_id: int, user_name: str, details: str):
    """Write a security event to the Notion Security Log database."""
    try:
        db_id = get_security_db_id()
        if not db_id:
            return
        timestamp = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
        _notion_request("https://api.notion.com/v1/pages", {
            "parent": {"database_id": db_id},
            "properties": {
                "Event":     {"title": [{"text": {"content": f"{event_type} — {timestamp}"}}]},
                "Type":      {"select": {"name": event_type}},
                "User ID":   {"rich_text": [{"text": {"content": str(user_id)}}]},
                "User Name": {"rich_text": [{"text": {"content": user_name}}]},
                "Details":   {"rich_text": [{"text": {"content": details[:1900]}}]},
                "Timestamp": {"rich_text": [{"text": {"content": timestamp}}]},
            }
        })
        logging.warning(f"[SECURITY] {event_type} | user={user_name}({user_id}) | {details}")
    except Exception as e:
        logging.error(f"Security log error: {e}")

async def send_security_alert(bot, event_type: str, user_id: int, user_name: str, details: str):
    """Send an instant Telegram alert to the owner for any security event."""
    if not OWNER_ID:
        return
    timestamp = datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST")
    icons = {
        "Unauthorised Access": "🚨",
        "Injection Attempt":   "⚠️",
        "Secret Leak Blocked": "🔒",
        "Boot Check":          "✅",
    }
    icon = icons.get(event_type, "🔐")
    msg = (
        f"{icon} *Security Alert — {event_type}*\n\n"
        f"👤 User: `{user_name}` (ID: `{user_id}`)\n"
        f"🕐 Time: {timestamp}\n"
        f"📋 Details: {details}"
    )
    try:
        await bot.send_message(chat_id=OWNER_ID, text=msg, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Security alert send error: {e}")

def run_boot_health_check():
    """Check all integrations on startup and log failures."""
    results = {}

    # 1. Telegram — already connected if bot started, just mark OK
    results["Telegram"] = "OK"

    # 2. Claude API
    try:
        client.messages.create(model="claude-sonnet-4-6", max_tokens=5,
                               messages=[{"role": "user", "content": "hi"}])
        results["Claude API"] = "OK"
    except Exception as e:
        results["Claude API"] = f"FAIL: {e}"

    # 3. Notion
    try:
        if notion and NOTION_TASKS_DB:
            notion.databases.retrieve(NOTION_TASKS_DB)
            results["Notion"] = "OK"
        else:
            results["Notion"] = "NOT CONFIGURED"
    except Exception as e:
        results["Notion"] = f"FAIL: {e}"

    # 4. Google Calendar
    try:
        get_calendar_service()
        results["Google Calendar"] = "OK"
    except Exception as e:
        results["Google Calendar"] = f"FAIL: {e}"

    # Log results
    failed = [k for k, v in results.items() if v.startswith("FAIL")]
    status = "All systems OK" if not failed else f"FAIL: {', '.join(failed)}"
    summary = " | ".join(f"{k}: {v}" for k, v in results.items())
    logging.info(f"[BOOT CHECK] {summary}")

    try:
        log_security_event("Boot Check", 0, "system", f"{status} — {summary}")
    except Exception:
        pass

    return results

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

# ── Ideas & Chat IDs persistence ─────────────────────────────────────────────

def load_ideas():
    if os.path.exists(IDEAS_FILE):
        with open(IDEAS_FILE) as f:
            return json.load(f)
    return []

def save_ideas(ideas):
    with open(IDEAS_FILE, "w") as f:
        json.dump(ideas, f, indent=2)

def load_chat_ids():
    if os.path.exists(CHAT_IDS_FILE):
        with open(CHAT_IDS_FILE) as f:
            return json.load(f)
    return []

def save_chat_id(chat_id: int):
    ids = load_chat_ids()
    if chat_id not in ids:
        ids.append(chat_id)
        with open(CHAT_IDS_FILE, "w") as f:
            json.dump(ids, f)

# ── Market indices (real-time via yfinance) ────────────────────────────────────

INDICES = [
    ("S&P 500",    "^GSPC",  "🇺🇸"),
    ("Dow Jones",  "^DJI",   "🇺🇸"),
    ("NASDAQ",     "^IXIC",  "🇺🇸"),
    ("Nifty 50",   "^NSEI",  "🇮🇳"),
    ("Sensex",     "^BSESN", "🇮🇳"),
    ("Gold",       "GC=F",   "🥇"),
    ("Silver",     "SI=F",   "🥈"),
]

def get_news_headlines() -> list[str]:
    import yfinance as yf
    lines = []
    for name, ticker, flag in INDICES:
        try:
            t = yf.Ticker(ticker)
            info = t.fast_info
            price = info.last_price
            prev  = info.previous_close
            if price is None or prev is None:
                continue
            change     = price - prev
            change_pct = (change / prev) * 100
            arrow      = "▲" if change >= 0 else "▼"
            sign       = "+" if change >= 0 else ""
            lines.append(
                f"{flag} *{name}:* {price:,.2f}  {arrow} {sign}{change_pct:.2f}%"
            )
        except Exception:
            pass
    return lines[:6]

# ── Notion sync ──────────────────────────────────────────────────────────────

def notion_add_task(title: str, added_by: str):
    if not notion or not NOTION_TASKS_DB:
        return
    try:
        notion.pages.create(
            parent={"database_id": NOTION_TASKS_DB},
            properties={
                "Name":       {"title": [{"text": {"content": title}}]},
                "Status":     {"select": {"name": "Pending"}},
                "Added By":   {"rich_text": [{"text": {"content": added_by}}]},
                "Created":    {"date": {"start": datetime.now(IST).isoformat()}},
            }
        )
    except Exception as e:
        logging.error(f"Notion task error: {e}")

def notion_query_db(database_id: str, filter_body: dict = None) -> list:
    """Query a Notion database via direct HTTP POST."""
    if not NOTION_TOKEN:
        return []
    try:
        payload = json.dumps({"filter": filter_body} if filter_body else {}).encode()
        req = urllib.request.Request(
            f"https://api.notion.com/v1/databases/{database_id}/query",
            data=payload, method="POST",
            headers={
                "Authorization": f"Bearer {NOTION_TOKEN}",
                "Notion-Version": "2022-06-28",
                "Content-Type": "application/json"
            }
        )
        res = urllib.request.urlopen(req)
        return json.loads(res.read()).get("results", [])
    except Exception as e:
        logging.error(f"Notion query error: {e}")
        return []

def notion_get_tasks() -> list:
    if not NOTION_TASKS_DB:
        return []
    try:
        pages = notion_query_db(NOTION_TASKS_DB, {"property": "Status", "select": {"equals": "Pending"}})
        tasks = []
        for page in pages:
            title = page["properties"].get("Name", {}).get("title", [])
            name = title[0]["text"]["content"] if title else "Untitled"
            tasks.append({"text": name, "page_id": page["id"]})
        return tasks
    except Exception as e:
        logging.error(f"Notion fetch tasks error: {e}")
        return []

def notion_complete_task(page_id: str):
    if not notion:
        return
    try:
        notion.pages.update(
            page_id=page_id,
            properties={"Status": {"select": {"name": "Done"}}}
        )
    except Exception as e:
        logging.error(f"Notion complete task error: {e}")

def notion_add_shopping(item: str, added_by: str):
    if not notion or not NOTION_SHOPPING_DB:
        return
    try:
        notion.pages.create(
            parent={"database_id": NOTION_SHOPPING_DB},
            properties={
                "Name":     {"title": [{"text": {"content": item}}]},
                "Status":   {"select": {"name": "Needed"}},
                "Added By": {"rich_text": [{"text": {"content": added_by}}]},
                "Created":  {"date": {"start": datetime.now().isoformat()}},
            }
        )
    except Exception as e:
        logging.error(f"Notion shopping error: {e}")

def notion_get_shopping() -> list:
    if not NOTION_SHOPPING_DB:
        return []
    try:
        pages = notion_query_db(NOTION_SHOPPING_DB, {"property": "Status", "select": {"equals": "Needed"}})
        items = []
        for page in pages:
            title = page["properties"].get("Name", {}).get("title", [])
            name = title[0]["text"]["content"] if title else "Untitled"
            items.append({"name": name, "page_id": page["id"]})
        return items
    except Exception as e:
        logging.error(f"Notion fetch shopping error: {e}")
        return []

def notion_bought_item(page_id: str):
    if not notion:
        return
    try:
        notion.pages.update(
            page_id=page_id,
            properties={"Status": {"select": {"name": "Bought"}}}
        )
    except Exception as e:
        logging.error(f"Notion bought item error: {e}")

def notion_add_idea(title: str, idea_type: str, added_by: str):
    if not notion or not NOTION_IDEAS_DB:
        return
    try:
        notion.pages.create(
            parent={"database_id": NOTION_IDEAS_DB},
            properties={
                "Name":     {"title": [{"text": {"content": title}}]},
                "Type":     {"select": {"name": idea_type}},
                "Status":   {"select": {"name": "New"}},
                "Added By": {"rich_text": [{"text": {"content": added_by}}]},
                "Created":  {"date": {"start": datetime.now().isoformat()}},
            }
        )
    except Exception as e:
        logging.error(f"Notion idea error: {e}")

def notion_get_ideas() -> list:
    if not NOTION_IDEAS_DB:
        return []
    try:
        pages = notion_query_db(NOTION_IDEAS_DB)
        ideas = []
        for page in pages:
            title = page["properties"].get("Name", {}).get("title", [])
            name = title[0]["text"]["content"] if title else "Untitled"
            idea_type = page["properties"].get("Type", {}).get("select", {})
            ideas.append({"text": name, "type": idea_type.get("name", "Other") if idea_type else "Other"})
        return ideas
    except Exception as e:
        logging.error(f"Notion fetch ideas error: {e}")
        return []

def sync_tasks_to_notion(tasks: list, added_by: str):
    """Sync any new local tasks to Notion."""
    if not notion:
        return
    existing = [t["text"].lower() for t in notion_get_tasks()]
    for task in tasks:
        if task["text"].lower() not in existing and not task.get("done"):
            notion_add_task(task["text"], added_by)

def sync_shopping_to_notion(items: list, added_by: str):
    """Sync any new local shopping items to Notion."""
    if not notion:
        return
    existing = [i["name"].lower() for i in notion_get_shopping()]
    for item in items:
        if item["name"].lower() not in existing and not item.get("bought"):
            notion_add_shopping(item["name"], added_by)

# ── Notion helpers ────────────────────────────────────────────────────────────

def _notion_request(url, payload, method="POST"):
    def _do():
        body = json.dumps(payload).encode() if payload and method != "GET" else None
        headers = {"Authorization": f"Bearer {NOTION_TOKEN}", "Notion-Version": "2022-06-28"}
        if body:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    result = with_retry(_do, retries=3, delay=2, label=f"Notion {method} {url[-40:]}")
    if result is None:
        raise Exception(f"Notion request failed after retries: {url}")
    return result

def _get_notion_parent_page_id():
    """Return the parent page id shared by the existing Notion databases."""
    if not NOTION_TASKS_DB:
        return None
    meta = _notion_request(f"https://api.notion.com/v1/databases/{NOTION_TASKS_DB}", {}, method="GET")
    return meta.get("parent", {}).get("page_id")

def _get_or_create_db(cache_file: str, title: str, icon: str, properties: dict):
    """Return a Notion DB id from cache, or create it alongside existing DBs."""
    if not notion:
        return None
    if os.path.exists(cache_file):
        with open(cache_file) as f:
            db_id = f.read().strip()
        if db_id:
            return db_id
    try:
        parent_page_id = _get_notion_parent_page_id()
        if not parent_page_id:
            logging.error(f"Could not find parent page for {title}")
            return None
        db = _notion_request("https://api.notion.com/v1/databases", {
            "parent": {"type": "page_id", "page_id": parent_page_id},
            "icon":   {"type": "emoji", "emoji": icon},
            "title":  [{"type": "text", "text": {"content": title}}],
            "properties": properties,
        })
        db_id = db["id"]
        with open(cache_file, "w") as f:
            f.write(db_id)
        logging.info(f"Created Notion DB '{title}': {db_id}")
        return db_id
    except Exception as e:
        logging.error(f"Could not create Notion DB '{title}': {e}")
        return None

def _notion_upsert(db_id: str, date_value: str, props: dict, label: str):
    """Upsert a row keyed by Date title into a Notion database."""
    try:
        results = _notion_request(
            f"https://api.notion.com/v1/databases/{db_id}/query",
            {"filter": {"property": "Date", "title": {"equals": date_value}}}
        ).get("results", [])
        if results:
            _notion_request(f"https://api.notion.com/v1/pages/{results[0]['id']}",
                            {"properties": props}, method="PATCH")
        else:
            _notion_request("https://api.notion.com/v1/pages",
                            {"parent": {"database_id": db_id}, "properties": props})
        logging.info(f"Notion {label} upserted for {date_value}")
    except Exception as e:
        logging.error(f"Notion {label} upsert error: {e}")

# ── Notion Cost Reports DB ────────────────────────────────────────────────────

def get_cost_db_id():
    return _get_or_create_db(
        COST_DB_FILE, "💰 Mira Cost Reports", "💰",
        {
            "Date":                {"title": {}},
            "Chargeable Messages": {"number": {"format": "number"}},
            "Input Tokens":        {"number": {"format": "number"}},
            "Output Tokens":       {"number": {"format": "number"}},
            "Input Cost ($)":      {"number": {"format": "dollar"}},
            "Output Cost ($)":     {"number": {"format": "dollar"}},
            "Total Cost ($)":      {"number": {"format": "dollar"}},
            "Avg Cost/Day ($)":    {"number": {"format": "dollar"}},
            "Projected Month ($)": {"number": {"format": "dollar"}},
            "Month":               {"rich_text": {}},
        }
    )

def notion_upsert_cost(s: dict, today: str, avg_per_day: float, projected: float, month_label: str):
    db_id = get_cost_db_id()
    if not db_id:
        return
    input_cost  = (s.get("input_tokens", 0)  / 1_000_000) * COST_INPUT_PER_MTK
    output_cost = (s.get("output_tokens", 0) / 1_000_000) * COST_OUTPUT_PER_MTK
    _notion_upsert(db_id, today, {
        "Date":                {"title": [{"text": {"content": today}}]},
        "Chargeable Messages": {"number": s.get("messages", 0)},
        "Input Tokens":        {"number": s.get("input_tokens", 0)},
        "Output Tokens":       {"number": s.get("output_tokens", 0)},
        "Input Cost ($)":      {"number": round(input_cost, 6)},
        "Output Cost ($)":     {"number": round(output_cost, 6)},
        "Total Cost ($)":      {"number": round(input_cost + output_cost, 6)},
        "Avg Cost/Day ($)":    {"number": round(avg_per_day, 6)},
        "Projected Month ($)": {"number": round(projected, 4)},
        "Month":               {"rich_text": [{"text": {"content": month_label}}]},
    }, "cost report")

# ── Notion Eval Reports DB ────────────────────────────────────────────────────

def get_eval_db_id():
    return _get_or_create_db(
        EVAL_DB_FILE, "📊 Mira Eval Reports", "📊",
        {
            "Date":             {"title": {}},
            "Status":           {"select": {}},
            "Messages Handled": {"number": {"format": "number"}},
            "Voice Messages":   {"number": {"format": "number"}},
            "Tasks Added":      {"number": {"format": "number"}},
            "Shopping Added":   {"number": {"format": "number"}},
            "Ideas Saved":      {"number": {"format": "number"}},
            "Calendar Events":  {"number": {"format": "number"}},
            "Errors":           {"number": {"format": "number"}},
        }
    )

def notion_upsert_eval(s: dict, today: str):
    db_id = get_eval_db_id()
    if not db_id:
        return
    errors = s.get("errors", 0)
    status = "🟢 Healthy" if errors == 0 else ("🟡 Minor issues" if errors <= 2 else "🔴 Needs attention")
    _notion_upsert(db_id, today, {
        "Date":             {"title": [{"text": {"content": today}}]},
        "Status":           {"select": {"name": status}},
        "Messages Handled": {"number": s.get("messages", 0)},
        "Voice Messages":   {"number": s.get("voice_messages", 0)},
        "Tasks Added":      {"number": s.get("tasks_added", 0)},
        "Shopping Added":   {"number": s.get("shopping_added", 0)},
        "Ideas Saved":      {"number": s.get("ideas_added", 0)},
        "Calendar Events":  {"number": s.get("calendar_events", 0)},
        "Errors":           {"number": errors},
    }, "eval report")

# ── Long-term Memory ─────────────────────────────────────────────────────────

MEMORY_CATEGORIES = ["Health", "Goals", "Preferences", "Important Dates", "Family", "Work", "Habits", "Other"]

def get_memory_db_id():
    return _get_or_create_db(
        MEMORY_DB_FILE, "🧠 Mira Memory", "🧠",
        {
            "Memory":     {"title": {}},
            "Person":     {"select": {}},
            "Category":   {"select": {}},
            "Source":     {"rich_text": {}},
            "Date Added": {"date": {}},
            "Active":     {"checkbox": {}},
        }
    )

def memory_save(fact: str, person: str = "Both", category: str = "Other", source: str = "conversation"):
    """Save a new memory to Notion. Skips if a very similar one already exists."""
    db_id = get_memory_db_id()
    if not db_id:
        return
    try:
        # Check for near-duplicate (same fact text)
        existing = _notion_request(
            f"https://api.notion.com/v1/databases/{db_id}/query",
            {"filter": {"property": "Memory", "title": {"contains": fact[:40]}}}
        ).get("results", [])
        if existing:
            return  # already stored
        _notion_request("https://api.notion.com/v1/pages", {
            "parent": {"database_id": db_id},
            "properties": {
                "Memory":     {"title": [{"text": {"content": fact}}]},
                "Person":     {"select": {"name": person}},
                "Category":   {"select": {"name": category}},
                "Source":     {"rich_text": [{"text": {"content": source}}]},
                "Date Added": {"date": {"start": datetime.now(IST).isoformat()}},
                "Active":     {"checkbox": True},
            }
        })
        logging.info(f"[MEMORY] Saved: {fact[:80]}")
    except Exception as e:
        logging.error(f"Memory save error: {e}")

def memory_load_all() -> list:
    """Return all active memories from Notion."""
    db_id = get_memory_db_id()
    if not db_id:
        return []
    try:
        pages = _notion_request(
            f"https://api.notion.com/v1/databases/{db_id}/query",
            {"filter": {"property": "Active", "checkbox": {"equals": True}},
             "sorts": [{"timestamp": "created_time", "direction": "descending"}],
             "page_size": 50}
        ).get("results", [])
        memories = []
        for p in pages:
            props = p["properties"]
            title = props.get("Memory", {}).get("title", [])
            fact  = title[0]["text"]["content"] if title else ""
            person   = props.get("Person", {}).get("select", {})
            category = props.get("Category", {}).get("select", {})
            if fact:
                memories.append({
                    "fact":     fact,
                    "person":   person.get("name", "Both") if person else "Both",
                    "category": category.get("name", "Other") if category else "Other",
                    "page_id":  p["id"],
                })
        return memories
    except Exception as e:
        logging.error(f"Memory load error: {e}")
        return []

def memory_forget(page_id: str):
    """Mark a memory as inactive (soft delete)."""
    try:
        _notion_request(f"https://api.notion.com/v1/pages/{page_id}",
                        {"properties": {"Active": {"checkbox": False}}}, method="PATCH")
    except Exception as e:
        logging.error(f"Memory forget error: {e}")

def format_memories_for_context(memories: list) -> str:
    """Format memories into a concise block for Claude's system prompt."""
    if not memories:
        return ""
    by_person = {}
    for m in memories:
        p = m["person"]
        by_person.setdefault(p, []).append(f"[{m['category']}] {m['fact']}")
    lines = ["Long-term memory about the family:"]
    for person, facts in by_person.items():
        lines.append(f"\n{person}:")
        for f in facts:
            lines.append(f"  - {f}")
    return "\n".join(lines)

def extract_and_save_memories(conversation_text: str, user_name: str):
    """Ask Claude to extract memorable facts from the conversation and save them."""
    try:
        prompt = f"""Read this conversation and extract any facts worth remembering long-term about the people involved.
Only extract concrete, specific, lasting facts — not temporary states or one-off events.
Good examples: allergies, goals, important dates, preferences, habits, family info.
Bad examples: "asked about the weather", "was hungry today".

Return ONLY a JSON array. If nothing worth remembering, return [].
Format: [{{"fact": "...", "person": "Abhishek|Alekya|Both", "category": "Health|Goals|Preferences|Important Dates|Family|Work|Habits|Other"}}]

Conversation:
{conversation_text[-2000:]}"""

        response = with_retry(
            lambda: client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}]
            ),
            retries=2, delay=2, label="memory extraction"
        )
        if not response:
            return
        raw = response.content[0].text.strip()
        # Extract JSON array from response
        match = re.search(r'\[.*\]', raw, re.DOTALL)
        if not match:
            return
        facts = json.loads(match.group())
        for item in facts:
            fact     = item.get("fact", "").strip()
            person   = item.get("person", "Both")
            category = item.get("category", "Other")
            if fact and len(fact) > 5:
                memory_save(fact, person, category, source=f"conversation with {user_name}")
    except Exception as e:
        logging.error(f"Memory extraction error: {e}")

# ── File search ──────────────────────────────────────────────────────────────

SEARCH_DIRS = [
    Path.home() / "Desktop",
    Path.home() / "Documents",
    Path.home() / "Downloads",
    Path.home() / "Pictures",
    Path.home() / "Movies",
    Path.home() / "Music",
]

def search_files(query: str, max_results: int = 5) -> list[Path]:
    """Search for files matching query keywords across common Mac directories."""
    keywords = query.lower().split()
    matches = []

    for search_dir in SEARCH_DIRS:
        if not search_dir.exists():
            continue
        try:
            for root, dirs, files in os.walk(search_dir):
                # Skip hidden folders
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                for filename in files:
                    if filename.startswith('.'):
                        continue
                    name_lower = filename.lower()
                    if all(kw in name_lower for kw in keywords):
                        matches.append(Path(root) / filename)
                        if len(matches) >= max_results:
                            return matches
        except PermissionError:
            continue

    # If no exact match, try partial — any keyword matches
    if not matches:
        for search_dir in SEARCH_DIRS:
            if not search_dir.exists():
                continue
            try:
                for root, dirs, files in os.walk(search_dir):
                    dirs[:] = [d for d in dirs if not d.startswith('.')]
                    for filename in files:
                        if filename.startswith('.'):
                            continue
                        name_lower = filename.lower()
                        if any(kw in name_lower for kw in keywords):
                            matches.append(Path(root) / filename)
                            if len(matches) >= max_results:
                                return matches
            except PermissionError:
                continue

    return matches

# ── Prompts ───────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are Mira, a personal AI assistant for a couple — Abhishek and Alekya.
You help them manage their lives together — tasks, reminders, calendar events, decisions, shopping, finances, and more.

You are warm, organised, proactive, and concise. You speak like a trusted personal assistant.

Current date and time: {datetime}

Current task list:
{tasks}

Shopping list:
{shopping}

Recent ideas:
{ideas}

Upcoming calendar events:
{events}

{memories}

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

IMPORTANT — File search detection:
If the user explicitly wants to find or retrieve a specific FILE or DOCUMENT from their Mac laptop (e.g. "find the invoice pdf", "send me the contract", "where is the presentation file", "share the photo from last year"), respond with a JSON block at the END of your reply:
<filesearch>
{{"query": "keywords to search for in filename"}}
</filesearch>

Only include <filesearch> for actual file/document retrieval requests. Do NOT use it for shopping items, tasks, or general questions.

IMPORTANT — Mark a specific task done:
If the user says a specific task is done (e.g. "1st task done", "mark task 2 done", "I called the dentist", "paid the rent", "dentist appointment done"), respond with:
<taskdone>{{"match": "partial text or number to identify the task"}}</taskdone>

Use a number (1, 2, 3) if the user says "1st", "2nd", "first", "second" etc. Otherwise use key words from the task name.

IMPORTANT — Mark all tasks done:
If the user wants to mark all tasks as done (e.g. "mark all tasks done", "clear all tasks", "all done", "everything is done"), respond with:
<markalldone></markalldone>

IMPORTANT — Mark a specific shopping item as bought:
If the user says a specific item is bought (e.g. "bought milk", "got the eggs", "2nd item done", "picked up bread"), respond with:
<shoppingdone>{{"match": "partial text or number to identify the item"}}</shoppingdone>

IMPORTANT — Mark all shopping as bought:
If the user wants to mark all shopping items as bought (e.g. "bought everything", "mark all shopping done", "all items bought"), respond with:
<markallbought></markallbought>

IMPORTANT — Mark an idea as done:
If the user says an idea is completed or no longer relevant (e.g. "published the blog", "built that AI product", "1st idea done", "remove that idea"), respond with:
<ideadone>{{"match": "partial text or number to identify the idea"}}</ideadone>

IMPORTANT — Idea capture:
If the user shares an idea (blog idea, AI product, business idea, article to read, or any creative thought they want to save), respond with a JSON block at the END of your reply:
<idea>
{{"title": "concise idea title", "type": "Blog Idea|AI Product|Article to Read|Business Idea|Other"}}
</idea>

Types must be exactly one of: Blog Idea, AI Product, Article to Read, Business Idea, Other.

IMPORTANT — Image analysis:
When the user sends a photo, analyse it carefully and respond naturally. Apply the same action tags as you would for text:
- Shopping receipt, grocery list, or product → extract items with <shopping> tag
- Handwritten to-do list or notes → create tasks
- Bill, invoice, or payment reminder → note the amount and add a payment task
- Calendar invite, event poster, or schedule → create a calendar event with <calendar> tag
- Prescription or medical document → summarise clearly and suggest storing as a memory
- Whiteboard or document → transcribe and summarise
- General photo → describe helpfully and ask how you can assist

IMPORTANT — Long-term memory:
If the user explicitly asks you to remember something (e.g. "remember that I'm allergic to peanuts", "note that Alekya's birthday is March 5"), respond with:
<remember>{{"fact": "the fact to remember", "person": "Abhishek|Alekya|Both", "category": "Health|Goals|Preferences|Important Dates|Family|Work|Habits|Other"}}</remember>

Only use <remember> when the user explicitly asks to save something. Do not use it for routine conversation.

Group chat rules:
- Both Abhishek and Alekya share the same task list and calendar
- Address people by their first name when relevant
- Keep responses short and use bullet points when listing things."""

# ── Command handlers ──────────────────────────────────────────────────────────

async def digest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await block_unauthorised(update, context): return
    await send_daily_digest(context)

async def notion_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await block_unauthorised(update, context): return
    await update.message.reply_text("🔄 Syncing with Notion...")
    try:
        # Sync tasks
        local_tasks = load_tasks()
        synced_tasks = 0
        existing_notion_tasks = [t["text"].lower() for t in notion_get_tasks()]
        for task in local_tasks:
            if not task.get("done") and task["text"].lower() not in existing_notion_tasks:
                notion_add_task(task["text"], task.get("added_by", "Mira"))
                synced_tasks += 1

        # Sync shopping
        local_shopping = load_shopping()
        synced_items = 0
        existing_notion_shopping = [i["name"].lower() for i in notion_get_shopping()]
        for item in local_shopping:
            if not item.get("bought") and item["name"].lower() not in existing_notion_shopping:
                notion_add_shopping(item["name"], item.get("added_by", "Mira"))
                synced_items += 1

        await update.message.reply_text(
            f"✅ *Notion sync complete!*\n\n"
            f"📋 Tasks synced: {synced_tasks}\n"
            f"🛒 Shopping items synced: {synced_items}\n\n"
            f"Open Notion to see your data!",
            parse_mode="Markdown"
        )
    except Exception as e:
        await update.message.reply_text(f"⚠️ Notion sync failed: {str(e)}")

async def find_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await block_unauthorised(update, context): return
    if not context.args:
        await update.message.reply_text("Usage: /find <filename or keywords>\nExample: /find invoice\nExample: /find pan card pdf")
        return

    query = " ".join(context.args)
    await update.message.reply_text(f"🔍 Searching your Mac for: *{query}*...", parse_mode="Markdown")

    found = search_files(query)
    if not found:
        await update.message.reply_text(f"😕 No files found matching *{query}*.\n\nSearched in: Desktop, Documents, Downloads, Pictures, Movies, Music.", parse_mode="Markdown")
        return

    await update.message.reply_text(f"Found *{len(found)}* file(s)! Sending now...", parse_mode="Markdown")
    for fpath in found:
        try:
            size_mb = fpath.stat().st_size / (1024 * 1024)
            if size_mb > 50:
                await update.message.reply_text(f"⚠️ *{fpath.name}* is {size_mb:.1f}MB — too large for Telegram (50MB limit).\n📁 Path: `{fpath}`", parse_mode="Markdown")
            else:
                with open(fpath, "rb") as f:
                    await update.message.reply_document(document=f, filename=fpath.name, caption=f"📎 {fpath.name}\n📁 {fpath.parent}")
        except Exception as e:
            await update.message.reply_text(f"⚠️ Could not send *{fpath.name}*: {str(e)}", parse_mode="Markdown")

async def myid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    name = update.effective_user.first_name
    await update.message.reply_text(f"👤 *{name}*, your Telegram user ID is:\n`{uid}`\n\nShare this with Abhishek to get added to Mira's allowlist.", parse_mode="Markdown")

async def costreport_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await block_unauthorised(update, context): return

    import calendar as cal_mod
    today     = _today()
    now_ist   = datetime.now(IST)
    s         = get_today_stats()
    all_stats = load_stats()

    # ── Today's cost ──
    input_cost  = (s.get("input_tokens", 0)  / 1_000_000) * COST_INPUT_PER_MTK
    output_cost = (s.get("output_tokens", 0) / 1_000_000) * COST_OUTPUT_PER_MTK
    total_today = input_cost + output_cost
    chargeable  = s.get("messages", 0)

    # ── Monthly estimate ──
    month_prefix = now_ist.strftime("%Y-%m")
    days_in_month = cal_mod.monthrange(now_ist.year, now_ist.month)[1]
    month_days = {d: v for d, v in all_stats.items() if d.startswith(month_prefix)}
    tracked_days = len(month_days)

    if tracked_days > 0:
        month_total_so_far = sum(
            ((v.get("input_tokens", 0) / 1_000_000) * COST_INPUT_PER_MTK +
             (v.get("output_tokens", 0) / 1_000_000) * COST_OUTPUT_PER_MTK)
            for v in month_days.values()
        )
        avg_per_day      = month_total_so_far / tracked_days
        projected_claude = avg_per_day * days_in_month
    else:
        month_total_so_far = 0.0
        avg_per_day        = 0.0
        projected_claude   = 0.0

    # ── Other fixed costs (monthly) ──
    other_costs = [
        ("Telegram Bot API",    0.00, "Free forever"),
        ("Google Calendar API", 0.00, "Free tier"),
        ("Notion API",          0.00, "Free tier"),
        ("OpenAI Whisper",      0.00, "Runs locally, free"),
        ("yfinance (markets)",  0.00, "Free"),
        ("Mac hosting",         0.00, "Runs on your Mac, free"),
        ("Railway (if deployed)", 5.00, "Only if you move to cloud"),
    ]
    projected_total_min = projected_claude
    projected_total_max = projected_claude + 5.00   # includes Railway

    msg = (
        f"💰 *Mira Cost Report — {today}*\n\n"

        f"*Today*\n"
        f"  Chargeable messages: `{chargeable}`\n"
        f"  Input tokens:        `{s.get('input_tokens', 0):,}`\n"
        f"  Output tokens:       `{s.get('output_tokens', 0):,}`\n"
        f"  Claude cost today:   `${total_today:.4f}`\n\n"

        f"*This Month — {now_ist.strftime('%B %Y')}*\n"
        f"  Days tracked:        `{tracked_days}` of `{days_in_month}`\n"
        f"  Avg cost/day:        `${avg_per_day:.4f}`\n"
        f"  Spent so far:        `${month_total_so_far:.4f}`\n"
        f"  Projected Claude:    `${projected_claude:.4f}`\n\n"

        f"*Other Monthly Costs*\n"
        f"  Telegram / Notion / Calendar / Whisper / yfinance: `Free`\n"
        f"  Mac hosting (current): `Free`\n"
        f"  Railway cloud hosting: `~$5.00` _(if you deploy 24/7)_\n\n"

        f"*Projected Total*\n"
        f"  Running on Mac:    `${projected_total_min:.2f}/month`\n"
        f"  Running on Railway: `${projected_total_max:.2f}/month`\n\n"

        f"_Model: claude-sonnet-4-6 · $3/1M input · $15/1M output_\n"
        f"_Syncing to Notion..._"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")
    notion_upsert_cost(s, today, avg_per_day, projected_claude, now_ist.strftime("%B %Y"))

async def eval_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await block_unauthorised(update, context): return

    today = _today()
    s = get_today_stats()

    errors = s.get("errors", 0)
    health = "🟢 Healthy" if errors == 0 else ("🟡 Minor issues" if errors <= 2 else "🔴 Needs attention")

    msg = (
        f"📊 *Mira Eval Report — {today}*\n\n"
        f"*Status:* {health}\n\n"
        f"*Activity*\n"
        f"  💬 Chargeable messages: `{s.get('messages', 0)}`\n"
        f"  🎙️ Voice messages:       `{s.get('voice_messages', 0)}`\n\n"
        f"*Actions taken*\n"
        f"  ✅ Tasks added:          `{s.get('tasks_added', 0)}`\n"
        f"  🛒 Shopping items added: `{s.get('shopping_added', 0)}`\n"
        f"  💡 Ideas saved:          `{s.get('ideas_added', 0)}`\n"
        f"  📅 Calendar events:      `{s.get('calendar_events', 0)}`\n\n"
        f"*Errors:* `{errors}`\n"
    )
    if errors > 0:
        msg += f"\n⚠️ {errors} error(s) occurred today. Check `mira.log` for details."
    else:
        msg += "\n✨ No errors today — Mira is running perfectly!"
    msg += "\n\n_Syncing to Notion..._"

    await update.message.reply_text(msg, parse_mode="Markdown")
    notion_upsert_eval(s, today)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await block_unauthorised(update, context): return

    await update.message.reply_text("🔍 Checking all systems...", parse_mode="Markdown")

    # Uptime
    now      = datetime.now(IST)
    delta    = now - BOT_START_TIME
    hours, remainder = divmod(int(delta.total_seconds()), 3600)
    minutes  = remainder // 60
    uptime   = f"{hours}h {minutes}m" if hours else f"{minutes}m"

    # Check each integration live
    checks = {}

    try:
        client.messages.create(model="claude-sonnet-4-6", max_tokens=5,
                               messages=[{"role": "user", "content": "hi"}])
        checks["Claude API"] = "🟢 OK"
    except Exception as e:
        checks["Claude API"] = f"🔴 FAIL"

    try:
        if notion and NOTION_TASKS_DB:
            notion.databases.retrieve(NOTION_TASKS_DB)
        checks["Notion"] = "🟢 OK"
    except Exception:
        checks["Notion"] = "🔴 FAIL"

    try:
        get_calendar_service()
        checks["Google Calendar"] = "🟢 OK"
    except Exception:
        checks["Google Calendar"] = "🔴 FAIL"

    checks["Telegram"] = "🟢 OK"  # we're responding so it's working

    # Today's stats
    s = get_today_stats()
    errors = s.get("errors", 0)
    overall = "🟢 All systems operational" if all("OK" in v for v in checks.values()) and errors == 0 \
              else "🟡 Some issues detected" if errors <= 2 \
              else "🔴 Needs attention"

    msg = (
        f"📡 *Mira Status Report*\n\n"
        f"*Overall:* {overall}\n"
        f"*Uptime:* `{uptime}` _(since last restart)_\n\n"
        f"*Integrations*\n"
        + "\n".join(f"  {v}  {k}" for k, v in checks.items()) +
        f"\n\n*Today's Activity*\n"
        f"  💬 Messages: `{s.get('messages', 0)}`\n"
        f"  🎙️ Voice: `{s.get('voice_messages', 0)}`\n"
        f"  ⚠️ Errors: `{errors}`\n\n"
        f"_Checked at {now.strftime('%I:%M %p IST')}_"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await block_unauthorised(update, context): return
    save_chat_id(update.effective_chat.id)
    await update.message.reply_text(
        "👋 Hi! I'm Mira, your personal family assistant.\n\n"
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
        # Mark done in Notion too
        notion_tasks = notion_get_tasks()
        for nt in notion_tasks:
            if nt["text"].lower() == task_text.lower():
                notion_complete_task(nt["page_id"])
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
    increment_stat("tasks_added")
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
        # Mark bought in Notion too
        notion_items = notion_get_shopping()
        for ni in notion_items:
            if ni["name"].lower() == item_name.lower():
                notion_bought_item(ni["page_id"])
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

async def memories_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await block_unauthorised(update, context): return
    await update.message.reply_text("🧠 Loading memories...", parse_mode="Markdown")
    memories = memory_load_all()
    if not memories:
        await update.message.reply_text("🧠 No memories stored yet.\n\nTell me something like:\n_'Remember that Alekya is allergic to shellfish'_\nor just have a few conversations — I'll learn automatically!", parse_mode="Markdown")
        return
    by_person = {}
    for m in memories:
        p = m["person"]
        by_person.setdefault(p, []).append((m["category"], m["fact"], m["page_id"]))
    msg = f"🧠 *Mira's Memory* ({len(memories)} facts stored)\n"
    for person, facts in by_person.items():
        msg += f"\n*{person}:*\n"
        for i, (category, fact, _) in enumerate(facts, 1):
            msg += f"  {i}. [{category}] {fact}\n"
    msg += "\n_Use /forget <number> to remove a memory_"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def remember_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await block_unauthorised(update, context): return
    if not context.args:
        await update.message.reply_text(
            "Usage: /remember <fact>\n\n"
            "Examples:\n"
            "• /remember Alekya is allergic to shellfish\n"
            "• /remember Our wedding anniversary is June 12\n"
            "• /remember Abhishek goes to the gym on Tuesday and Thursday",
            parse_mode="Markdown"
        )
        return
    fact = " ".join(context.args)
    user_name = update.message.from_user.first_name
    memory_save(fact, person="Both", category="Other", source=f"manual — {user_name}")
    await update.message.reply_text(f"🧠 Got it! Saved to memory:\n_{fact}_", parse_mode="Markdown")

async def forget_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await block_unauthorised(update, context): return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /forget <number>\nUse /memories to see the list with numbers.")
        return
    memories = memory_load_all()
    idx = int(context.args[0]) - 1
    # Flatten all memories in the same order as /memories shows them
    flat = []
    by_person = {}
    for m in memories:
        by_person.setdefault(m["person"], []).append(m)
    for person_mems in by_person.values():
        flat.extend(person_mems)
    if idx < 0 or idx >= len(flat):
        await update.message.reply_text("Invalid number. Use /memories to see the list.")
        return
    target = flat[idx]
    memory_forget(target["page_id"])
    await update.message.reply_text(f"🗑️ Removed from memory:\n_{target['fact']}_", parse_mode="Markdown")

# ── Voice message handler ─────────────────────────────────────────────────────

whisper_model = None

def get_whisper_model():
    global whisper_model
    if whisper_model is None:
        whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
    return whisper_model

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await block_unauthorised(update, context): return
    await update.message.reply_text("🎙️ Got your voice message, transcribing...")
    try:
        voice = update.message.voice or update.message.audio
        file = await context.bot.get_file(voice.file_id)

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp_path = tmp.name
        await file.download_to_drive(tmp_path)

        model = get_whisper_model()
        segments, _ = model.transcribe(tmp_path)
        transcription = " ".join(seg.text for seg in segments).strip()
        os.unlink(tmp_path)

        if not transcription:
            await update.message.reply_text("⚠️ Couldn't understand the audio. Please try again.")
            return

        await update.message.reply_text(f"📝 I heard: _{transcription}_", parse_mode="Markdown")
        increment_stat("voice_messages")

        # Process transcription through the main handler
        await process_text_message(update, context, transcription)

    except Exception as e:
        logging.error(f"Voice error: {e}")
        increment_stat("errors")
        await update.message.reply_text("⚠️ Couldn't process the voice message. Please try again.")

# ── Photo handler ─────────────────────────────────────────────────────────────

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await block_unauthorised(update, context): return
    await update.message.reply_text("🖼️ Got your photo, analysing...")
    try:
        import base64

        # Highest-res version is always the last in the array
        photo = update.message.photo[-1]
        file  = await context.bot.get_file(photo.file_id)

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = tmp.name
        await file.download_to_drive(tmp_path)

        with open(tmp_path, "rb") as f:
            image_b64 = base64.standard_b64encode(f.read()).decode("utf-8")
        os.unlink(tmp_path)

        user_name = update.message.from_user.first_name
        caption   = update.message.caption or ""
        prompt    = f"{user_name}: {caption}" if caption else f"{user_name}: [sent a photo]"

        # Build multi-modal content block for Claude
        image_content = [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64},
            },
            {"type": "text", "text": prompt},
        ]

        increment_stat("image_messages")
        await process_text_message(update, context, caption or "[photo]", image_content=image_content)

    except Exception as e:
        logging.error(f"Photo error: {e}")
        increment_stat("errors")
        await update.message.reply_text("⚠️ Couldn't process the photo. Please try again.")

# ── Main message handler ──────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await block_unauthorised(update, context): return
    chat_type = update.message.chat.type
    user_message = update.message.text
    bot_username = context.bot.username

    if chat_type in ["group", "supergroup"]:
        bot_mentioned = f"@{bot_username}" in user_message
        trigger_words = ["remind", "add task", "what do we", "can you", "please", "help",
                         "schedule", "need to", "have to", "don't forget", "todo", "to do",
                         "summarize", "summary", "shopping", "budget", "finance", "plan",
                         "what's on", "whats on", "calendar", "appointment", "book", "meeting",
                         "tomorrow", "tonight", "next week", "at ", "pm", "am",
                         "find", "search", "send me", "share", "file", "document", "photo",
                         "pdf", "invoice", "contract", "presentation", "where is"]
        triggered = any(w in user_message.lower() for w in trigger_words)
        if not bot_mentioned and not triggered:
            return
        user_message = user_message.replace(f"@{bot_username}", "").strip()

    await process_text_message(update, context, user_message)

async def process_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE, user_message: str, image_content=None):
    """Core logic: generates a response from text or image (used by text, voice, and photo handlers)."""
    user_name = update.message.from_user.first_name
    user_id   = update.effective_user.id
    save_chat_id(update.effective_chat.id)

    # ── Security layer 1: Prompt injection firewall ──
    matched = check_prompt_injection(user_message)
    if matched:
        increment_stat("errors")
        details = f"Blocked pattern: '{matched}' | Message: {user_message[:300]}"
        log_security_event("Injection Attempt", user_id, user_name, details)
        await send_security_alert(context.bot, "Injection Attempt", user_id, user_name, details)
        await update.message.reply_text(
            "⛔ That message looks like it's trying to manipulate me. I've logged this event.",
            parse_mode="Markdown"
        )
        return

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

    ideas = load_ideas()
    ideas_text = "\n".join([f"- [{i['type']}] {i['text']}" for i in ideas[-5:]]) if ideas else "No ideas yet."

    # Load long-term memories and format for system prompt
    memories = memory_load_all()
    memory_text = format_memories_for_context(memories)

    system = SYSTEM_PROMPT.format(
        datetime=datetime.now().strftime("%A, %B %d %Y at %I:%M %p"),
        tasks=task_text,
        shopping=shopping_text,
        ideas=ideas_text,
        events=event_text,
        memories=memory_text
    )

    conversation = load_conversation()

    if image_content:
        # For images: send the image + caption to Claude, but store only text in history
        api_message = {"role": "user", "content": image_content}
        history_message = {"role": "user", "content": f"{user_name}: [📷 photo] {user_message}"}
    else:
        api_message = {"role": "user", "content": f"{user_name}: {user_message}"}
        history_message = api_message

    # Build messages for API: prior history (text only) + current message
    api_messages = conversation + [api_message]
    conversation.append(history_message)

    response = with_retry(
        lambda: client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=system,
            messages=api_messages
        ),
        retries=3, delay=3, label="Claude API"
    )
    if response is None:
        await update.message.reply_text(
            "⚠️ I'm having trouble reaching my brain right now. Please try again in a moment.",
            parse_mode="Markdown"
        )
        increment_stat("errors")
        return
    reply = response.content[0].text

    # ── Security layer 2: Secret leak scanner ──
    reply_clean = scan_for_secrets(reply)
    if reply_clean != reply:
        details = "Claude's reply contained a secret-looking string — redacted before sending."
        log_security_event("Secret Leak Blocked", user_id, user_name, details)
        await send_security_alert(context.bot, "Secret Leak Blocked", user_id, user_name, details)
        reply = reply_clean

    logging.info(f"Claude reply: {reply[:200]}")

    # Track token usage
    increment_stat("messages")
    if hasattr(response, "usage") and response.usage:
        increment_stat("input_tokens",  response.usage.input_tokens)
        increment_stat("output_tokens", response.usage.output_tokens)

    conversation.append({"role": "assistant", "content": reply})
    save_conversation(conversation)

    # Check if Claude wants to search for a file
    file_match = re.search(r"<filesearch>(.*?)</filesearch>", reply, re.DOTALL)
    if file_match:
        try:
            file_data = json.loads(file_match.group(1).strip())
            # Handle both "query" and '"query"' keys (Claude sometimes wraps in extra quotes)
            query = file_data.get("query") or file_data.get('"query"', "")
            await update.message.reply_text(f"🔍 Searching your Mac for: *{query}*...", parse_mode="Markdown")
            found = search_files(query)
            if not found:
                await update.message.reply_text(f"😕 Couldn't find any files matching *{query}* in Desktop, Documents, Downloads, Pictures, Movies or Music.", parse_mode="Markdown")
            else:
                await update.message.reply_text(f"Found {len(found)} file(s)! Sending now...")
                for fpath in found:
                    size_mb = fpath.stat().st_size / (1024 * 1024)
                    if size_mb > 50:
                        await update.message.reply_text(f"⚠️ *{fpath.name}* is {size_mb:.1f}MB — too large to send via Telegram (50MB limit).\nPath: `{fpath}`", parse_mode="Markdown")
                    else:
                        with open(fpath, "rb") as f:
                            await update.message.reply_document(document=f, filename=fpath.name, caption=f"📎 {fpath.name}")
        except Exception as e:
            logging.error(f"File search error: {e}")
            await update.message.reply_text("⚠️ Something went wrong while searching for the file.")

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
                    notion_add_shopping(item, user_name)
                    increment_stat("shopping_added")
            save_shopping(shopping)
        except Exception as e:
            logging.error(f"Shopping error: {e}")
            increment_stat("errors")

    # Check if Claude wants to create a calendar event
    calendar_match = re.search(r"<calendar>(.*?)</calendar>", reply, re.DOTALL)
    # Handle specific task done via natural language
    taskdone_match = re.search(r"<taskdone>(.*?)</taskdone>", reply, re.DOTALL)
    if taskdone_match:
        try:
            data = json.loads(taskdone_match.group(1).strip())
            match = str(data.get("match", "")).strip()
            tasks = load_tasks()
            pending = [t for t in tasks if not t.get("done")]
            matched_task = None

            # Try matching by number
            if match.isdigit():
                idx = int(match) - 1
                if 0 <= idx < len(pending):
                    matched_task = pending[idx]["text"]
            else:
                # Match by keywords
                for t in pending:
                    if match.lower() in t["text"].lower() or t["text"].lower() in match.lower():
                        matched_task = t["text"]
                        break

            if matched_task:
                for t in tasks:
                    if t["text"] == matched_task:
                        t["done"] = True
                save_tasks(tasks)
                for nt in notion_get_tasks():
                    if nt["text"].lower() == matched_task.lower():
                        notion_complete_task(nt["page_id"])
                        break
        except Exception as e:
            logging.error(f"Task done error: {e}")

    # Handle specific shopping item bought via natural language
    shoppingdone_match = re.search(r"<shoppingdone>(.*?)</shoppingdone>", reply, re.DOTALL)
    if shoppingdone_match:
        try:
            data = json.loads(shoppingdone_match.group(1).strip())
            match = str(data.get("match", "")).strip()
            items = load_shopping()
            pending = [i for i in items if not i.get("bought")]
            matched_item = None
            if match.isdigit():
                idx = int(match) - 1
                if 0 <= idx < len(pending):
                    matched_item = pending[idx]["name"]
            else:
                for i in pending:
                    if match.lower() in i["name"].lower() or i["name"].lower() in match.lower():
                        matched_item = i["name"]
                        break
            if matched_item:
                for i in items:
                    if i["name"] == matched_item:
                        i["bought"] = True
                save_shopping(items)
                for ni in notion_get_shopping():
                    if ni["name"].lower() == matched_item.lower():
                        notion_bought_item(ni["page_id"])
                        break
        except Exception as e:
            logging.error(f"Shopping done error: {e}")

    # Handle specific idea done via natural language
    ideadone_match = re.search(r"<ideadone>(.*?)</ideadone>", reply, re.DOTALL)
    if ideadone_match:
        try:
            data = json.loads(ideadone_match.group(1).strip())
            match = str(data.get("match", "")).strip()
            ideas = load_ideas()
            matched_idea = None
            if match.isdigit():
                idx = int(match) - 1
                if 0 <= idx < len(ideas):
                    matched_idea = ideas[idx]["text"]
            else:
                for i in ideas:
                    if match.lower() in i["text"].lower() or i["text"].lower() in match.lower():
                        matched_idea = i["text"]
                        break
            if matched_idea:
                # Mark done in local ideas
                for i in ideas:
                    if i["text"] == matched_idea:
                        i["done"] = True
                save_ideas(ideas)
                # Archive in Notion
                pages = notion_query_db(NOTION_IDEAS_DB)
                for page in pages:
                    title = page["properties"].get("Name", {}).get("title", [])
                    name = title[0]["text"]["content"] if title else ""
                    if matched_idea.lower() in name.lower():
                        notion.pages.update(page_id=page["id"], properties={"Status": {"select": {"name": "Done"}}})
                        break
        except Exception as e:
            logging.error(f"Idea done error: {e}")

    # Handle mark all tasks done
    if re.search(r"<markalldone\s*/?>", reply, re.DOTALL):
        tasks = load_tasks()
        for t in tasks:
            t["done"] = True
        save_tasks(tasks)
        for nt in notion_get_tasks():
            notion_complete_task(nt["page_id"])

    # Handle mark all shopping bought
    if re.search(r"<markallbought\s*/?>", reply, re.DOTALL):
        items = load_shopping()
        for i in items:
            i["bought"] = True
        save_shopping(items)
        for ni in notion_get_shopping():
            notion_bought_item(ni["page_id"])

    # Handle idea capture
    idea_match = re.search(r"<idea>(.*?)</idea>", reply, re.DOTALL)
    if idea_match:
        try:
            idea_data = json.loads(idea_match.group(1).strip())
            title = idea_data.get("title", "")
            idea_type = idea_data.get("type", "Other")
            if title:
                ideas = load_ideas()
                ideas.append({"text": title, "type": idea_type, "added_by": user_name, "date": datetime.now().isoformat()})
                save_ideas(ideas)
                notion_add_idea(title, idea_type, user_name)
                increment_stat("ideas_added")
        except Exception as e:
            logging.error(f"Idea capture error: {e}")
            increment_stat("errors")

    # Handle explicit <remember> tag
    remember_match = re.search(r"<remember>(.*?)</remember>", reply, re.DOTALL)
    if remember_match:
        try:
            mem_data = json.loads(remember_match.group(1).strip())
            fact     = mem_data.get("fact", "").strip()
            person   = mem_data.get("person", "Both")
            category = mem_data.get("category", "Other")
            if fact and len(fact) > 3:
                memory_save(fact, person, category, source=f"explicit — {user_name}")
                logging.info(f"[MEMORY] Explicit save: {fact[:80]}")
        except Exception as e:
            logging.error(f"Remember tag error: {e}")

    clean_reply = re.sub(r"<idea>.*?</idea>", "", reply, flags=re.DOTALL)
    clean_reply = re.sub(r"<shopping>.*?</shopping>", "", clean_reply, flags=re.DOTALL)
    clean_reply = re.sub(r"<calendar>.*?</calendar>", "", clean_reply, flags=re.DOTALL)
    clean_reply = re.sub(r"<filesearch>.*?</filesearch>", "", clean_reply, flags=re.DOTALL)
    clean_reply = re.sub(r"<taskdone>.*?</taskdone>", "", clean_reply, flags=re.DOTALL)
    clean_reply = re.sub(r"<shoppingdone>.*?</shoppingdone>", "", clean_reply, flags=re.DOTALL)
    clean_reply = re.sub(r"<ideadone>.*?</ideadone>", "", clean_reply, flags=re.DOTALL)
    clean_reply = re.sub(r"<markalldone\s*/?>", "", clean_reply)
    clean_reply = re.sub(r"<markallbought\s*/?>", "", clean_reply)
    clean_reply = re.sub(r"<remember>.*?</remember>", "", clean_reply, flags=re.DOTALL).strip()

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
            increment_stat("calendar_events")
        except Exception as e:
            logging.error(f"Calendar error: {e}")
            increment_stat("errors")
            clean_reply += "\n\n⚠️ Couldn't create the calendar event. Try /calendar to check."

    # Auto-save tasks mentioned in message (skip if it's a shopping message)
    shopping_keywords = ["buy", "shopping", "groceries", "grocery", "supermarket", "get some", "pick up"]
    is_shopping_message = any(kw in user_message.lower() for kw in shopping_keywords)
    task_keywords = ["need to", "have to", "must", "don't forget", "todo", "to do"]
    if not is_shopping_message and any(kw in user_message.lower() for kw in task_keywords):
        for line in user_message.split('\n'):
            line = line.strip().lstrip('-•*123456789. ')
            if len(line) > 5 and line not in [t['text'] for t in tasks]:
                tasks.append({"text": line, "done": False, "added_by": user_name,
                               "date": datetime.now().isoformat()})
                notion_add_task(line, user_name)
                increment_stat("tasks_added")
        save_tasks(tasks)

    if clean_reply:
        try:
            await update.message.reply_text(clean_reply, parse_mode="Markdown")
        except Exception:
            # Fallback to plain text if Markdown parsing fails
            await update.message.reply_text(clean_reply)

    # Background memory extraction — runs after reply sent, doesn't block
    import asyncio as _asyncio
    convo_text = "\n".join(
        f"{m['role']}: {m['content']}" for m in conversation[-6:]
    )
    loop = _asyncio.get_event_loop()
    loop.run_in_executor(None, extract_and_save_memories, convo_text, user_name)

# ── Entry point ───────────────────────────────────────────────────────────────

async def send_daily_digest(context):
    """Sends 7 AM IST digest to all registered chats."""
    chat_ids = load_chat_ids()
    if not chat_ids:
        return

    now = datetime.now(IST).strftime("%A, %B %d %Y")

    # Tasks — read from Notion (source of truth, only Pending)
    notion_tasks = notion_get_tasks()
    tasks_text = "\n".join([f"  {i+1}. {t['text']}" for i, t in enumerate(notion_tasks)]) if notion_tasks else "  ✅ All clear!"

    # Shopping — read from Notion (only Needed)
    notion_shopping = notion_get_shopping()
    shopping_text = "\n".join([f"  • {s['name']}" for s in notion_shopping]) if notion_shopping else "  🛒 Nothing needed!"

    # Ideas — read from Notion
    notion_ideas = notion_get_ideas()
    recent_ideas = notion_ideas[-5:] if notion_ideas else []
    ideas_text = "\n".join([f"  💡 [{i['type']}] {i['text']}" for i in recent_ideas]) if recent_ideas else "  No new ideas yet!"

    # News
    headlines = get_news_headlines()
    news_text = "\n".join(headlines[:6]) if headlines else "  Could not fetch news."

    msg = (
        f"🌅 *Good morning, Abhishek & Alekya!*\n"
        f"_{now}_\n\n"
        f"📋 *Pending Tasks:*\n{tasks_text}\n\n"
        f"🛒 *Shopping List:*\n{shopping_text}\n\n"
        f"💡 *Recent Ideas:*\n{ideas_text}\n\n"
        f"📰 *Morning Headlines:*\n{news_text}"
    )

    for chat_id in chat_ids:
        try:
            await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")
        except Exception as e:
            logging.error(f"Digest send error for {chat_id}: {e}")

def notion_dedup_db(database_id: str, name_key: str = "Name"):
    """Remove duplicate entries from a Notion database, keeping the first occurrence."""
    try:
        pages = notion_query_db(database_id)
        seen = {}
        for page in pages:
            title = page["properties"].get(name_key, {}).get("title", [])
            name = title[0]["text"]["content"].lower().strip() if title else ""
            if name in seen:
                # Archive the duplicate
                payload = json.dumps({"archived": True}).encode()
                req = urllib.request.Request(
                    f"https://api.notion.com/v1/pages/{page['id']}",
                    data=payload, method="PATCH",
                    headers={
                        "Authorization": f"Bearer {NOTION_TOKEN}",
                        "Notion-Version": "2022-06-28",
                        "Content-Type": "application/json"
                    }
                )
                urllib.request.urlopen(req)
                logging.info(f"Removed duplicate: {name}")
            else:
                seen[name] = page["id"]
    except Exception as e:
        logging.error(f"Notion dedup error: {e}")

def background_notion_sync():
    """Silently sync local tasks and shopping to Notion, with dedup."""
    try:
        # Dedup first
        if NOTION_TASKS_DB:
            notion_dedup_db(NOTION_TASKS_DB)
        if NOTION_SHOPPING_DB:
            notion_dedup_db(NOTION_SHOPPING_DB)
        if NOTION_IDEAS_DB:
            notion_dedup_db(NOTION_IDEAS_DB)

        # Sync tasks
        tasks = load_tasks()
        existing_tasks = [t["text"].lower() for t in notion_get_tasks()]
        for task in tasks:
            if not task.get("done") and task["text"].lower() not in existing_tasks:
                notion_add_task(task["text"], task.get("added_by", "Mira"))

        # Sync shopping
        items = load_shopping()
        existing_items = [i["name"].lower() for i in notion_get_shopping()]
        for item in items:
            if not item.get("bought") and item["name"].lower() not in existing_items:
                notion_add_shopping(item["name"], item.get("added_by", "Mira"))

    except Exception as e:
        logging.error(f"Background Notion sync error: {e}")

async def _send_status_to_all(bot, text: str):
    """Send a status message to all registered chat IDs."""
    for chat_id in load_chat_ids():
        try:
            await bot.send_message(chat_id=chat_id, text=text)
        except Exception as e:
            logging.error(f"Status notify error for {chat_id}: {e}")

async def heartbeat_job(context):
    """Every 60s: update heartbeat file. On large gap, we just woke from sleep."""
    now = time.time()
    gap = None
    if os.path.exists(HEARTBEAT_FILE):
        try:
            with open(HEARTBEAT_FILE) as f:
                last = float(f.read().strip())
            gap = now - last
        except Exception:
            pass
    with open(HEARTBEAT_FILE, "w") as f:
        f.write(str(now))
    if gap is not None and gap > 300:  # 5+ minute gap = woke from sleep
        mins = int(gap / 60)
        await _send_status_to_all(
            context.bot,
            f"😴 Mira woke from sleep (Mac was asleep for ~{mins} min)"
        )

async def post_shutdown(app):
    """Runs after polling has stopped and Telegram connection is cleanly closed."""
    logging.info("[SHUTDOWN] Sending shutdown notification and syncing Notion...")
    # Use urllib (sync) — the async bot HTTP client is already closed at this point
    try:
        for chat_id in load_chat_ids():
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            data = urllib.parse.urlencode({"chat_id": chat_id, "text": "🔴 Mira is shutting down"}).encode()
            urllib.request.urlopen(url, data=data, timeout=5)
    except Exception as e:
        logging.error(f"[SHUTDOWN] Notify failed: {e}")
    try:
        background_notion_sync()
        logging.info("[SHUTDOWN] State synced to Notion. Goodbye.")
    except Exception as e:
        logging.error(f"[SHUTDOWN] Sync failed: {e}")

async def post_init(app):
    # Notify all chats that Mira is online
    await _send_status_to_all(app.bot, "🟢 Mira is online")
    # Seed heartbeat so the job has a baseline
    with open(HEARTBEAT_FILE, "w") as f:
        f.write(str(time.time()))

    await app.bot.set_my_commands([
        ("memories",   "Show everything Mira remembers about the family"),
        ("remember",   "Save a fact — /remember Alekya is allergic to shellfish"),
        ("forget",     "Remove a memory — /forget 3"),
        ("digest",     "Morning digest: tasks, shopping, ideas & markets"),
        ("tasks",      "Show all pending tasks"),
        ("add",        "Add a task — /add Buy groceries"),
        ("done",       "Mark a task done — /done 1"),
        ("clear",      "Clear all completed tasks"),
        ("shopping",   "Show shopping list"),
        ("bought",     "Mark item bought — /bought 1"),
        ("clearshop",  "Clear all bought items from shopping list"),
        ("calendar",   "Show upcoming calendar events"),
        ("find",       "Search a file on MacBook — /find filename"),
        ("costreport", "Today's Claude API cost and token usage"),
        ("eval",       "Today's Mira activity report and health check"),
        ("status",     "Live status: uptime, integrations, today's activity"),
        ("notion",     "Force sync to Notion"),
        ("myid",       "Show your Telegram user ID"),
    ])

def main():
    # ── Security layer 3: Boot health check ──
    logging.info("Running boot health check...")
    run_boot_health_check()

    # Sync to Notion on startup
    # Run startup Notion sync in a background thread so it never blocks bot startup
    import threading
    logging.info("Syncing to Notion on startup (background)...")
    threading.Thread(target=background_notion_sync, daemon=True).start()

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(post_init).post_shutdown(post_shutdown).build()

    # Schedule daily digest at 7:00 AM IST (01:30 UTC)
    app.job_queue.run_daily(
        send_daily_digest,
        time=datetime.now(IST).replace(hour=7, minute=0, second=0, microsecond=0).timetz()
    )
    # Heartbeat: updates every 60s, detects wake from sleep via time gap
    app.job_queue.run_repeating(heartbeat_job, interval=60, first=60)
    app.add_handler(CommandHandler("start",    start))
    app.add_handler(CommandHandler("myid",     myid_command))
    app.add_handler(CommandHandler("find",     find_command))
    app.add_handler(CommandHandler("notion",   notion_command))
    app.add_handler(CommandHandler("digest",   digest_command))
    app.add_handler(CommandHandler("tasks",    tasks_command))
    app.add_handler(CommandHandler("calendar", calendar_command))
    app.add_handler(CommandHandler("done",     done_command))
    app.add_handler(CommandHandler("clear",    clear_command))
    app.add_handler(CommandHandler("add",       add_command))
    app.add_handler(CommandHandler("shopping",    shopping_command))
    app.add_handler(CommandHandler("bought",      bought_command))
    app.add_handler(CommandHandler("clearshop",   clearshop_command))
    app.add_handler(CommandHandler("memories",    memories_command))
    app.add_handler(CommandHandler("remember",    remember_command))
    app.add_handler(CommandHandler("forget",      forget_command))
    app.add_handler(CommandHandler("costreport",  costreport_command))
    app.add_handler(CommandHandler("eval",        eval_command))
    app.add_handler(CommandHandler("status",      status_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))


    print("🤖 Mira is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
