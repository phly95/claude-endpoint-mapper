<#
    Install-StartMenuShortcut.ps1
    Adds "Claude Endpoint Mapper" to the current user's Start Menu.

    The shortcut launches the GUI with PowerShell, hidden console window,
    and uses the Claude Desktop icon when one can be found.

    Run:  powershell -ExecutionPolicy Bypass -File .\Install-StartMenuShortcut.ps1
    Undo: powershell -ExecutionPolicy Bypass -File .\Install-StartMenuShortcut.ps1 -Uninstall
#>
[CmdletBinding()]
param([switch]$Uninstall)

$ErrorActionPreference = 'Stop'
$gui = Join-Path $PSScriptRoot 'EndpointMapper-GUI.ps1'
$programs = [Environment]::GetFolderPath('Programs')
$lnkPath = Join-Path $programs 'Claude Endpoint Mapper.lnk'

if ($Uninstall) {
    if (Test-Path $lnkPath) {
        Remove-Item $lnkPath -Force
        "Removed: $lnkPath"
    } else {
        "Nothing to remove: $lnkPath does not exist."
    }
    return
}

if (-not (Test-Path $gui)) { throw "GUI script not found next to this installer: $gui" }

# Icon: prefer the newest installed Claude Desktop executable (numeric version sort)
$icon = $null
$claudeRoot = Join-Path $env:LOCALAPPDATA 'AnthropicClaude'
if (Test-Path $claudeRoot) {
    $app = Get-ChildItem $claudeRoot -Directory -Filter 'app-*' -ErrorAction SilentlyContinue |
        Sort-Object -Property @{
            Expression = { try { [version]($_.Name -replace '^app-', '') } catch { [version]'0.0.0' } }
            Descending = $true
        } | Select-Object -First 1
    if ($app) {
        $exe = Join-Path $app.FullName 'claude.exe'
        if (Test-Path $exe) { $icon = "$exe,0" }
    }
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($lnkPath)
$shortcut.TargetPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$gui`""
$shortcut.WorkingDirectory = $PSScriptRoot
$shortcut.Description = 'Map models from any Anthropic-compatible endpoint onto Claude tiers'
if ($icon) { $shortcut.IconLocation = $icon }
$shortcut.Save()

"Installed Start Menu shortcut: $lnkPath"
"Target: $($shortcut.TargetPath) $($shortcut.Arguments)"
