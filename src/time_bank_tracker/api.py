from __future__ import annotations

import json
import ssl
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .models import TimeBankState


class APIError(RuntimeError):
    def __init__(self, message: str, status: int = 0, retry_after: int = 0):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int


class TimeBankAPI:
    def __init__(self, server_url: str, ca_file: str | None = None):
        self.server_url = server_url.rstrip("/")
        self.ca_file = ca_file
        self.access_token = ""
        self.server_clock_offset: timedelta | None = None
        self.server_clock_sample: tuple[datetime, float] | None = None
        self.ssl_context = ssl.create_default_context()
        if ca_file:
            try:
                self.ssl_context.load_verify_locations(cafile=ca_file)
            except (OSError, ssl.SSLError) as error:
                raise APIError(f"Cannot load the configured TLS certificate authority: {ca_file}") from error

    def _request(self, path: str, method: str = "GET", body: dict | None = None, authenticated: bool = True) -> dict:
        headers = {"Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if authenticated and self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        request = urllib.request.Request(self.server_url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=10, context=self.ssl_context) as response:
                payload = response.read()
                return json.loads(payload) if payload else {}
        except urllib.error.HTTPError as error:
            try:
                message = json.loads(error.read()).get("error", "Request failed")
            except (ValueError, AttributeError):
                message = "Request failed"
            try:
                retry_after = max(0, int(error.headers.get("Retry-After", "0")))
            except (AttributeError, TypeError, ValueError):
                retry_after = 0
            if error.code == 429 and retry_after:
                message = f"{message} Try again in about {retry_after} seconds."
            raise APIError(message, error.code, retry_after) from error
        except urllib.error.URLError as error:
            if isinstance(error.reason, ssl.SSLCertVerificationError):
                trust_source = self.ca_file or "the system trust store"
                raise APIError(
                    f"Cannot verify the HabitReserve server certificate using {trust_source}"
                ) from error
            raise APIError(f"Cannot reach the HabitReserve server at {self.server_url}") from error

    def exchange_code(self, code: str, verifier: str, redirect_uri: str) -> TokenPair:
        result = self._request("/api/desktop/token", "POST", {
            "grant_type": "authorization_code", "code": code,
            "code_verifier": verifier, "redirect_uri": redirect_uri,
        }, authenticated=False)
        return self._tokens(result)

    def refresh(self, refresh_token: str) -> TokenPair:
        result = self._request("/api/desktop/token", "POST", {
            "grant_type": "refresh_token", "refresh_token": refresh_token,
        }, authenticated=False)
        return self._tokens(result)

    def logout(self, refresh_token: str) -> None:
        self._request("/api/desktop/logout", "POST", {"refresh_token": refresh_token}, authenticated=False)

    def state(self) -> TimeBankState:
        request_started = datetime.now(timezone.utc)
        request_started_mono = time.monotonic()
        result = self._request("/api/time-bank/v2")
        received_at = datetime.now(timezone.utc)
        received_mono = time.monotonic()
        sample = TimeBankState.from_json(result["time_bank"])
        round_trip = timedelta(seconds=received_mono - request_started_mono)
        if sample.server_now is not None and timedelta(0) <= round_trip <= timedelta(seconds=2):
            midpoint = request_started + round_trip / 2
            self.server_clock_offset = sample.server_now - midpoint
            self.server_clock_sample = (sample.server_now, (request_started_mono + received_mono) / 2)
            return TimeBankState.from_json(result["time_bank"], self.server_clock_offset)
        return sample

    def display_name(self) -> str:
        result = self._request("/api/me")
        return str(result.get("profile", {}).get("display_name", ""))

    def action(self, action: str, client_action_at: datetime, expected_version: int,
               clicked_monotonic: float | None = None) -> TimeBankState:
        body = {
            "action": action,
            "action_id": str(uuid.uuid4()),
            "expected_version": expected_version,
        }
        if self.server_clock_sample is not None and clicked_monotonic is not None:
            sampled_at, sampled_mono = self.server_clock_sample
            body["client_action_at"] = (sampled_at + timedelta(seconds=clicked_monotonic - sampled_mono)).isoformat()
        elif self.server_clock_offset is not None:
            body["client_action_at"] = (client_action_at + self.server_clock_offset).isoformat()
        try:
            result = self._request("/api/time-bank/v2", "POST", body)
        except APIError as error:
            if error.status not in (0, 500, 502, 503, 504):
                raise
            # A lost acknowledgement may follow a committed write. Reusing
            # the same action ID makes this retry safe.
            result = self._request("/api/time-bank/v2", "POST", body)
        return TimeBankState.from_json(
            result["time_bank"], self.server_clock_offset,
            local_action_at=client_action_at if action in {"work", "play"} and not result.get("clamped") else None,
        )

    def events(self, stopped: threading.Event):
        """Yield authoritative time-bank states from the server's SSE stream."""
        request = urllib.request.Request(
            self.server_url + "/api/time-bank/v2/events",
            headers={
                "Accept": "text/event-stream",
                "Authorization": f"Bearer {self.access_token}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=35, context=self.ssl_context) as response:
                yield from self._state_events(response, stopped, self.server_clock_offset)
        except urllib.error.HTTPError as error:
            raise APIError("Live timer sync was rejected", error.code) from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise APIError("Live timer sync disconnected") from error

    @staticmethod
    def _state_events(
        lines, stopped: threading.Event, clock_offset: timedelta | None = None
    ):
        event_name = ""
        data_lines: list[str] = []
        for raw_line in lines:
            if stopped.is_set():
                return
            line = raw_line.decode("utf-8").rstrip("\r\n")
            if not line:
                if event_name == "state" and data_lines:
                    try:
                        payload = json.loads("\n".join(data_lines))
                        yield TimeBankState.from_json(payload, clock_offset)
                    except (KeyError, TypeError, ValueError):
                        # Ignore one malformed event without losing live sync.
                        pass
                event_name = ""
                data_lines = []
            elif line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip())

    @staticmethod
    def _tokens(result: dict) -> TokenPair:
        return TokenPair(str(result["access_token"]), str(result["refresh_token"]), int(result["expires_in"]))
