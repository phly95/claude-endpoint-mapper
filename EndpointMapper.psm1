# EndpointMapper.psm1
# Core engine for Claude Endpoint Mapper.
# Maps user-chosen models from any Anthropic-compatible endpoint onto Claude
# Desktop / Claude Code tier slots (sonnet / opus / haiku / fable / mythos).
#
# Three concerns:
#   1. Patch Claude Desktop's model validator (app.asar, two bundled copies)
#      so non-Anthropic model names are accepted like Anthropic ones.
#   2. Keep Electron ASAR integrity valid (chunk hashes, header hash, and the
#      header hash expectation hardcoded in claude.exe).
#   3. Write Claude Desktop config + Claude Code settings.

# ---------------------------------------------------------------- helpers ---

function Get-Sha256Hex {
    param([byte[]]$Bytes, [int]$Offset, [int]$Length)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { $h = $sha.ComputeHash($Bytes, $Offset, $Length) }
    finally { $sha.Dispose() }
    return (($h | ForEach-Object { $_.ToString('x2') }) -join '')
}

function Find-AsciiAll {
    param([string]$Haystack, [string]$Needle)
    $out = @(); $start = 0
    while ($true) {
        $i = $Haystack.IndexOf($Needle, $start, [StringComparison]::Ordinal)
        if ($i -lt 0) { break }
        $out += $i; $start = $i + 1
    }
    return ,$out
}

# ------------------------------------------------------- install discovery ---

function Get-ClaudeDesktopApp {
    <# Returns newest app-* dir info: @{ Dir; Asar; Exe; Version } #>
    $root = Join-Path $env:LOCALAPPDATA 'AnthropicClaude'
    if (-not (Test-Path $root)) { throw "Claude Desktop not found at $root" }
    $dirs = @(Get-ChildItem $root -Directory -Filter 'app-*')
    if ($dirs.Count -eq 0) { throw "No app-* install directories under $root" }
    $best = $dirs | Sort-Object -Property @{
        Expression = { try { [version]($_.Name -replace '^app-', '') } catch { [version]'0.0.0' } }
        Descending = $true
    } | Select-Object -First 1
    $asar = Join-Path $best.FullName 'resources\app.asar'
    $exe  = Join-Path $best.FullName 'claude.exe'
    if (-not (Test-Path $asar)) { throw "app.asar missing: $asar" }
    if (-not (Test-Path $exe))  { throw "claude.exe missing: $exe" }
    return [pscustomobject]@{ Dir = $best.FullName; Asar = $asar; Exe = $exe; Version = $best.Name }
}

function Stop-ClaudeDesktop {
    <# Kills only Claude Desktop processes (never Claude Code). Retries because
       Electron's process tree does not disappear in one pass. #>
    $root = Join-Path $env:LOCALAPPDATA 'AnthropicClaude'
    $killed = @()
    for ($attempt = 0; $attempt -lt 10; $attempt++) {
        $targets = @(Get-Process -Name 'claude' -ErrorAction SilentlyContinue | Where-Object {
            $_.Path -and $_.Path.StartsWith($root, 'OrdinalIgnoreCase') })
        if ($targets.Count -eq 0) { break }
        foreach ($t in $targets) {
            try {
                if ($killed -notcontains $t.Id) { $killed += $t.Id }
                $t | Stop-Process -Force -ErrorAction Stop
            } catch { }
        }
        Start-Sleep -Seconds 1
    }
    $left = @(Get-Process -Name 'claude' -ErrorAction SilentlyContinue | Where-Object {
        $_.Path -and $_.Path.StartsWith($root, 'OrdinalIgnoreCase') })
    if ($left.Count -gt 0) { throw "Could not stop Claude Desktop (PIDs: $($left.Id -join ', '))" }
    return $killed
}

function Start-ClaudeDesktop {
    $root = Join-Path $env:LOCALAPPDATA 'AnthropicClaude'
    Start-Process (Join-Path $root 'claude.exe')
}

# ------------------------------------------------------------- asar patch ---

function Get-GatewayReplacement {
    param([int]$OriginalLength)
    # 'return{ok:!0}' is 13 bytes, 'return{ok:1}' is 12 bytes; pad longer
    # targets with spaces between 'return' and the object literal so the
    # byte length never changes (keeps every asar offset valid).
    if ($OriginalLength -eq 13) { return 'return{ok:!0}' }
    if ($OriginalLength -eq 12) { return 'return{ok:1}' }
    if ($OriginalLength -gt 13) { return 'return' + (' ' * ($OriginalLength - 13)) + '{ok:!0}' }
    return $null   # shorter than 12 bytes: unsupported minification
}

