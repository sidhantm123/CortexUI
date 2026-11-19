"""Update notifier: versions, releases, the file for each machine, and the window's answers (no network)."""
import io
import json
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

app = QApplication.instance() or QApplication([])

from core import update_check as uc  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402

ASSETS = [
    {"name": "CortexUI-1.1.0-macOS-AppleSilicon.dmg", "browser_download_url": "https://x/as.dmg"},
    {"name": "CortexUI-1.1.0-macOS-Intel.dmg", "browser_download_url": "https://x/intel.dmg"},
    {"name": "CortexUI-1.1.0-Windows-x64.zip", "browser_download_url": "https://x/win.zip"},
]


def release_json(tag, prerelease=False, draft=False):
    return {"tag_name": tag, "prerelease": prerelease, "draft": draft, "assets": ASSETS,
            "html_url": f"https://github.com/sidhantm123/CortexUI/releases/tag/{tag}"}


class TestVersions(unittest.TestCase):

    def test_order(self):
        ordered = ["1.0.0", "1.0.1", "1.1.0-alpha.1", "1.1.0-beta.1", "1.1.0-beta.2", "1.1.0-rc.1", "1.1.0",
                   "1.1.1", "2.0.0"]
        self.assertEqual(sorted(ordered, key=uc.parse_version), ordered)
        self.assertEqual(uc.parse_version("v1.2.3"), uc.parse_version("1.2.3"))

    def test_not_versions(self):
        for text in ["", "1.0", "1.0.0.0", "latest", "v1.0.0-", "1.0.0-beta.x"]:
            self.assertIsNone(uc.parse_version(text), text)

    def test_is_newer(self):
        self.assertTrue(uc.is_newer("1.0.1", "1.0.0"))
        self.assertTrue(uc.is_newer("1.1.0", "1.1.0-beta.3"))
        self.assertFalse(uc.is_newer("1.0.0", "1.0.0"))
        self.assertFalse(uc.is_newer("1.1.0-beta.1", "1.1.0"))
        self.assertFalse(uc.is_newer("garbage", "1.0.0"))


class TestReleases(unittest.TestCase):

    def test_newest_skips_drafts_and_betas_unless_asked(self):
        items = [release_json("v1.2.0", draft=True), release_json("v1.2.0-beta.1", prerelease=True),
                 release_json("v1.1.0"), release_json("v1.0.0")]
        self.assertEqual(uc.newest_release(items).version, "1.1.0")
        self.assertEqual(uc.newest_release(items, include_prereleases=True).version, "1.2.0-beta.1")
        self.assertIsNone(uc.newest_release([]))

    def test_single_release_from_latest(self):
        r = uc.newest_release(release_json("v1.1.0"))
        self.assertEqual((r.version, r.prerelease), ("1.1.0", False))
        self.assertEqual(r.assets["CortexUI-1.1.0-Windows-x64.zip"], "https://x/win.zip")
        self.assertTrue(r.page.endswith("/releases/tag/v1.1.0"))

    def test_file_for_each_machine(self):
        assets = uc.newest_release(release_json("v1.1.0")).assets
        self.assertEqual(uc.asset_for(assets, "win32", False), "https://x/win.zip")
        self.assertEqual(uc.asset_for(assets, "darwin", True), "https://x/as.dmg")
        self.assertEqual(uc.asset_for(assets, "darwin", False), "https://x/intel.dmg")
        self.assertIsNone(uc.asset_for(assets, "linux", False))
        self.assertIsNone(uc.asset_for({}, "win32", False))

    def test_intel_build_under_rosetta_counts_as_apple_silicon(self):
        with mock.patch.object(uc.sys, "platform", "darwin"), \
                mock.patch.object(uc.platform, "machine", return_value="x86_64"), \
                mock.patch.object(uc.subprocess, "run", return_value=SimpleNamespace(stdout="1\n")):
            self.assertTrue(uc._apple_silicon())
        with mock.patch.object(uc.sys, "platform", "darwin"), \
                mock.patch.object(uc.platform, "machine", return_value="x86_64"), \
                mock.patch.object(uc.subprocess, "run", return_value=SimpleNamespace(stdout="")):
            self.assertFalse(uc._apple_silicon())


