from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from time_bank_tracker.app import TimeBankApplication
from time_bank_tracker.models import TimeBankState


class TimerControllerHarness:
    _task_succeeded = TimeBankApplication._task_succeeded
    _accept_state = TimeBankApplication._accept_state

    def __init__(self, state):
        self.state = state
        self.action_in_flight = True
        self.queued_actions = []
        self.previous_action_state = state
        self.pending_action_anchor = state.anchor_at
        self.deferred_state = None
        self.overlay = SimpleNamespace(set_state=lambda _state: None)
        self.rendered = []

    def _render(self):
        self.rendered.append(self.state.live_activity())


def test_delayed_v2_start_ack_does_not_restart_desktop_count():
    clicked_at = datetime.now(timezone.utc) - timedelta(seconds=6)
    preview = TimeBankState(0, "working", "working", 0, clicked_at, 1)
    controller = TimerControllerHarness(preview)
    server_boundary = clicked_at + timedelta(seconds=6)
    acknowledgement = TimeBankState.from_json({
        "balance_ms_at_boundary": 0,
        "activity_ms_at_boundary": 0,
        "mode": "working",
        "activity_mode": "working",
        "boundary_at": server_boundary.isoformat(),
        "sampled_at": server_boundary.isoformat(),
        "version": 2,
    }, local_action_at=clicked_at)

    controller._task_succeeded(("action", acknowledgement))

    assert controller.state.version == 2
    assert controller.state.live_activity() >= 6
    assert controller.rendered[-1] >= 6


def test_newer_event_deferred_during_action_is_applied_after_ack():
    clicked_at = datetime.now(timezone.utc) - timedelta(seconds=2)
    preview = TimeBankState(0, "working", "working", 0, clicked_at, 1)
    controller = TimerControllerHarness(preview)
    newer = TimeBankState(3, "idle", "working", 3, clicked_at + timedelta(seconds=3), 3)
    controller._accept_state(newer)
    assert controller.state is preview

    acknowledgement = TimeBankState(2, "idle", "working", 2, clicked_at + timedelta(seconds=2), 2)
    controller._task_succeeded(("action", acknowledgement))

    assert controller.state.version == 3
    assert controller.state.live_activity() == 3
