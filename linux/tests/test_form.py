import json
import pathlib
import shutil
import tempfile
import unittest

import em_config
import em_form


class GuessTierTests(unittest.TestCase):
    def test_guess_tier_by_family_name(self):
        self.assertEqual(em_form.guess_tier("anthropic/Claude-Haiku-4"), "haiku")
        self.assertEqual(em_form.guess_tier("claude-sonnet-5-5"), "sonnet")
        self.assertEqual(em_form.guess_tier("claude-opus-5"), "opus")
        self.assertEqual(em_form.guess_tier("claude-fable-5-2"), "fable")
        self.assertEqual(em_form.guess_tier("mythos-1"), "mythos")
        self.assertEqual(em_form.guess_tier("deepseek-v4"), "")


class BuildRequestTests(unittest.TestCase):
    ROWS = [
        {"use": True, "name": "vendor-a", "tier": "sonnet", "label": "Sonnet"},
        {"use": False, "name": "skipped", "tier": "opus", "label": ""},
        {"use": True, "name": " qwen3:30b ", "tier": "", "label": ""},
        {"use": True, "name": "", "tier": "haiku", "label": "blank name"},
    ]

    def build(self, **kw):
        args = dict(url=" https://gw.example.com/ ", auth="bearer", key="", rows=self.ROWS, code=True,
                    ws_enabled=False, ws_provider="tavily", ws_key="", ws_url="")
        args.update(kw)
        return em_form.build_apply_request(**args)

    def test_models_only_checked_and_named(self):
        argv, stdin = self.build()
        self.assertEqual(argv[0], "apply")
        pairs = [argv[i + 1] for i, a in enumerate(argv) if a == "--model"]
        self.assertEqual(pairs, ["vendor-a=sonnet=Sonnet", "qwen3:30b"])
        self.assertEqual(argv[argv.index("--url") + 1], "https://gw.example.com/")
        self.assertEqual(stdin, "")
        self.assertNotIn("--key-stdin", argv)

    def test_keys_go_through_stdin_in_order_never_argv(self):
        argv, stdin = self.build(key="K1", ws_enabled=True, ws_provider="brave", ws_key="W1")
        self.assertIn("--key-stdin", argv)
        self.assertIn("--web-search-key-stdin", argv)
        self.assertEqual(stdin, "K1\nW1\n")
        self.assertNotIn("K1", argv)
        self.assertNotIn("W1", argv)

    def test_web_search_key_alone_is_first_stdin_line(self):
        argv, stdin = self.build(ws_enabled=True, ws_provider="exa", ws_key="W1")
        self.assertNotIn("--key-stdin", argv)
        self.assertEqual(stdin, "W1\n")

    def test_web_search_off_removes_and_other_means_custom(self):
        argv, _ = self.build()
        self.assertEqual(argv[argv.index("--web-search") + 1], "none")
        argv, _ = self.build(ws_enabled=True, ws_provider="other", ws_url="https://s.example/api")
        self.assertEqual(argv[argv.index("--web-search") + 1], "custom")
        self.assertEqual(argv[argv.index("--web-search-url") + 1], "https://s.example/api")

    def test_code_unchecked_adds_no_code_and_restart_always(self):
        argv, _ = self.build(code=False)
        self.assertIn("--no-code", argv)
        self.assertIn("--restart", argv)
        argv, _ = self.build(code=True)
        self.assertNotIn("--no-code", argv)

    def test_x_api_key_auth(self):
        argv, _ = self.build(auth="x-api-key")
        self.assertEqual(argv[argv.index("--auth") + 1], "x-api-key")

    def test_no_usable_models_or_url_raises(self):
        with self.assertRaises(ValueError):
            self.build(rows=[{"use": True, "name": "", "tier": "", "label": ""}])
        with self.assertRaises(ValueError):
            self.build(url="  ")


class ReadPrefillTests(unittest.TestCase):
    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)

    def test_missing_config_gives_none(self):
        self.assertIsNone(em_config.read_prefill(self.d))

    def test_prefill_reads_everything_but_the_key(self):
        em_config.write_desktop_config(
            self.d, "https://gw.example.com", "SECRET", "x-api-key",
            [{"name": "a", "tier": "opus", "label": "Opus"}, {"name": "b", "tier": "", "label": ""}],
            web_search="custom", web_search_url="https://s.example/api", web_search_key="t")
        p = em_config.read_prefill(self.d)
        self.assertEqual(p["base_url"], "https://gw.example.com")
        self.assertEqual(p["auth_scheme"], "x-api-key")
        self.assertTrue(p["has_key"])
        self.assertEqual(p["models"], [{"name": "a", "tier": "opus", "label": "Opus"},
                                       {"name": "b", "tier": "", "label": ""}])
        self.assertEqual(p["web_search"], {"provider": "custom", "custom_url": "https://s.example/api"})
        self.assertNotIn("SECRET", json.dumps(p))

    def test_prefill_without_web_search(self):
        em_config.write_desktop_config(self.d, "https://gw", "k", "bearer",
                                       [{"name": "a", "tier": "", "label": ""}])
        self.assertIsNone(em_config.read_prefill(self.d)["web_search"])


if __name__ == "__main__":
    unittest.main()
