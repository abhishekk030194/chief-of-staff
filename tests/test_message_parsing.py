"""
Tests for the XML tag parsing logic used in process_text_message().

Rather than spinning up a full async Telegram handler, we test the regex
extraction and JSON parsing patterns that bot.py applies to Claude's replies.
These are pure-Python operations with no external dependencies.

Functions/patterns under test:
  - <shopping> tag extraction and deduplication
  - <taskdone> tag: keyword match vs numeric match
  - <shoppingdone> tag: keyword match vs numeric match
  - <idea> tag: extraction of title and type
  - <calendar> tag: date + time parsing via strptime
  - <markalldone /> and <markallbought /> self-closing tags
  - Clean-reply stripping: all XML tags removed from text sent to the user
"""
import json
import re
from datetime import datetime

import pytest


# ── Helpers that mirror the logic in bot.py ───────────────────────────────────
# These replicate the exact regex patterns used in process_text_message so that
# tests exercise the real parsing behaviour.

def extract_tag(tag: str, text: str):
    """Return the inner content of the first <tag>...</tag> block, or None."""
    m = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.DOTALL)
    return m.group(1).strip() if m else None


def extract_self_closing(tag: str, text: str) -> bool:
    """Return True if a self-closing <tag /> or <tag> appears in text."""
    return bool(re.search(rf"<{tag}\s*/?>", text, re.DOTALL))


def strip_all_xml_tags(reply: str) -> str:
    """Replicate the clean_reply stripping logic from process_text_message."""
    clean = re.sub(r"<idea>.*?</idea>", "", reply, flags=re.DOTALL)
    clean = re.sub(r"<shopping>.*?</shopping>", "", clean, flags=re.DOTALL)
    clean = re.sub(r"<calendar>.*?</calendar>", "", clean, flags=re.DOTALL)
    clean = re.sub(r"<filesearch>.*?</filesearch>", "", clean, flags=re.DOTALL)
    clean = re.sub(r"<taskdone>.*?</taskdone>", "", clean, flags=re.DOTALL)
    clean = re.sub(r"<shoppingdone>.*?</shoppingdone>", "", clean, flags=re.DOTALL)
    clean = re.sub(r"<ideadone>.*?</ideadone>", "", clean, flags=re.DOTALL)
    clean = re.sub(r"<markalldone\s*/?>", "", clean)
    clean = re.sub(r"<markallbought\s*/?>", "", clean).strip()
    return clean


def resolve_task_match(match_str: str, pending_tasks: list):
    """Mirror the taskdone resolution logic from process_text_message."""
    if match_str.isdigit():
        idx = int(match_str) - 1
        if 0 <= idx < len(pending_tasks):
            return pending_tasks[idx]["text"]
        return None
    for t in pending_tasks:
        if match_str.lower() in t["text"].lower() or t["text"].lower() in match_str.lower():
            return t["text"]
    return None


def resolve_shopping_match(match_str: str, pending_items: list):
    """Mirror the shoppingdone resolution logic from process_text_message."""
    if match_str.isdigit():
        idx = int(match_str) - 1
        if 0 <= idx < len(pending_items):
            return pending_items[idx]["name"]
        return None
    for i in pending_items:
        if match_str.lower() in i["name"].lower() or i["name"].lower() in match_str.lower():
            return i["name"]
    return None


# ── <shopping> tag ────────────────────────────────────────────────────────────

class TestShoppingTagExtraction:
    def test_extracts_item_list_from_reply(self):
        reply = 'Sure! <shopping>["Eggs", "Bread", "Milk"]</shopping>'
        raw = extract_tag("shopping", reply)
        items = json.loads(raw)
        assert items == ["Eggs", "Bread", "Milk"]

    def test_returns_none_when_tag_absent(self):
        reply = "Here are your tasks."
        assert extract_tag("shopping", reply) is None

    def test_deduplication_does_not_add_existing_item(self):
        existing = [{"name": "milk", "bought": False}]
        new_items = ["Milk", "Eggs"]  # "Milk" duplicates "milk" (case-insensitive)
        existing_lower = [i["name"].lower() for i in existing]
        added = [item for item in new_items if item.lower() not in existing_lower]
        assert added == ["Eggs"]

    def test_all_new_items_added_when_no_duplicates(self):
        existing = []
        new_items = ["Butter", "Cheese"]
        existing_lower = [i["name"].lower() for i in existing]
        added = [item for item in new_items if item.lower() not in existing_lower]
        assert added == ["Butter", "Cheese"]

    def test_single_item_list(self):
        reply = 'Got it. <shopping>["Tomatoes"]</shopping>'
        raw = extract_tag("shopping", reply)
        assert json.loads(raw) == ["Tomatoes"]

    def test_multiline_tag_content(self):
        reply = 'Adding items:\n<shopping>\n["Apples", "Oranges"]\n</shopping>'
        raw = extract_tag("shopping", reply)
        assert json.loads(raw) == ["Apples", "Oranges"]


