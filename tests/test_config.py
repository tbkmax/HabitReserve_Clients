import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from time_bank_tracker.config import DEFAULT_SERVER_URL, server_url, tls_ca_file


class ConfigurationTests(unittest.TestCase):
    def test_default_server_uses_https_proxy(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(server_url(), DEFAULT_SERVER_URL)
            self.assertEqual(DEFAULT_SERVER_URL, "https://localhost")

    def test_environment_server_url_is_normalized(self):
        with patch.dict(os.environ, {"HABITRESERVE_SERVER_URL": " https://time.example/ "}, clear=True):
            self.assertEqual(server_url(), "https://time.example")

    def test_legacy_environment_server_url_is_still_supported(self):
        with patch.dict(os.environ, {"TIME_BANK_SERVER_URL": "https://legacy.example"}, clear=True):
            self.assertEqual(server_url(), "https://legacy.example")

    def test_packaged_release_server_url_is_used(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "_habitreserve_build.json").write_text(
                json.dumps({"server_url": "https://production.example"}),
                encoding="utf-8",
            )
            with (
                patch.dict(os.environ, {}, clear=True),
                patch.object(sys, "_MEIPASS", str(root), create=True),
            ):
                self.assertEqual(server_url(), "https://production.example")

    def test_environment_override_wins_over_packaged_release_url(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "_habitreserve_build.json").write_text(
                json.dumps({"server_url": "https://production.example"}),
                encoding="utf-8",
            )
            with (
                patch.dict(os.environ, {"HABITRESERVE_SERVER_URL": "https://override.example"}, clear=True),
                patch.object(sys, "_MEIPASS", str(root), create=True),
            ):
                self.assertEqual(server_url(), "https://override.example")

    def test_explicit_ca_file_wins(self):
        with patch.dict(os.environ, {"HABITRESERVE_TLS_CA_FILE": "C:/certs/local-ca.crt"}, clear=True):
            self.assertEqual(tls_ca_file("https://localhost"), "C:/certs/local-ca.crt")

    def test_production_host_does_not_use_development_ca(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(tls_ca_file("https://time.example"))

    def test_packaged_executable_finds_checkout_ca(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ca_file = root / "deploy" / "nginx" / "certs" / "dev-ca.crt"
            ca_file.parent.mkdir(parents=True)
            ca_file.write_text("development CA placeholder", encoding="utf-8")
            executable = root / "desktop" / "dist" / "HabitReserve.exe"
            with (
                patch.dict(os.environ, {}, clear=True),
                patch.object(sys, "executable", str(executable)),
            ):
                self.assertEqual(tls_ca_file("https://localhost"), str(ca_file))


if __name__ == "__main__":
    unittest.main()