function Repair-AsarPatch {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$AsarPath)

    $report = [pscustomobject]@{
        GatewayPatched      = 0
        GatewayAlreadyOk    = $false
        GatewayOk           = $false
        BlocklistNeutered   = 0
        BlocklistAlreadyOk  = 0
        BlocklistClean      = $true
        Supported           = $false
        Warnings            = New-Object System.Collections.Generic.List[string]
    }

    $bytes = [System.IO.File]::ReadAllBytes($AsarPath)
    $enc = [System.Text.Encoding]::ASCII
    $s = $enc.GetString($bytes)

    # --- pass 1: provider switch ('gateway' / 'mantle' must return {ok:true}) ---
    # Capture only the return expression so the case labels remain intact.
    $gwRe      = [regex]'case"gateway":case"mantle":(return[ \t]*[$A-Za-z_][\w$]*\([$\w]+\))'
    $gwDoneRe  = [regex]'case"gateway":case"mantle":return[ \t]*\{ok:'

    if ($gwDoneRe.IsMatch($s)) { $report.GatewayAlreadyOk = $true }

    $gwMatches = @($gwRe.Matches($s))
    if ($gwMatches.Count -eq 0 -and -not $report.GatewayAlreadyOk) {
        $report.Warnings.Add('Provider switch pattern for gateway/mantle not found - this Claude Desktop version may be unsupported.')
    }
    # splice from the end so earlier indices stay valid
    for ($i = $gwMatches.Count - 1; $i -ge 0; $i--) {
        $m = $gwMatches[$i]
        $retGroup = $m.Groups[1]
        $repl = Get-GatewayReplacement -OriginalLength $retGroup.Length
        if (-not $repl) {
            $report.Warnings.Add("gateway/mantle return expression at offset $($retGroup.Index) is shorter than supported ($($retGroup.Length) bytes); skipped.")
            continue
        }
        $nb = $enc.GetBytes($repl)
        if ($nb.Length -ne $retGroup.Length) { throw "Internal length mismatch patching gateway switch at offset $($retGroup.Index)" }
        [Array]::Copy($nb, 0, $bytes, $retGroup.Index, $nb.Length)
        $report.GatewayPatched++
    }
    if ($report.GatewayPatched -gt 0) { $s = $enc.GetString($bytes) }
    if ($gwDoneRe.IsMatch($s)) { $report.GatewayAlreadyOk = $true }
    $report.Supported = $report.GatewayAlreadyOk -or ($report.GatewayPatched -gt 0)

    # --- pass 2: non-Anthropic fragment blocklist (feeds the tier gate) ---
    # call site: NAME(t)&&!OTHER(t)?{ok:!1,why:"not_anthropic"}
    $blCallRe = [regex]'(\w+)\([$\w]+\)&&!(\w+)\([$\w]+\)\?\{ok:!1,why:"not_anthropic"\}'
    $seen = @{}
    foreach ($cm in $blCallRe.Matches($s)) {
        $fn = $cm.Groups[1].Value
        if ($seen.ContainsKey($fn)) { continue }
        $seen[$fn] = $true

        $fnEsc      = [regex]::Escape($fn)
        $neuteredRe = [regex]('function ' + $fnEsc + '\([$\w]+\)\{return!1\}')
        $defRe      = [regex]('function ' + $fnEsc + '\(([$\w]+)\)\{return [^{};]{1,400}?\.test\(\1\.toLowerCase\(\)\)\}')

        $dm = $defRe.Match($s)
        if (-not $dm.Success) {
            if ($neuteredRe.IsMatch($s)) { $report.BlocklistAlreadyOk++ }
            else { $report.Warnings.Add("Blocklist function '$fn' not found in expected shape; its tier gate may still reject non-Anthropic models.") }
            continue
        }
        $paramName = $dm.Groups[1].Value
        $base = "function $fn($paramName){return!1}"
        $pad = $dm.Length - $base.Length - 4      # -4 for /* and */
        if ($pad -lt 0) {
            $report.Warnings.Add("Blocklist definition for '$fn' shorter than expected; skipped.")
            continue
        }
        $repl = $base + '/*' + (' ' * $pad) + '*/'
        if ($repl.Length -ne $dm.Length) { throw "Internal length mismatch neutering '$fn'" }
        $nb = $enc.GetBytes($repl)
        [Array]::Copy($nb, 0, $bytes, $dm.Index, $nb.Length)
        $s = $enc.GetString($bytes)
        $report.BlocklistNeutered++
    }
    if ($report.BlocklistNeutered -eq 0 -and $seen.Count -eq 0) {
        $report.Warnings.Add('Tier-gate call site not recognized; model names may load but lose their tier slot.')
    }

    if ($report.GatewayPatched -gt 0 -or $report.BlocklistNeutered -gt 0) {
        [System.IO.File]::WriteAllBytes($AsarPath, $bytes)
    }

    # post-condition verification (string level)
    $check = $enc.GetString([System.IO.File]::ReadAllBytes($AsarPath))
    $unpatched = @($gwRe.Matches($check))
    $report.GatewayOk = ($unpatched.Count -eq 0 -and $gwDoneRe.IsMatch($check))
    $report.BlocklistClean = $true
    foreach ($fn in $seen.Keys) {
        $fnEsc = [regex]::Escape($fn)
        $stillActive = ([regex]('function ' + $fnEsc + '\(([$\w]+)\)\{return [^{};]{1,400}?\.test\(\1\.toLowerCase\(\)\)\}')).IsMatch($check)
        if ($stillActive) { $report.BlocklistClean = $false }
    }
    return $report
}

