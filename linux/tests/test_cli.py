import contextlib
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import em_desktop
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
        os.environ.pop("CLAUDE_CONFIG_DIR", None)  # never let a test reach the real Claude Code dir
        # No test may stop or start the real Claude Desktop.
        for name, ret in (("stop", []), ("find_pids", [])):
            q = mock.patch.object(em_desktop, name, return_value=ret)
            setattr(self, name, q.start())
            self.addCleanup(q.stop)
        q = mock.patch.object(endpoint_mapper, "_spawn")
        self.spawn = q.start()
        self.addCleanup(q.stop)
        q = mock.patch.object(em_desktop, "desktop_command", return_value="claude-desktop-fake")
        q.start()
        self.addCleanup(q.stop)

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
        return self.home / ".config" / "Claude-3p"

    @property
    def code_settings(self):
        return self.home / ".claude" / "settings.json"

    def config(self):
        meta = json.loads((self.user3p / "configLibrary" / "_meta.json").read_text())
        return json.loads((self.user3p / "configLibrary" / (meta["appliedId"] + ".json")).read_text())


class ApplyTests(CliCase):
    def test_apply_writes_config_pref_and_claude_code_settings(self):
        rc, out, err = self.apply()
        self.assertEqual(rc, 0, err)
        cfg = self.config()
        self.assertEqual(cfg["inferenceGatewayBaseUrl"], "https://gw.example.com")
        self.assertEqual([m["name"] for m in cfg["inferenceModels"]], ["vendor-a", "qwen3:30b"])
        self.assertEqual(cfg["inferenceModels"][0]["anthropicFamilyTier"], "sonnet")
        self.assertEqual(cfg["inferenceModels"][0]["labelOverride"], "Sonnet")
        extra = json.loads((self.user3p / "claude-desktop-extra.json").read_text())
        self.assertIs(extra["allowNonAnthropicModels"], True)
        s = json.loads(self.code_settings.read_text())
        self.assertEqual(s["env"]["ANTHROPIC_BASE_URL"], "https://gw.example.com")
        self.assertEqual(s["env"]["ANTHROPIC_DEFAULT_SONNET_MODEL"], "vendor-a")
        self.assertFalse((self.home / ".local").exists())  # apply installs nothing

    def test_apply_twice_is_idempotent(self):
        self.apply()
        first = (self.config(), (self.user3p / "claude-desktop-extra.json").read_text(),
                 self.code_settings.read_text())
        rc, _, err = self.apply()
        self.assertEqual(rc, 0, err)
        second = (self.config(), (self.user3p / "claude-desktop-extra.json").read_text(),
                  self.code_settings.read_text())
        self.assertEqual(first, second)
        meta = json.loads((self.user3p / "configLibrary" / "_meta.json").read_text())
        self.assertEqual(len(meta["entries"]), 1)

    def test_apply_fails_clearly_without_native_switch(self):
        self.asar.write_bytes(b"no switch in here")
        rc, out, err = self.apply()
        self.assertEqual(rc, 1)
        self.assertIn("allowNonAnthropicModels", err)
        self.assertFalse((self.home / ".config").exists())
        self.assertFalse((self.home / ".claude").exists())

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
        self.assertFalse((self.home / ".claude").exists())

    def test_code_settings_path_override(self):
        target = self.home / "elsewhere.json"
        rc, _, err = self.apply("--code-settings", str(target))
        self.assertEqual(rc, 0, err)
        self.assertIn("ANTHROPIC_BASE_URL", json.loads(target.read_text())["env"])
        self.assertFalse((self.home / ".claude").exists())

    def test_invalid_existing_settings_not_clobbered(self):
        self.code_settings.parent.mkdir()
        self.code_settings.write_text("{broken")
        rc, _, err = self.apply()
        self.assertEqual(rc, 1)
        self.assertEqual(self.code_settings.read_text(), "{broken")

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

    def test_restart_stops_claude_desktop_applies_and_starts_it(self):
        self.stop.return_value = [11, 12]
        rc, out, err = self.apply("--restart")
        self.assertEqual(rc, 0, err)
        self.stop.assert_called_once()
        self.assertEqual(str(self.stop.call_args.args[0]), str(self.home / ".config" / "Claude"))
        self.spawn.assert_called_once_with(["claude-desktop-fake"])
        self.assertTrue((self.user3p / "configLibrary" / "_meta.json").exists())

    def test_no_restart_leaves_processes_alone(self):
        self.apply()
        self.stop.assert_not_called()
        self.spawn.assert_not_called()

    def test_running_desktop_without_restart_gets_a_notice(self):
        self.find_pids.return_value = [5]
        rc, out, err = self.apply()
        self.assertEqual(rc, 0, err)
        self.assertIn("restart", out.lower())


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
        self.assertFalse((self.home / ".claude").exists())
        self.assertEqual((self.user3p / "claude-desktop-extra.json").read_text(), "{bad")

    def test_non_object_env_in_code_settings_writes_nothing(self):
        self.code_settings.parent.mkdir()
        self.code_settings.write_text(json.dumps({"env": []}))
        rc, _, err = self.apply()
        self.assertEqual(rc, 1)
        self.assertIn("nothing was written", err)
        self.assert_nothing_written()
        self.assertEqual(json.loads(self.code_settings.read_text()), {"env": []})

    def test_restart_keeps_claude_desktop_running_when_inputs_are_invalid(self):
        rc, _, _ = self.apply("--restart", "--web-search", "brave")  # brave needs a key
        self.assertEqual(rc, 1)
        self.user3p.mkdir(parents=True, exist_ok=True)
        (self.user3p / "claude-desktop-extra.json").write_text("{bad")
        rc2, _, _ = self.apply("--restart")
        self.assertEqual(rc2, 1)
        rc3, _, _ = self.run_cli("apply", *BASE, *MODELS, "--restart")  # no key anywhere
        self.assertEqual(rc3, 1)
        self.stop.assert_not_called()
        self.spawn.assert_not_called()


