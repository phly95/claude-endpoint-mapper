# Claude Endpoint Mapper

Map **models from any Anthropic-compatible endpoint** (your own gateway, a proxy
like LiteLLM / claude-code-router, or a hosted aggregator) onto Claude's model
tier slots — `sonnet`, `opus`, `haiku`, `fable`, `mythos` — in **Claude Desktop**
and **Claude Code**, with optional custom friendly names shown in Claude's model
picker.

You choose the endpoint, you choose the models. Nothing is tied to any specific
provider.

```
            ┌─────────────────────┐        ┌───────────────────────┐
 your endpoint  ──── models ────▶ │  Claude Endpoint    │ ────▶ │ Claude Desktop config │
 (any base URL)  ──── tiers ────▶ │  Mapper (GUI)       │ ────▶ │ Claude Code settings  │
            └─────────────────────┘        └───────────────────────┘
```

---

## Requirements

- Windows 10/11
- Claude Desktop installed (for the Desktop target)
- Windows PowerShell 5.1 (built in) — no other dependencies, no installs
- An endpoint that speaks the Anthropic Messages API (Claude Code) and exposes a
  model list at `/v1/models` or `/models`

## Quick start

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

Applying to Claude Desktop will close and restart the app.

## What "Apply" actually does

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

## Backups and restoring

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

## CLI usage (optional)

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
  ) -ConfigureDesktop -ConfigureClaudeCode -RestartDesktop
```

Useful cmdlets: `Get-ClaudeDesktopApp`, `Test-AsarPatched`,
`Repair-AsarPatch`, `Repair-AsarIntegrity`, `Write-DesktopConfig`,
`Write-ClaudeCodeConfig`, `Restore-DesktopPatch`.

## Troubleshooting

- **"Validator patch ... gateway switch not found"** — this Claude Desktop
  version changed its bundled code. The tool refuses to guess; open an issue with
  your Claude Desktop version (`app-*` folder name).
- **App quits right after launch / "does not hold the single-instance lock"** —
  an older Claude process survived. Close all Claude Desktop processes (Task
  Manager) and launch again.
- **Models show in the picker but requests fail** — the endpoint must accept
  Anthropic-format requests for your model IDs. Check the base URL and auth
  scheme; some gateways expect `/v1` in the base URL.
- **After a Claude Desktop update, mappings are gone** — re-run Apply. The
  update replaced `app.asar` and `claude.exe`.

## Uninstall / revert

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
