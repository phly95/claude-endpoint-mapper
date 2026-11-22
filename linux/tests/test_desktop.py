import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import em_desktop

ELECTRON = "/usr/lib/claude-desktop/claude"
UD = pathlib.Path("/home/u/.config/Claude")


class TmpCase(unittest.TestCase):
    def tmp(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        return pathlib.Path(d)


class FindPidsTests(TmpCase):
    def setUp(self):
        self.root = self.tmp()

    def proc(self, pid, exe, *args, joined=False):
        d = self.root / str(pid)
        d.mkdir()
        if joined:
            args = (" ".join(args),)
        (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in args) + b"\0")
        os.symlink(exe, d / "exe")

    def find(self, userdata=UD, **kw):
        return em_desktop.find_pids(userdata, proc_root=str(self.root), **kw)

    def test_default_profile_matches_main_and_its_children_only(self):
        self.proc(101, ELECTRON, ELECTRON, "--ozone-platform=wayland")
        self.proc(102, ELECTRON, ELECTRON, "--type=utility", f"--user-data-dir={UD}")
        self.proc(103, ELECTRON, ELECTRON, "--type=gpu-process", f"--user-data-dir={UD}-3p")
        self.proc(104, ELECTRON, ELECTRON, "--type=zygote")
        self.proc(105, ELECTRON, ELECTRON, "--user-data-dir=/home/u/.config/Claude-other")
        self.proc(106, ELECTRON, ELECTRON, "--type=renderer", "--user-data-dir=/home/u/.config/Claude-other-3p")
        self.proc(107, "/home/u/.local/bin/claude", "claude", "--resume")
        self.proc(108, "/usr/lib/claude-desktop/chrome_crashpad_handler", "/usr/lib/claude-desktop/chrome_crashpad_handler")
        self.proc(109, "/usr/bin/bash", "bash", "-c", f"echo --user-data-dir={UD}")
        self.assertEqual(self.find(), [101, 102, 103])

    def test_children_with_space_joined_argv_still_match(self):
        self.proc(201, ELECTRON, ELECTRON, "--type=utility", f"--user-data-dir={UD}", joined=True)
        self.assertEqual(self.find(), [201])

    def test_binary_replaced_by_a_package_update_still_counts(self):
        self.proc(301, ELECTRON + " (deleted)", ELECTRON, "--ozone-platform=wayland")
        self.assertEqual(self.find(), [301])

    def test_other_electron_install_is_configurable(self):
        self.proc(401, "/opt/claude/claude", "/opt/claude/claude")
        self.assertEqual(self.find(), [])
        self.assertEqual(self.find(electron_bins=("/opt/claude/claude",)), [401])

    def test_vanished_process_and_non_numeric_entries_are_ignored(self):
        (self.root / "55").mkdir()
        (self.root / "self").mkdir()
        self.assertEqual(self.find(), [])


class NamedProfilePidTests(TmpCase):
    UD = pathlib.Path("/home/u/.config/Claude-gateway")

    def setUp(self):
        self.root = self.tmp()

    def proc(self, pid, exe, *args):
        d = self.root / str(pid)
        d.mkdir()
        (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in args) + b"\0")
        os.symlink(exe, d / "exe")

    def test_named_profile_never_matches_the_default_main_process(self):
        self.proc(101, ELECTRON, ELECTRON, f"--user-data-dir={self.UD}")                    # named main
        self.proc(102, ELECTRON, ELECTRON, "--type=gpu-process", f"--user-data-dir={self.UD}-3p")
        self.proc(103, ELECTRON, ELECTRON, "--ozone-platform=wayland")                       # DEFAULT main
        self.proc(104, ELECTRON, ELECTRON, "--type=utility", "--user-data-dir=/home/u/.config/Claude")
        self.proc(105, ELECTRON, ELECTRON, "--type=zygote")
        self.proc(106, "/home/u/.local/bin/claude", "claude")
        pids = em_desktop.find_pids(self.UD, proc_root=str(self.root), default_profile=False)
        self.assertEqual(pids, [101, 102])

    def test_default_profile_ignores_the_named_profile(self):
        self.proc(201, ELECTRON, ELECTRON, f"--user-data-dir={self.UD}")
        self.proc(202, ELECTRON, ELECTRON, "--ozone-platform=wayland")
        self.assertEqual(em_desktop.find_pids(UD, proc_root=str(self.root)), [202])

    def test_stop_passes_default_profile_through(self):
        with mock.patch.object(em_desktop, "find_pids", return_value=[]) as fp:
            em_desktop.stop(self.UD, timeout=1, default_profile=False)
        self.assertIs(fp.call_args.kwargs["default_profile"], False)