class SeparateLauncherTests(CliCase):
    @property
    def sep3p(self):
        return self.home / ".config" / "Claude-gateway-3p"

    @property
    def sep_code(self):
        return self.home / ".claude-gateway" / "settings.json"

    def sep_config(self):
        meta = json.loads((self.sep3p / "configLibrary" / "_meta.json").read_text())
        return json.loads((self.sep3p / "configLibrary" / (meta["appliedId"] + ".json")).read_text())

    def test_apply_separate_writes_its_own_profile_and_a_launcher(self):
        rc, out, err = self.apply("--separate")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.sep_config()["inferenceGatewayBaseUrl"], "https://gw.example.com")
        self.assertIs(json.loads((self.sep3p / "claude-desktop-extra.json").read_text())
                      ["allowNonAnthropicModels"], True)
        self.assertIn("ANTHROPIC_BASE_URL", json.loads(self.sep_code.read_text())["env"])
        self.assertTrue((self.home / ".local" / "bin" / "claude-gateway").exists())
        self.assertTrue((self.home / ".local" / "share" / "applications"
                         / "com.anthropic.Claude-gateway.desktop").exists())
        # the normal Claude Desktop and Claude Code are left alone
        self.assertFalse(self.user3p.exists())
        self.assertFalse((self.home / ".claude").exists())

    def test_separate_restart_stops_only_the_gateway_profile(self):
        self.stop.return_value = [7]
        rc, out, err = self.apply("--separate", "--restart")
        self.assertEqual(rc, 0, err)
        self.assertEqual(str(self.stop.call_args.args[0]), str(self.home / ".config" / "Claude-gateway"))
        self.assertIs(self.stop.call_args.kwargs["default_profile"], False)
        self.spawn.assert_called_once_with([str(self.home / ".local" / "bin" / "claude-gateway")])

    def test_default_restart_still_targets_the_default_profile(self):
        self.apply("--restart")
        self.assertIs(self.stop.call_args.kwargs["default_profile"], True)
        self.assertFalse((self.home / ".local" / "bin" / "claude-gateway").exists())

    def test_separate_without_restart_prints_the_launcher_name(self):
        rc, out, _ = self.apply("--separate")
        self.assertIn("claude-gateway", out)
        self.spawn.assert_not_called()

    def test_failed_separate_apply_stops_nothing_and_writes_nothing(self):
        self.sep3p.mkdir(parents=True)
        (self.sep3p / "claude-desktop-extra.json").write_text("{bad")
        rc, _, err = self.apply("--separate", "--restart")
        self.assertEqual(rc, 1)
        self.assertIn("nothing was written", err)
        self.stop.assert_not_called()
        self.assertFalse((self.sep3p / "configLibrary").exists())
        self.assertFalse((self.home / ".local").exists())

    def test_status_separate_reports_the_gateway_profile(self):
        rc, out, _ = self.run_cli("status", "--separate")
        self.assertIn("desktop_config: missing", out)
        self.assertIn("launcher: missing", out)
        self.apply("--separate")
        rc, out, _ = self.run_cli("status", "--separate")
        self.assertIn("models: 2", out)
        self.assertIn("launcher: present", out)
        _, default_out, _ = self.run_cli("status")
        self.assertIn("desktop_config: missing", default_out)  # the normal profile is untouched

    def test_uninstall_also_removes_the_launcher_and_keeps_the_profile(self):
        self.apply("--separate")
        rc, out, err = self.run_cli("uninstall")
        self.assertEqual(rc, 0, err)
        self.assertFalse((self.home / ".local" / "bin" / "claude-gateway").exists())
        self.assertTrue((self.sep3p / "configLibrary" / "_meta.json").exists())
        self.assertIn(str(self.sep3p), out)


