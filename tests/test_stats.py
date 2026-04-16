"""
Tests for the daily-stats tracking functions in bot.py:
  - _today()
  - load_stats / save_stats
  - get_today_stats()  — creates entry with all expected keys
  - increment_stat()   — creates day entry if absent; accumulates correctly
"""
import json
from datetime import datetime

import pytest
import pytz

import bot


IST = pytz.timezone("Asia/Kolkata")

EXPECTED_STAT_KEYS = {
    "messages",
    "voice_messages",
    "input_tokens",
    "output_tokens",
    "tasks_added",
    "shopping_added",
    "ideas_added",
    "calendar_events",
    "errors",
}


# ── _today ────────────────────────────────────────────────────────────────────

class TestToday:
    def test_returns_string_in_yyyy_mm_dd_format(self):
        today = bot._today()
        # Must match YYYY-MM-DD
        datetime.strptime(today, "%Y-%m-%d")  # raises if format wrong

    def test_matches_current_ist_date(self):
        expected = datetime.now(IST).strftime("%Y-%m-%d")
        assert bot._today() == expected


# ── load_stats / save_stats ───────────────────────────────────────────────────

class TestLoadSaveStats:
    def test_load_stats_returns_empty_dict_when_no_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert bot.load_stats() == {}

    def test_save_then_load_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        data = {"2025-01-01": {"messages": 5, "errors": 0}}
        bot.save_stats(data)
        assert bot.load_stats() == data

    def test_save_stats_writes_valid_json(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        data = {"2025-06-15": {"messages": 3}}
        bot.save_stats(data)
        with open(bot.STATS_FILE) as f:
            loaded = json.load(f)
        assert loaded == data


# ── get_today_stats ───────────────────────────────────────────────────────────

class TestGetTodayStats:
    def test_creates_entry_for_today_when_absent(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        stats = bot.get_today_stats()
        today = bot._today()
        assert today in bot.load_stats()

    def test_returns_dict_with_all_expected_keys(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        stats = bot.get_today_stats()
        assert EXPECTED_STAT_KEYS == set(stats.keys())

    def test_all_initial_values_are_zero(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        stats = bot.get_today_stats()
        for key in EXPECTED_STAT_KEYS:
            assert stats[key] == 0, f"Expected {key}=0, got {stats[key]}"

    def test_returns_same_entry_on_second_call(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        bot.get_today_stats()
        # Manually increment messages on disk
        s = bot.load_stats()
        s[bot._today()]["messages"] = 7
        bot.save_stats(s)
        # Second call should return the existing entry, not reset it
        stats = bot.get_today_stats()
        assert stats["messages"] == 7


# ── increment_stat ────────────────────────────────────────────────────────────

class TestIncrementStat:
    def test_increments_messages_by_one(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        bot.increment_stat("messages")
        assert bot.get_today_stats()["messages"] == 1

    def test_increments_by_custom_amount(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        bot.increment_stat("input_tokens", 512)
        assert bot.get_today_stats()["input_tokens"] == 512

    def test_accumulates_across_multiple_calls(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        bot.increment_stat("messages", 3)
        bot.increment_stat("messages", 5)
        assert bot.get_today_stats()["messages"] == 8

    def test_creates_day_entry_if_absent(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # Stats file doesn't exist yet
        bot.increment_stat("errors")
        assert bot.get_today_stats()["errors"] == 1

    def test_increments_errors_stat(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        bot.increment_stat("errors")
        bot.increment_stat("errors")
        assert bot.get_today_stats()["errors"] == 2

    def test_increments_multiple_different_stats_independently(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        bot.increment_stat("messages", 2)
        bot.increment_stat("tasks_added", 3)
        bot.increment_stat("calendar_events", 1)
        stats = bot.get_today_stats()
        assert stats["messages"] == 2
        assert stats["tasks_added"] == 3
        assert stats["calendar_events"] == 1
        assert stats["errors"] == 0  # untouched
