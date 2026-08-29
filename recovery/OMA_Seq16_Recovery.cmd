@echo off
setlocal
title OMA Sequenz 16 Recovery
set "OMA_RECOVERY_SELF=%~f0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $raw=[IO.File]::ReadAllText($env:OMA_RECOVERY_SELF); $marker=':__OMA_POWERSHELL_PAYLOAD__'; $index=$raw.LastIndexOf($marker,[StringComparison]::Ordinal); if($index -lt 0){throw 'PowerShell-Payload fehlt.'}; $payload=$raw.Substring($index+$marker.Length).TrimStart([char[]]@([char]13,[char]10)); $temp=Join-Path ([IO.Path]::GetTempPath()) ('OMA_Seq16_Recovery_'+[Guid]::NewGuid().ToString('N')+'.ps1'); [IO.File]::WriteAllText($temp,$payload,(New-Object Text.UTF8Encoding($false))); try { & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $temp; exit $LASTEXITCODE } finally { Remove-Item -LiteralPath $temp -Force -ErrorAction SilentlyContinue }"
set "OMA_RECOVERY_EXIT=%ERRORLEVEL%"
echo.
if "%OMA_RECOVERY_EXIT%"=="0" (
  echo Sequenz-16-Recovery abgeschlossen. Der neue Launcher wird geoeffnet.
) else (
  echo Recovery sicher gestoppt. OMA bleibt OFFLINE.
)
echo.
pause
exit /b %OMA_RECOVERY_EXIT%
:__OMA_POWERSHELL_PAYLOAD__
#requires -Version 5.1

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$ExpectedSequence = 16
$ExpectedSuiteVersion = '0.5.11'
$ExpectedRuntimeBridgeVersion = '1.2.13'
$ExpectedLauncherVersion = '1.2.13'
$ExpectedBridgeBuildSha256 = 'e34a52c2bf590e91c5abaa0d7949479f7cbc5c978de9929df5c6aeb589ded2ef'
$ExpectedLauncherTreeSha256 = 'f35dd616e78cded40a18deb3369d4528a1c9e08c0ca563059f3b312d3e64f1a2'
$ExpectedTrustedKeysSha256 = '4ff4184111f4701fddc210083b249940f2d3e95ca67dabc17350cce4276a25fd'
$ChannelUrl = 'https://raw.githubusercontent.com/viodennisschundiak-hub/OMA-GrandMAKI-Updates/main/stable/channel.json'
$BundleUrl = 'https://raw.githubusercontent.com/viodennisschundiak-hub/OMA-GrandMAKI-Updates/main/stable/OMA_GrandMAKI_Update_0_5_11_seq16.zip'

if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
    throw 'LOCALAPPDATA fehlt. Recovery wird nicht ausgeführt.'
}

$Root = Join-Path $env:LOCALAPPDATA 'OMA_GrandMAKI_BOT'
$BasementCodeRoot = Join-Path $Root 'bootstrap\phase5-0.5.0'
$ConfigPath = Join-Path $Root 'config.json'
$LogRoot = Join-Path $Root 'recovery_logs'
New-Item -ItemType Directory -Path $LogRoot -Force | Out-Null
$LogPath = Join-Path $LogRoot ("OMA_Seq16_Recovery_{0}.log" -f (Get-Date -Format 'yyyyMMdd_HHmmss_fff'))

function Write-RecoveryStatus {
    param([Parameter(Mandatory=$true)][string]$Message)
    $line = '[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-ddTHH:mm:ss.fffK'), $Message
    Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
    Write-Host $Message
}