class OtherCommandTests(CliCase):
    def test_install_puts_the_gui_in_the_menu_without_touching_claude(self):
        rc, out, err = self.run_cli("install")
        self.assertEqual(rc, 0, err)
        share = self.home / ".local" / "share" / "claude-endpoint-mapper"
        self.assertTrue((share / "endpoint-mapper-gui.py").exists())
        self.assertTrue((share / "endpoint_mapper.py").exists())
        self.assertFalse((share / "tests").exists())
        self.assertTrue((self.home / ".local" / "bin" / "claude-endpoint-mapper").exists())
        self.assertTrue((self.home / ".local" / "share" / "applications"
                         / "claude-endpoint-mapper.desktop").exists())
        self.assertFalse((self.home / ".config").exists())
        self.assertFalse((self.home / ".claude").exists())
        self.assertIn("claude-endpoint-mapper", out)

    def test_installed_copy_runs(self):
        self.run_cli("install")
        share = self.home / ".local" / "share" / "claude-endpoint-mapper"
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        done = subprocess.run([sys.executable, str(share / "endpoint_mapper.py"), "status"],
                              capture_output=True, text=True, env=env, cwd=str(self.home))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("native_switch: yes", done.stdout)

    def test_status_reports_fields_without_key(self):
        rc, out, _ = self.run_cli("status")
        self.assertEqual(rc, 0)
        self.assertIn("native_switch: yes", out)
        self.assertIn("desktop_config: missing", out)
        self.apply()
        rc, out, _ = self.run_cli("status")
        self.assertNotIn("desktop_config: missing", out)
        self.assertIn("models: 2", out)
        self.assertIn("extra_pref: on", out)
        self.assertIn("running: no", out)
        self.assertNotIn(KEY, out)

    def test_status_without_native_switch(self):
        self.asar.write_bytes(b"x")
        _, out, _ = self.run_cli("status")
        self.assertIn("native_switch: NO", out)

    def test_uninstall_removes_the_configurator_keeps_claude_config(self):
        self.run_cli("install")
        self.apply()
        rc, out, err = self.run_cli("uninstall")
        self.assertEqual(rc, 0, err)
        self.assertFalse((self.home / ".local" / "bin" / "claude-endpoint-mapper").exists())
        self.assertFalse((self.home / ".local" / "share" / "claude-endpoint-mapper").exists())
        self.assertTrue((self.user3p / "configLibrary" / "_meta.json").exists())
        self.assertIn(str(self.user3p), out)

    def test_models_lists_ids(self):
        gw = FakeGateway({"/v1/models": {"data": [{"id": "b"}, {"id": "a"}]}})
        self.addCleanup(gw.close)
        rc, out, err = self.run_cli("models", "--url", gw.url, "--key-stdin", stdin="k\n")
        self.assertEqual((rc, out.split()), (0, ["a", "b"]), err)


if __name__ == "__main__":
    unittest.main()
