"""Tests for the cron expression evaluator."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from llm_wiki.cron import (
    CronExpression,
    CronField,
    is_valid_cron,
    next_cron_time,
    parse_cron,
    _parse_field,
)


# ---------------------------------------------------------------------------
# Field parsing
# ---------------------------------------------------------------------------


class TestParseField:
    def test_star(self):
        f = _parse_field("*", 0, 59)
        assert f.values == frozenset(range(0, 60))

    def test_single_value(self):
        f = _parse_field("15", 0, 59)
        assert f.values == frozenset([15])

    def test_list(self):
        f = _parse_field("0,15,30,45", 0, 59)
        assert f.values == frozenset([0, 15, 30, 45])

    def test_range(self):
        f = _parse_field("1-5", 0, 6)
        assert f.values == frozenset([1, 2, 3, 4, 5])

    def test_step(self):
        f = _parse_field("*/15", 0, 59)
        assert f.values == frozenset([0, 15, 30, 45])

    def test_range_with_step(self):
        f = _parse_field("0-23/2", 0, 23)
        assert f.values == frozenset(range(0, 24, 2))

    def test_value_with_step(self):
        # "5/10" means starting from 5, every 10
        f = _parse_field("5/10", 0, 59)
        assert f.values == frozenset([5, 15, 25, 35, 45, 55])

    def test_out_of_range_raises(self):
        with pytest.raises(ValueError, match="out of bounds"):
            _parse_field("60", 0, 59)

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError, match="Invalid value"):
            _parse_field("abc", 0, 59)

    def test_invalid_step_raises(self):
        with pytest.raises(ValueError, match="Invalid step"):
            _parse_field("*/abc", 0, 59)

    def test_zero_step_raises(self):
        with pytest.raises(ValueError, match="Step must be >= 1"):
            _parse_field("*/0", 0, 59)

    def test_combined_list_and_range(self):
        f = _parse_field("1,5-8,15", 0, 59)
        assert f.values == frozenset([1, 5, 6, 7, 8, 15])


# ---------------------------------------------------------------------------
# Expression parsing
# ---------------------------------------------------------------------------


class TestParseCron:
    def test_every_minute(self):
        cron = parse_cron("* * * * *")
        assert cron.minute.values == frozenset(range(0, 60))
        assert cron.hour.values == frozenset(range(0, 24))

    def test_every_30_minutes(self):
        cron = parse_cron("*/30 * * * *")
        assert cron.minute.values == frozenset([0, 30])

    def test_weekdays_at_9am(self):
        cron = parse_cron("0 9 * * 1-5")
        assert cron.minute.values == frozenset([0])
        assert cron.hour.values == frozenset([9])
        assert cron.dow.values == frozenset([1, 2, 3, 4, 5])

    def test_first_of_month(self):
        cron = parse_cron("0 0 1 * *")
        assert cron.day.values == frozenset([1])

    def test_too_few_fields_raises(self):
        with pytest.raises(ValueError, match="5 fields"):
            parse_cron("* * *")

    def test_too_many_fields_raises(self):
        with pytest.raises(ValueError, match="5 fields"):
            parse_cron("* * * * * *")

    def test_raw_preserved(self):
        cron = parse_cron("  */15 * * * *  ")
        assert cron.raw == "*/15 * * * *"


class TestIsValidCron:
    def test_valid(self):
        assert is_valid_cron("*/15 * * * *") is True
        assert is_valid_cron("0 9 * * 1-5") is True

    def test_invalid(self):
        assert is_valid_cron("bad cron") is False
        assert is_valid_cron("") is False


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


class TestCronMatches:
    def test_every_minute_matches_all(self):
        cron = parse_cron("* * * * *")
        dt = datetime(2024, 6, 15, 14, 30, tzinfo=timezone.utc)
        assert cron.matches(dt) is True

    def test_specific_minute(self):
        cron = parse_cron("30 * * * *")
        assert cron.matches(datetime(2024, 1, 1, 10, 30, tzinfo=timezone.utc)) is True
        assert cron.matches(datetime(2024, 1, 1, 10, 15, tzinfo=timezone.utc)) is False

    def test_weekday_match(self):
        # 2024-06-17 is Monday (weekday=0, cron dow=1)
        cron = parse_cron("0 9 * * 1")
        monday = datetime(2024, 6, 17, 9, 0, tzinfo=timezone.utc)
        tuesday = datetime(2024, 6, 18, 9, 0, tzinfo=timezone.utc)
        assert cron.matches(monday) is True
        assert cron.matches(tuesday) is False

    def test_sunday_is_0(self):
        # 2024-06-16 is Sunday
        cron = parse_cron("0 0 * * 0")
        sunday = datetime(2024, 6, 16, 0, 0, tzinfo=timezone.utc)
        assert cron.matches(sunday) is True

    def test_specific_month(self):
        cron = parse_cron("0 0 1 6 *")  # June 1st
        assert cron.matches(datetime(2024, 6, 1, 0, 0, tzinfo=timezone.utc)) is True
        assert cron.matches(datetime(2024, 7, 1, 0, 0, tzinfo=timezone.utc)) is False


# ---------------------------------------------------------------------------
# Next run computation
# ---------------------------------------------------------------------------


class TestNextRun:
    def test_every_15_minutes(self):
        cron = parse_cron("*/15 * * * *")
        after = datetime(2024, 1, 1, 10, 5, 0, tzinfo=timezone.utc)
        nxt = cron.next_run(after)
        assert nxt == datetime(2024, 1, 1, 10, 15, 0, tzinfo=timezone.utc)

    def test_next_hour_rollover(self):
        cron = parse_cron("0 * * * *")
        after = datetime(2024, 1, 1, 10, 30, 0, tzinfo=timezone.utc)
        nxt = cron.next_run(after)
        assert nxt == datetime(2024, 1, 1, 11, 0, 0, tzinfo=timezone.utc)

    def test_next_day_rollover(self):
        cron = parse_cron("0 9 * * *")
        after = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        nxt = cron.next_run(after)
        assert nxt == datetime(2024, 1, 2, 9, 0, 0, tzinfo=timezone.utc)

    def test_weekday_skip(self):
        # After Friday 10am, next Monday 9am
        cron = parse_cron("0 9 * * 1")
        # 2024-06-14 is Friday
        after = datetime(2024, 6, 14, 10, 0, 0, tzinfo=timezone.utc)
        nxt = cron.next_run(after)
        # 2024-06-17 is Monday
        assert nxt == datetime(2024, 6, 17, 9, 0, 0, tzinfo=timezone.utc)

    def test_month_skip(self):
        cron = parse_cron("0 0 1 6 *")  # June 1st
        after = datetime(2024, 6, 2, 0, 0, 0, tzinfo=timezone.utc)
        nxt = cron.next_run(after)
        # Next June 1st
        assert nxt == datetime(2025, 6, 1, 0, 0, 0, tzinfo=timezone.utc)

    def test_strictly_after(self):
        """next_run should never return the same time as 'after'."""
        cron = parse_cron("*/5 * * * *")
        after = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)  # exact match
        nxt = cron.next_run(after)
        assert nxt > after
        assert nxt == datetime(2024, 1, 1, 10, 5, 0, tzinfo=timezone.utc)

    def test_every_2_hours(self):
        cron = parse_cron("0 */2 * * *")
        after = datetime(2024, 1, 1, 3, 0, 0, tzinfo=timezone.utc)
        nxt = cron.next_run(after)
        assert nxt == datetime(2024, 1, 1, 4, 0, 0, tzinfo=timezone.utc)

    def test_list_of_minutes(self):
        cron = parse_cron("15,45 * * * *")
        after = datetime(2024, 1, 1, 10, 20, 0, tzinfo=timezone.utc)
        nxt = cron.next_run(after)
        assert nxt == datetime(2024, 1, 1, 10, 45, 0, tzinfo=timezone.utc)

    def test_year_rollover(self):
        cron = parse_cron("0 0 1 1 *")  # Jan 1st midnight
        after = datetime(2024, 12, 31, 23, 59, 0, tzinfo=timezone.utc)
        nxt = cron.next_run(after)
        assert nxt == datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------


class TestNextCronTime:
    def test_valid_expression(self):
        after = datetime(2024, 1, 1, 10, 5, 0, tzinfo=timezone.utc)
        nxt = next_cron_time("*/30 * * * *", after)
        assert nxt is not None
        assert nxt == datetime(2024, 1, 1, 10, 30, 0, tzinfo=timezone.utc)

    def test_invalid_returns_none(self):
        after = datetime(2024, 1, 1, tzinfo=timezone.utc)
        result = next_cron_time("invalid cron", after)
        assert result is None