# ── <taskdone> tag ────────────────────────────────────────────────────────────

class TestTaskdoneResolution:
    PENDING = [
        {"text": "Call the dentist", "done": False},
        {"text": "Pay electricity bill", "done": False},
        {"text": "Book flight tickets", "done": False},
    ]

    def test_numeric_match_first_task(self):
        assert resolve_task_match("1", self.PENDING) == "Call the dentist"

    def test_numeric_match_second_task(self):
        assert resolve_task_match("2", self.PENDING) == "Pay electricity bill"

    def test_numeric_match_third_task(self):
        assert resolve_task_match("3", self.PENDING) == "Book flight tickets"

    def test_numeric_match_out_of_range_returns_none(self):
        assert resolve_task_match("99", self.PENDING) is None

    def test_keyword_match_partial_text(self):
        assert resolve_task_match("dentist", self.PENDING) == "Call the dentist"

    def test_keyword_match_case_insensitive(self):
        assert resolve_task_match("DENTIST", self.PENDING) == "Call the dentist"

    def test_keyword_match_middle_word(self):
        assert resolve_task_match("electricity", self.PENDING) == "Pay electricity bill"

    def test_keyword_no_match_returns_none(self):
        assert resolve_task_match("grocery", self.PENDING) is None

    def test_empty_pending_list_returns_none(self):
        assert resolve_task_match("1", []) is None

    def test_taskdone_tag_parsed_correctly(self):
        reply = '<taskdone>{"match": "dentist"}</taskdone>'
        raw = extract_tag("taskdone", reply)
        data = json.loads(raw)
        assert data["match"] == "dentist"

    def test_taskdone_numeric_parsed_correctly(self):
        reply = '<taskdone>{"match": "2"}</taskdone>'
        raw = extract_tag("taskdone", reply)
        data = json.loads(raw)
        assert data["match"] == "2"


# ── <shoppingdone> tag ────────────────────────────────────────────────────────

class TestShoppingdoneResolution:
    PENDING = [
        {"name": "Milk", "bought": False},
        {"name": "Eggs", "bought": False},
        {"name": "Bread", "bought": False},
    ]

    def test_numeric_match_first_item(self):
        assert resolve_shopping_match("1", self.PENDING) == "Milk"

    def test_numeric_match_second_item(self):
        assert resolve_shopping_match("2", self.PENDING) == "Eggs"

    def test_numeric_out_of_range_returns_none(self):
        assert resolve_shopping_match("10", self.PENDING) is None

    def test_keyword_match(self):
        assert resolve_shopping_match("milk", self.PENDING) == "Milk"

    def test_keyword_match_case_insensitive(self):
        assert resolve_shopping_match("EGGS", self.PENDING) == "Eggs"

    def test_keyword_no_match_returns_none(self):
        assert resolve_shopping_match("butter", self.PENDING) is None

    def test_shoppingdone_tag_parsed_correctly(self):
        reply = '<shoppingdone>{"match": "milk"}</shoppingdone>'
        raw = extract_tag("shoppingdone", reply)
        data = json.loads(raw)
        assert data["match"] == "milk"


# ── <idea> tag ────────────────────────────────────────────────────────────────

class TestIdeaTagExtraction:
    def test_extracts_title_and_type(self):
        reply = 'Great idea! <idea>{"title": "AI meal planner", "type": "AI Product"}</idea>'
        raw = extract_tag("idea", reply)
        data = json.loads(raw)
        assert data["title"] == "AI meal planner"
        assert data["type"] == "AI Product"

    def test_extracts_blog_idea(self):
        reply = '<idea>{"title": "Parenting tips blog", "type": "Blog Idea"}</idea>'
        raw = extract_tag("idea", reply)
        data = json.loads(raw)
        assert data["type"] == "Blog Idea"

    def test_returns_none_when_absent(self):
        assert extract_tag("idea", "No ideas here.") is None

    def test_title_is_non_empty(self):
        reply = '<idea>{"title": "Voice-first finance tracker", "type": "AI Product"}</idea>'
        raw = extract_tag("idea", reply)
        data = json.loads(raw)
        assert len(data["title"]) > 0


# ── <calendar> tag ────────────────────────────────────────────────────────────

