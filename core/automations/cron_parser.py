"""M53 — Deterministic Cron Parser & Slot Calculation Engine."""

from __future__ import annotations

import datetime
import math
import re
from typing import Set

from core.automations.types import AutomationValidationError


def _parse_field(field_str: str, min_val: int, max_val: int, field_name: str) -> Set[int]:
    """Parse a single cron field into a set of valid integer values."""
    field_str = field_str.strip()
    if not field_str:
        raise AutomationValidationError(f"Empty cron field for {field_name}")

    result: set[int] = set()
    for part in field_str.split(","):
        part = part.strip()
        if not part:
            continue

        step = 1
        if "/" in part:
            subparts = part.split("/")
            if len(subparts) != 2:
                raise AutomationValidationError(f"Invalid step expression in {field_name}: '{part}'")
            range_part, step_str = subparts[0], subparts[1]
            try:
                step = int(step_str)
                if step <= 0:
                    raise ValueError()
            except ValueError:
                raise AutomationValidationError(f"Invalid step value in {field_name}: '{step_str}'")
        else:
            range_part = part

        if range_part == "*":
            start_val, end_val = min_val, max_val
        elif "-" in range_part:
            bounds = range_part.split("-")
            if len(bounds) != 2:
                raise AutomationValidationError(f"Invalid range in {field_name}: '{range_part}'")
            try:
                start_val, end_val = int(bounds[0]), int(bounds[1])
            except ValueError:
                raise AutomationValidationError(f"Non-integer range in {field_name}: '{range_part}'")
            if start_val < min_val or end_val > max_val or start_val > end_val:
                raise AutomationValidationError(
                    f"Range {start_val}-{end_val} out of bounds ({min_val}-{max_val}) in {field_name}"
                )
        else:
            try:
                single_val = int(range_part)
            except ValueError:
                raise AutomationValidationError(f"Non-integer value in {field_name}: '{range_part}'")
            if single_val < min_val or single_val > max_val:
                raise AutomationValidationError(
                    f"Value {single_val} out of bounds ({min_val}-{max_val}) in {field_name}"
                )
            start_val, end_val = single_val, single_val

        for val in range(start_val, end_val + 1, step):
            result.add(val)

    if not result:
        raise AutomationValidationError(f"Cron field '{field_name}' matched zero values")
    return result


class CronExpression:
    """Parses and evaluates standard 5-field cron expressions."""

    def __init__(self, expr: str) -> None:
        self.expr = expr.strip()
        parts = self.expr.split()
        if len(parts) != 5:
            raise AutomationValidationError(
                f"Cron expression must contain exactly 5 fields, got {len(parts)} in '{expr}'"
            )

        self.minutes = _parse_field(parts[0], 0, 59, "minute")
        self.hours = _parse_field(parts[1], 0, 23, "hour")
        self.days_of_month = _parse_field(parts[2], 1, 31, "day-of-month")
        self.months = _parse_field(parts[3], 1, 12, "month")
        
        # Day of week: 0-7 where 0 and 7 are Sunday
        dow_raw = _parse_field(parts[4], 0, 7, "day-of-week")
        self.days_of_week = set()
        for d in dow_raw:
            self.days_of_week.add(0 if d == 7 else d)

    def matches(self, dt: datetime.datetime) -> bool:
        """Check if a given datetime (in UTC) matches the cron expression."""
        if dt.minute not in self.minutes:
            return False
        if dt.hour not in self.hours:
            return False
        if dt.month not in self.months:
            return False
        if dt.day not in self.days_of_month:
            return False
        # Python weekday: Monday=0, Sunday=6. Convert to Sunday=0..Saturday=6
        cron_dow = (dt.weekday() + 1) % 7
        if cron_dow not in self.days_of_week:
            return False
        return True

    def next_fire_at(self, from_timestamp: float | None = None) -> float:
        """Calculate next future fire timestamp strictly after from_timestamp."""
        start_ts = from_timestamp if from_timestamp is not None else datetime.datetime.now(datetime.timezone.utc).timestamp()
        # Truncate to start of current minute, then advance 1 minute
        start_dt = datetime.datetime.fromtimestamp(start_ts, tz=datetime.timezone.utc)
        curr = start_dt.replace(second=0, microsecond=0) + datetime.timedelta(minutes=1)

        # Search forward minute-by-minute up to 366 days
        max_minutes = 366 * 24 * 60
        for _ in range(max_minutes):
            if self.matches(curr):
                return curr.timestamp()
            curr += datetime.timedelta(minutes=1)

        raise AutomationValidationError(f"Could not find matching next fire time within 366 days for cron '{self.expr}'")

    def previous_fire_at(self, from_timestamp: float | None = None) -> float:
        """Calculate latest fire timestamp strictly on or before from_timestamp."""
        start_ts = from_timestamp if from_timestamp is not None else datetime.datetime.now(datetime.timezone.utc).timestamp()
        curr = datetime.datetime.fromtimestamp(start_ts, tz=datetime.timezone.utc).replace(second=0, microsecond=0)

        max_minutes = 366 * 24 * 60
        for _ in range(max_minutes):
            if self.matches(curr):
                return curr.timestamp()
            curr -= datetime.timedelta(minutes=1)

        raise AutomationValidationError(f"Could not find matching previous fire time within 366 days for cron '{self.expr}'")

    def get_missed_slots(self, from_timestamp: float, to_timestamp: float, max_slots: int = 100) -> list[float]:
        """Return all scheduled slots strictly in the interval (from_timestamp, to_timestamp]."""
        if from_timestamp >= to_timestamp:
            return []

        slots: list[float] = []
        start_dt = datetime.datetime.fromtimestamp(from_timestamp, tz=datetime.timezone.utc)
        curr = start_dt.replace(second=0, microsecond=0) + datetime.timedelta(minutes=1)
        end_dt = datetime.datetime.fromtimestamp(to_timestamp, tz=datetime.timezone.utc)

        while curr <= end_dt:
            if self.matches(curr):
                slots.append(curr.timestamp())
                if len(slots) >= max_slots:
                    break
            curr += datetime.timedelta(minutes=1)

        return slots


def validate_cron(cron_expr: str) -> None:
    """Validate a cron expression string, raising AutomationValidationError on failure."""
    CronExpression(cron_expr)


def calculate_next_fire(cron_expr: str, from_timestamp: float | None = None) -> float:
    """Convenience helper to compute next fire timestamp for a cron expression."""
    return CronExpression(cron_expr).next_fire_at(from_timestamp)
