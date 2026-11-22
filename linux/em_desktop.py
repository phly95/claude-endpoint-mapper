"""Desktop integration for Claude Endpoint Mapper (Linux).

Finds and stops the running Claude Desktop, and installs the configurator (this
tool's GUI) as a menu entry. Nothing here touches the Claude Desktop package.
"""
import os
import pathlib
import re
import shutil
import signal
import subprocess
import time

# Where the claude-desktop packages put the Electron binary.
ELECTRON_BINS = ("/usr/lib/claude-desktop/claude", "/usr/lib/claude-desktop-bin/claude")
STOCK_LAUNCHER = "/usr/bin/claude-desktop"
GUI_FILES = ("em_config.py", "em_form.py", "em_desktop.py", "endpoint_mapper.py", "endpoint-mapper-gui.py")


def desktop_command() -> str:
    return shutil.which("claude-desktop") or STOCK_LAUNCHER


def _exec_quote(path) -> str:
    text = str(path)
    return f'"{text}"' if re.search(r"\s", text) else text


def _refresh_desktop_db(apps_dir: pathlib.Path) -> None:
    exe = shutil.which("update-desktop-database")
    if not exe:
        return
    try:
        subprocess.run([exe, str(apps_dir)], check=False, timeout=15,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        pass


# ------------------------------------------------------------------ processes --

def find_pids(userdata, proc_root: str = "/proc", electron_bins=ELECTRON_BINS,
              default_profile: bool = True) -> list:
    """PIDs of the Claude Desktop that uses <userdata> (normally ~/.config/Claude).

    A process counts only when its executable is the Claude Desktop Electron binary
    (Claude Code's own `claude` and other programs never match). Of those:
    - the main process of the DEFAULT profile (default_profile=True): it has neither
      --type= nor --user-data-dir=. A named profile's main process carries
      --user-data-dir=<userdata>, so pass default_profile=False for it, otherwise
      the default profile's main process would match too, and
    - children, which carry --user-data-dir=<userdata> or its relocated <userdata>-3p.
    Another profile's processes always carry a different --user-data-dir, so they
    are left alone. Electron rewrites the main argv into one space-joined string,
    so the whole command line is searched, not individual arguments.
    """
    child = re.compile(r"(?:^|\s)--user-data-dir=" + re.escape(str(userdata)) + r"(?:-3p)?(?=\s|$)")
    typed = re.compile(r"(?:^|\s)--type=")
    has_udd = re.compile(r"(?:^|\s)--user-data-dir=")
    bins = set(electron_bins)
    pids = []
    try:
        names = os.listdir(proc_root)
    except OSError:
        return pids
    for name in names:
        if not name.isdigit():
            continue
        try:
            exe = os.readlink(os.path.join(proc_root, name, "exe"))
            with open(os.path.join(proc_root, name, "cmdline"), "rb") as f:
                raw = f.read()
        except OSError:
            continue
        if exe.endswith(" (deleted)"):  # the package was updated while it ran
            exe = exe[:-len(" (deleted)")]
        if exe not in bins:
            continue
        text = raw.replace(b"\0", b" ").decode("utf-8", "replace").strip()
        is_default_main = default_profile and not typed.search(text) and not has_udd.search(text)
        if is_default_main or child.search(text):
            pids.append(int(name))
    return sorted(pids)


def stop(userdata, timeout: float = 10.0, electron_bins=ELECTRON_BINS,
         default_profile: bool = True) -> list:
    """SIGTERM every matching process, SIGKILL whatever is left after timeout."""
    pids = find_pids(userdata, electron_bins=electron_bins, default_profile=default_profile)
    if not pids:
        return []
    for pid in pids:
        _signal(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not find_pids(userdata, electron_bins=electron_bins, default_profile=default_profile):
            return pids
        time.sleep(0.1)
    for pid in find_pids(userdata, electron_bins=electron_bins, default_profile=default_profile):
        _signal(pid, signal.SIGKILL)
    return pids


def _signal(pid: int, sig: int) -> None:
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        pass


# ------------------------------------------------------------ separate launcher --

def _launcher_paths(bin_dir, apps_dir, profile):
    return (pathlib.Path(bin_dir) / f"claude-{profile}",
            pathlib.Path(apps_dir) / f"com.anthropic.Claude-{profile}.desktop")


def write_launcher(bin_dir, apps_dir, profile: str) -> dict:
    """A `claude-<profile>` command and menu entry that run the stock launcher in its
    own profile (own data, own login), so the normal Claude Desktop is left alone."""
    script, desktop = _launcher_paths(bin_dir, apps_dir, profile)
    script.parent.mkdir(parents=True, exist_ok=True)
    desktop.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(
        "#!/usr/bin/env bash\n"
        f"# Claude {profile.capitalize()}: Claude Desktop in its own profile, on your gateway.\n"
        "# Managed by Claude Endpoint Mapper; safe to delete (`endpoint_mapper.py uninstall`).\n"
        "if command -v claude-desktop >/dev/null 2>&1; then\n"
        f'    exec claude-desktop --profile={profile} --3p "$@"\n'
        "fi\n"
        f'exec {STOCK_LAUNCHER} --profile={profile} --3p "$@"\n', encoding="utf-8")
    script.chmod(0o755)
    desktop.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name=Claude {profile.capitalize()}\n"
        "Comment=Claude Desktop on a custom gateway (separate profile)\n"
        "GenericName=AI Assistant\n"
        f"Exec={_exec_quote(script)} %U\n"
        "Icon=claude-desktop\n"
        "StartupNotify=true\n"
        f"StartupWMClass=com.anthropic.Claude-{profile}\n"
        "Categories=Utility;Development;\n", encoding="utf-8")
    desktop.chmod(0o644)
    _refresh_desktop_db(desktop.parent)
    return {"script": str(script), "desktop": str(desktop)}


def remove_launcher(bin_dir, apps_dir, profile: str) -> list:
    removed = []
    for p in _launcher_paths(bin_dir, apps_dir, profile):
        if p.exists() or p.is_symlink():
            p.unlink()
            removed.append(str(p))
    if removed:
        _refresh_desktop_db(pathlib.Path(apps_dir))
    return removed


# ---------------------------------------------------------------- configurator --

def install_gui(src_dir, share_dir, bin_dir, apps_dir) -> dict:
    """Install the configurator: a private copy of the Python files (so it keeps
    working when the repo checkout is gone), a `claude-endpoint-mapper` command and
    a menu entry. Touches nothing outside those three places."""
    src, share = pathlib.Path(src_dir), pathlib.Path(share_dir)
    missing = [n for n in GUI_FILES if not (src / n).is_file()]
    if missing:
        raise FileNotFoundError(f"missing from {src}: {', '.join(missing)}")
    share.mkdir(parents=True, exist_ok=True)
    if src.resolve() != share.resolve():
        for name in GUI_FILES:
            shutil.copyfile(src / name, share / name)
            (share / name).chmod(0o644)
    script = pathlib.Path(bin_dir) / "claude-endpoint-mapper"
    desktop = pathlib.Path(apps_dir) / "claude-endpoint-mapper.desktop"
    script.parent.mkdir(parents=True, exist_ok=True)
    desktop.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("#!/usr/bin/env bash\n"
                      "# Claude Endpoint Mapper: point Claude Desktop at your own gateway.\n"
                      f'exec python3 "{share / "endpoint-mapper-gui.py"}" "$@"\n', encoding="utf-8")
    script.chmod(0o755)
    desktop.write_text("[Desktop Entry]\n"
                       "Type=Application\n"
                       "Name=Claude Endpoint Mapper\n"
                       "Comment=Point Claude Desktop at your own gateway: endpoint, key, models\n"
                       f"Exec={_exec_quote(script)}\n"
                       "Icon=preferences-system-network\n"
                       "Terminal=false\n"
                       "Categories=Utility;Settings;\n", encoding="utf-8")
    desktop.chmod(0o644)
    _refresh_desktop_db(desktop.parent)
    return {"script": str(script), "desktop": str(desktop), "share": str(share)}


def remove_gui(share_dir, bin_dir, apps_dir) -> list:
    removed = []
    for p in (pathlib.Path(bin_dir) / "claude-endpoint-mapper",
              pathlib.Path(apps_dir) / "claude-endpoint-mapper.desktop"):
        if p.exists() or p.is_symlink():
            p.unlink()
            removed.append(str(p))
    share = pathlib.Path(share_dir)
    if share.is_dir():
        shutil.rmtree(share)
        removed.append(str(share))
    if removed:
        _refresh_desktop_db(pathlib.Path(apps_dir))
    return removed
