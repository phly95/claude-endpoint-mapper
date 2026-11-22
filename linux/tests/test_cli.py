import contextlib
import io
import json
import os
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock

import em_config
import em_launcher
import endpoint_mapper
from tests.test_config import FakeGateway

KEY = "KEY-123-SECRET"
WS_KEY = "WSKEY-456-SECRET"
BASE = ["--url", "https://gw.example.com"]
MODELS = ["--model", "vendor-a=sonnet=Sonnet", "--model", "qwen3:30b"]


class CliCase(unittest.TestCase):
    def setUp(self):
        self.home = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.asar = self.home / "app.asar"
        self.asar.write_bytes(b"..;/*__CDB_ALLOW_NONANTHROPIC__*/(function(){})();")
        env = {"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / ".config"),
               "XDG_DATA_HOME": str(self.home / ".local" / "share"), "EM_ASAR": str(self.asar)}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)

    def run_cli(self, *argv, stdin=""):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch("sys.stdin", io.StringIO(stdin)):
            rc = endpoint_mapper.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def apply(self, *extra, stdin=KEY + "\n"):
        return self.run_cli("apply", *BASE, *MODELS, "--key-stdin", *extra, stdin=stdin)

    @property
    def user3p(self):
        return self.home / ".config" / "Claude-v100-3p"

    def config(self):
        meta = json.loads((self.user3p / "configLibrary" / "_meta.json").read_text())
        return json.loads((self.user3p / "configLibrary" / (meta["appliedId"] + ".json")).read_text())


class ApplyTests(CliCase):
    def test_apply_writes_config_pref_and_launchers(self):
        rc, out, err = self.apply()
        self.assertEqual(rc, 0, err)
        cfg = self.config()
        self.assertEqual(cfg["inferenceGatewayBaseUrl"], "https://gw.example.com")
        self.assertEqual([m["name"] for m in cfg["inferenceModels"]], ["vendor-a", "qwen3:30b"])
        self.assertEqual(cfg["inferenceModels"][0]["anthropicFamilyTier"], "sonnet")
        self.assertEqual(cfg["inferenceModels"][0]["labelOverride"], "Sonnet")
        extra = json.loads((self.user3p / "claude-desktop-extra.json").read_text())
        self.assertIs(extra["allowNonAnthropicModels"], True)
        s = json.loads((self.home / ".claude-v100" / "settings.json").read_text())
        self.assertEqual(s["env"]["ANTHROPIC_BASE_URL"], "https://gw.example.com")
        self.assertEqual(s["env"]["ANTHROPIC_DEFAULT_SONNET_MODEL"], "vendor-a")
        self.assertTrue((self.home / ".local" / "bin" / "claude-v100").exists())
        self.assertTrue((self.home / ".local" / "share" / "applications"
                         / "com.anthropic.Claude-v100.desktop").exists())

    def test_apply_twice_is_idempotent(self):
        self.apply()
        first = (self.config(), (self.user3p / "claude-desktop-extra.json").read_text(),
                 (self.home / ".claude-v100" / "settings.json").read_text())
        rc, _, err = self.apply()
        self.assertEqual(rc, 0, err)
        second = (self.config(), (self.user3p / "claude-desktop-extra.json").read_text(),
                  (self.home / ".claude-v100" / "settings.json").read_text())
        self.assertEqual(first, second)
        meta = json.loads((self.user3p / "configLibrary" / "_meta.json").read_text())
        self.assertEqual(len(meta["entries"]), 1)

    def test_apply_fails_clearly_without_native_switch(self):
        self.asar.write_bytes(b"no switch in here")
        rc, out, err = self.apply()
        self.assertEqual(rc, 1)
        self.assertIn("allowNonAnthropicModels", err)
        self.assertFalse((self.home / ".config").exists())
        self.assertFalse((self.home / ".local").exists())

    def test_apply_rejects_bad_tier(self):
        rc, _, err = self.run_cli("apply", *BASE, "--model", "a=bogus", "--key-stdin", stdin=KEY + "\n")
        self.assertEqual(rc, 1)
        self.assertIn("tier", err)
        self.assertFalse((self.home / ".config").exists())

    def test_apply_without_any_key_fails(self):
        rc, _, err = self.run_cli("apply", *BASE, *MODELS)
        self.assertEqual(rc, 1)
        self.assertIn("API key", err)

    def test_blank_key_second_time_keeps_key(self):
        self.apply()
        rc, _, err = self.run_cli("apply", *BASE, *MODELS)
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.config()["inferenceGatewayApiKey"], KEY)

    def test_no_code_skips_claude_code_settings(self):
        rc, _, err = self.apply("--no-code")
        self.assertEqual(rc, 0, err)
        self.assertFalse((self.home / ".claude-v100").exists())

    def test_code_settings_path_override(self):
        target = self.home / "elsewhere.json"
        rc, _, err = self.apply("--code-settings", str(target))
        self.assertEqual(rc, 0, err)
        self.assertIn("ANTHROPIC_BASE_URL", json.loads(target.read_text())["env"])
        self.assertFalse((self.home / ".claude-v100").exists())

    def test_invalid_existing_settings_not_clobbered(self):
        d = self.home / ".claude-v100"
        d.mkdir()
        (d / "settings.json").write_text("{broken")
        rc, _, err = self.apply()
        self.assertEqual(rc, 1)
        self.assertEqual((d / "settings.json").read_text(), "{broken")

    def test_output_never_contains_key(self):
        rc, out, err = self.apply("--web-search", "brave", "--web-search-key-stdin",
                                  stdin=KEY + "\n" + WS_KEY + "\n")
        self.assertEqual(rc, 0, err)
        blob = out + err
        self.assertNotIn(KEY, blob)
        self.assertNotIn(WS_KEY, blob)
        self.assertEqual(self.config()["managedMcpServers"][0]["headers"],
                         {"X-Subscription-Token": WS_KEY})

    def test_error_output_never_contains_key(self):
        rc, out, err = self.run_cli("models", "--url", "http://127.0.0.1:9", "--key-stdin",
                                    stdin=KEY + "\n")
        self.assertEqual(rc, 1)
        self.assertNotIn(KEY, out + err)

    def test_restart_stops_v100_and_spawns(self):
        with mock.patch.object(em_launcher, "stop", return_value=[11, 12]) as stop, \
                mock.patch.object(endpoint_mapper, "_spawn") as spawn:
            rc, out, err = self.apply("--restart")
        self.assertEqual(rc, 0, err)
        stop.assert_called_once()
        self.assertEqual(str(stop.call_args.args[0]), str(self.home / ".config" / "Claude-v100"))
        spawn.assert_called_once_with(str(self.home / ".local" / "bin" / "claude-v100"))

    def test_no_restart_leaves_processes_alone(self):
        with mock.patch.object(em_launcher, "stop") as stop, \
                mock.patch.object(endpoint_mapper, "_spawn") as spawn:
            self.apply()
        stop.assert_not_called()
        spawn.assert_not_called()


