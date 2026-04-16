"""
Tests for the with_retry() helper in bot.py.

Covers:
  - Immediate success (no retries needed)
  - Returns None after all retries are exhausted
  - Retries the correct number of times before giving up
  - Succeeds on a subsequent attempt after initial failures
  - Delay argument of 0 keeps tests fast (no real sleeping)
"""
import pytest

import bot


class TestWithRetry:
    def test_returns_result_on_first_success(self):
        result = bot.with_retry(lambda: 42, retries=3, delay=0)
        assert result == 42

    def test_returns_none_after_all_retries_exhausted(self):
        def always_fails():
            raise RuntimeError("boom")

        result = bot.with_retry(always_fails, retries=3, delay=0)
        assert result is None

    def test_calls_function_exactly_retries_times_on_failure(self):
        calls = []

        def always_fails():
            calls.append(1)
            raise RuntimeError("fail")

        bot.with_retry(always_fails, retries=3, delay=0)
        assert len(calls) == 3

    def test_succeeds_on_second_attempt(self):
        calls = []

        def fail_once():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("first call fails")
            return "ok"

        result = bot.with_retry(fail_once, retries=3, delay=0)
        assert result == "ok"
        assert len(calls) == 2

    def test_succeeds_on_third_attempt(self):
        calls = []

        def fail_twice():
            calls.append(1)
            if len(calls) < 3:
                raise RuntimeError("not yet")
            return "success"

        result = bot.with_retry(fail_twice, retries=3, delay=0)
        assert result == "success"
        assert len(calls) == 3

    def test_returns_none_with_retries_equal_one(self):
        """A single attempt that fails should return None immediately."""
        calls = []

        def always_fails():
            calls.append(1)
            raise RuntimeError("fail")

        result = bot.with_retry(always_fails, retries=1, delay=0)
        assert result is None
        assert len(calls) == 1

    def test_returns_none_for_different_exception_types(self):
        def raises_value_error():
            raise ValueError("bad value")

        assert bot.with_retry(raises_value_error, retries=2, delay=0) is None

    def test_passes_return_value_correctly(self):
        data = {"key": "value", "number": 99}
        result = bot.with_retry(lambda: data, retries=3, delay=0)
        assert result == data

    def test_does_not_retry_after_success(self):
        calls = []

        def succeed():
            calls.append(1)
            return "done"

        bot.with_retry(succeed, retries=5, delay=0)
        assert len(calls) == 1