class LauncherFileTests(TmpCase):
    def test_write_launcher_script_and_desktop(self):
        bin_dir, apps = self.tmp() / "bin", self.tmp() / "apps"
        res = em_desktop.write_launcher(bin_dir, apps, "gateway")
        script, desktop = pathlib.Path(res["script"]), pathlib.Path(res["desktop"])
        self.assertEqual(script, bin_dir / "claude-gateway")
        self.assertTrue(os.access(script, os.X_OK))
        body = script.read_text()
        self.assertTrue(body.startswith("#!/usr/bin/env bash"))
        self.assertIn("--profile=gateway --3p", body)
        self.assertNotIn("CLAUDE_ELECTRON", body)
        self.assertEqual(desktop, apps / "com.anthropic.Claude-gateway.desktop")
        d = desktop.read_text()
        self.assertIn("Name=Claude Gateway", d)
        self.assertIn(f"Exec={script} %U", d)
        self.assertIn("StartupWMClass=com.anthropic.Claude-gateway", d)
        self.assertIn("Type=Application", d)
        self.assertNotIn("MimeType=", d)  # claude:// stays with the normal Claude Desktop

    def test_write_launcher_is_idempotent_and_quotes_paths_with_spaces(self):
        bin_dir, apps = self.tmp() / "my bin", self.tmp() / "apps"
        em_desktop.write_launcher(bin_dir, apps, "gateway")
        first = (bin_dir / "claude-gateway").read_text()
        res = em_desktop.write_launcher(bin_dir, apps, "gateway")
        self.assertEqual((bin_dir / "claude-gateway").read_text(), first)
        self.assertIn(f'Exec="{bin_dir}/claude-gateway" %U', pathlib.Path(res["desktop"]).read_text())

    def test_remove_launcher(self):
        bin_dir, apps = self.tmp() / "bin", self.tmp() / "apps"
        res = em_desktop.write_launcher(bin_dir, apps, "gateway")
        removed = em_desktop.remove_launcher(bin_dir, apps, "gateway")
        self.assertEqual(sorted(removed), sorted([res["script"], res["desktop"]]))
        self.assertEqual(em_desktop.remove_launcher(bin_dir, apps, "gateway"), [])


class StopTests(TmpCase):
    def spawn(self, code):
        p = subprocess.Popen([sys.executable, "-c", code])
        self.addCleanup(lambda: (p.kill(), p.wait()))
        return p

    def test_stop_terminates_only_what_find_pids_returns(self):
        mine = self.spawn("import time; time.sleep(60)")
        other = self.spawn("import time; time.sleep(60)")
        with mock.patch.object(em_desktop, "find_pids",
                               side_effect=lambda *a, **k: [mine.pid] if mine.poll() is None else []):
            self.assertEqual(em_desktop.stop(UD, timeout=5), [mine.pid])
        mine.wait(timeout=5)
        self.assertIsNone(other.poll())

    def test_stop_escalates_to_sigkill_after_timeout(self):
        stubborn = self.spawn("import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                              "print('ready', flush=True); time.sleep(60)")
        time.sleep(0.5)
        with mock.patch.object(em_desktop, "find_pids",
                               side_effect=lambda *a, **k: [stubborn.pid] if stubborn.poll() is None else []):
            em_desktop.stop(UD, timeout=0.5)
        self.assertEqual(stubborn.wait(timeout=5), -signal.SIGKILL)

    def test_stop_with_nothing_running(self):
        with mock.patch.object(em_desktop, "find_pids", return_value=[]):
            self.assertEqual(em_desktop.stop(UD, timeout=1), [])


class DesktopCommandTests(TmpCase):
    def test_prefers_claude_desktop_on_path_else_usr_bin(self):
        d = self.tmp()
        exe = d / "claude-desktop"
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o755)
        with mock.patch.dict(os.environ, {"PATH": str(d)}):
            self.assertEqual(em_desktop.desktop_command(), str(exe))
        with mock.patch.dict(os.environ, {"PATH": str(self.tmp())}):
            self.assertEqual(em_desktop.desktop_command(), "/usr/bin/claude-desktop")


