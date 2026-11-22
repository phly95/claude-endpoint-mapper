# Nobara port and "Claude V100" launcher

Date: 2026-11-22. Status: approved by user in chat; mechanism revised during
implementation (see Approaches, 1) because the RPM ships a built-in switch.

## Intent

Claude Endpoint Mapper is Windows/PowerShell only. The user runs Nobara Linux 44
(KDE) with the RPM `claude-desktop-extra` and wants:

1. The same tool working on Nobara.
2. A separate launcher, **Claude V100**, that runs Claude Desktop against their
   own gateway (`ai.kewaii.com`, already used by `claude-kw` with
   `CLAUDE_CONFIG_DIR=~/.claude-v100`) without touching their normal Claude.

## Goals

- Map gateway models onto tier slots (sonnet/opus/haiku/fable/mythos) with
  friendly names, for Claude Desktop (V100 profile) and Claude Code settings.
- Optional built-in web search toggle (same `managedMcpServers` entry as Windows).
- GUI with the same fields as the Windows GUI, plus a CLI.
- `claude-v100` command and "Claude V100" menu entry.
- Stock Claude Desktop and the RPM stay untouched and keep working.

## Non-goals / anti-goals

- No sudo, no edits under `/usr`, no patching of `app.asar`.
- No change to the Windows files (`*.ps1`, `*.psm1`).
- No dependencies beyond Python 3 stdlib (+ Tk for the GUI). No `pwsh`.
- Never print or log API keys.

## Constraints and findings

- No `pwsh` on Nobara. Python 3.14 with Tk present.
- Stock launcher `/usr/bin/claude-desktop`: `--profile=NAME` gives userData
  `~/.config/Claude-NAME` (passed as `--user-data-dir=`), `CLAUDE_CONFIG_DIR=
  ~/.claude-NAME`, window id `com.anthropic.Claude-NAME`. `--3p` persists
  `deploymentMode`; 3p mode relocates userData to `<userData>-3p`, whose
  `configLibrary/` holds the gateway config (`_meta.json` + `<id>.json`).
- `claude-desktop-extra` 4.23118.1 ships an opt-in switch in `app.asar` (module
  `__CDB_ALLOW_NONANTHROPIC__`): `allowNonAnthropicModels: true` in
  `<userData>/claude-desktop-extra.json` makes the three gateway model-name
  checks (validator, `/v1/models` discovery, tier-pin sanitizer) accept any route.
  `claude-desktop-extra.jsonc` is hand-owned and wins when it sets the key.
  Applies on restart. Per mode, so it must live in the `-3p` dir.
- The Windows tool's patch target (`case"gateway":case"mantle":return X(Y)`) is
  therefore not needed here; the Windows `not_anthropic` blocklist is absent too.

## Approaches considered

1. **Built-in switch + stock profile (chosen).** Write the 3p gateway config and
   `allowNonAnthropicModels` for profile `v100`, add `claude-v100` + a menu entry.
   No patching, no copy of the 330 MB install, survives RPM updates.
2. Private patched copy of the install (`CLAUDE_ELECTRON` tree with a patched
   `app.asar`). Built and verified on a copy of the real asar during planning
   (gateway switch patched in 2 files, header hashes refreshed, idempotent), then
   dropped: unnecessary while the RPM ships the switch. Only needed if a future RPM
   drops it; `status` reports that condition.
3. Patch `/usr/lib/claude-desktop` in place with sudo. Rejected: affects stock
   Claude, wiped by updates, needs root.
4. Install `pwsh` and run the existing module. Rejected: Windows paths and
   process handling throughout.

## Design

New under `linux/`:

- `em_config.py`: `fetch_models`, `library_dir`, `write_desktop_config`,
  `write_claude_code_config`, `write_extra_pref`, `has_native_switch`.
- `em_launcher.py`: `write_launchers`, `find_pids`, `stop`.
- `endpoint_mapper.py`: CLI (`models`, `apply`, `status`, `uninstall`).
- `endpoint-mapper-gui.py`: Tk GUI, same fields as the Windows GUI.

Behaviour:

- `claude-v100` (in `~/.local/bin`) = `exec claude-desktop --profile=v100 --3p "$@"`.
- Menu entry "Claude V100" at
  `~/.local/share/applications/com.anthropic.Claude-v100.desktop`
  (`StartupWMClass=com.anthropic.Claude-v100`, no `MimeType`, so `claude://`
  stays with stock Claude).
- 3p config: `~/.config/Claude-v100-3p/configLibrary/<id>.json` + `_meta.json`,
  same JSON schema as Windows. Existing key kept when the key field is blank.
- Extra pref: `~/.config/Claude-v100-3p/claude-desktop-extra.json`
  `{"allowNonAnthropicModels": true}`, other keys kept; warn when the `.jsonc`
  sets it to `false`.
- Claude Code: merge `env` into `~/.claude-v100/settings.json`, not `~/.claude`.
- `apply --restart` stops V100 only (processes whose cmdline has
  `--user-data-dir=<...>/Claude-v100`), then starts `claude-v100`.
- `status` reports: native switch present in the installed asar, config present,
  pref set, V100 running.
- `uninstall` removes the launcher script and menu entry; config dirs stay.

## Left to the builder

- Live check: launch V100 against a local fake gateway and confirm the models
  survive (no "known non-Anthropic model fragment; ignoring" warning in
  `~/.config/Claude-v100-3p/logs`).
- README: add a Nobara/Linux section; keep the Windows docs.
