import io
import json
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

from time_bank_tracker import updater
from time_bank_tracker.app import TimeBankApplication


class UpdaterTests(unittest.TestCase):
    repository = "owner/habitreserve-desktop"

    def test_release_repository_comes_from_packaged_build(self):
        with patch.object(updater, "packaged_build_config", return_value={"release_repository": self.repository}):
            self.assertEqual(updater.release_repository(), self.repository)
        with patch.object(updater, "packaged_build_config", return_value={"release_repository": "bad repo"}):
            with self.assertRaisesRegex(updater.UpdateError, "not configured"):
                updater.release_repository()

    def test_version_comparison_and_invalid_release(self):
        self.assertTrue(updater.is_newer("1.0.1", "1.0.0"))
        self.assertFalse(updater.is_newer("1.0.0", "1.0.0"))
        self.assertFalse(updater.is_newer("1.0.0", "1.1.0"))
        with self.assertRaises(updater.UpdateError):
            updater.version_tuple("1.0.0-rc.1")

    def test_published_release_is_validated_before_an_update_is_offered(self):
        version = "1.2.3"
        document = {
            "tag_name": f"v{version}",
            "html_url": f"https://github.com/{self.repository}/releases/tag/v{version}",
        }
        with patch.object(updater, "release_repository", return_value=self.repository), patch.object(updater, "_open", return_value=io.BytesIO(json.dumps(document).encode())) as opened:
            release = updater.fetch_release()
            self.assertEqual(release.version, version)
            self.assertEqual(release.release_notes_url, document["html_url"])
            opened.assert_called_once_with(f"https://api.github.com/repos/{self.repository}/releases/latest")
        document["html_url"] = "https://other.example/releases/tag/v1.2.3"
        with patch.object(updater, "release_repository", return_value=self.repository), patch.object(updater, "_open", return_value=io.BytesIO(json.dumps(document).encode())):
            with self.assertRaisesRegex(updater.UpdateError, "untrusted URL"):
                updater.fetch_release()

    def test_release_information_size_is_bounded(self):
        with patch.object(updater, "release_repository", return_value=self.repository), patch.object(updater, "_open", return_value=io.BytesIO(b"x" * (updater.MAX_RELEASE_BYTES + 1))):
            with self.assertRaisesRegex(updater.UpdateError, "too large"):
                updater.fetch_release()

    def test_version_check_does_not_require_release_assets(self):
        document = {
            "tag_name": "v1.2.3",
            "html_url": f"https://github.com/{self.repository}/releases/tag/v1.2.3",
            "assets": [],
        }
        with patch.object(updater, "release_repository", return_value=self.repository), patch.object(updater, "_open", return_value=io.BytesIO(json.dumps(document).encode())):
            self.assertEqual(updater.fetch_release().version, "1.2.3")

    def test_automatic_checks_are_daily_and_manual_checks_bypass_schedule(self):
        class Settings:
            values = {"updates/automatic": True}

            def value(self, key, default=None, *_args):
                return self.values.get(key, default)

            def setValue(self, key, value):
                self.values[key] = value

        class Checker:
            check_for_updates = TimeBankApplication.check_for_updates

            def __init__(self):
                self.update_check_in_flight = False
                self.settings = Settings()
                self.window = SimpleNamespace(update_status=SimpleNamespace(setText=lambda _text: None))
                self.submitted = []

            def _submit(self, function, kind):
                self.submitted.append(kind)

        checker = Checker()
        with (
            patch("time_bank_tracker.app.packaged_build_config", return_value={"release": True}),
            patch("time_bank_tracker.app.time.time", return_value=100_000),
        ):
            checker.check_for_updates()
            checker.update_check_in_flight = False
            checker.check_for_updates()
            checker.check_for_updates(manual=True)
        self.assertEqual(checker.submitted, ["update_check_auto", "update_check_manual"])

    def test_newer_release_opens_download_page(self):
        release = updater.Release(
            "1.2.3", f"https://github.com/{self.repository}/releases/tag/v1.2.3"
        )
        offerer = SimpleNamespace(
            window=SimpleNamespace(update_status=SimpleNamespace(setText=Mock())),
            settings=SimpleNamespace(setValue=Mock()),
        )
        buttons = SimpleNamespace(Yes=1, No=2)
        message_box = SimpleNamespace(StandardButton=buttons, question=Mock(return_value=buttons.Yes))
        with (
            patch("time_bank_tracker.app.QMessageBox", message_box),
            patch("time_bank_tracker.app.webbrowser.open_new") as open_page,
        ):
            TimeBankApplication._offer_update(offerer, release, manual=True)
        open_page.assert_called_once_with(release.release_notes_url)
        offerer.window.update_status.setText.assert_called_once_with(
            "Version 1.2.3 is available on GitHub Releases."
        )

if __name__ == "__main__":
    unittest.main()