function Test-AsarPatched {
    param([Parameter(Mandatory)][string]$AsarPath)
    $s = [System.Text.Encoding]::ASCII.GetString([System.IO.File]::ReadAllBytes($AsarPath))
    $gwDone = ([regex]'case"gateway":case"mantle":return[ \t]*\{ok:').IsMatch($s)
    $gwRaw  = @(([regex]'case"gateway":case"mantle":(return[ \t]*[$A-Za-z_][\w$]*\([$\w]+\))').Matches($s)).Count
    return [pscustomobject]@{ GatewayOk = $gwDone -and ($gwRaw -eq 0); HasUnpatchedGateway = ($gwRaw -gt 0) }
}

# ------------------------------------------------------- asar integrity -----

function Repair-AsarIntegrity {
    <# Electron validates: (a) per-file chunk hashes stored in the asar header,
       (b) the header's own hash, which is hardcoded in claude.exe.
       After any chunk edit we recompute all stale entries, then sync the
       expectation in claude.exe (same 64-hex length, in place). #>
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$AsarPath, [Parameter(Mandatory)][string]$ExePath)

    $bytes = [System.IO.File]::ReadAllBytes($AsarPath)
    $N = [BitConverter]::ToUInt32($bytes, 4)
    $dataStart = 8 + $N
    $jsonLen = $N - 8
    $headerHashBefore = Get-Sha256Hex -Bytes $bytes -Offset 16 -Length $jsonLen

    # the exe must currently expect the header hash we are about to replace
    $exeBytes = [System.IO.File]::ReadAllBytes($ExePath)
    $exeStr = [System.Text.Encoding]::ASCII.GetString($exeBytes)
    # the hash is content-addressed: every ASCII occurrence equal to our current
    # header hash refers to this header, so replace all of them.
    $exeIdxAll = Find-AsciiAll -Haystack $exeStr -Needle $headerHashBefore
    if ($exeIdxAll.Count -eq 0) {
        throw ("claude.exe does not contain the current asar header hash ($headerHashBefore). " +
               "Refusing to modify a mismatched install; the app may already be in an unknown state.")
    }

    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    $ascii = [System.Text.Encoding]::ASCII
    $json = $utf8NoBom.GetString($bytes, 16, $jsonLen)
    $obj = $json | ConvertFrom-Json

    # collect every file entry that declares integrity
    $entries = New-Object System.Collections.ArrayList
    $walk = {
        param($node, $path)
        if ($null -eq $node) { return }
        $props = @($node.PSObject.Properties.Name)
        if ($props -contains 'files') {
            foreach ($k in $node.files.PSObject.Properties.Name) { & $walk $node.files.$k ($path + '/' + $k) }
        } elseif ($props -contains 'integrity' -and $node.integrity -and ($props -contains 'offset') -and ($props -contains 'size')) {
            [void]$entries.Add([pscustomobject]@{
                Path = $path; Size = [int64]$node.size; Offset = $node.offset
                Hash = $node.integrity.hash; Blocks = @($node.integrity.blocks); BlockSize = $node.integrity.blockSize
            })
        }
    }
    foreach ($k in $obj.files.PSObject.Properties.Name) { & $walk $obj.files.$k $k }

    $checked = 0; $fixed = 0
    foreach ($e in $entries) {
        $checked++
        $start = [int]($dataStart + [int64]$e.Offset)
        if ($start -lt 0 -or ($start + $e.Size) -gt $bytes.Length) {
            throw "Entry '$($e.Path)' points outside the archive; aborting."
        }
        $h = Get-Sha256Hex -Bytes $bytes -Offset $start -Length ([int]$e.Size)
        $newBlocks = @()
        if ($e.BlockSize -gt 0) {
            for ($p = 0; $p -lt $e.Size; $p += $e.BlockSize) {
                $len = [Math]::Min([int64]$e.BlockSize, $e.Size - $p)
                $newBlocks += Get-Sha256Hex -Bytes $bytes -Offset ([int]($start + $p)) -Length ([int]$len)
            }
        }
        $blocksDiffer = ($newBlocks.Count -ne $e.Blocks.Count)
        if (-not $blocksDiffer) {
            for ($i = 0; $i -lt $newBlocks.Count; $i++) {
                if ($newBlocks[$i] -ne $e.Blocks[$i]) { $blocksDiffer = $true; break }
            }
        }
        if ($h -eq $e.Hash -and -not $blocksDiffer) { continue }

        # stale entry: rewrite hashes inside this entry's exact integrity object (same byte length)
        $pat = '"size":' + $e.Size + ',"offset":"' + $e.Offset + '"'
        $pos = $json.IndexOf($pat, [StringComparison]::Ordinal)
        if ($pos -lt 0) {
            $pat = '"size":' + $e.Size + ',"offset":' + $e.Offset
            $pos = $json.IndexOf($pat, [StringComparison]::Ordinal)
        }
        if ($pos -lt 0) { throw "Cannot locate header entry for '$($e.Path)'; aborting." }

        $intKeyPos = $json.IndexOf('"integrity":{', $pos, [StringComparison]::Ordinal)
        if ($intKeyPos -lt 0) { throw "Cannot locate integrity block for '$($e.Path)'; aborting." }
        $intEnd = $json.IndexOf('}', $intKeyPos, [StringComparison]::Ordinal)
        if ($intEnd -lt 0) { throw "Unterminated integrity block for '$($e.Path)'; aborting." }

        $win = $json.Substring($intKeyPos, $intEnd - $intKeyPos + 1)
        $orig = $win
        if ($e.Hash) { $win = $win.Replace('"' + $e.Hash + '"', '"' + $h + '"') }
        for ($i = 0; $i -lt $e.Blocks.Count; $i++) {
            if ($i -lt $newBlocks.Count -and $e.Blocks[$i] -and $e.Blocks[$i] -ne $newBlocks[$i]) {
                $win = $win.Replace('"' + $e.Blocks[$i] + '"', '"' + $newBlocks[$i] + '"')
            }
        }
        if ($win.Length -ne $orig.Length) { throw "Header rewrite changed length for '$($e.Path)'." }
        if ($win -eq $orig) { throw "No-op header rewrite for '$($e.Path)'; aborting." }
        $json = $json.Remove($intKeyPos, $win.Length).Insert($intKeyPos, $win)
        $fixed++
    }

    if ($fixed -gt 0) {
        $jb = $utf8NoBom.GetBytes($json)
        if ($jb.Length -ne $jsonLen) { throw "Header JSON byte length changed ($($jb.Length) != $jsonLen); aborting." }
        [Array]::Copy($jb, 0, $bytes, 16, $jb.Length)
    }

    $headerHashAfter = Get-Sha256Hex -Bytes $bytes -Offset 16 -Length $jsonLen
    $exePatched = $false
    if ($headerHashAfter -ne $headerHashBefore) {
        $nb = $ascii.GetBytes($headerHashAfter)
        foreach ($idx in $exeIdxAll) {
            [Array]::Copy($nb, 0, $exeBytes, $idx, 64)
        }
        [System.IO.File]::WriteAllBytes($ExePath, $exeBytes)
        $exePatched = $true
    }
    if ($fixed -gt 0 -or $exePatched) { [System.IO.File]::WriteAllBytes($AsarPath, $bytes) }

    # final verification pass (checks whole-file hash and all block hashes)
    $vb = [System.IO.File]::ReadAllBytes($AsarPath)
    $vN = [BitConverter]::ToUInt32($vb, 4)
    $vHdr = Get-Sha256Hex -Bytes $vb -Offset 16 -Length ($vN - 8)
    $vjson = $utf8NoBom.GetString($vb, 16, ($vN - 8))
    $vobj = $vjson | ConvertFrom-Json
    $vStart = 8 + $vN
    $vwalk = {
        param($node)
        if ($null -eq $node) { return }
        $props = @($node.PSObject.Properties.Name)
        if ($props -contains 'files') {
            foreach ($k in $node.files.PSObject.Properties.Name) { & $vwalk $node.files.$k }
        } elseif ($props -contains 'integrity' -and $node.integrity -and ($props -contains 'offset') -and ($props -contains 'size')) {
            $sz = [int64]$node.size
            $st = [int]($vStart + [int64]$node.offset)
            $hh = Get-Sha256Hex -Bytes $vb -Offset $st -Length ([int]$sz)
            $entryStale = ($hh -ne $node.integrity.hash)
            if (-not $entryStale -and $node.integrity.blockSize -gt 0) {
                $vBlocks = @($node.integrity.blocks)
                $bi = 0
                for ($p = 0; $p -lt $sz; $p += $node.integrity.blockSize) {
                    $len = [Math]::Min([int64]$node.integrity.blockSize, $sz - $p)
                    $bh = Get-Sha256Hex -Bytes $vb -Offset ([int]($st + $p)) -Length ([int]$len)
                    if ($bi -ge $vBlocks.Count -or $bh -ne $vBlocks[$bi]) {
                        $entryStale = $true
                        break
                    }
                    $bi++
                }
                if ($bi -ne $vBlocks.Count) { $entryStale = $true }
            }
            if ($entryStale) { $script:__stalePaths += $node }
        }
    }
    $script:__stalePaths = @()
    foreach ($k in $vobj.files.PSObject.Properties.Name) { & $vwalk $vobj.files.$k }
    $stale = $script:__stalePaths.Count
    Remove-Variable __stalePaths -Scope Script -ErrorAction SilentlyContinue

    $exeOk = $ascii.GetString([System.IO.File]::ReadAllBytes($ExePath)).Contains($vHdr)
    if ($stale -gt 0 -or -not $exeOk) {
        throw "Integrity verification failed (stale entries: $stale, exe expectation match: $exeOk)."
    }

    return [pscustomobject]@{
        EntriesChecked = $checked
        EntriesFixed   = $fixed
        HeaderHash     = $vHdr
        ExePatched     = $exePatched
    }
}

