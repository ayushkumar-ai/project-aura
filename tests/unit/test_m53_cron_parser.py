"""M53 Unit Tests — Cron Parser & Slot Calculation Engine."""

import datetime
import pytest

from core.automations.cron_parser import (
    CronExpression,
    calculate_next_fire,
    validate_cron,
)
from core.automations.types import AutomationValidationError


def test_valid_cron_expressions():
    """Verify standard 5-part cron syntax parsing."""
    valid_exprs = [
        "* * * * *",
        "*/5 * * * *",
        "0 0 * * *",
        "30 4 1,15 * 5",
        "0 12 * * 1-5",
        "15,45 8-18/2 * * *",
        "0 0 1 1 *",
    ]
    for expr in valid_exprs:
        validate_cron(expr)
        cron = CronExpression(expr)
        assert cron is not None


def test_invalid_cron_expressions():
    """Verify invalid syntax raises AutomationValidationError."""
    invalid_exprs = [
        "* * * *",           # 4 fields
        "* * * * * *",       # 6 fields
        "60 * * * *",        # minute > 59
        "* 24 * * *",        # hour > 23
        "* * 0 * *",         # dom < 1
        "* * 32 * *",        # dom > 31
        "* * * 0 *",         # month < 1
        "* * * 13 *",        # month > 12
        "* * * * 8",         # dow > 7
        "abc * * * *",       # non-numeric
        "*/0 * * * *",       # step == 0
        "10-5 * * * *",      # reversed range
    ]
    for expr in invalid_exprs:
        with pytest.raises(AutomationValidationError):
            validate_cron(expr)


def test_deterministic_next_fire_calculation():
    """Verify next_fire_at advances deterministically strictly into the future."""
    # Fixed base timestamp: 2026-09-17 10:15:30 UTC
    base_dt = datetime.datetime(2026, 9, 17, 10, 15, 30, tzinfo=datetime.timezone.utc)
    base_ts = base_dt.timestamp()

    # Every minute -> 10:16:00
    cron_every_min = CronExpression("* * * * *")
    next_ts = cron_every_min.next_fire_at(base_ts)
    next_dt = datetime.datetime.fromtimestamp(next_ts, tz=datetime.timezone.utc)
    assert next_dt == datetime.datetime(2026, 9, 17, 10, 16, 0, tzinfo=datetime.timezone.utc)

    # Top of the hour -> 11:00:00
    cron_hourly = CronExpression("0 * * * *")
    next_hr_ts = cron_hourly.next_fire_at(base_ts)
    next_hr_dt = datetime.datetime.fromtimestamp(next_hr_ts, tz=datetime.timezone.utc)
    assert next_hr_dt == datetime.datetime(2026, 9, 17, 11, 0, 0, tzinfo=datetime.timezone.utc)

    # Daily at midnight -> 2026-09-18 00:00:00
    cron_daily = CronExpression("0 0 * * *")
    next_day_ts = cron_daily.next_fire_at(base_ts)
    next_day_dt = datetime.datetime.fromtimestamp(next_day_ts, tz=datetime.timezone.utc)
    assert next_day_dt == datetime.datetime(2026, 9, 18, 0, 0, 0, tzinfo=datetime.timezone.utc)


def test_missed_slots_calculation():
    """Verify calculation of missed scheduled slots between timestamps."""
    cron = CronExpression("*/15 * * * *")
    # From 10:00:00 to 11:00:00 (slots: 10:15, 10:30, 10:45, 11:00)
    t0 = datetime.datetime(2026, 9, 17, 10, 0, 0, tzinfo=datetime.timezone.utc).timestamp()
    t1 = datetime.datetime(2026, 9, 17, 11, 0, 0, tzinfo=datetime.timezone.utc).timestamp()

    slots = cron.get_missed_slots(t0, t1)
    assert len(slots) == 4
    expected_dts = [
        datetime.datetime(2026, 9, 17, 10, 15, 0, tzinfo=datetime.timezone.utc),
        datetime.datetime(2026, 9, 17, 10, 30, 0, tzinfo=datetime.timezone.utc),
        datetime.datetime(2026, 9, 17, 10, 45, 0, tzinfo=datetime.timezone.utc),
        datetime.datetime(2026, 9, 17, 11, 0, 0, tzinfo=datetime.timezone.utc),
    ]
    assert [datetime.datetime.fromtimestamp(s, tz=datetime.timezone.utc) for s in slots] == expected_dts
