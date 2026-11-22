"""Launcher files and process control for the Claude V100 profile.

`claude-v100` is a thin script around the stock /usr/bin/claude-desktop, which
already isolates a named profile (userData ~/.config/Claude-<profile>, own
window id, own Claude Code config dir). Nothing here touches the stock install.
"""
import os
import pathlib
import re
import shutil
import signal
import subprocess
import time

STOCK_LAUNCHER = "/usr/bin/claude-desktop"


def _script_text(profile: str) -> str:
    return (
        "#!/usr/bin/env bash\n"
        f"# Claude {profile.upper()}: Claude Desktop in its own profile, on the gateway.\n"
        "# Managed by Claude Endpoint Mapper; safe to delete (see `endpoint_mapper.py uninstall`).\n"
        "if command -v claude-desktop >/dev/null 2>&1; then\n"
        f'    exec claude-desktop --profile={profile} --3p "$@"\n'
        "fi\n"
        f'exec {STOCK_LAUNCHER} --profile={profile} --3p "$@"\n'
    )


def _desktop_text(script: pathlib.Path, profile: str) -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name=Claude {profile.upper()}\n"
        "Comment=Claude Desktop on a custom gateway (separate profile)\n"
        "GenericName=AI Assistant\n"
        f"Exec={script} %U\n"
        "Icon=claude-desktop\n"
        "StartupNotify=true\n"
        f"StartupWMClass=com.anthropic.Claude-{profile}\n"
        "Categories=Utility;Development;\n"
    )


def _paths(bin_dir, apps_dir, profile):
    return (pathlib.Path(bin_dir) / f"claude-{profile}",
            pathlib.Path(apps_dir) / f"com.anthropic.Claude-{profile}.desktop")


def write_launchers(bin_dir, apps_dir, profile: str = "v100") -> dict:
    script, desktop = _paths(bin_dir, apps_dir, profile)
    script.parent.mkdir(parents=True, exist_ok=True)
    desktop.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(_script_text(profile), encoding="utf-8")
    script.chmod(0o755)
    desktop.write_text(_desktop_text(script, profile), encoding="utf-8")
    desktop.chmod(0o644)
    _refresh_desktop_db(desktop.parent)
    return {"script": str(script), "desktop": str(desktop)}


def remove_launchers(bin_dir, apps_dir, profile: str = "v100") -> list:
    removed = []
    for p in _paths(bin_dir, apps_dir, profile):
        if p.exists() or p.is_symlink():
            p.unlink()
            removed.append(str(p))
    if removed:
        _refresh_desktop_db(pathlib.Path(apps_dir))
    return removed


def _refresh_desktop_db(apps_dir: pathlib.Path) -> None:
    exe = shutil.which("update-desktop-database")
    if not exe:
        return
    try:
        subprocess.run([exe, str(apps_dir)], check=False, timeout=15,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        pass


def find_pids(userdata, proc_root: str = "/proc", exe_name: str = "claude") -> list:
    """PIDs of the Electron processes running with --user-data-dir=<userdata>.

    The main process uses <userdata>; its children use the relocated <userdata>-3p.
    Electron rewrites the main process argv into one space-joined string, so the
    whole command line is searched, not individual NUL-separated arguments. Only
    programs whose argv[0] is named exe_name qualify (None = any), so a shell or
    pgrep that merely mentions the directory is never matched.
    """
    pattern = re.compile(r"(?:^|\s)--user-data-dir=" + re.escape(str(userdata)) + r"(?:-3p)?(?=\s|$)")
    pids = []
    try:
        names = os.listdir(proc_root)
    except OSError:
        return pids
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open(os.path.join(proc_root, name, "cmdline"), "rb") as f:
                raw = f.read()
        except OSError:
            continue
        text = raw.replace(b"\0", b" ").decode("utf-8", "replace").strip()
        if not text:
            continue
        if exe_name and os.path.basename(text.split(" ", 1)[0]) != exe_name:
            continue
        if pattern.search(text):
            pids.append(int(name))
    return sorted(pids)


def stop(userdata, timeout: float = 10.0, exe_name: str = "claude") -> list:
    """SIGTERM every process of this userData dir, SIGKILL leftovers after timeout.
    Matches on --user-data-dir only, so the stock Claude profile is never touched."""
    pids = find_pids(userdata, exe_name=exe_name)
    if not pids:
        return []
    for pid in pids:
        _signal(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not find_pids(userdata, exe_name=exe_name):
            return pids
        time.sleep(0.1)
    for pid in find_pids(userdata, exe_name=exe_name):
        _signal(pid, signal.SIGKILL)
    return pids


def _signal(pid: int, sig: int) -> None:
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        pass