# ------------------------------------------------------------- models -------

function Get-EndpointModels {
    <# Fetch available model ids from an endpoint. Understands both
       OpenAI-style {data:[{id}]} and Anthropic-style {models:[{id}]} replies. #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$BaseUrl,
        [string]$ApiKey,
        [ValidateSet('bearer', 'x-api-key')][string]$AuthScheme = 'bearer'
    )
    $base = $BaseUrl.TrimEnd('/')
    $candidates = @()
    if ($base -match '/v1$') {
        $candidates += "$base/models"
        $rootBase = ($base -replace '/v1$', '')
        $candidates += "$rootBase/models"
    } else {
        $candidates += "$base/v1/models"
        $candidates += "$base/models"
    }
    $candidates = $candidates | Select-Object -Unique

    $headers = @{}
    if ($ApiKey) {
        if ($AuthScheme -eq 'x-api-key') { $headers['x-api-key'] = $ApiKey }
        else { $headers['Authorization'] = "Bearer $ApiKey" }
    }

    # Identify ourselves explicitly: some gateways sit behind a WAF (e.g. Cloudflare) that
    # blocks generic HTTP-client User-Agents outright with a 403 before the request
    # reaches the gateway.
    $userAgent = 'claude-endpoint-mapper/1.0'

    $lastErr = $null
    foreach ($url in $candidates) {
        try {
            $resp = Invoke-RestMethod -Uri $url -Headers $headers -Method Get -TimeoutSec 20 -UserAgent $userAgent
            $ids = @()
            if ($resp -and $resp.data -and ($resp.data | Get-Member -Name id -ErrorAction SilentlyContinue)) {
                $ids = @($resp.data | ForEach-Object { $_.id } | Where-Object { $_ })
            } elseif ($resp -and $resp.models) {
                $ids = @($resp.models | ForEach-Object { if ($_.id) { $_.id } elseif ($_.name) { $_.name } } | Where-Object { $_ })
            } elseif ($resp -is [array]) {
                $ids = @($resp | ForEach-Object { if ($_.id) { $_.id } elseif ($_.name) { $_.name } } | Where-Object { $_ })
            }
            if ($ids.Count -gt 0) { return @($ids | Select-Object -Unique | Sort-Object) }
            $lastErr = "Endpoint replied but no model ids were recognized ($url)."
        } catch {
            $lastErr = "$url -> $($_.Exception.Message)"
        }
    }
    throw "Could not list models from endpoint. $lastErr"
}