class TestCalendarTagParsing:
    def test_valid_date_and_time_parse(self):
        date_str = "2025-06-15"
        time_str = "14:30"
        dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        assert dt.year == 2025
        assert dt.month == 6
        assert dt.day == 15
        assert dt.hour == 14
        assert dt.minute == 30

    def test_calendar_tag_extracts_all_fields(self):
        reply = (
            'I will schedule that.\n'
            '<calendar>{"title": "Dentist", "date": "2025-07-01", "time": "10:00", "description": "Regular checkup"}</calendar>'
        )
        raw = extract_tag("calendar", reply)
        data = json.loads(raw)
        assert data["title"] == "Dentist"
        assert data["date"] == "2025-07-01"
        assert data["time"] == "10:00"
        assert data["description"] == "Regular checkup"

    def test_calendar_tag_absent_returns_none(self):
        assert extract_tag("calendar", "No event here.") is None

    def test_default_time_is_parseable(self):
        # Default time used in bot is "09:00"
        datetime.strptime("2025-01-01 09:00", "%Y-%m-%d %H:%M")

    def test_midnight_time_is_parseable(self):
        datetime.strptime("2025-12-31 00:00", "%Y-%m-%d %H:%M")

    def test_end_of_day_time_is_parseable(self):
        datetime.strptime("2025-12-31 23:59", "%Y-%m-%d %H:%M")


# ── <markalldone /> and <markallbought /> ─────────────────────────────────────

class TestSelfClosingTags:
    def test_markalldone_self_closing_detected(self):
        reply = "All tasks cleared! <markalldone />"
        assert extract_self_closing("markalldone", reply) is True

    def test_markalldone_open_close_detected(self):
        reply = "Done! <markalldone></markalldone>"
        assert extract_self_closing("markalldone", reply) is True

    def test_markallbought_self_closing_detected(self):
        reply = "Shopping complete! <markallbought />"
        assert extract_self_closing("markallbought", reply) is True

    def test_markalldone_absent_returns_false(self):
        assert extract_self_closing("markalldone", "Just a normal reply.") is False

    def test_markallbought_absent_returns_false(self):
        assert extract_self_closing("markallbought", "Just a normal reply.") is False


# ── Clean reply stripping ─────────────────────────────────────────────────────

class TestCleanReplyStripping:
    def test_shopping_tag_removed_from_reply(self):
        reply = 'Adding items! <shopping>["Milk"]</shopping>'
        clean = strip_all_xml_tags(reply)
        assert "<shopping>" not in clean
        assert "</shopping>" not in clean
        assert "Milk" not in clean

    def test_calendar_tag_removed_from_reply(self):
        reply = 'Scheduled! <calendar>{"title":"Meeting","date":"2025-01-01","time":"09:00"}</calendar>'
        clean = strip_all_xml_tags(reply)
        assert "<calendar>" not in clean

    def test_taskdone_tag_removed_from_reply(self):
        reply = 'Marked done! <taskdone>{"match": "dentist"}</taskdone>'
        clean = strip_all_xml_tags(reply)
        assert "<taskdone>" not in clean

    def test_shoppingdone_tag_removed(self):
        reply = 'Got it! <shoppingdone>{"match": "milk"}</shoppingdone>'
        clean = strip_all_xml_tags(reply)
        assert "<shoppingdone>" not in clean

    def test_ideadone_tag_removed(self):
        reply = 'Idea archived! <ideadone>{"match": "AI planner"}</ideadone>'
        clean = strip_all_xml_tags(reply)
        assert "<ideadone>" not in clean

    def test_idea_tag_removed(self):
        reply = 'Saved! <idea>{"title": "New startup", "type": "Business Idea"}</idea>'
        clean = strip_all_xml_tags(reply)
        assert "<idea>" not in clean

    def test_filesearch_tag_removed(self):
        reply = 'Searching... <filesearch>{"query": "invoice"}</filesearch>'
        clean = strip_all_xml_tags(reply)
        assert "<filesearch>" not in clean

    def test_markalldone_removed(self):
        reply = "All done! <markalldone />"
        clean = strip_all_xml_tags(reply)
        assert "<markalldone" not in clean

    def test_markallbought_removed(self):
        reply = "All bought! <markallbought />"
        clean = strip_all_xml_tags(reply)
        assert "<markallbought" not in clean

    def test_visible_text_preserved_after_stripping(self):
        reply = 'Great, I have added milk to the list! <shopping>["Milk"]</shopping>'
        clean = strip_all_xml_tags(reply)
        assert "Great, I have added milk to the list!" in clean

    def test_multiple_tags_all_stripped(self):
        reply = (
            "Adding to list! <shopping>[\"Eggs\"]</shopping> "
            "And scheduled: <calendar>{\"title\":\"Meeting\",\"date\":\"2025-01-01\",\"time\":\"09:00\"}</calendar>"
        )
        clean = strip_all_xml_tags(reply)
        assert "<shopping>" not in clean
        assert "<calendar>" not in clean

    def test_reply_with_no_tags_unchanged(self):
        reply = "Here are your pending tasks: call the dentist, pay rent."
        assert strip_all_xml_tags(reply) == reply

    def test_empty_reply_returns_empty(self):
        assert strip_all_xml_tags("") == ""
