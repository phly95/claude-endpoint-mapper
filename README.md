# Claude Endpoint Mapper

Map **models from any Anthropic-compatible endpoint** (your own gateway, a proxy
like LiteLLM / claude-code-router, or a hosted aggregator) onto Claude's model
tier slots — `sonnet`, `opus`, `haiku`, `fable`, `mythos` — in **Claude Desktop**
and **Claude Code**, with optional custom friendly names shown in Claude's model
picker. Runs on Windows (PowerShell) and on Nobara / Fedora Linux (Python).

You choose the endpoint, you choose the models. Nothing is tied to any specific
provider.

```
            ┌─────────────────────┐        ┌───────────────────────┐
 your endpoint  ──── models ────▶ │  Claude Endpoint    │ ────▶ │ Claude Desktop config │
 (any base URL)  ──── tiers ────▶ │  Mapper (GUI)       │ ────▶ │ Claude Code settings  │
            └─────────────────────┘        └───────────────────────┘
```

---

## Linux (Nobara / Fedora)

The same mapper for Claude Desktop on Linux, written in Python (standard library
only). The Windows instructions follow below this section.

**Applying switches your Claude Desktop to the gateway.** It then runs in
third-party mode, without the Anthropic sign-in, and `--restart` closes and
restarts it (open sessions end). To go back to your normal account run
`claude-desktop --1p`. To leave your normal Claude Desktop alone, use the
[separate launcher](#separate-launcher) instead.

### How it works

- The gateway config goes to `~/.config/Claude-3p/configLibrary/` (same JSON as on
  Windows).
- **No patching.** The `claude-desktop-extra` package ships an opt-in switch,
  `allowNonAnthropicModels`, that makes Claude Desktop accept non-Anthropic model
  names on a gateway. The tool sets it in `~/.config/Claude-3p/claude-desktop-extra.json`,
  so nothing under `/usr` is modified and package updates do not undo anything. If
  the switch is not in the installed app, `apply` refuses and says so (`status`
  shows `native_switch: NO`).
- Claude Code gets the same endpoint and models through `env` in
  `~/.claude/settings.json` (skip with `--no-code`).

### Separate launcher

`apply --separate` (the GUI's **Separate launcher** box, ticked by default) configures
a second, independent Claude Desktop instead of your normal one:

- a `claude-gateway` command and a "Claude Gateway" menu entry that run
  `claude-desktop --profile=gateway --3p`
- its own data in `~/.config/Claude-gateway` and `~/.config/Claude-gateway-3p`, its own
  login, and Claude Code settings in `~/.claude-gateway/settings.json`
- your normal Claude Desktop, its login, your chats and `~/.claude` are not touched;
  `--restart` stops and starts only the separate one, and its menu entry does not take
  over `claude://` links

```bash
python3 linux/endpoint_mapper.py apply --separate --url https://gateway.example.com --key-stdin \
  --model vendor-model-a=sonnet=Sonnet --restart <<<"$KEY"
python3 linux/endpoint_mapper.py status --separate
```

`uninstall` removes the command and menu entry and keeps the profile data.

### Requirements

- Nobara / Fedora with the `claude-desktop-extra` package (it provides
  `/usr/bin/claude-desktop` and the switch above)
- Python 3 (standard library only). The GUI also needs Tk (`python3-tkinter`).
- An endpoint that speaks the Anthropic Messages API and lists models at
  `/v1/models` or `/models`

### GUI

```bash
python3 linux/endpoint_mapper.py install     # once: adds "Claude Endpoint Mapper" to the app menu
claude-endpoint-mapper                       # or open it from the menu
```

`install` copies the Python files to `~/.local/share/claude-endpoint-mapper`, adds the
`claude-endpoint-mapper` command and a menu entry. It does not touch Claude. Without
installing you can also run `python3 linux/endpoint-mapper-gui.py` straight from the checkout.

Enter the endpoint and key, **Fetch models**, choose a tier and friendly name per
model, then **Apply and restart Claude Desktop**. An already saved key is kept when
the key field is left blank.

### CLI

Keys are read from stdin, never from the command line:

```bash
read -rs KEY                                   # type the key, nothing is echoed
python3 linux/endpoint_mapper.py models --url https://gateway.example.com --key-stdin <<<"$KEY"
python3 linux/endpoint_mapper.py apply --url https://gateway.example.com --key-stdin \
  --model vendor-model-a=sonnet=Sonnet --model vendor-model-b=opus --model qwen3:30b \
  --restart <<<"$KEY"
```

- `--model NAME[=TIER[=LABEL]]`, repeatable. TIER is one of `sonnet opus haiku
  fable mythos`; blank means the model shows in the picker without a tier slot.
- `--web-search tavily|brave|exa|custom|none` (+ `--web-search-key-stdin`,
  `--web-search-url`) controls the built-in web search, see [Web search](#web-search).
  The web search key is the second stdin line when both keys are given.
- `--no-code` skips Claude Code settings; `--code-settings PATH` writes them elsewhere.
- `--restart` stops Claude Desktop, applies, and starts it again. Without it a
  running Claude Desktop keeps its old config until you restart it.
- `--separate` targets the [separate launcher](#separate-launcher) instead of your
  normal Claude Desktop.

```bash
python3 linux/endpoint_mapper.py status      # switch present? config? running?
python3 linux/endpoint_mapper.py uninstall   # removes the configurator and the separate launcher
```

`uninstall` keeps the gateway setup and logins; run `claude-desktop --1p` to put
your normal Claude Desktop back on your account.

Run the tests with `python3 -m unittest discover -s linux/tests -t linux`.

### Linux troubleshooting

- **`apply` says there is no allowNonAnthropicModels switch** — the installed
  Claude Desktop is not `claude-desktop-extra` (or a version without it), or its
  `app.asar` is not at `/usr/lib/claude-desktop/resources/app.asar`; point the tool
  at it with `EM_ASAR=/path/to/app.asar`. Nothing was written.
- **Models do not show up** — the switch applies at startup; restart Claude Desktop
  (`apply --restart`). If `claude-desktop-extra.jsonc` in `~/.config/Claude-3p` sets
  `allowNonAnthropicModels` to `false`, that file wins and `apply` warns about it.
- **Claude Desktop still shows the normal sign-in** — run `claude-desktop --3p` once
  to switch it to third-party mode.
- **"config_model_rejected" in `~/.config/Claude-3p/logs/main.log`** — the gateway
  refused the probe request for the first model. Check the model id and that
  `POST /v1/messages` works on the endpoint.

## Windows: requirements

- Windows 10/11
- Claude Desktop installed (for the Desktop target)
- Windows PowerShell 5.1 (built in) — no other dependencies, no installs
- An endpoint that speaks the Anthropic Messages API (Claude Code) and exposes a
  model list at `/v1/models` or `/models`

## Windows: quick start

1. Download / clone this repository.
2. Double-click `EndpointMapper-GUI.ps1` (or run
   `powershell -ExecutionPolicy Bypass -File .\EndpointMapper-GUI.ps1`).
   If you prefer, run `Install-StartMenuShortcut.ps1` once to add
   "Claude Endpoint Mapper" to your Start Menu.
3. Enter your endpoint base URL (e.g. `https://your-gateway.example.com`).
4. Pick the auth scheme: `bearer` (`Authorization: Bearer …`) or `x-api-key`.
5. Enter your API key and press **Fetch models**.
6. For each model, choose a **tier** and optionally a **friendly name**:
   - Tier `sonnet` → used wherever Claude asks for Sonnet
   - Tier `opus` → used wherever Claude asks for Opus
   - Tier `haiku` → used wherever Claude asks for Haiku
   - Tier `fable` → used wherever Claude asks for Fable
   - Tier `mythos` → used wherever Claude asks for Mythos
   - Blank tier → the model still appears in the picker, but is not bound to a slot
   - Friendly name → what Claude's model picker displays (Desktop only)
7. Select targets (Claude Desktop / Claude Code) and press **Apply**.
8. Optionally tick **Enable built-in web search** before applying — see
   [Web search](#web-search). It runs the search inside Claude Desktop, so it
   works even when your gateway does not.

Applying to Claude Desktop will close and restart the app.

## Windows: what "Apply" actually does

### Claude Code

Writes environment variables into `%USERPROFILE%\.claude\settings.json`,
merging with your existing settings:

| Setting | Value |
| --- | --- |
| `ANTHROPIC_BASE_URL` | your endpoint |
| `ANTHROPIC_AUTH_TOKEN` | your key (only when you supply one) |
| `ANTHROPIC_DEFAULT_SONNET_MODEL` | model mapped to `sonnet` |
| `ANTHROPIC_DEFAULT_OPUS_MODEL` | model mapped to `opus` |
| `ANTHROPIC_DEFAULT_HAIKU_MODEL` | model mapped to `haiku` |
| `ANTHROPIC_DEFAULT_FABLE_MODEL` | model mapped to `fable` |
| `ANTHROPIC_DEFAULT_MYTHOS_MODEL` | model mapped to `mythos` |

Nothing else in the file is touched. Slots you leave blank are left as they were
(or removed when a previously set model is cleared).

### Claude Desktop

Two things are needed, and the tool does both automatically:

1. **Config** at `%LOCALAPPDATA%\Claude-3p\configLibrary\<id>.json`
   (the file `_meta.json` points at). The tool merges in:

   ```json
   {
     "inferenceProvider": "gateway",
     "inferenceCredentialKind": "static",
     "inferenceGatewayBaseUrl": "https://your-gateway.example.com",
     "inferenceGatewayAuthScheme": "bearer",
     "inferenceGatewayApiKey": "…",
     "inferenceModels": [
       { "name": "vendor-model-id", "labelOverride": "My Name",
         "anthropicFamilyTier": "sonnet", "isFamilyDefault": true }
     ]
   }
   ```

   An existing API key is preserved when you leave the key field blank — including
   a key Claude has stored in its sealed `__sealed__:v1:…` form, which the tool
   does not need to decrypt. Plaintext keys are accepted by the application as-is.
   Optionally the tool also writes a `managedMcpServers` entry that turns on
   Desktop's built-in web search — see [Web search](#web-search).

2. **Validator patch.** Out of the box, Claude Desktop's bundled JavaScript only
   accepts Anthropic model names for tier slots and silently drops anything else.
   The tool patches two bundled copies inside `resources\app.asar` so that the
   `gateway`/`mantle` provider reports healthy and the "not an Anthropic model"
   check no longer rejects your models.

   Because `app.asar` is integrity-checked by Electron, the tool then
   **recomputes the asar integrity metadata** (per-file hashes, per-chunk hashes,
   and the archive header hash) and updates the header-hash expectation embedded
   in `claude.exe`, all with byte-length-preserving edits so every archive offset
   stays valid. It verifies every entry afterwards and refuses to leave a broken
   install.

## Web search

Claude Code's built-in `WebSearch` tool is an **Anthropic server-side tool**: the
API runs the search and returns the results. A gateway that does not execute the
`web_search_20250305` server tool (most do not) hands back a client-side tool call
instead, so Claude Code reports **"Did 0 searches"** and never sees results.

Claude Desktop's third-party build ships a **built-in web-search connector** that
runs the search in the app itself, so it does not depend on the gateway. The GUI
can turn it on (the *Web search* section):

1. Tick **Enable built-in web search**.
2. Choose a provider: `tavily` (default), `brave`, `exa`, or `other`.
3. Optionally enter an API key:
   - **Tavily with no key** uses its free keyless mode.
   - **Brave / Exa** require a key.
   - **Other** points at your own search endpoint: enter its URL, plus a key if
     it expects `Authorization: Bearer ...`.
   - Leaving the key blank keeps the header already stored in the config.
4. Apply.

That writes a `managedMcpServers` entry into the Desktop config:

```json
"managedMcpServers": [
  {
    "name": "Web search",
    "server": "websearch",
    "provider": "tavily",
    "headers": { "X-Tavily-Access-Mode": "keyless" },
    "toolPolicy": { "web_search": "allow" }
  }
]
```

The `other` choice writes `"provider": "custom"` with a `customUrl` instead. That
endpoint receives `POST {"q":"..."}` and must return
`{"results":[{"title":...,"url":...,"snippet":...}]}`. Any headers you configure
are forwarded with the request.

In Code sessions started from Claude Desktop, the app aliases that server onto
Claude Code's `WebSearch`, so the built-in tool starts returning results. Untick
the box and Apply to remove the entry again; other `managedMcpServers` entries
are preserved.

CLI equivalent (Windows PowerShell; on Linux use `--web-search` as shown above):

```powershell
# keyless Tavily
Invoke-Apply -BaseUrl https://gateway.example.com -ApiKey $key -Models $models `
  -WebSearchProvider tavily -ConfigureDesktop

# with a vendor key (tavily / brave / exa)
Invoke-Apply ... -WebSearchProvider brave -WebSearchApiKey $braveKey

# your own search endpoint (provider "custom")
Invoke-Apply ... -WebSearchProvider custom `
  -WebSearchCustomUrl https://search.example.com/api -WebSearchApiKey $token

# remove the built-in search entry
Invoke-Apply ... -WebSearchProvider none

# omit -WebSearchProvider (default 'keep') to leave the setting untouched
```

Keys are stored as plain headers in the local config file, so treat that file as
a secret when you use one.

For Claude Code **outside** Claude Desktop, `WebSearch` still goes through the
gateway. Add an MCP search server instead — for example Tavily's remote server,
which also works keyless:

```powershell
claude mcp add tavily --transport http https://mcp.tavily.com/mcp/ `
  --header "X-Tavily-Access-Mode: keyless"
```

Then allow `mcp__tavily__tavily-search` in your permissions. Gateway operators
can alternatively run LiteLLM's `websearch_interception`, which executes the
search inside the proxy so the built-in tool keeps working unchanged.

## Windows: backups and restoring

Before the first modification, the tool saves:

- `resources\app.asar.origbak`
- `claude.exe.origbak`

These are the state of the install **before this tool first touched it**.
`Restore-DesktopPatch` (or the equivalent module call) restores the pair — both
or neither. Note:

- The pair must come from the same install (they are version-specific).
- A Claude Desktop update installs into a new `app-*` directory; re-run Apply
  afterwards. The tool always targets the newest `app-*` folder.
- Patch application is idempotent — re-running Apply is safe.

## Windows: CLI usage (optional)

The GUI is a thin layer over a PowerShell module:

```powershell
Import-Module .\EndpointMapper.psm1

# see what models an endpoint offers
Get-EndpointModels -BaseUrl https://gateway.example.com -ApiKey $key -AuthScheme bearer

# apply everything without the GUI
Invoke-Apply -BaseUrl https://gateway.example.com -ApiKey $key -AuthScheme bearer `
  -Models @(
    @{ Name = 'vendor-model-a'; Tier = 'sonnet'; Label = 'Sonnet' },
    @{ Name = 'vendor-model-b'; Tier = 'opus';   Label = 'Opus'   }
  ) -ConfigureDesktop -ConfigureClaudeCode -RestartDesktop -CreateIfMissing
```

Useful cmdlets: `Get-ClaudeDesktopApp`, `Test-AsarPatched`,
`Repair-AsarPatch`, `Repair-AsarIntegrity`, `Write-DesktopConfig`,
`Write-ClaudeCodeConfig`, `Restore-DesktopPatch`.

## Windows: troubleshooting

- **"Validator patch ... gateway switch not found"** — this Claude Desktop
  version changed its bundled code. The tool refuses to guess; open an issue with
  your Claude Desktop version (`app-*` folder name).
- **App quits right after launch / "does not hold the single-instance lock"** —
  an older Claude process survived. Close all Claude Desktop processes (Task
  Manager) and launch again.
- **Models show in the picker but requests fail** — the endpoint must accept
  Anthropic-format requests for your model IDs. Check the base URL and auth
  scheme; some gateways expect `/v1` in the base URL.
- **WebSearch says "Did 0 searches"** — the gateway is not executing Anthropic's
  `web_search_20250305` server tool. Enable the built-in web search for Desktop,
  or add an MCP search server for standalone Claude Code — see
  [Web search](#web-search).
- **After a Claude Desktop update, mappings are gone** — re-run Apply. The
  update replaced `app.asar` and `claude.exe`.

## Windows: uninstall / revert

1. Run `Restore-DesktopPatch` from the module (restores the `.origbak` pair).
2. Remove the `env` entries from `%USERPROFILE%\.claude\settings.json` (or delete
   the file if you added nothing else to it).
3. Delete the config JSON under `%LOCALAPPDATA%\Claude-3p\configLibrary\` and its
   entry in `_meta.json`.

## Security notes

- This repository contains **no keys or credentials**. Your key is only written to
  your own local config files.
- The tool modifies files inside your local Claude Desktop installation. That is
  inherent to making non-Anthropic models usable there. Review the code — it is
  plain PowerShell with no network calls beyond the endpoint you configure
  (`/v1/models`).
- Claude's own code is not redistributed here.

## License

MIT — see [LICENSE](LICENSE).