# ------------------------------------------------------------- config -------

function Get-DesktopConfigPath {
    $lib = Join-Path $env:LOCALAPPDATA 'Claude-3p\configLibrary'
    $metaPath = Join-Path $lib '_meta.json'
    if (Test-Path $metaPath) {
        try {
            $meta = Get-Content $metaPath -Raw | ConvertFrom-Json
            if ($meta.appliedId) {
                $p = Join-Path $lib "$($meta.appliedId).json"
                if (Test-Path $p) { return $p }
            }
        } catch { }
    }
    $any = @(Get-ChildItem $lib -Filter '*.json' -ErrorAction SilentlyContinue | Where-Object { $_.Name -ne '_meta.json' } | Select-Object -First 1)
    if ($any.Count -gt 0) { return $any[0].FullName }
    return $null
}

function Write-DesktopConfig {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$BaseUrl,
        [string]$ApiKey,
        [ValidateSet('bearer', 'x-api-key')][string]$AuthScheme = 'bearer',
        [Parameter(Mandatory)][array]$Models,   # @{ Name; Tier; Label }
        [ValidateSet('keep', 'none', 'tavily', 'brave', 'exa', 'custom')][string]$WebSearchProvider = 'keep',
        [string]$WebSearchApiKey,
        [string]$WebSearchCustomUrl,
        [switch]$CreateIfMissing
    )
    $cfgPath = Get-DesktopConfigPath
    if (-not $cfgPath) {
        if (-not $CreateIfMissing) { throw 'No Claude Desktop third-party config found.' }
        $lib = Join-Path $env:LOCALAPPDATA 'Claude-3p\configLibrary'
        New-Item -ItemType Directory -Path $lib -Force | Out-Null
        $id = [guid]::NewGuid().ToString()
        $cfgPath = Join-Path $lib "$id.json"
        $metaPath = Join-Path $lib '_meta.json'
        $meta = [ordered]@{ appliedId = $id; entries = @([ordered]@{ id = $id; name = 'Custom endpoint' }) }
        $metaJson = $meta | ConvertTo-Json -Depth 6
        [System.IO.File]::WriteAllText($metaPath, $metaJson, (New-Object System.Text.UTF8Encoding($false)))
    }

    # merge into whatever exists so unrelated settings survive
    $cfg = $null
    if (Test-Path $cfgPath) {
        try { $cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json } catch { $cfg = $null }
    }
    if (-not $cfg) { $cfg = [pscustomobject]@{} }

    function Set-Prop($obj, [string]$name, $value) {
        if ($obj.PSObject.Properties.Name -contains $name) { $obj.$name = $value }
        else { $obj | Add-Member -NotePropertyName $name -NotePropertyValue $value }
    }

    # key handling: keep existing (possibly sealed) key when none supplied
    $keyOut = $ApiKey
    if (-not $keyOut) {
        if ($cfg.PSObject.Properties.Name -contains 'inferenceGatewayApiKey') { $keyOut = $cfg.inferenceGatewayApiKey }
        else { throw 'No API key supplied and no existing key in config.' }
    }

    Set-Prop $cfg 'inferenceProvider' 'gateway'
    Set-Prop $cfg 'inferenceCredentialKind' 'static'
    Set-Prop $cfg 'inferenceGatewayBaseUrl' $BaseUrl.TrimEnd('/')
    Set-Prop $cfg 'inferenceGatewayApiKey' $keyOut
    Set-Prop $cfg 'inferenceGatewayAuthScheme' $AuthScheme

    $entries = @()
    foreach ($m in $Models) {
        if (-not $m.Name) { continue }
        $e = [ordered]@{ name = $m.Name }
        if ($m.Label) { $e.labelOverride = $m.Label }
        $tier = "$($m.Tier)".ToLowerInvariant()
        if ($tier -in @('sonnet', 'opus', 'haiku', 'fable', 'mythos')) {
            $e.anthropicFamilyTier = $tier
            $e.isFamilyDefault = $true
        }
        $entries += [pscustomobject]$e
    }
    if ($entries.Count -eq 0) { throw 'No models selected.' }
    Set-Prop $cfg 'inferenceModels' $entries
    if (-not ($cfg.PSObject.Properties.Name -contains 'alwaysStartWithDefaultModel')) {
        Set-Prop $cfg 'alwaysStartWithDefaultModel' $false
    }
    if (-not ($cfg.PSObject.Properties.Name -contains 'developerModeEnabled')) {
        Set-Prop $cfg 'developerModeEnabled' $true
    }

    # --- optional built-in web search (runs in the desktop app, not the gateway) ---
    # 'keep' leaves the config alone; 'none' removes our entry; a provider adds or
    # replaces it. Unrelated managedMcpServers entries are always preserved.
    $wsExisting = $null
    $otherServers = @()
    if ($cfg.PSObject.Properties.Name -contains 'managedMcpServers' -and $cfg.managedMcpServers) {
        foreach ($srv in @($cfg.managedMcpServers)) {
            if ("$($srv.server)" -eq 'websearch') {
                if (-not $wsExisting) { $wsExisting = $srv }
            } else {
                $otherServers += $srv
            }
        }
    }

    $webSearchState = 'unchanged'
    if ($WebSearchProvider -ne 'keep') {
        $servers = $otherServers
        if ($WebSearchProvider -eq 'none') {
            $webSearchState = 'disabled'
        } else {
            $key = "$WebSearchApiKey".Trim()
            $reuse = ($wsExisting -and "$($wsExisting.provider)" -eq $WebSearchProvider)
            $headers = $null
            # blank key + same provider: reuse the headers already on disk
            if (-not $key -and $reuse -and
                $wsExisting.PSObject.Properties.Name -contains 'headers' -and $wsExisting.headers) {
                $headers = $wsExisting.headers
            }

            $customUrl = "$WebSearchCustomUrl".Trim()
            if ($WebSearchProvider -eq 'custom' -and -not $customUrl -and $reuse -and
                $wsExisting.PSObject.Properties.Name -contains 'customUrl') {
                $customUrl = "$($wsExisting.customUrl)"
            }
            if ($WebSearchProvider -eq 'custom' -and -not $customUrl) {
                throw 'Custom web search needs a URL. The built-in server POSTs {"q":"..."} to it and expects {"results":[{title,url,snippet}]} back.'
            }

            if (-not $headers) {
                switch ($WebSearchProvider) {
                    'tavily' {
                        if ($key) { $headers = [ordered]@{ Authorization = "Bearer $key" } }
                        else      { $headers = [ordered]@{ 'X-Tavily-Access-Mode' = 'keyless' } }
                    }
                    'brave' {
                        if (-not $key) { throw 'Brave web search needs an API key (sent as X-Subscription-Token).' }
                        $headers = [ordered]@{ 'X-Subscription-Token' = $key }
                    }
                    'exa' {
                        if (-not $key) { throw 'Exa web search needs an API key (sent as x-api-key).' }
                        $headers = [ordered]@{ 'x-api-key' = $key }
                    }
                    'custom' {
                        if ($key) { $headers = [ordered]@{ Authorization = "Bearer $key" } }
                        else      { $headers = [ordered]@{} }
                    }
                }
            }

            $entry = [ordered]@{
                name       = 'Web search'
                server     = 'websearch'
                provider   = $WebSearchProvider
                headers    = $headers
                toolPolicy = [ordered]@{ 'web_search' = 'allow' }
            }
            if ($WebSearchProvider -eq 'custom') { $entry['customUrl'] = $customUrl }
            $servers = @($entry) + $otherServers

            $webSearchState = $WebSearchProvider
            $keyless = $false
            if ($headers -is [System.Collections.IDictionary]) {
                $keyless = $headers.Contains('X-Tavily-Access-Mode')
            } elseif ($headers.PSObject.Properties.Name -contains 'X-Tavily-Access-Mode') {
                $keyless = $true
            }
            if ($keyless) { $webSearchState += ' (keyless)' }
        }

        if ($servers.Count -gt 0) {
            Set-Prop $cfg 'managedMcpServers' @($servers)
        } elseif ($cfg.PSObject.Properties.Name -contains 'managedMcpServers') {
            $cfg.PSObject.Properties.Remove('managedMcpServers')
        }
    }

    $json = $cfg | ConvertTo-Json -Depth 12
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($cfgPath, $json, $utf8NoBom)
    return [pscustomobject]@{ Path = $cfgPath; ModelCount = $entries.Count; Bytes = (Get-Item $cfgPath).Length; WebSearch = $webSearchState }
}