function Assert-ExactHttpsUri {
    param(
        [Parameter(Mandatory=$true)][string]$Value,
        [Parameter(Mandatory=$true)][string]$Expected
    )
    try { $parsed = [Uri]::new($Value, [UriKind]::Absolute) }
    catch { throw 'Signierter Updatekanal enthält eine ungültige URL.' }
    if (
        $parsed.Scheme -ne 'https' -or
        -not $parsed.IsDefaultPort -or
        -not [string]::IsNullOrEmpty($parsed.UserInfo) -or
        -not [string]::IsNullOrEmpty($parsed.Query) -or
        -not [string]::IsNullOrEmpty($parsed.Fragment) -or
        -not [string]::Equals($parsed.AbsoluteUri, $Expected, [StringComparison]::Ordinal)
    ) {
        throw 'Signierter Updatekanal verweist nicht exakt auf das freigegebene Sequenz-16-Paket.'
    }
    return $parsed.AbsoluteUri
}

function Assert-OmaOffline {
    try {
        $processes = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    }
    catch {
        throw 'Der lokale Prozesszustand konnte nicht sicher geprüft werden. OMA bleibt OFFLINE.'
    }
    $active = @($processes | Where-Object {
        $name = [string]$_.Name
        $commandLine = [string]$_.CommandLine
        $name -ieq 'tunnel-client.exe' -or
        $commandLine -match '(?i)OMA_GrandMAKI_BOT\.ps1|OMA_Launcher_Bootstrap\.ps1|run_mcp\.py|\-m\s+src\.server'
    })
    if ($active.Count -ne 0) {
        throw 'OMA, Launcher, MCP-Server oder Tunnel läuft noch. Zuerst vollständig stoppen.'
    }
}

function Resolve-RecoveryPython {
    $candidates = New-Object System.Collections.Generic.List[string]
    if (Test-Path -LiteralPath $ConfigPath -PathType Leaf) {
        try {
            $config = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($null -ne $config.PSObject.Properties['RuntimePythonPath']) {
                $candidates.Add([string]$config.RuntimePythonPath)
            }
        }
        catch {
            throw 'config.json ist ungültig. Recovery verändert die Konfiguration nicht.'
        }
    }
    if (-not [string]::IsNullOrWhiteSpace($env:USERPROFILE)) {
        $candidates.Add((Join-Path $env:USERPROFILE 'MA2_AI_BRIDGE\.venv\Scripts\python.exe'))
    }
    foreach ($candidate in $candidates) {
        if (
            -not [string]::IsNullOrWhiteSpace($candidate) -and
            (Test-Path -LiteralPath $candidate -PathType Leaf)
        ) {
            return [IO.Path]::GetFullPath($candidate)
        }
    }
    throw 'Die bestehende OMA-Python-Runtime wurde nicht gefunden.'
}

function Resolve-TrustedKeys {
    $launcherRoot = Join-Path $Root 'launcher'
    if (-not (Test-Path -LiteralPath $launcherRoot -PathType Container)) {
        throw 'Der lokale Launcher-Ordner fehlt.'
    }
    $candidates = @(Get-ChildItem -LiteralPath $launcherRoot -Directory -ErrorAction Stop |
        Sort-Object Name -Descending |
        ForEach-Object { Join-Path $_.FullName 'trusted_update_keys.json' })
    foreach ($candidate in $candidates) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        $actual = (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -eq $ExpectedTrustedKeysSha256) { return $candidate }
    }
    throw 'Kein exakt freigegebener öffentlicher OMA-Prüfschlüssel wurde gefunden.'
}

$PythonExe = $null
$TrustedKeys = $null

function Invoke-BasementJson {
    param([Parameter(Mandatory=$true)][string[]]$Arguments)
    $output = @(& $PythonExe -m oma_basement @Arguments 2>&1)
    $exitCode = $LASTEXITCODE
    $plain = (($output | ForEach-Object { [string]$_ }) -join [Environment]::NewLine).Trim()
    if ($exitCode -ne 0) {
        throw "Basement-Steuerung wurde sicher gestoppt (Exit $exitCode)."
    }
    if ([string]::IsNullOrWhiteSpace($plain)) { return $null }
    try { return ($plain | ConvertFrom-Json) }
    catch { throw 'Basement lieferte keine eindeutige JSON-Antwort.' }
}

