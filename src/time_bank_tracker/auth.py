from __future__ import annotations

import base64
import hashlib
import http.server
import secrets
import threading
import urllib.parse
import webbrowser

import keyring

from .api import TimeBankAPI, TokenPair

SERVICE_NAME = "HabitReserve"
LEGACY_SERVICE_NAME = "Time Bank Tracker"
REFRESH_ACCOUNT = "desktop-refresh-token"


class BrowserLogin:
    def __init__(self, api: TimeBankAPI):
        self.api = api

    def run(self, timeout: float = 180) -> TokenPair:
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        result: dict[str, str] = {}
        completed = threading.Event()

        class Callback(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                if self.path.startswith("/callback") and query.get("state", [""])[0] == state:
                    result["code"] = query.get("code", [""])[0]
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(
                        b"<!doctype html><title>HabitReserve</title>"
                        b"<h1>HabitReserve is connected.</h1>"
                        b"<p>Return to the HabitReserve app. You can close this window.</p>"
                        b"<script>window.setTimeout(() => window.close(), 800);</script>"
                    )
                    completed.set()
                else:
                    self.send_error(400)

            def log_message(self, *_args) -> None:
                return

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Callback)
        redirect_uri = f"http://127.0.0.1:{server.server_port}/callback"
        query = urllib.parse.urlencode({
            "redirect_uri": redirect_uri, "state": state,
            "code_challenge": challenge, "code_challenge_method": "S256",
        })
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        if not webbrowser.open_new(f"{self.api.server_url}/desktop/authorize?{query}"):
            raise RuntimeError("Could not open the browser for sign-in")
        completed.wait(timeout)
        server.shutdown()
        server.server_close()
        if not result.get("code"):
            raise TimeoutError("Browser sign-in was not completed")
        return self.api.exchange_code(result["code"], verifier, redirect_uri)


def open_registration(api: TimeBankAPI) -> None:
    """Open account creation in the user's normal browser."""
    webbrowser.open_new(f"{api.server_url}/register")


def saved_refresh_token() -> str:
    token = keyring.get_password(SERVICE_NAME, REFRESH_ACCOUNT) or ""
    if token:
        return token

    legacy_token = keyring.get_password(LEGACY_SERVICE_NAME, REFRESH_ACCOUNT) or ""
    if legacy_token:
        keyring.set_password(SERVICE_NAME, REFRESH_ACCOUNT, legacy_token)
        _delete_refresh_token(LEGACY_SERVICE_NAME)
    return legacy_token


def save_refresh_token(token: str) -> None:
    keyring.set_password(SERVICE_NAME, REFRESH_ACCOUNT, token)
    _delete_refresh_token(LEGACY_SERVICE_NAME)


def clear_refresh_token() -> None:
    _delete_refresh_token(SERVICE_NAME)
    _delete_refresh_token(LEGACY_SERVICE_NAME)


def _delete_refresh_token(service_name: str) -> None:
    try:
        keyring.delete_password(service_name, REFRESH_ACCOUNT)
    except keyring.errors.PasswordDeleteError:
        pass
