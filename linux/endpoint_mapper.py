#!/usr/bin/env python3
"""Claude Endpoint Mapper for Nobara / Linux: CLI.

Maps models from any Anthropic-compatible gateway onto Claude's tier slots and
sets up a separate "Claude V100" Claude Desktop launcher for it. Keys are read
from stdin only (--key-stdin, --web-search-key-stdin) and never printed.
"""
import argparse
import os
import pathlib
import subprocess
import sys

import em_config
import em_launcher

PROFILE = "v100"


def _home() -> pathlib.Path:
    return pathlib.Path.home()


def _bin_dir() -> pathlib.Path:
    return _home() / ".local" / "bin"


def _apps_dir() -> pathlib.Path:
    return pathlib.Path(os.environ.get("XDG_DATA_HOME") or _home() / ".local" / "share") / "applications"


def _asar() -> str:
    return os.environ.get("EM_ASAR") or em_config.SYSTEM_ASAR


def _spawn(script: str) -> None:
    subprocess.Popen([script], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def _read_secret(enabled: bool) -> str:
    return sys.stdin.readline().rstrip("\r\n") if enabled else ""


def _parse_model(spec: str) -> dict:
    name, _, rest = spec.partition("=")
    tier, _, label = rest.partition("=")
    name, tier, label = name.strip(), tier.strip().lower(), label.strip()
    if not name:
        raise ValueError(f"empty model name in --model {spec!r}")
    if tier and tier not in em_config.TIERS:
        raise ValueError(f"unknown tier {tier!r} for model {name!r} (use one of {', '.join(em_config.TIERS)})")
    return {"name": name, "tier": tier, "label": label}


def cmd_models(args) -> int:
    key = _read_secret(args.key_stdin)
    for model_id in em_config.fetch_models(args.url, key, args.auth):
        print(model_id)
    return 0


def cmd_apply(args) -> int:
    if not em_config.has_native_switch(_asar()):
        raise RuntimeError(
            "The installed Claude Desktop has no allowNonAnthropicModels switch "
            "(claude-desktop-extra provides it), so non-Anthropic model names would be "
            "dropped. Nothing was written.")
    models = [_parse_model(m) for m in args.model]
    key = _read_secret(args.key_stdin)
    ws_key = _read_secret(args.web_search_key_stdin)
    code_path = None
    if not args.no_code:
        code_path = pathlib.Path(args.code_settings) if args.code_settings \
            else _home() / f".claude-{PROFILE}" / "settings.json"
    userdata = em_config.userdata(PROFILE)
    user_dir = em_config.userdata_3p(PROFILE)

    def make_plans():
        # Validates every input and every existing file; touches nothing.
        plans = {
            "desktop": em_config.plan_desktop_config(
                user_dir, args.url, key, args.auth, models, web_search=args.web_search,
                web_search_key=ws_key, web_search_url=args.web_search_url or ""),
            "extra": em_config.plan_extra_pref(user_dir),
        }
        if code_path is not None:
            plans["code"] = em_config.plan_claude_code_config(code_path, args.url, key, models)
        return plans

    plans = make_plans()  # fails here, before anything is stopped or written
    if args.restart:
        stopped = em_launcher.stop(userdata)
        if stopped:
            print(f"Stopped Claude {PROFILE.upper()} ({len(stopped)} processes).")
            plans = make_plans()  # the app may have rewritten its config while quitting
    running_before = not args.restart and bool(em_launcher.find_pids(userdata))
    em_config.commit(*plans.values())

    cfg, extra = plans["desktop"].result, plans["extra"].result
    print(f"Desktop config: {cfg['model_count']} models -> {cfg['path']}")
    if cfg["web_search"] != "unchanged":
        print(f"Web search: {cfg['web_search']}")
    print(f"Non-Anthropic models enabled: {extra['path']}")
    if extra["warning"]:
        print("warning: " + extra["warning"], file=sys.stderr)
    if code_path is not None:
        print(f"Claude Code settings: {code_path}")
    files = em_launcher.write_launchers(_bin_dir(), _apps_dir(), PROFILE)
    print(f"Launcher: {files['script']}")
    print(f"Menu entry: {files['desktop']}")

    if args.restart:
        _spawn(files["script"])
        print(f"Started Claude {PROFILE.upper()}.")
    elif running_before:
        print(f"Claude {PROFILE.upper()} is running: restart it to apply (apply --restart).")
    else:
        print(f"Start it with: claude-{PROFILE}")
    return 0


def cmd_status(args) -> int:
    asar = _asar()
    print("native_switch: " + ("yes" if em_config.has_native_switch(asar)
                               else f"NO (not found in {asar})"))
    user_dir = em_config.userdata_3p(PROFILE)
    try:
        state = em_config.read_desktop_state(user_dir)
    except ValueError as e:
        print(f"desktop_config: unreadable ({e})")
        state = None
    else:
        if state is None:
            print("desktop_config: missing")
        else:
            print(f"desktop_config: {state['path']}")
            print(f"gateway: {state['base_url']}")
            print(f"models: {state['model_count']}")
            print("key: " + ("set" if state["has_key"] else "missing"))
            print("web_search: " + ("on" if state["web_search"] else "off"))
    print(f"extra_pref: {em_config.read_extra_pref(user_dir)}")
    script = _bin_dir() / f"claude-{PROFILE}"
    print("launcher: " + ("present" if script.exists() else "missing"))
    pids = em_launcher.find_pids(em_config.userdata(PROFILE))
    print("running: " + (f"yes (pids {', '.join(map(str, pids))})" if pids else "no"))
    return 0


def cmd_uninstall(args) -> int:
    removed = em_launcher.remove_launchers(_bin_dir(), _apps_dir(), PROFILE)
    for p in removed:
        print(f"Removed {p}")
    if not removed:
        print("No launcher files found.")
    print("Kept (delete yourself to also drop the login and gateway key):")
    print(f"  {em_config.userdata(PROFILE)}")
    print(f"  {em_config.userdata_3p(PROFILE)}")
    print(f"  {_home() / f'.claude-{PROFILE}'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="endpoint_mapper.py", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--url", required=True, help="gateway base URL")
        sp.add_argument("--auth", choices=("bearer", "x-api-key"), default="bearer")
        sp.add_argument("--key-stdin", action="store_true", help="read the API key from the first stdin line")

    sp = sub.add_parser("models", help="list model ids offered by the endpoint")
    common(sp)
    sp.set_defaults(func=cmd_models)

    sp = sub.add_parser("apply", help="write the Claude V100 config and launcher")
    common(sp)
    sp.add_argument("--model", action="append", required=True, metavar="NAME[=TIER[=LABEL]]",
                    help="model to expose; TIER is one of " + ", ".join(em_config.TIERS))
    sp.add_argument("--web-search", choices=em_config.WEB_SEARCH_CHOICES, default="keep")
    sp.add_argument("--web-search-key-stdin", action="store_true",
                    help="read the web search key from the next stdin line (after the API key)")
    sp.add_argument("--web-search-url", help="search endpoint for --web-search custom")
    sp.add_argument("--no-code", action="store_true", help="do not write Claude Code settings")
    sp.add_argument("--code-settings", metavar="PATH",
                    help="Claude Code settings file (default ~/.claude-v100/settings.json)")
    sp.add_argument("--restart", action="store_true", help="stop Claude V100, apply, start it again")
    sp.set_defaults(func=cmd_apply)

    sp = sub.add_parser("status", help="show what is installed and configured")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("uninstall", help="remove the launcher script and menu entry")
    sp.set_defaults(func=cmd_uninstall)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, RuntimeError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