function Assert-SignedSeq16Manifest {
    param([Parameter(Mandatory=$true)]$Manifest)
    if (
        [int]$Manifest.schema_version -ne 2 -or
        [string]$Manifest.product -ne 'oma-grandmaki-suite' -or
        [int]$Manifest.sequence -ne $ExpectedSequence -or
        [string]$Manifest.release.version -ne $ExpectedSuiteVersion -or
        ([string]$Manifest.release.build_sha256).ToLowerInvariant() -ne $ExpectedBridgeBuildSha256 -or
        [string]$Manifest.launcher.version -ne $ExpectedLauncherVersion -or
        ([string]$Manifest.launcher.tree_sha256).ToLowerInvariant() -ne $ExpectedLauncherTreeSha256
    ) {
        throw 'Das signierte Bundle passt nicht exakt zum freigegebenen Seq-16-Vertrag.'
    }
}

function Assert-ActiveSeq16 {
    $currentPath = Join-Path $Root 'current.json'
    if (-not (Test-Path -LiteralPath $currentPath -PathType Leaf)) {
        throw 'Der aktive A/B-Zeiger fehlt nach der Installation.'
    }
    $current = Get-Content -LiteralPath $currentPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $slot = [string]$current.slot
    if (@('release-A','release-B') -notcontains $slot) {
        throw 'Der aktive A/B-Slot ist ungültig.'
    }
    if (([string]$current.build_sha256).ToLowerInvariant() -ne $ExpectedBridgeBuildSha256) {
        throw 'Der aktive A/B-Zeiger zeigt nicht auf den Seq-16-Bridge-Build.'
    }
    $releaseRoot = Join-Path (Join-Path $Root 'releases') $slot
    $releaseManifestPath = Join-Path $releaseRoot 'OMA_RELEASE_MANIFEST.json'
    $buildMarkerPath = Join-Path $releaseRoot 'OMA_EXPECTED_BUILD_SHA256.txt'
    $runtimeContractPath = Join-Path $releaseRoot 'OMA_RUNTIME_CONTRACT.json'
    foreach ($required in @($releaseManifestPath,$buildMarkerPath,$runtimeContractPath)) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
            throw 'Der aktive Seq-16-A/B-Slot ist unvollständig.'
        }
    }
    $releaseManifest = Get-Content -LiteralPath $releaseManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (
        [string]$releaseManifest.slot -ne $slot -or
        [string]$releaseManifest.release_id -ne [string]$current.release_id -or
        [string]$releaseManifest.status -ne 'verified' -or
        [string]$releaseManifest.source -ne 'signed_update_bundle' -or
        ([string]$releaseManifest.build_sha256).ToLowerInvariant() -ne $ExpectedBridgeBuildSha256
    ) {
        throw 'A/B-Zeiger und verifiziertes Release-Manifest stimmen nicht überein.'
    }
    $buildMarker = (Get-Content -LiteralPath $buildMarkerPath -Raw -Encoding UTF8).Trim().ToLowerInvariant()
    if ($buildMarker -ne $ExpectedBridgeBuildSha256) {
        throw 'Der aktive Bridge-Buildmarker stimmt nicht.'
    }
    $contract = Get-Content -LiteralPath $runtimeContractPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (
        [string]$contract.release.suite_version -ne $ExpectedSuiteVersion -or
        [string]$contract.release.bridge_version -ne $ExpectedRuntimeBridgeVersion -or
        [string]$contract.release.launcher_version -ne $ExpectedLauncherVersion -or
        [int]$contract.release.update_sequence -ne $ExpectedSequence
    ) {
        throw 'Der installierte Runtime-Vertrag ist nicht Seq 16.'
    }
}

