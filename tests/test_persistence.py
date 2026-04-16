"""
Tests for all persistence helper functions in bot.py:
  - load_tasks / save_tasks
  - load_shopping / save_shopping
  - load_conversation / save_conversation  (including 20-message truncation)
  - load_ideas / save_ideas
  - load_chat_ids / save_chat_id  (including deduplication)
"""
import json
import os

import pytest

import bot


# ── tasks ─────────────────────────────────────────────────────────────────────

class TestTasks:
    def test_load_tasks_returns_empty_list_when_no_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert bot.load_tasks() == []

    def test_save_then_load_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        tasks = [
            {"text": "Buy milk", "done": False, "added_by": "Abhishek"},
            {"text": "Call dentist", "done": True, "added_by": "Alekya"},
        ]
        bot.save_tasks(tasks)
        assert bot.load_tasks() == tasks

    def test_save_overwrites_existing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        bot.save_tasks([{"text": "Old task", "done": False}])
        new_tasks = [{"text": "New task", "done": False}]
        bot.save_tasks(new_tasks)
        assert bot.load_tasks() == new_tasks

    def test_save_tasks_writes_valid_json(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        tasks = [{"text": "Test", "done": False}]
        bot.save_tasks(tasks)
        with open(bot.TASKS_FILE) as f:
            data = json.load(f)
        assert data == tasks

    def test_load_tasks_empty_list_in_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        bot.save_tasks([])
        assert bot.load_tasks() == []


# ── shopping ──────────────────────────────────────────────────────────────────

class TestShopping:
    def test_load_shopping_returns_empty_list_when_no_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert bot.load_shopping() == []

    def test_save_then_load_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        items = [
            {"name": "Eggs", "bought": False, "added_by": "Alekya"},
            {"name": "Bread", "bought": True, "added_by": "Abhishek"},
        ]
        bot.save_shopping(items)
        assert bot.load_shopping() == items

    def test_save_overwrites_existing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        bot.save_shopping([{"name": "Milk", "bought": False}])
        new_items = [{"name": "Butter", "bought": False}]
        bot.save_shopping(new_items)
        assert bot.load_shopping() == new_items

    def test_save_shopping_writes_valid_json(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        items = [{"name": "Tomatoes", "bought": False}]
        bot.save_shopping(items)
        with open(bot.SHOPPING_FILE) as f:
            data = json.load(f)
        assert data == items


# ── conversation ──────────────────────────────────────────────────────────────

class TestConversation:
    def test_load_conversation_returns_empty_list_when_no_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert bot.load_conversation() == []

    def test_save_then_load_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        msgs = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        bot.save_conversation(msgs)
        assert bot.load_conversation() == msgs

    def test_truncated_to_last_20_messages(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        msgs = [{"role": "user", "content": str(i)} for i in range(25)]
        bot.save_conversation(msgs)
        loaded = bot.load_conversation()
        assert len(loaded) == 20

    def test_truncation_keeps_most_recent_messages(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        msgs = [{"role": "user", "content": str(i)} for i in range(25)]
        bot.save_conversation(msgs)
        loaded = bot.load_conversation()
        # Should keep messages 5–24 (the last 20)
        assert loaded[0]["content"] == "5"
        assert loaded[-1]["content"] == "24"

    def test_exactly_20_messages_not_truncated(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        msgs = [{"role": "user", "content": str(i)} for i in range(20)]
        bot.save_conversation(msgs)
        assert len(bot.load_conversation()) == 20

    def test_fewer_than_20_messages_not_truncated(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        msgs = [{"role": "user", "content": str(i)} for i in range(5)]
        bot.save_conversation(msgs)
        assert len(bot.load_conversation()) == 5


# ── ideas ─────────────────────────────────────────────────────────────────────

class TestIdeas:
    def test_load_ideas_returns_empty_list_when_no_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert bot.load_ideas() == []

    def test_save_then_load_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        ideas = [
            {"text": "AI meal planner", "type": "AI Product", "added_by": "Abhishek"},
            {"text": "Blog about parenting", "type": "Blog Idea", "added_by": "Alekya"},
        ]
        bot.save_ideas(ideas)
        assert bot.load_ideas() == ideas

    def test_save_overwrites_existing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        bot.save_ideas([{"text": "Old idea", "type": "Other"}])
        new_ideas = [{"text": "New idea", "type": "Business Idea"}]
        bot.save_ideas(new_ideas)
        assert bot.load_ideas() == new_ideas


# ── chat ids ──────────────────────────────────────────────────────────────────

class TestChatIds:
    def test_load_chat_ids_returns_empty_list_when_no_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert bot.load_chat_ids() == []

    def test_save_chat_id_persists(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        bot.save_chat_id(12345)
        assert 12345 in bot.load_chat_ids()

    def test_save_chat_id_no_duplicates(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        bot.save_chat_id(12345)
        bot.save_chat_id(12345)
        bot.save_chat_id(12345)
        ids = bot.load_chat_ids()
        assert ids.count(12345) == 1

    def test_multiple_different_ids_all_saved(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        bot.save_chat_id(111)
        bot.save_chat_id(222)
        bot.save_chat_id(333)
        ids = bot.load_chat_ids()
        assert 111 in ids
        assert 222 in ids
        assert 333 in ids

    def test_mix_of_new_and_duplicate_ids(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        bot.save_chat_id(100)
        bot.save_chat_id(200)
        bot.save_chat_id(100)  # duplicate
        ids = bot.load_chat_ids()
        assert len(ids) == 2
        assert ids.count(100) == 1
