from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
import time
from typing import Any


RUNNING_MODES = frozenset({"working", "playing"})


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    """Return an aware UTC datetime for safe elapsed-time arithmetic."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_server_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return _as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


@dataclass
class TimeBankState:
    """A server snapshot rendered with the web timer's live-counting rules."""

    balance_seconds: int = 0
    mode: str = "idle"
    activity_mode: str | None = None
    activity_elapsed_seconds: int = 0
    anchor_at: datetime | None = None
    version: int = 0
    server_now: datetime | None = None
    balance_milliseconds: int | None = None
    activity_milliseconds: int | None = None

    def __post_init__(self) -> None:
        self.balance_seconds = math.floor(self.balance_seconds)
        self.mode = self.mode if self.mode in RUNNING_MODES else "idle"
        self.activity_mode = (
            self.activity_mode if self.activity_mode in RUNNING_MODES else None
        )
        self.activity_elapsed_seconds = max(
            0, math.floor(self.activity_elapsed_seconds)
        )
        self.anchor_at = _as_utc(self.anchor_at) if self.anchor_at else _utc_now()
        self.version = math.floor(self.version)
        self.server_now = _as_utc(self.server_now) if self.server_now else None
        if self.balance_milliseconds is None:
            self.balance_milliseconds = self.balance_seconds * 1000
        if self.activity_milliseconds is None:
            self.activity_milliseconds = self.activity_elapsed_seconds * 1000
        wall_elapsed = max(0.0, (_utc_now() - self.anchor_at).total_seconds())
        self._monotonic_anchor = time.monotonic() - wall_elapsed

    @classmethod
    def from_json(
        cls, payload: dict[str, Any], clock_offset: timedelta | None = None,
        local_action_at: datetime | None = None,
    ) -> "TimeBankState":
        """Build a state from an API/SSE payload.

        Translate the server anchor into this process's monotonic clock. Use
        the measured server offset when available so response latency does not
        erase elapsed time; otherwise use the snapshot age at receipt.
        """
        v2 = "balance_ms_at_boundary" in payload
        balance = payload.get("balance_ms_at_boundary") if v2 else payload["balance_seconds"]
        mode = payload.get("mode", "idle")
        activity_mode = payload.get("activity_mode")
        activity_elapsed = payload.get("activity_ms_at_boundary", 0) if v2 else payload.get("activity_elapsed_seconds", 0)
        version = payload.get("version", 0)
        server_anchor = _parse_server_time(payload.get("boundary_at" if v2 else "anchor_at"))
        server_now = _parse_server_time(payload.get("sampled_at" if v2 else "server_now"))
        if not isinstance(balance, (int, float)) or isinstance(balance, bool):
            raise TypeError("balance_seconds must be a number")
        if not isinstance(activity_elapsed, (int, float)) or isinstance(
            activity_elapsed, bool
        ):
            raise TypeError("activity_elapsed_seconds must be a number")
        if not isinstance(version, (int, float)) or isinstance(version, bool):
            raise TypeError("version must be a number")

        received_at = _utc_now()
        elapsed_at_snapshot = 0.0
        if server_anchor is not None and server_now is not None:
            elapsed_at_snapshot = max(0.0, (server_now - server_anchor).total_seconds())

        local_anchor = received_at - timedelta(seconds=elapsed_at_snapshot)
        if server_anchor is not None and clock_offset is not None:
            local_anchor = server_anchor - clock_offset
        if v2 and local_action_at is not None and mode in RUNNING_MODES:
            # This acknowledgement belongs to our own Start or mode switch.
            # Preserve its click boundary even when the response was delayed.
            local_anchor = _as_utc(local_action_at)

        balance_ms = math.floor(balance) if v2 else math.floor(balance) * 1000
        activity_ms = max(0, math.floor(activity_elapsed)) if v2 else max(0, math.floor(activity_elapsed)) * 1000

        return cls(
            balance_seconds=math.trunc(balance_ms / 1000),
            mode=mode if isinstance(mode, str) else "idle",
            activity_mode=activity_mode if isinstance(activity_mode, str) else None,
            activity_elapsed_seconds=activity_ms // 1000,
            anchor_at=local_anchor,
            version=math.floor(version),
            server_now=server_now,
            balance_milliseconds=balance_ms,
            activity_milliseconds=activity_ms,
        )

    def _elapsed_milliseconds(self, now: datetime | None = None) -> int:
        if now is None:
            seconds = time.monotonic() - self._monotonic_anchor
        else:
            seconds = (_as_utc(now) - self.anchor_at).total_seconds()
        return max(0, math.floor(seconds * 1000))

    def elapsed_since_anchor(self, now: datetime | None = None) -> int:
        """Return elapsed whole seconds, never counting a negative interval."""
        if now is None:
            elapsed = math.floor(time.monotonic() - self._monotonic_anchor)
        else:
            current = _as_utc(now)
            elapsed = math.floor((current - self.anchor_at).total_seconds())
        return max(0, elapsed)

    def live_balance(self, now: datetime | None = None) -> int:
        """Calculate the current balance without mutating the snapshot."""
        elapsed = self._elapsed_milliseconds(now)
        if self.mode == "working":
            return math.trunc((self.balance_milliseconds + elapsed) / 1000)
        if self.mode == "playing":
            return math.trunc((self.balance_milliseconds - elapsed) / 1000)
        return math.trunc(self.balance_milliseconds / 1000)

    def live_activity(self, now: datetime | None = None) -> int:
        """Calculate the displayed activity timer without mutating the snapshot."""
        if self.mode not in RUNNING_MODES:
            return self.activity_elapsed_seconds
        return (self.activity_milliseconds + self._elapsed_milliseconds(now)) // 1000

    def preview_action(self, action: str, clicked_at: datetime) -> "TimeBankState":
        """Freeze or restart the display at the user's click boundary."""
        elapsed_ms = self._elapsed_milliseconds(clicked_at) if self.mode in RUNNING_MODES else 0
        balance_ms = self.balance_milliseconds + (elapsed_ms if self.mode == "working" else -elapsed_ms if self.mode == "playing" else 0)
        activity_ms = self.activity_milliseconds + elapsed_ms
        activity_mode = self.activity_mode
        mode = "idle"
        if action in {"work", "play"}:
            mode = "working" if action == "work" else "playing"
            if activity_mode != mode:
                activity_mode = mode
                activity_ms = 0
        elif action == "reset_activity":
            if activity_mode == "working":
                balance_ms -= activity_ms
            elif activity_mode == "playing":
                balance_ms += activity_ms
            activity_mode = None
            activity_ms = 0
        return TimeBankState(
            math.trunc(balance_ms / 1000), mode, activity_mode, activity_ms // 1000,
            clicked_at, self.version, balance_milliseconds=balance_ms,
            activity_milliseconds=activity_ms,
        )


def format_duration(total_seconds: int | float, signed: bool = False) -> str:
    """Format seconds as the web timer does: ``HH:MM:SS``.

    Negative balances retain their minus sign when ``signed`` is requested;
    activity durations remain unsigned.
    """
    seconds_value = math.floor(total_seconds)
    negative = seconds_value < 0
    seconds = abs(seconds_value)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    prefix = "-" if negative and signed else ""
    return f"{prefix}{hours:02d}:{minutes:02d}:{seconds:02d}"


def format_duration_parts(
    total_seconds: int | float,
    *,
    signed: bool = False,
    show_hours: bool = True,
    show_minutes: bool = True,
    show_seconds: bool = True,
) -> str:
    """Format only the selected clock fields while keeping each field stable."""
    seconds_value = math.floor(total_seconds)
    negative = seconds_value < 0
    seconds = abs(seconds_value)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = (
        (show_hours, hours),
        (show_minutes, minutes),
        (show_seconds, seconds),
    )
    prefix = "-" if negative and signed else ""
    return prefix + ":".join(f"{value:02d}" for visible, value in parts if visible)