function Assert-LauncherSeq16 {
    $launcherRoot = Join-Path (Join-Path $Root 'launcher') $ExpectedLauncherVersion
    $markerPath = Join-Path $launcherRoot 'installed_launcher.json'
    $shaManifestPath = Join-Path $launcherRoot 'SHA256SUMS.txt'
    $launcherScriptPath = Join-Path $launcherRoot 'OMA_GrandMAKI_BOT.ps1'
    $bootstrapPath = Join-Path $launcherRoot 'OMA_Launcher_Bootstrap.ps1'
    foreach ($required in @($markerPath,$shaManifestPath,$launcherScriptPath,$bootstrapPath)) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
            throw 'Der installierte Launcher 1.2.13 ist unvollständig.'
        }
    }
    $marker = Get-Content -LiteralPath $markerPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (
        [string]$marker.launcher_version -ne $ExpectedLauncherVersion -or
        [string]$marker.source -ne 'signed_update_bundle'
    ) {
        throw 'Der Launcher besitzt keinen gültigen signierten Herkunftsnachweis.'
    }
    $seen = @{}
    foreach ($line in @(Get-Content -LiteralPath $shaManifestPath -Encoding UTF8)) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        if ($line -notmatch '^([0-9a-f]{64})  ([^\\]+)$') {
            throw 'Launcher-SHA256SUMS.txt ist ungültig.'
        }
        $expected = $Matches[1]
        $relative = $Matches[2]
        if ($relative.Contains('..') -or $seen.ContainsKey($relative)) {
            throw 'Launcher-Prüfsummenpfad ist unsicher oder doppelt.'
        }
        $seen[$relative] = $true
        $path = Join-Path $launcherRoot $relative
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Launcher-Datei fehlt: $relative"
        }
        $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $expected) {
            throw "Launcher-Dateihash stimmt nicht: $relative"
        }
    }
    if ($seen.Count -ne 7) {
        throw 'Launcher-Prüfsummenabdeckung ist nicht exakt.'
    }
    $launcherScript = Get-Content -LiteralPath $launcherScriptPath -Raw -Encoding UTF8
    $appVersionPattern = "(?m)^`$script:AppVersion\s*=\s*'1\.2\.13'\s*$"
    $bridgeBindingPattern = "(?m)^`$script:PackagedBridgeBuildSha256\s*=\s*'$ExpectedBridgeBuildSha256'\s*$"
    if (
        $launcherScript -notmatch $appVersionPattern -or
        $launcherScript -notmatch $bridgeBindingPattern
    ) {
        throw 'Launcher-Version oder Bridge-Bindung stimmt nach der Installation nicht.'
    }
    return $launcherRoot
}

function Set-Seq16Shortcut {
    param([Parameter(Mandatory=$true)][string]$LauncherRoot)
    $bootstrap = Join-Path $LauncherRoot 'OMA_Launcher_Bootstrap.ps1'
    $powerShellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    if (-not (Test-Path -LiteralPath $powerShellExe -PathType Leaf)) {
        $powerShellExe = (Get-Command powershell.exe -ErrorAction Stop).Source
    }
    $desktop = [Environment]::GetFolderPath('Desktop')
    $shortcutPath = Join-Path $desktop 'OMA GrandMAKI-BOT.lnk'
    $backupPath = $null
    $script:RecoveryShortcutPath = $shortcutPath
    if (Test-Path -LiteralPath $shortcutPath -PathType Leaf) {
        $backupPath = "$shortcutPath.before-seq16-$(Get-Date -Format 'yyyyMMdd_HHmmss_fff').bak"
        Copy-Item -LiteralPath $shortcutPath -Destination $backupPath
        $script:RecoveryShortcutBackupPath = $backupPath
    }
    $shortcutArguments = '-NoLogo -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -STA -File "' + $bootstrap + '"'
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $powerShellExe
    $shortcut.Arguments = $shortcutArguments
    $shortcut.WorkingDirectory = $LauncherRoot
    $shortcut.Description = 'OMA GrandMAKI-BOT Launcher 1.2.13'
    $iconPath = Join-Path $LauncherRoot 'assets\OMA.ico'
    if (Test-Path -LiteralPath $iconPath -PathType Leaf) { $shortcut.IconLocation = $iconPath }
    $shortcut.Save()
    $verified = $shell.CreateShortcut($shortcutPath)
    if (
        -not [string]::Equals([string]$verified.TargetPath, [string]$powerShellExe, [StringComparison]::OrdinalIgnoreCase) -or
        [string]$verified.Arguments -ne $shortcutArguments -or
        -not [string]::Equals([string]$verified.WorkingDirectory, [string]$LauncherRoot, [StringComparison]::OrdinalIgnoreCase)
    ) {
        throw 'Die Desktop-Verknüpfung konnte nicht eindeutig auf Launcher 1.2.13 gesetzt werden.'
    }
    return [pscustomobject]@{
        ShortcutPath = $shortcutPath
        BackupPath = $backupPath
        BootstrapPath = $bootstrap
        PowerShellExe = $powerShellExe
    }
}