class OtherCommandTests(CliCase):
    def test_status_reports_fields_without_key(self):
        rc, out, _ = self.run_cli("status")
        self.assertEqual(rc, 0)
        self.assertIn("native_switch: yes", out)
        self.assertIn("desktop_config: missing", out)
        self.assertIn("launcher: missing", out)
        self.apply()
        rc, out, _ = self.run_cli("status")
        self.assertIn("desktop_config: ", out)
        self.assertNotIn("desktop_config: missing", out)
        self.assertIn("models: 2", out)
        self.assertIn("extra_pref: on", out)
        self.assertIn("launcher: present", out)
        self.assertIn("running: no", out)
        self.assertNotIn(KEY, out)

    def test_status_without_native_switch(self):
        self.asar.write_bytes(b"x")
        _, out, _ = self.run_cli("status")
        self.assertIn("native_switch: NO", out)

    def test_uninstall_removes_launchers_keeps_config(self):
        self.apply()
        rc, out, err = self.run_cli("uninstall")
        self.assertEqual(rc, 0, err)
        self.assertFalse((self.home / ".local" / "bin" / "claude-v100").exists())
        self.assertTrue((self.user3p / "configLibrary" / "_meta.json").exists())
        self.assertIn(str(self.user3p), out)

    def test_models_lists_ids(self):
        gw = FakeGateway({"/v1/models": {"data": [{"id": "b"}, {"id": "a"}]}})
        self.addCleanup(gw.close)
        rc, out, err = self.run_cli("models", "--url", gw.url, "--key-stdin", stdin="k\n")
        self.assertEqual((rc, out.split()), (0, ["a", "b"]), err)


if __name__ == "__main__":
    unittest.main()


class FailedApplyTests(CliCase):
    def assert_nothing_written(self):
        self.assertFalse((self.user3p / "configLibrary").exists())
        self.assertFalse((self.home / ".local").exists())

    def test_invalid_extra_json_writes_nothing_and_says_so(self):
        self.user3p.mkdir(parents=True)
        (self.user3p / "claude-desktop-extra.json").write_text("{bad")
        rc, _, err = self.apply()
        self.assertEqual(rc, 1)
        self.assertIn("nothing was written", err)
        self.assert_nothing_written()
        self.assertFalse((self.home / ".claude-v100").exists())
        self.assertEqual((self.user3p / "claude-desktop-extra.json").read_text(), "{bad")

    def test_non_object_env_in_code_settings_writes_nothing(self):
        d = self.home / ".claude-v100"
        d.mkdir()
        (d / "settings.json").write_text(json.dumps({"env": []}))
        rc, _, err = self.apply()
        self.assertEqual(rc, 1)
        self.assertIn("nothing was written", err)
        self.assert_nothing_written()
        self.assertEqual(json.loads((d / "settings.json").read_text()), {"env": []})

    def test_restart_keeps_v100_running_when_inputs_are_invalid(self):
        with mock.patch.object(em_launcher, "stop") as stop, \
                mock.patch.object(endpoint_mapper, "_spawn") as spawn:
            rc, _, err = self.apply("--restart", "--web-search", "brave")  # brave needs a key
            self.assertEqual(rc, 1)
            self.user3p.mkdir(parents=True, exist_ok=True)
            (self.user3p / "claude-desktop-extra.json").write_text("{bad")
            rc2, _, _ = self.apply("--restart")
            self.assertEqual(rc2, 1)
            rc3, _, _ = self.run_cli("apply", *BASE, *MODELS, "--restart")  # no key anywhere
            self.assertEqual(rc3, 1)
        stop.assert_not_called()
        spawn.assert_not_called()