function Write-ClaudeCodeConfig {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$BaseUrl,
        [string]$ApiKey,
        [array]$Models   # @{ Name; Tier; Label } - empty tier = leave that slot untouched
    )
    $path = Join-Path $env:USERPROFILE '.claude\settings.json'
    $dir = Split-Path $path -Parent
    New-Item -ItemType Directory -Path $dir -Force | Out-Null

    $settings = $null
    if (Test-Path $path) {
        try { $settings = Get-Content $path -Raw | ConvertFrom-Json } catch { $settings = $null }
    }
    if (-not $settings) { $settings = [pscustomobject]@{} }

    if (-not ($settings.PSObject.Properties.Name -contains 'env') -or -not $settings.env) {
        $settings | Add-Member -NotePropertyName 'env' -NotePropertyValue ([pscustomobject]@{})
    }
    $envObj = $settings.env
    function Set-EnvProp($obj, [string]$name, $value) {
        if ($obj.PSObject.Properties.Name -contains $name) { $obj.$name = $value }
        else { $obj | Add-Member -NotePropertyName $name -NotePropertyValue $value }
    }
    function Remove-EnvProp($obj, [string]$name) {
        if ($obj.PSObject.Properties.Name -contains $name) { $obj.PSObject.Properties.Remove($name) }
    }

    Set-EnvProp $envObj 'ANTHROPIC_BASE_URL' $BaseUrl.TrimEnd('/')
    if ($ApiKey) { Set-EnvProp $envObj 'ANTHROPIC_AUTH_TOKEN' $ApiKey }

    $knownTiers = @('sonnet', 'opus', 'haiku', 'fable', 'mythos')
    foreach ($m in @($Models)) {
        $tier = "$($m.Tier)".ToLowerInvariant()
        if ($tier -in $knownTiers) {
            $key = 'ANTHROPIC_DEFAULT_' + $tier.ToUpperInvariant() + '_MODEL'
            if ($m.Name) { Set-EnvProp $envObj $key $m.Name }
            else { Remove-EnvProp $envObj $key }
        }
    }

    $json = $settings | ConvertTo-Json -Depth 12
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($path, $json, $utf8NoBom)
    return [pscustomobject]@{ Path = $path; Bytes = (Get-Item $path).Length }
}