$stagedUpdateId = $null
$mustRollback = $false
$shortcutResult = $null
$script:RecoveryShortcutPath = $null
$script:RecoveryShortcutBackupPath = $null

try {
    Write-RecoveryStatus 'OMA Seq 16 Recovery gestartet.'
    Assert-OmaOffline
    Write-RecoveryStatus 'OFFLINE-Zustand bestätigt; keine laufende OMA-/Tunnel-Sitzung gefunden.'

    if (-not (Test-Path -LiteralPath (Join-Path $BasementCodeRoot 'oma_basement') -PathType Container)) {
        throw 'OMA Basement 0.5.0 fehlt. Recovery wird nicht ausgeführt.'
    }
    $PythonExe = Resolve-RecoveryPython
    $TrustedKeys = Resolve-TrustedKeys
    Write-RecoveryStatus 'Bestehende Basement-Runtime und öffentlicher Prüfschlüssel bestätigt.'

    foreach ($name in @(
        'CONTROL_PLANE_API_KEY','CONTROL_PLANE_TUNNEL_ID','MCP_COMMAND',
        'GITHUB_TOKEN','GITHUB_MODELS_TOKEN','GMA_PASSWORD','MA_PASSWORD',
        'OMA_AUTH_BYPASS','GMA_AUTH_BYPASS','PYTHONHOME','PYTHONPATH'
    )) {
        Remove-Item -LiteralPath ("Env:" + $name) -ErrorAction SilentlyContinue
    }
    $env:PYTHONPATH = $BasementCodeRoot
    $env:PYTHONNOUSERSITE = '1'
    $env:OMA_DATA_ROOT = Join-Path $Root 'runtime_data'

    $checked = Invoke-BasementJson -Arguments @(
        'update-check','--root',$Root,'--channel-url',$ChannelUrl,'--trusted-keys',$TrustedKeys
    )
    if (
        [int]$checked.channel.sequence -ne $ExpectedSequence -or
        [string]$checked.channel.release.version -ne $ExpectedSuiteVersion
    ) {
        throw 'Stable-Kanal ist nicht exakt OMA 0.5.11 / Sequenz 16.'
    }
    $verifiedBundleUrl = Assert-ExactHttpsUri -Value ([string]$checked.channel.release.bundle_url) -Expected $BundleUrl
    Write-RecoveryStatus 'Stable-Kanal, Ed25519-Signatur und Sequenz 16 bestätigt.'

    if ([bool]$checked.update_available) {
        $stagingRoot = Join-Path $Root 'launcher_updates'
        New-Item -ItemType Directory -Path $stagingRoot -Force | Out-Null
        $bundle = Join-Path $stagingRoot ("oma-seq16-recovery-{0}.zip" -f [Guid]::NewGuid().ToString('N'))
        Invoke-WebRequest -Uri $verifiedBundleUrl -OutFile $bundle -UseBasicParsing -TimeoutSec 120
        $downloadHash = (Get-FileHash -LiteralPath $bundle -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($downloadHash -ne ([string]$checked.channel.release.bundle_sha256).ToLowerInvariant()) {
            throw 'Download-Hash stimmt nicht mit dem signierten Stable-Kanal überein.'
        }
        Write-RecoveryStatus 'Bundle heruntergeladen; signierter SHA-256 stimmt.'

        $staged = Invoke-BasementJson -Arguments @(
            'update-stage','--root',$Root,'--bundle',$bundle,'--trusted-keys',$TrustedKeys
        )
        if ([string]$staged.state -ne 'staged' -or [string]::IsNullOrWhiteSpace([string]$staged.update_id)) {
            throw 'Sequenz 16 wurde nicht vollständig in den inaktiven Slot gestagt.'
        }
        Assert-SignedSeq16Manifest -Manifest $staged.manifest
        $stagedUpdateId = [string]$staged.update_id
        Write-RecoveryStatus 'Inaktiver A/B-Slot und kompletter Seq-16-Vertrag verifiziert.'

        $mustRollback = $true
        $activated = Invoke-BasementJson -Arguments @(
            'update-apply','--root',$Root,'--update-id',$stagedUpdateId
        )
        if ([bool]$activated.rolled_back) {
            $mustRollback = $false
            throw 'Healthprüfung schlug fehl; Basement hat automatisch zurückgerollt.'
        }
        Write-RecoveryStatus 'A/B-Aktivierung ohne automatischen Rollback abgeschlossen.'
    }
    else {
        Write-RecoveryStatus 'Sequenz 16 ist laut signiertem Kanal bereits aktiv; Zustand wird vollständig rückgeprüft.'
    }

    Assert-ActiveSeq16
    $newLauncherRoot = Assert-LauncherSeq16
    Write-RecoveryStatus 'Aktive Bridge, Runtime-Vertrag und Launcher 1.2.13 stimmen exakt.'

    $shortcutResult = Set-Seq16Shortcut -LauncherRoot $newLauncherRoot
    Write-RecoveryStatus 'Desktop-Verknüpfung atomar auf Launcher 1.2.13 gesetzt und rückgelesen.'
    $mustRollback = $false

    Start-Process -FilePath $shortcutResult.PowerShellExe `
        -ArgumentList ('-NoLogo -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -STA -File "' + $shortcutResult.BootstrapPath + '"') `
        -WorkingDirectory $newLauncherRoot -WindowStyle Hidden | Out-Null
    Write-RecoveryStatus 'PASS: Sequenz 16 installiert. Neuer Launcher wurde gestartet. Keine grandMA2-Befehle ausgeführt.'
    exit 0
}
catch {
    $failure = $_.Exception.Message
    if ($mustRollback -and -not [string]::IsNullOrWhiteSpace($stagedUpdateId)) {
        try {
            $null = Invoke-BasementJson -Arguments @(
                'update-rollback','--root',$Root,'--update-id',$stagedUpdateId
            )
            Write-RecoveryStatus 'Rollback auf den vorherigen A/B-Zeiger ausgeführt.'
        }
        catch {
            Write-RecoveryStatus 'WARNUNG: Rollback konnte nicht eindeutig bestätigt werden; OMA bleibt OFFLINE.'
        }
    }
    if (
        -not [string]::IsNullOrWhiteSpace([string]$script:RecoveryShortcutPath) -and
        -not [string]::IsNullOrWhiteSpace([string]$script:RecoveryShortcutBackupPath)
    ) {
        try {
            Copy-Item -LiteralPath $script:RecoveryShortcutBackupPath -Destination $script:RecoveryShortcutPath -Force
            Write-RecoveryStatus 'Vorherige Desktop-Verknüpfung wiederhergestellt.'
        }
        catch {
            Write-RecoveryStatus 'WARNUNG: Vorherige Desktop-Verknüpfung konnte nicht wiederhergestellt werden.'
        }
    }
    Write-RecoveryStatus ("FAIL-CLOSED: " + $failure)
    Write-RecoveryStatus ("Diagnose: " + $LogPath)
    exit 1
}
