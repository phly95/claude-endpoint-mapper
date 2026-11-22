import importlib.util
import os
import pathlib
import shutil
import tempfile
import time
import unittest
from unittest import mock

GUI = pathlib.Path(__file__).resolve().parents[1] / "endpoint-mapper-gui.py"


@unittest.skipUnless(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"), "needs a display")
class GuiTests(unittest.TestCase):
    def setUp(self):
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        p = mock.patch.dict(os.environ, {"HOME": home, "XDG_CONFIG_HOME": home + "/.config",
                                         "XDG_DATA_HOME": home + "/.local/share"})
        p.start()
        self.addCleanup(p.stop)
        spec = importlib.util.spec_from_file_location("em_gui", GUI)
        self.gui = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.gui)
        self.app = self.gui.App()
        self.addCleanup(self.app.destroy)

    def pump(self, until, seconds=5.0):
        end = time.time() + seconds
        while time.time() < end and not until():
            self.app.update()
            time.sleep(0.02)
        return until()

    def log_text(self):
        return self.app.log.get("1.0", "end")

    def test_fetch_failure_is_reported_and_button_recovers(self):
        self.app.url.set("http://gw.test")
        with mock.patch.object(self.gui.em_config, "fetch_models", side_effect=RuntimeError("boom")):
            self.app._fetch()
            self.assertTrue(self.pump(lambda: "Fetch failed: boom" in self.log_text()), self.log_text())
        self.assertEqual(str(self.app.fetch_btn.cget("state")), "normal")

    def test_fetch_success_fills_rows_and_guesses_tiers(self):
        self.app.url.set("http://gw.test")
        with mock.patch.object(self.gui.em_config, "fetch_models", return_value=["claude-opus-x", "qwen"]):
            self.app._fetch()
            self.assertTrue(self.pump(lambda: len(self.app.rows) == 2))
        self.assertEqual([r.tier.get() for r in self.app.rows], ["opus", ""])

    def test_apply_needs_an_api_key_even_with_a_web_search_key(self):
        self.app.url.set("http://gw.test")
        self.app._add_row("m", "", "")
        self.app.ws_on.set(True)
        self.app.ws_provider.set("brave")
        self.app.ws_key.set("only-a-search-key")
        with mock.patch.object(self.gui.subprocess, "run", side_effect=AssertionError("must not run")):
            self.app._apply()
        self.assertIn("Enter the API key", self.log_text())
        self.assertEqual(str(self.app.apply_btn.cget("state")), "normal")

    def apply_and_get_argv(self):
        self.app.url.set("http://gw.test")
        self.app.key.set("k")
        self.app._add_row("m", "", "")
        done = mock.Mock(returncode=0, stdout="ok", stderr="")
        with mock.patch.object(self.gui.subprocess, "run", return_value=done) as run:
            self.app._apply()
            self.assertTrue(self.pump(lambda: "Done." in self.log_text()), self.log_text())
        return run.call_args.args[0]

    def test_separate_launcher_is_ticked_by_default_and_passed_to_the_cli(self):
        self.assertTrue(self.app.separate.get())
        self.assertIn("--separate", self.apply_and_get_argv())

    def test_unticking_separate_applies_to_the_normal_claude_desktop(self):
        self.app.separate.set(False)
        self.assertNotIn("--separate", self.apply_and_get_argv())


if __name__ == "__main__":
    unittest.main()
