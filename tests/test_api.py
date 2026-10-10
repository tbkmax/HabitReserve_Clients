import json
import ssl
import threading
import unittest
import urllib.error
from email.message import Message
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from time_bank_tracker.api import APIError, TimeBankAPI


class APIEventTests(unittest.TestCase):
    def test_rate_limit_error_includes_retry_after(self):
        api = TimeBankAPI("http://127.0.0.1:8080")
        headers = Message()
        headers["Retry-After"] = "45"
        error = urllib.error.HTTPError(
            "http://127.0.0.1:8080/api/desktop/token", 429, "Too Many Requests", headers, None,
        )
        error.read = lambda: b'{"error":"too many requests; try again later"}'
        with patch("urllib.request.urlopen", side_effect=error) as urlopen:
            with self.assertRaisesRegex(APIError, "45 seconds") as raised:
                api.refresh("refresh-token")
        self.assertEqual(raised.exception.status, 429)
        self.assertEqual(raised.exception.retry_after, 45)
        self.assertIs(urlopen.call_args.kwargs["context"], api.ssl_context)

    def test_certificate_error_is_reported_separately(self):
        api = TimeBankAPI("https://localhost")
        certificate_error = ssl.SSLCertVerificationError(1, "certificate verify failed")
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError(certificate_error)):
            with self.assertRaisesRegex(APIError, "Cannot verify.*system trust store"):
                api.refresh("refresh-token")

    def test_invalid_additional_ca_has_clear_error(self):
        with self.assertRaisesRegex(APIError, "Cannot load the configured TLS certificate authority"):
            TimeBankAPI("https://localhost", "missing-development-ca.crt")

    def test_action_sends_click_boundary_and_expected_version(self):
        api = TimeBankAPI("http://127.0.0.1:8080")
        api.server_clock_offset = timedelta(seconds=6)
        captured = {}

        def request(path, method="GET", body=None, authenticated=True):
            captured.update(path=path, method=method, body=body, authenticated=authenticated)
            return {"time_bank": {
                "balance_ms_at_boundary": 12000,
                "mode": "idle",
                "activity_ms_at_boundary": 7000,
                "version": 4,
            }}

        api._request = request
        clicked_at = datetime(2026, 9, 24, 12, 30, tzinfo=timezone.utc)

        state = api.action("stop", clicked_at, 3)

        self.assertEqual(captured["path"], "/api/time-bank/v2")
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["body"]["action"], "stop")
        self.assertEqual(captured["body"]["client_action_at"], (clicked_at + timedelta(seconds=6)).isoformat())
        self.assertEqual(captured["body"]["expected_version"], 3)
        self.assertRegex(captured["body"]["action_id"], r"^[0-9a-f-]{36}$")
        self.assertEqual(state.version, 4)

    def test_action_without_clock_sample_uses_server_receive_time(self):
        api = TimeBankAPI("http://127.0.0.1:8080")
        captured = {}

        def request(path, method="GET", body=None, authenticated=True):
            captured.update(body=body)
            return {"time_bank": {"balance_ms_at_boundary": 0, "mode": "idle", "version": 1}}

        api._request = request
        api.action("work", datetime.now(timezone.utc), 0)

        self.assertEqual(captured["body"]["action"], "work")
        self.assertEqual(captured["body"]["expected_version"], 0)
        self.assertNotIn("client_action_at", captured["body"])

    def test_action_uses_monotonic_server_sample_when_wall_clock_changes(self):
        api = TimeBankAPI("http://127.0.0.1:8080")
        sample = datetime(2026, 9, 24, 12, 30, tzinfo=timezone.utc)
        api.server_clock_sample = (sample, 100.0)
        captured = {}

        def request(_path, _method="GET", body=None):
            captured.update(body)
            return {"time_bank": {"balance_ms_at_boundary": 0, "mode": "idle", "version": 1}}

        api._request = request
        api.action("stop", sample + timedelta(minutes=1), 0, clicked_monotonic=102.0)

        self.assertEqual(captured["client_action_at"], (sample + timedelta(seconds=2)).isoformat())

    def test_state_samples_server_clock(self):
        api = TimeBankAPI("http://127.0.0.1:8080")
        server_now = datetime.now(timezone.utc) + timedelta(seconds=6)
        api._request = lambda *_args, **_kwargs: {"time_bank": {
            "balance_ms_at_boundary": 0,
            "mode": "idle",
            "boundary_at": server_now.isoformat(),
            "sampled_at": server_now.isoformat(),
            "version": 0,
        }}

        api.state()

        self.assertIsNotNone(api.server_clock_offset)
        self.assertAlmostEqual(api.server_clock_offset.total_seconds(), 6, delta=1)

    def test_action_retry_reuses_action_id(self):
        api = TimeBankAPI("http://127.0.0.1:8080")
        bodies = []

        def request(path, method="GET", body=None, authenticated=True):
            bodies.append(dict(body))
            if len(bodies) == 1:
                raise APIError("connection lost")
            return {"time_bank": {
                "balance_ms_at_boundary": 0, "activity_ms_at_boundary": 0,
                "mode": "idle", "version": 1,
            }}

        api._request = request
        api.action("stop", datetime.now(timezone.utc), 0)

        self.assertEqual(len(bodies), 2)
        self.assertEqual(bodies[0]["action_id"], bodies[1]["action_id"])

    def test_state_events_reads_state_and_ignores_keepalive(self):
        payload = {
            "balance_seconds": 12,
            "mode": "idle",
            "activity_mode": "working",
            "activity_elapsed_seconds": 7,
            "version": 3,
        }
        lines = [
            b": keep-alive\n",
            b"\n",
            b"event: state\n",
            f"data: {json.dumps(payload)}\n".encode(),
            b"\n",
        ]

        states = list(TimeBankAPI._state_events(lines, threading.Event()))

        self.assertEqual(len(states), 1)
        self.assertEqual(states[0].mode, "idle")
        self.assertEqual(states[0].activity_mode, "working")
        self.assertEqual(states[0].version, 3)

    def test_state_events_stops_before_consuming_more_lines(self):
        stopped = threading.Event()
        stopped.set()

        self.assertEqual(
            list(TimeBankAPI._state_events([b"event: state\n"], stopped)),
            [],
        )


if __name__ == "__main__":
    unittest.main()