class TestFetch(unittest.TestCase):

    def _response(self, payload):
        return mock.MagicMock(__enter__=lambda s: io.BytesIO(json.dumps(payload).encode()),
                              __exit__=lambda s, *a: False)

    def test_asks_latest_with_a_user_agent(self):
        with mock.patch.object(uc.urllib.request, "urlopen", return_value=self._response(release_json("v1.1.0"))) as op:
            r = uc.fetch_newest_release()
        self.assertEqual(r.version, "1.1.0")
        request = op.call_args.args[0]
        self.assertTrue(request.full_url.endswith("/repos/sidhantm123/CortexUI/releases/latest"))
        self.assertIn("CortexUI/", request.get_header("User-agent"))
        self.assertEqual(op.call_args.kwargs["timeout"], uc.TIMEOUT_S)

    def test_beta_builds_ask_for_the_list(self):
        payload = [release_json("v1.1.0-beta.2", prerelease=True), release_json("v1.0.0")]
        with mock.patch.object(uc.urllib.request, "urlopen", return_value=self._response(payload)) as op:
            r = uc.fetch_newest_release(include_prereleases=True)
        self.assertEqual(r.version, "1.1.0-beta.2")
        self.assertIn("/releases?per_page=", op.call_args.args[0].full_url)

    def test_no_network_or_nonsense_is_none(self):
        with mock.patch.object(uc.urllib.request, "urlopen", side_effect=OSError("offline")):
            self.assertIsNone(uc.fetch_newest_release())
        with mock.patch.object(uc.urllib.request, "urlopen", return_value=self._response({"message": "rate"})):
            self.assertIsNone(uc.fetch_newest_release())


class FakeSettings:
    def __init__(self, **values):
        self.values = dict(values)

    def value(self, key, default=None, type=None):
        v = self.values.get(key, default)
        return type(v) if type is not None and v is not None else v

    def setValue(self, key, value):
        self.values[key] = value


class TestWindowAnswers(unittest.TestCase):
    """MainWindow's update methods on a stand-in window (building the real one is slow)."""

    def setUp(self):
        self.boxes = []
        patches = [
            mock.patch.object(QMessageBox, "warning", side_effect=lambda *a, **k: self.boxes.append("warning")),
            mock.patch.object(QMessageBox, "information", side_effect=lambda *a, **k: self.boxes.append("info")),
            mock.patch("ui.main_window.APP_VERSION", "1.0.0"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.win = SimpleNamespace(_settings=FakeSettings(), _offer_update=mock.Mock(),
                                   _check_for_updates=mock.Mock(), UPDATE_INTERVAL_S=MainWindow.UPDATE_INTERVAL_S,
                                   _auto_update_action=SimpleNamespace(isChecked=lambda: True))

    def answer(self, release, manual, current="1.0.0"):
        with mock.patch.object(uc, "APP_VERSION", current):
            MainWindow._on_update_checked(self.win, release, manual)

    def test_offline(self):
        self.answer(None, manual=False)
        self.assertEqual(self.boxes, [])
        self.answer(None, manual=True)
        self.assertEqual(self.boxes, ["warning"])
        self.assertNotIn("update_last_check", self.win._settings.values)

    def test_up_to_date(self):
        current = uc.newest_release(release_json("v1.0.0"))
        self.answer(current, manual=False)
        self.assertEqual(self.boxes, [])
        self.answer(current, manual=True)
        self.assertEqual(self.boxes, ["info"])
        self.assertIn("update_last_check", self.win._settings.values)
        self.win._offer_update.assert_not_called()

    def test_newer_is_offered_unless_skipped(self):
        newer = uc.newest_release(release_json("v1.1.0"))
        self.answer(newer, manual=False)
        self.win._offer_update.assert_called_once_with(newer)
        self.win._settings.setValue("update_skip", "1.1.0")
        self.answer(newer, manual=False)
        self.assertEqual(self.win._offer_update.call_count, 1)  # skipped: the automatic check stays quiet
        self.answer(newer, manual=True)
        self.assertEqual(self.win._offer_update.call_count, 2)  # a manual check still offers it

    def test_launch_check_once_a_day_and_only_when_on(self):
        MainWindow.check_for_updates_at_launch(self.win)
        self.win._check_for_updates.assert_called_once_with(manual=False)
        import time
        self.win._settings.setValue("update_last_check", time.time() - 3600)
        MainWindow.check_for_updates_at_launch(self.win)
        self.assertEqual(self.win._check_for_updates.call_count, 1)  # checked an hour ago
        self.win._settings.setValue("update_last_check", 0.0)
        self.win._auto_update_action = SimpleNamespace(isChecked=lambda: False)
        MainWindow.check_for_updates_at_launch(self.win)
        self.assertEqual(self.win._check_for_updates.call_count, 1)  # switched off


if __name__ == "__main__":
    unittest.main()