# --------------------------------------------------------- orchestration ----

function Invoke-Apply {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$BaseUrl,
        [string]$ApiKey,
        [ValidateSet('bearer', 'x-api-key')][string]$AuthScheme = 'bearer',
        [Parameter(Mandatory)][array]$Models,
        [ValidateSet('keep', 'none', 'tavily', 'brave', 'exa', 'custom')][string]$WebSearchProvider = 'keep',
        [string]$WebSearchApiKey,
        [string]$WebSearchCustomUrl,
        [switch]$ConfigureDesktop,
        [switch]$ConfigureClaudeCode,
        [switch]$SkipPatch,
        [switch]$RestartDesktop,
        [switch]$CreateIfMissing
    )
    $log = New-Object System.Collections.Generic.List[string]
    if (-not $ConfigureDesktop -and -not $ConfigureClaudeCode) { $ConfigureDesktop = $true }

    if ($ConfigureDesktop) {
        $app = Get-ClaudeDesktopApp
        $log.Add("Desktop install: $($app.Version)")

        if (-not $SkipPatch) {
            Stop-ClaudeDesktop | Out-Null
            # one-time backup of install state as seen at first run
            if (-not (Test-Path "$($app.Asar).origbak")) {
                Copy-Item $app.Asar "$($app.Asar).origbak" -Force
                $log.Add("Backup: $($app.Asar).origbak")
            }
            if (-not (Test-Path "$($app.Exe).origbak")) {
                Copy-Item $app.Exe "$($app.Exe).origbak" -Force
                $log.Add("Backup: $($app.Exe).origbak")
            }

            $pr = Repair-AsarPatch -AsarPath $app.Asar
            foreach ($w in $pr.Warnings) { $log.Add("WARN: $w") }
            if (-not $pr.Supported) { throw 'Claude Desktop version not supported by the validator patch (gateway switch not found).' }
            $log.Add("Validator patch: gateway switch patched=$($pr.GatewayPatched) alreadyOk=$($pr.GatewayAlreadyOk); blocklist neutered=$($pr.BlocklistNeutered) alreadyOk=$($pr.BlocklistAlreadyOk)")

            $ir = Repair-AsarIntegrity -AsarPath $app.Asar -ExePath $app.Exe
            $log.Add("Integrity: $($ir.EntriesChecked) entries checked, $($ir.EntriesFixed) fixed; exe expectation synced=$($ir.ExePatched)")
        }

        $cfgResult = Write-DesktopConfig -BaseUrl $BaseUrl -ApiKey $ApiKey -AuthScheme $AuthScheme -Models $Models `
            -WebSearchProvider $WebSearchProvider -WebSearchApiKey $WebSearchApiKey `
            -WebSearchCustomUrl $WebSearchCustomUrl -CreateIfMissing:$CreateIfMissing
        $log.Add("Desktop config: $($cfgResult.ModelCount) models -> $($cfgResult.Path) ($($cfgResult.Bytes) bytes)")
        if ($cfgResult.WebSearch -ne 'unchanged') { $log.Add("Desktop web search: $($cfgResult.WebSearch)") }
    }

    if ($ConfigureClaudeCode) {
        $ccResult = Write-ClaudeCodeConfig -BaseUrl $BaseUrl -ApiKey $ApiKey -Models $Models
        $log.Add("Claude Code settings: $($ccResult.Path) ($($ccResult.Bytes) bytes)")
    }

    if ($RestartDesktop -and $ConfigureDesktop) {
        Stop-ClaudeDesktop -ErrorAction SilentlyContinue | Out-Null
        Start-ClaudeDesktop
        $log.Add('Claude Desktop restarted.')
    }

    return [pscustomobject]@{ Messages = $log.ToArray(); Success = $true }
}

function Restore-DesktopPatch {
    <# Restores original asar + exe from the .origbak pair (both or neither). #>
    $app = Get-ClaudeDesktopApp
    $asarBak = "$($app.Asar).origbak"; $exeBak = "$($app.Exe).origbak"
    $hasAsar = Test-Path $asarBak; $hasExe = Test-Path $exeBak
    if (-not $hasAsar -and -not $hasExe) { throw 'No .origbak backups found; nothing to restore.' }
    if ($hasAsar -ne $hasExe) { throw "Incomplete backup pair (asar: $hasAsar, exe: $hasExe); refusing a half restore." }
    Stop-ClaudeDesktop | Out-Null
    Copy-Item $asarBak $app.Asar -Force
    Copy-Item $exeBak $app.Exe -Force
    return "Restored original app.asar and claude.exe for $($app.Version)."
}

Export-ModuleMember -Function Get-ClaudeDesktopApp, Stop-ClaudeDesktop, Start-ClaudeDesktop,
    Repair-AsarPatch, Test-AsarPatched, Repair-AsarIntegrity, Get-EndpointModels,
    Get-DesktopConfigPath, Write-DesktopConfig, Write-ClaudeCodeConfig,
    Invoke-Apply, Restore-DesktopPatch