class InstallGuiTests(TmpCase):
    FILES = em_desktop.GUI_FILES

    def fake_src(self):
        src = self.tmp() / "src"
        (src / "tests").mkdir(parents=True)
        for name in self.FILES:
            (src / name).write_text(f"# {name}\n")
        (src / "tests" / "test_x.py").write_text("# test\n")
        (src / "notes.md").write_text("not shipped\n")
        return src

    def test_install_gui_copies_modules_and_writes_entry(self):
        src, share = self.fake_src(), self.tmp() / "share" / "claude-endpoint-mapper"
        bin_dir, apps = self.tmp() / "bin", self.tmp() / "apps"
        res = em_desktop.install_gui(src, share, bin_dir, apps)
        self.assertEqual(sorted(p.name for p in share.iterdir()), sorted(self.FILES))
        script, desktop = pathlib.Path(res["script"]), pathlib.Path(res["desktop"])
        self.assertEqual(script, bin_dir / "claude-endpoint-mapper")
        self.assertTrue(os.access(script, os.X_OK))
        self.assertIn(f"{share}/endpoint-mapper-gui.py", script.read_text())
        self.assertTrue(script.read_text().startswith("#!/usr/bin/env bash"))
        self.assertEqual(desktop, apps / "claude-endpoint-mapper.desktop")
        d = desktop.read_text()
        self.assertIn("Name=Claude Endpoint Mapper", d)
        self.assertIn(f"Exec={script}", d)
        self.assertIn("Terminal=false", d)
        self.assertNotIn("MimeType=", d)

    def test_install_gui_overwrites_stale_copy_and_is_idempotent(self):
        src, share = self.fake_src(), self.tmp() / "share"
        bin_dir, apps = self.tmp() / "bin", self.tmp() / "apps"
        em_desktop.install_gui(src, share, bin_dir, apps)
        (src / "em_config.py").write_text("# new\n")
        em_desktop.install_gui(src, share, bin_dir, apps)
        self.assertEqual((share / "em_config.py").read_text(), "# new\n")
        self.assertEqual(len(list(share.iterdir())), len(self.FILES))

    def test_install_gui_missing_module_raises(self):
        src = self.fake_src()
        (src / "em_form.py").unlink()
        with self.assertRaises(FileNotFoundError):
            em_desktop.install_gui(src, self.tmp() / "share", self.tmp() / "bin", self.tmp() / "apps")

    def test_exec_path_with_spaces_is_quoted(self):
        src = self.fake_src()
        bin_dir = self.tmp() / "my bin"
        res = em_desktop.install_gui(src, self.tmp() / "share", bin_dir, self.tmp() / "apps")
        self.assertIn(f'Exec="{bin_dir}/claude-endpoint-mapper"', pathlib.Path(res["desktop"]).read_text())

    def test_install_gui_from_the_installed_copy_itself(self):
        src, share = self.fake_src(), self.tmp() / "share"
        bin_dir, apps = self.tmp() / "bin", self.tmp() / "apps"
        em_desktop.install_gui(src, share, bin_dir, apps)
        em_desktop.install_gui(share, share, bin_dir, apps)
        self.assertEqual(len(list(share.iterdir())), len(self.FILES))

    def test_remove_gui(self):
        src, share = self.fake_src(), self.tmp() / "share"
        bin_dir, apps = self.tmp() / "bin", self.tmp() / "apps"
        res = em_desktop.install_gui(src, share, bin_dir, apps)
        removed = em_desktop.remove_gui(share, bin_dir, apps)
        self.assertEqual(sorted(removed), sorted([res["script"], res["desktop"], str(share)]))
        self.assertFalse(share.exists())
        self.assertEqual(em_desktop.remove_gui(share, bin_dir, apps), [])


if __name__ == "__main__":
    unittest.main()
