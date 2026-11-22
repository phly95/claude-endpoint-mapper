#Requires -Version 5.1
<#
    Claude Endpoint Mapper - GUI
    Selects an Anthropic-compatible endpoint, fetches its models, maps them to
    Claude tiers (sonnet / opus / haiku / fable / mythos) with optional custom
    friendly names, then applies everything: Desktop validator patch + integrity
    sync + Desktop config + Claude Code settings.
#>
param()
$ErrorActionPreference = 'Stop'

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Import-Module (Join-Path $here 'EndpointMapper.psm1') -Force

Add-Type -AssemblyName PresentationFramework
Add-Type -AssemblyName PresentationCore
Add-Type -AssemblyName WindowsBase

# ---- row model for the DataGrid (real CLR type so WPF binding works) -------
if (-not ('EndpointMapper.ModelRow' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
namespace EndpointMapper {
    public class ModelRow {
        public bool Selected { get; set; }
        public string ModelId { get; set; }
        public string Tier { get; set; }
        public string Label { get; set; }
        public ModelRow() {
            Selected = true; ModelId = ""; Tier = ""; Label = "";
        }
    }
}
'@
}

# ---- window ----------------------------------------------------------------
$xaml = @'
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Claude Endpoint Mapper" Width="920" Height="860"
        MinWidth="780" MinHeight="700" WindowStartupLocation="CenterScreen">
  <Grid Margin="12">
    <Grid.RowDefinitions>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="*"/>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="170"/>
    </Grid.RowDefinitions>

    <GroupBox Grid.Row="0" Header="Endpoint" Padding="8">
      <Grid>
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="Auto"/>
          <ColumnDefinition Width="*"/>
          <ColumnDefinition Width="Auto"/>
          <ColumnDefinition Width="140"/>
        </Grid.ColumnDefinitions>
        <Grid.RowDefinitions>
          <RowDefinition Height="Auto"/>
          <RowDefinition Height="Auto"/>
          <RowDefinition Height="Auto"/>
        </Grid.RowDefinitions>

        <TextBlock Text="Base URL:" VerticalAlignment="Center" Margin="0,4,6,4"/>
        <ComboBox Grid.Column="1" x:Name="cbEndpoint" IsEditable="True"
                  Margin="0,4,6,4" VerticalContentAlignment="Center"/>
        <TextBlock Grid.Column="2" Text="Auth:" VerticalAlignment="Center" Margin="0,4,6,4"/>
        <ComboBox Grid.Column="3" x:Name="cbAuth" Margin="0,4,0,4"
                  VerticalContentAlignment="Center"/>

        <TextBlock Grid.Row="1" Text="API key:" VerticalAlignment="Center" Margin="0,4,6,4"/>
        <PasswordBox Grid.Row="1" Grid.Column="1" x:Name="pbKey"
                     Margin="0,4,6,4" VerticalContentAlignment="Center"/>
        <Button Grid.Row="1" Grid.Column="2" Grid.ColumnSpan="2" x:Name="btnFetch"
                Content="Fetch models" Margin="0,4,0,4" Padding="10,3"/>

        <TextBlock Grid.Row="2" Grid.ColumnSpan="4" x:Name="tbStatus" Margin="0,6,0,0"
                   Foreground="#FF555555" TextWrapping="Wrap"/>
      </Grid>
    </GroupBox>

    <Grid Grid.Row="1" Margin="0,8,0,2">
      <DockPanel>
        <StackPanel Orientation="Horizontal" DockPanel.Dock="Right">
          <Button x:Name="btnAddRow" Content="Add row" Padding="10,3" Margin="0,0,6,0"/>
          <Button x:Name="btnRemoveRow" Content="Remove selected" Padding="10,3"/>
        </StackPanel>
        <TextBlock VerticalAlignment="Center" TextWrapping="Wrap"
                   Text="Models - pick a tier per model (blank = shows in the picker with no tier slot). Friendly name is what Claude's model picker displays."/>
      </DockPanel>
    </Grid>

    <DataGrid Grid.Row="2" x:Name="dgModels" AutoGenerateColumns="False"
              CanUserAddRows="False" HeadersVisibility="Column"
              GridLinesVisibility="Horizontal" Margin="0,4,0,0"/>

    <Grid Grid.Row="3" Margin="0,8,0,0">
      <GroupBox Header="Web search (Claude Desktop)" Padding="8">
        <Grid>
          <Grid.ColumnDefinitions>
            <ColumnDefinition Width="Auto"/>
            <ColumnDefinition Width="Auto"/>
            <ColumnDefinition Width="Auto"/>
            <ColumnDefinition Width="Auto"/>
            <ColumnDefinition Width="*"/>
          </Grid.ColumnDefinitions>
          <Grid.RowDefinitions>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="Auto"/>
          </Grid.RowDefinitions>

          <CheckBox x:Name="cbWebSearch" Content="Enable built-in web search"
                    VerticalAlignment="Center" Margin="0,2,16,2"/>
          <TextBlock Grid.Column="1" Text="Provider:" VerticalAlignment="Center" Margin="0,2,6,2"/>
          <ComboBox Grid.Column="2" x:Name="cbSearchProvider" Width="110"
                    VerticalContentAlignment="Center" Margin="0,2,12,2"/>
          <TextBlock Grid.Column="3" Text="Key:" VerticalAlignment="Center" Margin="0,2,6,2"/>
          <PasswordBox Grid.Column="4" x:Name="pbSearchKey" VerticalContentAlignment="Center" Margin="0,2,0,2"/>

          <TextBlock Grid.Row="1" Grid.Column="0" Text="Other URL:" VerticalAlignment="Center"
                     Margin="0,6,4,2"/>
          <TextBox Grid.Row="1" Grid.Column="1" Grid.ColumnSpan="4" x:Name="tbSearchUrl"
                   VerticalContentAlignment="Center" Margin="0,4,0,2"/>

          <TextBlock Grid.Row="2" Grid.ColumnSpan="5" Foreground="#FF777777" TextWrapping="Wrap"
                     Margin="0,4,0,0"
                     Text="Runs inside Claude Desktop, so it works with any gateway. Tavily works without a key; Brave / Exa need one, and Other needs a URL that accepts POST {&quot;q&quot;:&quot;...&quot;} and returns {&quot;results&quot;:[{title,url,snippet}]}. Leave the key blank to keep the one already stored."/>
        </Grid>
      </GroupBox>
    </Grid>

    <Grid Grid.Row="4" Margin="0,8,0,0">
      <StackPanel Orientation="Horizontal" VerticalAlignment="Center">
        <CheckBox x:Name="cbDesktop" Content="Claude Desktop" IsChecked="True" Margin="0,0,14,0"/>
        <CheckBox x:Name="cbCode" Content="Claude Code (~/.claude/settings.json)" IsChecked="True" Margin="0,0,14,0"/>
        <TextBlock Foreground="#FF777777"
                   Text="Applying closes and restarts Claude Desktop."/>
      </StackPanel>
      <Button x:Name="btnApply" Content="Apply" Width="150" Padding="10,5"
              HorizontalAlignment="Right" FontWeight="SemiBold"/>
    </Grid>

    <TextBox Grid.Row="5" x:Name="tbLog" IsReadOnly="True" VerticalScrollBarVisibility="Auto"
             FontFamily="Consolas" FontSize="11" Margin="0,8,0,0"
             Background="#FF1E1E1E" Foreground="#FFDCDCDC"/>
  </Grid>
</Window>
'@

$window = [System.Windows.Markup.XamlReader]::Parse($xaml)
$cbEndpoint = $window.FindName('cbEndpoint')
$cbAuth     = $window.FindName('cbAuth')
$pbKey      = $window.FindName('pbKey')
$btnFetch   = $window.FindName('btnFetch')
$tbStatus   = $window.FindName('tbStatus')
$dgModels   = $window.FindName('dgModels')
$btnAddRow  = $window.FindName('btnAddRow')
$btnRemoveRow = $window.FindName('btnRemoveRow')
$cbDesktop  = $window.FindName('cbDesktop')
$cbCode     = $window.FindName('cbCode')
$cbWebSearch = $window.FindName('cbWebSearch')
$cbSearchProvider = $window.FindName('cbSearchProvider')
$pbSearchKey = $window.FindName('pbSearchKey')
$tbSearchUrl = $window.FindName('tbSearchUrl')
$btnApply   = $window.FindName('btnApply')
$tbLog      = $window.FindName('tbLog')

# ---- DataGrid columns (built in code to keep the XAML simple) --------------
$items = New-Object 'System.Collections.ObjectModel.ObservableCollection[EndpointMapper.ModelRow]'

$colUse = New-Object System.Windows.Controls.DataGridCheckBoxColumn
$colUse.Header = 'Use'
$colUse.Width = 50
$colUse.Binding = New-Object System.Windows.Data.Binding('Selected')

$colId = New-Object System.Windows.Controls.DataGridTextColumn
$colId.Header = 'Model ID'
$colId.Width = '*'
$colId.Binding = New-Object System.Windows.Data.Binding('ModelId')

$colTier = New-Object System.Windows.Controls.DataGridComboBoxColumn
$colTier.Header = 'Tier'
$colTier.Width = 120
$colTier.SelectedItemBinding = New-Object System.Windows.Data.Binding('Tier')
$colTier.ItemsSource = @('', 'sonnet', 'opus', 'haiku', 'fable', 'mythos')

$colLabel = New-Object System.Windows.Controls.DataGridTextColumn
$colLabel.Header = 'Friendly name'
$colLabel.Width = 200
$colLabel.Binding = New-Object System.Windows.Data.Binding('Label')

[void]$dgModels.Columns.Add($colUse)
[void]$dgModels.Columns.Add($colId)
[void]$dgModels.Columns.Add($colTier)
[void]$dgModels.Columns.Add($colLabel)
$dgModels.ItemsSource = $items

# ---- helpers ---------------------------------------------------------------
function Add-Log([string]$Message) {
    $tbLog.AppendText(('[{0}] {1}' -f (Get-Date -Format 'HH:mm:ss'), $Message) + "`r`n")
    $tbLog.ScrollToEnd()
}

function Guess-Tier([string]$Id) {
    $l = $Id.ToLowerInvariant()
    if ($l.Contains('haiku'))  { return 'haiku' }
    if ($l.Contains('sonnet')) { return 'sonnet' }
    if ($l.Contains('opus'))   { return 'opus' }
    if ($l.Contains('fable'))  { return 'fable' }
    if ($l.Contains('mythos')) { return 'mythos' }
    return ''
}

function Add-Row([string]$Id, [string]$Tier, [string]$Label) {
    $r = New-Object EndpointMapper.ModelRow
    $r.ModelId = $Id
    $r.Tier = $Tier
    $r.Label = $Label
    [void]$items.Add($r)
    return $r
}

function Get-SelectedAuth {
    $a = "$($cbAuth.SelectedItem)"
    if ($a -eq 'x-api-key') { return 'x-api-key' }
    return 'bearer'
}

# ---- defaults --------------------------------------------------------------
$cbAuth.ItemsSource = @('bearer', 'x-api-key')
$cbAuth.SelectedIndex = 0
$cbSearchProvider.ItemsSource = @('tavily', 'brave', 'exa', 'other')
$cbSearchProvider.SelectedIndex = 0
$cbEndpoint.ItemsSource = @(
    'http://localhost:4000',    # LiteLLM
    'http://localhost:3456',    # claude-code-router
    'https://api.anthropic.com'
)

function Update-SearchControls {
    $on = [bool]$cbWebSearch.IsChecked -and [bool]$cbDesktop.IsChecked
    $cbSearchProvider.IsEnabled = $on
    $pbSearchKey.IsEnabled = $on
    $tbSearchUrl.IsEnabled = $on -and "$($cbSearchProvider.SelectedItem)" -eq 'other'
}
$cbWebSearch.Add_Click({ Update-SearchControls })
$cbSearchProvider.Add_SelectionChanged({ Update-SearchControls })
$cbDesktop.Add_Click({ Update-SearchControls })
Update-SearchControls

# ---- prefill from existing config -----------------------------------------
$cfgPath = $null
try { $cfgPath = Get-DesktopConfigPath } catch { }
$prefilledKey = $false
if ($cfgPath) {
    try {
        $cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
        if ($cfg.PSObject.Properties['inferenceGatewayBaseUrl'] -and $cfg.inferenceGatewayBaseUrl) {
            $cbEndpoint.Text = $cfg.inferenceGatewayBaseUrl
        }
        if ($cfg.PSObject.Properties['inferenceGatewayAuthScheme'] -and $cfg.inferenceGatewayAuthScheme) {
            $cbAuth.SelectedItem = "$($cfg.inferenceGatewayAuthScheme)"
        }
        if ($cfg.PSObject.Properties['inferenceGatewayApiKey'] -and $cfg.inferenceGatewayApiKey -and
            -not $cfg.inferenceGatewayApiKey.StartsWith('__sealed__')) {
            $pbKey.Password = $cfg.inferenceGatewayApiKey
            $prefilledKey = $true
        }
        if ($cfg.PSObject.Properties['inferenceModels'] -and $cfg.inferenceModels) {
            foreach ($m in $cfg.inferenceModels) {
                $tier = ''
                if ($m.PSObject.Properties['anthropicFamilyTier']) { $tier = "$($m.anthropicFamilyTier)" }
                $label = ''
                if ($m.PSObject.Properties['labelOverride']) { $label = "$($m.labelOverride)" }
                $name = ''
                if ($m.PSObject.Properties['name']) { $name = "$($m.name)" }
                if ($name) { [void](Add-Row $name $tier $label) }
            }
        }
        if ($cfg.PSObject.Properties['managedMcpServers'] -and $cfg.managedMcpServers) {
            $ws = @($cfg.managedMcpServers) | Where-Object { "$($_.server)" -eq 'websearch' } | Select-Object -First 1
            if ($ws) {
                $cbWebSearch.IsChecked = $true
                $p = "$($ws.provider)"
                if ($p -in @('tavily', 'brave', 'exa')) {
                    $cbSearchProvider.SelectedItem = $p
                } elseif ($p -eq 'custom') {
                    $cbSearchProvider.SelectedItem = 'other'
                    if ($ws.PSObject.Properties['customUrl'] -and $ws.customUrl) {
                        $tbSearchUrl.Text = "$($ws.customUrl)"
                    }
                } elseif ($p) {
                    Add-Log "Existing web search provider '$p' is not editable here; Apply will replace it."
                }
            }
        }
    } catch {
        Add-Log "Could not prefill from config: $($_.Exception.Message)"
    }
}
if (-not $cbEndpoint.Text) { $cbEndpoint.SelectedIndex = 0 }
Update-SearchControls

# ---- status line -----------------------------------------------------------
try {
    $app = Get-ClaudeDesktopApp
    $ps = Test-AsarPatched -AsarPath $app.Asar
    $state = if ($ps.GatewayOk) { 'patched' } else { 'NOT patched' }
    $tbStatus.Text = "Claude Desktop $($app.Version): validator $state.  Config: $(if ($cfgPath) { Split-Path $cfgPath -Leaf } else { 'none found (will be created on Apply)' })."
} catch {
    $tbStatus.Text = "Claude Desktop not found ($($_.Exception.Message)) - Desktop target will be unavailable."
    $cbDesktop.IsChecked = $false
    $cbDesktop.IsEnabled = $false
}
if ($prefilledKey) { Add-Log 'Loaded existing API key from config (leave blank to keep it).' }

# ---- events ----------------------------------------------------------------
$btnFetch.Add_Click({
    $url = "$($cbEndpoint.Text)".Trim()
    if ($url -notmatch '^https?://') {
        Add-Log 'ERROR: Base URL must start with http:// or https://'
        return
    }
    $btnFetch.IsEnabled = $false
    try {
        Add-Log "Fetching models from $url ..."
        $ids = Get-EndpointModels -BaseUrl $url -ApiKey $pbKey.Password -AuthScheme (Get-SelectedAuth)
        $existing = @()
        foreach ($r in $items) { $existing += $r.ModelId }
        $added = 0
        foreach ($id in $ids) {
            if ($existing -notcontains $id) {
                $tier = Guess-Tier $id
                $label = ''
                if ($tier) { $label = (Get-Culture).TextInfo.ToTitleCase($tier) }
                [void](Add-Row $id $tier $label)
                $added++
            }
        }
        Add-Log ("{0} models available, {1} new rows added (existing rows kept)." -f $ids.Count, $added)
    } catch {
        Add-Log "ERROR: $($_.Exception.Message)"
        [void][System.Windows.MessageBox]::Show($window, $_.Exception.Message, 'Fetch failed', 'OK', 'Error')
    } finally {
        $btnFetch.IsEnabled = $true
    }
})

$btnAddRow.Add_Click({
    [void](Add-Row '' '' '')
    $dgModels.SelectedIndex = $items.Count - 1
    $dgModels.Focus()
})

$btnRemoveRow.Add_Click({
    $selected = @($dgModels.SelectedItems)
    foreach ($row in $selected) { [void]$items.Remove($row) }
})

$btnApply.Add_Click({
    $url = "$($cbEndpoint.Text)".Trim()
    if ($url -notmatch '^https?://') {
        Add-Log 'ERROR: Base URL must start with http:// or https://'
        return
    }
    $models = @()
    foreach ($r in $items) {
        if ($r.Selected -and "$($r.ModelId)".Trim()) {
            $models += [pscustomobject]@{
                Name  = "$($r.ModelId)".Trim()
                Tier  = "$($r.Tier)".Trim().ToLowerInvariant()
                Label = "$($r.Label)".Trim()
            }
        }
    }
    if ($models.Count -eq 0) {
        Add-Log 'ERROR: check at least one model with a non-empty Model ID.'
        return
    }
    $doDesktop = [bool]$cbDesktop.IsChecked
    $doCode = [bool]$cbCode.IsChecked
    if (-not $doDesktop -and -not $doCode) {
        Add-Log 'ERROR: select at least one target (Claude Desktop / Claude Code).'
        return
    }

    $wsProvider = 'none'
    $wsCustomUrl = ''
    if ($doDesktop -and [bool]$cbWebSearch.IsChecked) {
        $choice = "$($cbSearchProvider.SelectedItem)"
        if (-not $choice) { $choice = 'tavily' }
        if ($choice -eq 'other') {
            $wsCustomUrl = "$($tbSearchUrl.Text)".Trim()
            if (-not $wsCustomUrl) {
                Add-Log 'ERROR: Other web search needs a URL.'
                return
            }
            if ($wsCustomUrl -notmatch '^https?://') {
                Add-Log 'ERROR: the Other web search URL must start with http:// or https://'
                return
            }
            $wsProvider = 'custom'
        } else {
            $wsProvider = $choice
        }
        if ($wsProvider -in @('brave', 'exa') -and -not $pbSearchKey.Password) {
            Add-Log "ERROR: $wsProvider web search needs an API key."
            return
        }
    }

    $btnApply.IsEnabled = $false
    $btnFetch.IsEnabled = $false
    $window.Cursor = [System.Windows.Input.Cursors]::Wait
    try {
        Add-Log "Applying: $($models.Count) model(s), desktop=$doDesktop, code=$doCode, websearch=$wsProvider ..."
        if ($doDesktop) { Add-Log 'Note: Claude Desktop will be closed for patching.' }
        $res = Invoke-Apply -BaseUrl $url -ApiKey $pbKey.Password -AuthScheme (Get-SelectedAuth) `
            -Models $models -WebSearchProvider $wsProvider -WebSearchApiKey $pbSearchKey.Password `
            -WebSearchCustomUrl $wsCustomUrl `
            -ConfigureDesktop:$doDesktop -ConfigureClaudeCode:$doCode -RestartDesktop -CreateIfMissing
        foreach ($m in $res.Messages) { Add-Log $m }
        Add-Log 'Done.'
        if ($doDesktop) {
            $ps = Test-AsarPatched -AsarPath (Get-ClaudeDesktopApp).Asar
            Add-Log ("Validator patch check: {0}" -f $(if ($ps.GatewayOk) { 'OK' } else { 'FAILED' }))
        }
    } catch {
        Add-Log "ERROR: $($_.Exception.Message)"
        [void][System.Windows.MessageBox]::Show($window, $_.Exception.Message, 'Apply failed', 'OK', 'Error')
    } finally {
        $window.Cursor = [System.Windows.Input.Cursors]::Arrow
        $btnApply.IsEnabled = $true
        $btnFetch.IsEnabled = $true
    }
})

Add-Log 'Ready. Set an endpoint, fetch models, choose tiers / friendly names, then Apply.'
[void]$window.ShowDialog()
