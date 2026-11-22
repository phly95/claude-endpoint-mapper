import json
import os
import pathlib
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

import em_config

MODELS = [
    {"name": "vendor-a", "tier": "sonnet", "label": "Sonnet"},
    {"name": "vendor-b", "tier": "", "label": ""},
]


class TmpCase(unittest.TestCase):
    def tmp(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        return pathlib.Path(d)

    @staticmethod
    def load(p):
        return json.loads(pathlib.Path(p).read_text())


class FakeGateway:
    def __init__(self, routes):
        self.seen = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.seen.append((self.path, {k.lower(): v for k, v in self.headers.items()}))
                body = routes.get(self.path)
                if body is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d" % self.srv.server_port
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class FetchModelsTests(unittest.TestCase):
    def test_fetch_models_openai_and_anthropic_shapes(self):
        gw = FakeGateway({"/v1/models": {"data": [{"id": "b"}, {"id": "a"}, {"id": "a"}]}})
        self.addCleanup(gw.close)
        self.assertEqual(em_config.fetch_models(gw.url), ["a", "b"])
        gw2 = FakeGateway({"/models": {"models": [{"name": "x"}, {"id": "y"}]}})
        self.addCleanup(gw2.close)
        self.assertEqual(em_config.fetch_models(gw2.url), ["x", "y"])
        gw3 = FakeGateway({"/v1/models": [{"id": "z"}]})
        self.addCleanup(gw3.close)
        self.assertEqual(em_config.fetch_models(gw3.url + "/v1"), ["z"])

    def test_fetch_models_sends_bearer_or_x_api_key(self):
        gw = FakeGateway({"/v1/models": {"data": [{"id": "m"}]}})
        self.addCleanup(gw.close)
        em_config.fetch_models(gw.url, "k1", "bearer")
        em_config.fetch_models(gw.url, "k2", "x-api-key")
        self.assertEqual(gw.seen[0][1].get("authorization"), "Bearer k1")
        self.assertEqual(gw.seen[1][1].get("x-api-key"), "k2")

    def test_fetch_models_error_names_the_problem(self):
        gw = FakeGateway({})
        self.addCleanup(gw.close)
        with self.assertRaises(RuntimeError) as cm:
            em_config.fetch_models(gw.url, "secret-key")
        self.assertNotIn("secret-key", str(cm.exception))


class PathTests(unittest.TestCase):
    def test_userdata_3p_honours_xdg(self):
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "/x/cfg"}):
            self.assertEqual(em_config.userdata_3p(), pathlib.Path("/x/cfg/Claude-v100-3p"))
            self.assertEqual(em_config.userdata_3p("foo"), pathlib.Path("/x/cfg/Claude-foo-3p"))

    def test_userdata_is_the_profile_dir_the_launcher_passes(self):
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "/x/cfg"}):
            self.assertEqual(em_config.userdata(), pathlib.Path("/x/cfg/Claude-v100"))

    def test_userdata_3p_default_home(self):
        env = {k: v for k, v in os.environ.items() if k != "XDG_CONFIG_HOME"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(em_config.userdata_3p(),
                             pathlib.Path.home() / ".config" / "Claude-v100-3p")


class DesktopConfigTests(TmpCase):
    def write(self, d, **kw):
        args = dict(base_url="https://gw.example.com/", api_key="k", auth_scheme="bearer", models=MODELS)
        args.update(kw)
        return em_config.write_desktop_config(d, **args)

    def test_desktop_config_created_with_meta(self):
        d = self.tmp()
        res = self.write(d)
        lib = d / "configLibrary"
        meta = self.load(lib / "_meta.json")
        cfg_path = lib / (meta["appliedId"] + ".json")
        self.assertEqual(pathlib.Path(res["path"]), cfg_path)
        self.assertEqual(meta["entries"][0]["id"], meta["appliedId"])
        cfg = self.load(cfg_path)
        self.assertEqual(oct(cfg_path.stat().st_mode & 0o777), "0o600")
        self.assertEqual(cfg["inferenceProvider"], "gateway")
        self.assertEqual(cfg["inferenceCredentialKind"], "static")
        self.assertEqual(cfg["inferenceGatewayBaseUrl"], "https://gw.example.com")
        self.assertEqual(cfg["inferenceGatewayAuthScheme"], "bearer")
        self.assertEqual(cfg["inferenceGatewayApiKey"], "k")
        self.assertEqual(cfg["alwaysStartWithDefaultModel"], False)
        self.assertEqual(cfg["developerModeEnabled"], True)
        a, b = cfg["inferenceModels"]
        self.assertEqual(a, {"name": "vendor-a", "labelOverride": "Sonnet",
                             "anthropicFamilyTier": "sonnet", "isFamilyDefault": True})
        self.assertEqual(b, {"name": "vendor-b"})
        self.assertEqual(res["model_count"], 2)

    def test_apply_twice_same_models_no_duplicates(self):
        d = self.tmp()
        self.write(d)
        res = self.write(d)
        cfg = self.load(res["path"])
        self.assertEqual(len(cfg["inferenceModels"]), 2)
        self.assertEqual(len(self.load(d / "configLibrary" / "_meta.json")["entries"]), 1)

    def test_blank_key_keeps_existing_sealed_key(self):
        d = self.tmp()
        res = self.write(d, api_key="seed")
        cfg = self.load(res["path"])
        cfg["inferenceGatewayApiKey"] = "__sealed__:v1:abcdef"
        cfg["someUnrelatedKey"] = {"keep": True}
        pathlib.Path(res["path"]).write_text(json.dumps(cfg))
        res = self.write(d, api_key="")
        cfg = self.load(res["path"])
        self.assertEqual(cfg["inferenceGatewayApiKey"], "__sealed__:v1:abcdef")
        self.assertEqual(cfg["someUnrelatedKey"], {"keep": True})

    def test_no_key_and_no_existing_raises(self):
        with self.assertRaises(ValueError):
            self.write(self.tmp(), api_key="")

    def test_no_models_raises(self):
        with self.assertRaises(ValueError):
            self.write(self.tmp(), models=[{"name": "", "tier": "", "label": ""}])

    def test_invalid_json_not_overwritten(self):
        d = self.tmp()
        res = self.write(d)
        bad = pathlib.Path(res["path"])
        bad.write_text("{not json")
        with self.assertRaises(ValueError):
            self.write(d)
        self.assertEqual(bad.read_text(), "{not json")

    def test_web_search_tavily_keyless_adds_header(self):
        res = self.write(self.tmp(), web_search="tavily")
        srv = self.load(res["path"])["managedMcpServers"][0]
        self.assertEqual(srv["server"], "websearch")
        self.assertEqual(srv["name"], "Web search")
        self.assertEqual(srv["provider"], "tavily")
        self.assertEqual(srv["headers"], {"X-Tavily-Access-Mode": "keyless"})
        self.assertEqual(srv["toolPolicy"], {"web_search": "allow"})
        self.assertIn("keyless", res["web_search"])

    def test_web_search_vendor_headers(self):
        d = self.tmp()
        r = self.write(d, web_search="tavily", web_search_key="tk")
        self.assertEqual(self.load(r["path"])["managedMcpServers"][0]["headers"],
                         {"Authorization": "Bearer tk"})
        r = self.write(d, web_search="brave", web_search_key="bk")
        self.assertEqual(self.load(r["path"])["managedMcpServers"][0]["headers"],
                         {"X-Subscription-Token": "bk"})
        r = self.write(d, web_search="exa", web_search_key="ek")
        self.assertEqual(self.load(r["path"])["managedMcpServers"][0]["headers"],
                         {"x-api-key": "ek"})

    def test_web_search_none_removes_only_ours(self):
        d = self.tmp()
        r = self.write(d, web_search="tavily")
        cfg = self.load(r["path"])
        cfg["managedMcpServers"].append({"name": "Other", "server": "other"})
        pathlib.Path(r["path"]).write_text(json.dumps(cfg))
        r = self.write(d, web_search="none")
        servers = self.load(r["path"])["managedMcpServers"]
        self.assertEqual([s["server"] for s in servers], ["other"])

    def test_web_search_keep_leaves_servers_alone(self):
        d = self.tmp()
        r = self.write(d, web_search="tavily")
        before = self.load(r["path"])["managedMcpServers"]
        r = self.write(d)
        self.assertEqual(self.load(r["path"])["managedMcpServers"], before)

    def test_web_search_blank_key_same_provider_reuses_headers(self):
        d = self.tmp()
        self.write(d, web_search="brave", web_search_key="bk")
        r = self.write(d, web_search="brave")
        self.assertEqual(self.load(r["path"])["managedMcpServers"][0]["headers"],
                         {"X-Subscription-Token": "bk"})

    def test_brave_without_key_raises(self):
        with self.assertRaises(ValueError):
            self.write(self.tmp(), web_search="brave")

    def test_custom_requires_url(self):
        with self.assertRaises(ValueError):
            self.write(self.tmp(), web_search="custom")
        r = self.write(self.tmp(), web_search="custom", web_search_url="https://s.example/api",
                       web_search_key="t")
        srv = self.load(r["path"])["managedMcpServers"][0]
        self.assertEqual(srv["provider"], "custom")
        self.assertEqual(srv["customUrl"], "https://s.example/api")
        self.assertEqual(srv["headers"], {"Authorization": "Bearer t"})


class ClaudeCodeConfigTests(TmpCase):
    def test_claude_code_env_merge(self):
        p = self.tmp() / "settings.json"
        p.write_text(json.dumps({"theme": "dark", "env": {"KEEP": "1"}}))
        em_config.write_claude_code_config(p, "https://gw.example.com/", "tok", MODELS)
        s = self.load(p)
        self.assertEqual(s["theme"], "dark")
        self.assertEqual(s["env"]["KEEP"], "1")
        self.assertEqual(s["env"]["ANTHROPIC_BASE_URL"], "https://gw.example.com")
        self.assertEqual(s["env"]["ANTHROPIC_AUTH_TOKEN"], "tok")
        self.assertEqual(s["env"]["ANTHROPIC_DEFAULT_SONNET_MODEL"], "vendor-a")
        self.assertNotIn("ANTHROPIC_DEFAULT_OPUS_MODEL", s["env"])
        self.assertEqual(oct(p.stat().st_mode & 0o777), "0o600")

    def test_blank_key_leaves_token_untouched(self):
        p = self.tmp() / "settings.json"
        p.write_text(json.dumps({"env": {"ANTHROPIC_AUTH_TOKEN": "old"}}))
        em_config.write_claude_code_config(p, "https://gw", "", MODELS)
        self.assertEqual(self.load(p)["env"]["ANTHROPIC_AUTH_TOKEN"], "old")

    def test_created_when_missing(self):
        p = self.tmp() / "sub" / "settings.json"
        em_config.write_claude_code_config(p, "https://gw", "k", MODELS)
        self.assertEqual(self.load(p)["env"]["ANTHROPIC_BASE_URL"], "https://gw")

    def test_cleared_tier_removes_env(self):
        p = self.tmp() / "settings.json"
        em_config.write_claude_code_config(p, "https://gw", "k", MODELS)
        em_config.write_claude_code_config(p, "https://gw", "", [{"name": "", "tier": "sonnet", "label": ""}])
        self.assertNotIn("ANTHROPIC_DEFAULT_SONNET_MODEL", self.load(p)["env"])

    def test_invalid_json_not_overwritten(self):
        p = self.tmp() / "settings.json"
        p.write_text("{oops")
        with self.assertRaises(ValueError):
            em_config.write_claude_code_config(p, "https://gw", "k", MODELS)
        self.assertEqual(p.read_text(), "{oops")


class ExtraPrefTests(TmpCase):
    def test_extra_pref_sets_flag_keeps_other_keys(self):
        d = self.tmp()
        (d / "claude-desktop-extra.json").write_text(json.dumps({"nativeTitlebar": True}))
        res = em_config.write_extra_pref(d)
        cfg = self.load(d / "claude-desktop-extra.json")
        self.assertEqual(cfg, {"nativeTitlebar": True, "allowNonAnthropicModels": True})
        self.assertIsNone(res["warning"])

    def test_extra_pref_created_when_missing(self):
        d = self.tmp() / "Claude-v100-3p"
        em_config.write_extra_pref(d)
        self.assertEqual(self.load(d / "claude-desktop-extra.json"), {"allowNonAnthropicModels": True})

    def test_extra_pref_warns_when_jsonc_disables(self):
        d = self.tmp()
        (d / "claude-desktop-extra.jsonc").write_text('{\n // mine\n "allowNonAnthropicModels": false,\n}\n')
        res = em_config.write_extra_pref(d)
        self.assertIn("claude-desktop-extra.jsonc", res["warning"])

    def test_extra_pref_invalid_json_not_overwritten(self):
        d = self.tmp()
        (d / "claude-desktop-extra.json").write_text("{bad")
        with self.assertRaises(ValueError):
            em_config.write_extra_pref(d)
        self.assertEqual((d / "claude-desktop-extra.json").read_text(), "{bad")


class NativeSwitchTests(TmpCase):
    def test_has_native_switch_true_false(self):
        d = self.tmp()
        yes, no = d / "yes.asar", d / "no.asar"
        yes.write_bytes(b"xx;/*__CDB_ALLOW_NONANTHROPIC__*/(function(){})();")
        no.write_bytes(b"nothing here")
        self.assertTrue(em_config.has_native_switch(str(yes)))
        self.assertFalse(em_config.has_native_switch(str(no)))
        self.assertFalse(em_config.has_native_switch(str(d / "missing.asar")))


if __name__ == "__main__":
    unittest.main()
