"""Missing-document chasing schedule (REQ-F-006): reminders on day 3 and day 7, close on day 30."""
from __future__ import annotations

REMINDER_DAYS = (3, 7)
CLOSE_DAY = 30


def due_actions(days_waiting: int, reminders_sent: int) -> list[tuple[str, int]]:
    """Actions due now given days since the request and reminders already sent."""
    actions = [("reminder", d) for i, d in enumerate(REMINDER_DAYS) if days_waiting >= d and reminders_sent <= i]
    if days_waiting >= CLOSE_DAY:
        actions.append(("close_incomplete", CLOSE_DAY))
    return actions
