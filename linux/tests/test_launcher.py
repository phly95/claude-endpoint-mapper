import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

import em_launcher

SLEEP = "import time; time.sleep(60)"


class TmpCase(unittest.TestCase):
    def tmp(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        return pathlib.Path(d)


class LauncherFileTests(TmpCase):
    def test_write_launchers_script_and_desktop(self):
        bin_dir, apps = self.tmp() / "bin", self.tmp() / "apps"
        res = em_launcher.write_launchers(bin_dir, apps)
        script, desktop = pathlib.Path(res["script"]), pathlib.Path(res["desktop"])
        self.assertEqual(script, bin_dir / "claude-v100")
        self.assertTrue(os.access(script, os.X_OK))
        body = script.read_text()
        self.assertTrue(body.startswith("#!/usr/bin/env bash"))
        self.assertIn("--profile=v100 --3p", body)
        self.assertNotIn("CLAUDE_ELECTRON", body)
        self.assertEqual(desktop, apps / "com.anthropic.Claude-v100.desktop")
        d = desktop.read_text()
        self.assertIn("[Desktop Entry]", d)
        self.assertIn("Name=Claude V100", d)
        self.assertIn(f"Exec={script} %U", d)
        self.assertIn("StartupWMClass=com.anthropic.Claude-v100", d)
        self.assertIn("Type=Application", d)
        self.assertNotIn("MimeType=", d)

    def test_write_launchers_idempotent(self):
        bin_dir, apps = self.tmp() / "bin", self.tmp() / "apps"
        em_launcher.write_launchers(bin_dir, apps)
        first = ((bin_dir / "claude-v100").read_text(),
                 (apps / "com.anthropic.Claude-v100.desktop").read_text())
        em_launcher.write_launchers(bin_dir, apps)
        second = ((bin_dir / "claude-v100").read_text(),
                  (apps / "com.anthropic.Claude-v100.desktop").read_text())
        self.assertEqual(first, second)

    def test_remove_launchers(self):
        bin_dir, apps = self.tmp() / "bin", self.tmp() / "apps"
        res = em_launcher.write_launchers(bin_dir, apps)
        removed = em_launcher.remove_launchers(bin_dir, apps)
        self.assertEqual(sorted(removed), sorted([res["script"], res["desktop"]]))
        self.assertFalse(os.path.exists(res["script"]))
        self.assertEqual(em_launcher.remove_launchers(bin_dir, apps), [])


class ProcessTests(TmpCase):
    def test_find_pids_matches_only_v100_userdata(self):
        root = self.tmp()
        ud = pathlib.Path("/home/u/.config/Claude-v100")

        def proc(name, *args):
            (root / name).mkdir()
            (root / name / "cmdline").write_bytes(b"\0".join(a.encode() for a in args) + b"\0")

        proc("101", "/usr/lib/claude-desktop/claude", f"--user-data-dir={ud}", "--type=renderer")
        proc("102", "/usr/lib/claude-desktop/claude", "--user-data-dir=/home/u/.config/Claude")
        proc("103", "/usr/lib/claude-desktop/claude", "--user-data-dir=/home/u/.config/Claude-v100-extra")
        proc("104", "bash", "-c", f"echo --user-data-dir={ud}")
        (root / "self").mkdir()
        (root / "self" / "cmdline").write_bytes(f"x\0--user-data-dir={ud}\0".encode())
        self.assertEqual(em_launcher.find_pids(ud, proc_root=str(root)), [101])

    def test_find_pids_matches_electron_main_with_space_joined_argv(self):
        # Electron rewrites its main process argv into ONE space-separated string.
        root = self.tmp()
        ud = pathlib.Path("/home/u/.config/Claude-v100")
        (root / "201").mkdir()
        joined = f"/usr/lib/claude-desktop/claude --ozone-platform=wayland --user-data-dir={ud}"
        (root / "201" / "cmdline").write_bytes(joined.encode() + b"\0")
        self.assertEqual(em_launcher.find_pids(ud, proc_root=str(root)), [201])

    def test_find_pids_matches_relocated_3p_children(self):
        root = self.tmp()
        ud = pathlib.Path("/home/u/.config/Claude-v100")
        (root / "301").mkdir()
        (root / "301" / "cmdline").write_bytes(
            f"/usr/lib/claude-desktop/claude\0--type=gpu-process\0--user-data-dir={ud}-3p\0".encode())
        self.assertEqual(em_launcher.find_pids(ud, proc_root=str(root)), [301])

    def test_find_pids_handles_vanished_process(self):
        root = self.tmp()
        (root / "55").mkdir()  # no cmdline file
        self.assertEqual(em_launcher.find_pids(pathlib.Path("/x/Claude-v100"), proc_root=str(root)), [])

    def test_find_pids_ignores_other_programs_that_only_mention_the_dir(self):
        root = self.tmp()
        ud = pathlib.Path("/home/u/.config/Claude-v100")
        (root / "401").mkdir()
        (root / "401" / "cmdline").write_bytes(f"pgrep\0-f\0--user-data-dir={ud}\0".encode())
        self.assertEqual(em_launcher.find_pids(ud, proc_root=str(root)), [])

    def test_stop_signals_only_matches(self):
        ud = self.tmp() / "Claude-v100"
        other = self.tmp() / "Claude"
        mine = subprocess.Popen([sys.executable, "-c", SLEEP, f"--user-data-dir={ud}"])
        stock = subprocess.Popen([sys.executable, "-c", SLEEP, f"--user-data-dir={other}"])
        for p in (stock, mine):
            self.addCleanup(lambda p=p: (p.kill(), p.wait()))
        exe = os.path.basename(sys.executable)
        deadline = time.time() + 5
        while time.time() < deadline and em_launcher.find_pids(ud, exe_name=exe) != [mine.pid]:
            time.sleep(0.05)
        stopped = em_launcher.stop(ud, timeout=5, exe_name=exe)
        self.assertEqual(stopped, [mine.pid])
        mine.wait(timeout=5)
        self.assertIsNone(stock.poll())

    def test_stop_with_nothing_running(self):
        self.assertEqual(em_launcher.stop(self.tmp() / "Claude-v100", timeout=1), [])


if __name__ == "__main__":
    unittest.main()
