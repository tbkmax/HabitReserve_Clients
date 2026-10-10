import unittest
from datetime import datetime, timedelta, timezone

from time_bank_tracker.models import TimeBankState, format_duration, format_duration_parts
from time_bank_tracker.shortcuts import parse_hotkey


class ModelTests(unittest.TestCase):
    def test_live_working_state_uses_server_anchor(self):
        anchor = datetime(2026, 1, 1, tzinfo=timezone.utc)
        state = TimeBankState(100, "working", "working", 20, anchor, 1)
        now = anchor + timedelta(seconds=7)
        self.assertEqual(state.live_balance(now), 107)
        self.assertEqual(state.live_activity(now), 27)

    def test_live_playing_state_subtracts_from_balance(self):
        anchor = datetime(2026, 1, 1, tzinfo=timezone.utc)
        state = TimeBankState(100, "playing", "playing", 20, anchor, 1)
        self.assertEqual(state.live_balance(anchor + timedelta(seconds=7)), 93)

    def test_legacy_running_state_uses_client_receipt_time(self):
        state = TimeBankState.from_json({
            "balance_seconds": 100,
            "mode": "working",
            "activity_mode": "working",
            "activity_elapsed_seconds": 20,
            "anchor_at": "2026-01-01T00:00:00Z",
            "version": 2,
        })
        self.assertIsNotNone(state.anchor_at)
        self.assertLessEqual(state.elapsed_since_anchor(), 1)

    def test_server_snapshot_preserves_canonical_running_elapsed(self):
        state = TimeBankState.from_json({
            "balance_seconds": 100,
            "mode": "working",
            "activity_mode": "working",
            "activity_elapsed_seconds": 20,
            "anchor_at": "2026-01-01T00:00:00Z",
            "server_now": "2026-01-01T00:00:07Z",
            "version": 2,
        })

        self.assertIn(state.elapsed_since_anchor(), {7, 8})
        self.assertIn(state.live_balance(), {107, 108})
        self.assertIn(state.live_activity(), {27, 28})

    def test_clock_offset_keeps_delayed_action_response_at_click_boundary(self):
        local_now = datetime.now(timezone.utc)
        server_anchor = local_now + timedelta(seconds=6)
        state = TimeBankState.from_json({
            "balance_seconds": 100,
            "mode": "working",
            "activity_mode": "working",
            "activity_elapsed_seconds": 0,
            "anchor_at": server_anchor.isoformat(),
            "server_now": server_anchor.isoformat(),
            "version": 2,
        }, timedelta(seconds=6))

        self.assertEqual(state.live_balance(), 100)
        self.assertEqual(state.live_activity(), 0)
        self.assertEqual(state.live_activity(local_now + timedelta(seconds=5)), 5)

    def test_v2_delayed_start_ack_keeps_click_anchor(self):
        clicked_at = datetime.now(timezone.utc) - timedelta(seconds=6)
        server_boundary = clicked_at + timedelta(seconds=6)
        state = TimeBankState.from_json({
            "balance_ms_at_boundary": 123400,
            "mode": "working",
            "activity_mode": "working",
            "activity_ms_at_boundary": 0,
            "boundary_at": server_boundary.isoformat(),
            "sampled_at": server_boundary.isoformat(),
            "version": 8,
        }, local_action_at=clicked_at)

        self.assertEqual(state.live_activity(), 6)
        self.assertEqual(state.live_balance(), 129)

    def test_stop_preview_freezes_at_click_while_server_response_is_pending(self):
        started = datetime.now(timezone.utc) - timedelta(seconds=20)
        clicked = started + timedelta(seconds=7)
        running = TimeBankState(100, "working", "working", 20, started, 4)
        stopped = running.preview_action("stop", clicked)

        self.assertEqual(stopped.mode, "idle")
        self.assertEqual(stopped.live_balance(), 107)
        self.assertEqual(stopped.live_activity(), 27)
        self.assertEqual(stopped.live_balance(clicked + timedelta(seconds=10)), 107)
        self.assertEqual(stopped.version, 4)

    def test_stop_preview_after_pending_start_uses_stop_click_time(self):
        clicked_start = datetime.now(timezone.utc) - timedelta(seconds=12)
        waiting = TimeBankState(0, "idle", None, 0, clicked_start, 2)
        started = waiting.preview_action("work", clicked_start)
        stopped = started.preview_action("stop", clicked_start + timedelta(seconds=3))

        self.assertEqual(stopped.mode, "idle")
        self.assertEqual(stopped.live_balance(), 3)
        self.assertEqual(stopped.live_activity(), 3)

    def test_duration_and_hotkey_parsing(self):
        self.assertEqual(format_duration(-3661, signed=True), "-01:01:01")
        modifiers, key = parse_hotkey("Ctrl+Alt+W")
        self.assertTrue(modifiers)
        self.assertEqual(key, ord("W"))

    def test_duration_can_hide_individual_clock_fields(self):
        self.assertEqual(
            format_duration_parts(3661, show_hours=False, show_seconds=False),
            "01",
        )
        self.assertEqual(
            format_duration_parts(-3661, signed=True, show_minutes=False),
            "-01:01",
        )


if __name__ == "__main__":
    unittest.main()
