param(
    [Parameter(Mandatory = $true)]
    [string]$ProtocolRoot,

    [Parameter(Mandatory = $true)]
    [string]$MaterializationRoot,

    [Parameter(Mandatory = $true)]
    [string]$RuntimeIdentity,

    [Parameter(Mandatory = $true)]
    [string]$MaterializationReceipt,

    [Parameter(Mandatory = $true)]
    [string]$ConditionalGo,

    [Parameter(Mandatory = $true)]
    [string]$EvidenceRoot,

    [Parameter(Mandatory = $true)]
    [string]$OperationRoot,

    [switch]$Execute,

    [switch]$ValidateOnly
)

$ErrorActionPreference = "Stop"

$ExpectedProtocolTag = "v5-measurement-protocol-v3"
$ExpectedNormalApp = "C:\Users\leste\AppData\Local\Programs\Ollama\ollama app.exe"
$ExpectedNormalServer = "C:\Users\leste\AppData\Local\Programs\Ollama\ollama.exe"
$ExpectedIsolatedExe = "C:\Users\leste\Downloads\Repos\anachron-v4-evidence\ollama-0.33.2-isolated\runtime\ollama.exe"
$ExpectedIsolatedExeSha256 = "c79df1e0c1bfa10ed813c7030ac4c3ba38bb0e350bd7322d9bb58320343235c6"
$ExpectedIsolatedModels = "C:\Users\leste\Downloads\Repos\anachron-v4-evidence\ollama-0.33.2-isolated\models"
$ExpectedOperationParent = "C:\Users\leste\Downloads\Repos\anachron-v5-evidence"
$HostUrl = "http://127.0.0.1:11434"
$MaximumLogBytes = 1048576

$isolatedProcess = $null
$normalWasStopped = $false
$restoreSucceeded = $false
$runnerExitCode = $null
$analyzerExitCode = $null
$campaignSucceeded = $false
$campaignError = $null
$cleanupError = $null
$baselineVersion = $null
$baselineTags = $null

function Normalize-FullPath([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path)
    $root = [IO.Path]::GetPathRoot($full)
    $trimmed = $full.TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
    if ([string]::Equals($trimmed, $root.TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar), [StringComparison]::OrdinalIgnoreCase)) {
        return $root
    }
    return $trimmed
}

function Assert-SafeEntry([string]$Path, [string]$Label) {
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "$Label contains a reparse point: $Path"
    }
    $streams = @(Get-Item -LiteralPath $Path -Force -Stream * -ErrorAction Stop | Select-Object -ExpandProperty Stream)
    if (@($streams | Where-Object { $_ -notin @(':$DATA', '$DATA') }).Count -ne 0) {
        throw "$Label contains an alternate data stream: $Path"
    }
}

function Assert-SafePathComponents([string]$Path, [string]$Label) {
    $full = Normalize-FullPath $Path
    $root = [IO.Path]::GetPathRoot($full)
    $current = $root
    foreach ($component in $full.Substring($root.Length).Split([char[]]@([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar), [StringSplitOptions]::RemoveEmptyEntries)) {
        $current = Join-Path $current $component
        Assert-SafeEntry $current $Label
    }
}

function Assert-SafeTree([string]$Path, [string]$Label) {
    Assert-SafePathComponents $Path $Label
    foreach ($candidate in @(Get-ChildItem -LiteralPath $Path -Force -Recurse -ErrorAction Stop)) {
        Assert-SafeEntry $candidate.FullName $Label
    }
}

function Assert-ExistingFile([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Label is missing: $Path"
    }
    Assert-SafePathComponents $Path $Label
}

function Assert-ExistingDirectory([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        throw "$Label is missing: $Path"
    }
    Assert-SafePathComponents $Path $Label
}

function Get-Sha256([string]$Path) {
    $stream = $null
    $hasher = $null
    try {
        $stream = [IO.File]::Open((Normalize-FullPath $Path), [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
        $hasher = [Security.Cryptography.SHA256]::Create()
        return [BitConverter]::ToString($hasher.ComputeHash($stream)).Replace("-", "").ToLowerInvariant()
    }
    finally {
        if ($null -ne $hasher) {
            $hasher.Dispose()
        }
        if ($null -ne $stream) {
            $stream.Dispose()
        }
    }
}

function Assert-Sha256([string]$Path, [string]$Expected, [string]$Label) {
    if ($Expected -notmatch "^[0-9a-f]{64}$" -or (Get-Sha256 $Path) -ne $Expected) {
        throw "$Label SHA-256 differs"
    }
}

function Get-CanonicalJson([string]$Path, [string]$Label) {
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if ($item.Length -gt $MaximumLogBytes) {
        throw "$Label exceeds the fixed byte cap"
    }
    try {
        return Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json -ErrorAction Stop
    }
    catch {
        throw "$Label is not valid JSON"
    }
}

function Get-JsonString([object]$Value, [string]$Name, [string]$Label) {
    $property = $Value.PSObject.Properties[$Name]
    if ($null -eq $property -or $property.Value -isnot [string] -or [string]::IsNullOrWhiteSpace($property.Value)) {
        throw "$Label lacks $Name"
    }
    return [string]$property.Value
}

function Assert-ImmediateCreateOnlyChild([string]$Path, [string]$Label) {
    if (Test-Path -LiteralPath $Path) {
        throw "$Label must be absent: $Path"
    }
    $parent = Normalize-FullPath ([IO.Path]::GetDirectoryName($Path))
    if (-not [string]::Equals($parent, $ExpectedOperationParent, [StringComparison]::OrdinalIgnoreCase)) {
        throw "$Label must be an immediate child of the approved evidence parent"
    }
    Assert-SafePathComponents $parent "approved evidence parent"
}

function Assert-ExternalCreateOnlyChild([string]$Path, [string]$Label) {
    if (Test-Path -LiteralPath $Path) {
        throw "$Label must be absent: $Path"
    }
    $parent = Normalize-FullPath ([IO.Path]::GetDirectoryName($Path))
    Assert-ExistingDirectory $parent "$Label parent"
    $protocolPrefix = (Normalize-FullPath $ProtocolRoot) + [IO.Path]::DirectorySeparatorChar
    if ($Path.StartsWith($protocolPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "$Label must be external to the protocol checkout"
    }
}

function Assert-MaterializationTopology {
    $expected = [ordered]@{
        compatibility = Join-Path $MaterializationRoot "compatibility_plan.json"
        carry = Join-Path $MaterializationRoot "carry_forward.json"
        full = Join-Path $MaterializationRoot "full_plan.json"
        receipt = Join-Path $MaterializationRoot "materialization_receipt.json"
        runtime = Join-Path $MaterializationRoot "runtime_identity.json"
        schedule = Join-Path $MaterializationRoot "schedule.json"
        source = Join-Path $MaterializationRoot "source_manifest.json"
    }
    $members = @(Get-ChildItem -LiteralPath $MaterializationRoot -Force)
    if ($members.Count -ne 7 -or @($members | Where-Object { -not $_.PSIsContainer -and $_.Length -le 1048576 }).Count -ne 7 -or (@($members | Measure-Object -Property Length -Sum).Sum -gt 7340032)) {
        throw "materialization root topology or byte budget differs"
    }
    foreach ($entry in $expected.GetEnumerator()) {
        Assert-ExistingFile $entry.Value "materialized $($entry.Key)"
    }
    if ((Normalize-FullPath $ConditionalGo) -eq (Normalize-FullPath $expected.full)) {
        throw "conditional GO must not replace the full plan"
    }
    return $expected
}

function Assert-FrozenProtocol([object]$Go, [object]$SourceManifest) {
    $status = & git.exe -C $ProtocolRoot status --porcelain --untracked-files=all
    $statusExitCode = $LASTEXITCODE
    if ($statusExitCode -ne 0) {
        throw "protocol git status failed"
    }
    if ($status) {
        throw "protocol checkout is dirty"
    }
    $branch = & git.exe -C $ProtocolRoot branch --show-current
    $branchExitCode = $LASTEXITCODE
    if ($branchExitCode -ne 0) {
        throw "protocol git branch query failed"
    }
    if ($branch) {
        throw "protocol checkout must be detached"
    }
    $headOutput = & git.exe -C $ProtocolRoot rev-parse HEAD
    $headExitCode = $LASTEXITCODE
    if ($headExitCode -ne 0) {
        throw "protocol git head query failed"
    }
    $tagObjectOutput = & git.exe -C $ProtocolRoot rev-parse ("refs/tags/" + $ExpectedProtocolTag + "^{tag}")
    $tagObjectExitCode = $LASTEXITCODE
    if ($tagObjectExitCode -ne 0) {
        throw "protocol tag is absent or not annotated"
    }
    $peeledOutput = & git.exe -C $ProtocolRoot rev-parse ("refs/tags/" + $ExpectedProtocolTag + "^{}")
    $peeledExitCode = $LASTEXITCODE
    if ($peeledExitCode -ne 0) {
        throw "protocol tag peel query failed"
    }
    $head = $headOutput.Trim().ToLowerInvariant()
    $tagObject = $tagObjectOutput.Trim().ToLowerInvariant()
    $peeled = $peeledOutput.Trim().ToLowerInvariant()
    if ($head -ne (Get-JsonString $Go "protocol_commit" "conditional GO") -or $tagObject -ne (Get-JsonString $Go "protocol_tag_object" "conditional GO") -or $peeled -ne $head) {
        throw "protocol release identity differs"
    }
    $release = $SourceManifest.release
    if ($null -eq $release -or $release.tag -ne $ExpectedProtocolTag -or $release.commit -ne $head -or $release.tag_object -ne $tagObject -or $release.tag_peeled -ne $peeled) {
        throw "source manifest release differs"
    }
}

function Get-OllamaProcessSnapshot {
    return [pscustomobject]@{
        captured_at_utc = [DateTime]::UtcNow.ToString("o")
        listeners = @(Get-NetTCPConnection -State Listen -LocalPort 11434 -ErrorAction SilentlyContinue | Select-Object LocalAddress, LocalPort, OwningProcess)
        processes = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -ieq "ollama.exe" -or $_.Name -ieq "ollama app.exe" } | Select-Object ProcessId, ParentProcessId, Name, ExecutablePath, CommandLine)
    }
}

function Get-NormalTree([object]$Snapshot) {
    $listeners = @($Snapshot.listeners)
    if ($listeners.Count -ne 1 -or $listeners[0].LocalAddress -ne "127.0.0.1") {
        throw "Expected exactly one normal loopback listener"
    }
    $server = @($Snapshot.processes | Where-Object { $_.ProcessId -eq $listeners[0].OwningProcess })
    $app = @($Snapshot.processes | Where-Object { $_.ProcessId -eq $server[0].ParentProcessId })
    if ($server.Count -ne 1 -or $app.Count -ne 1 -or $server[0].ExecutablePath -ine $ExpectedNormalServer -or $app[0].ExecutablePath -ine $ExpectedNormalApp -or @($Snapshot.processes).Count -ne 2) {
        throw "normal Ollama process chain differs"
    }
    return [pscustomobject]@{ app = $app[0]; server = $server[0] }
}

function Assert-IsolatedTree([object]$Snapshot, [int]$ProcessId) {
    $listeners = @($Snapshot.listeners)
    $processes = @($Snapshot.processes)
    if ($listeners.Count -ne 1 -or $listeners[0].LocalAddress -ne "127.0.0.1" -or $listeners[0].OwningProcess -ne $ProcessId -or $processes.Count -ne 1 -or $processes[0].ProcessId -ne $ProcessId -or $processes[0].ExecutablePath -ine $ExpectedIsolatedExe) {
        throw "isolated Ollama process chain differs"
    }
}

function Wait-ForFreeListener([int]$TimeoutSeconds) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        if (@(Get-NetTCPConnection -State Listen -LocalPort 11434 -ErrorAction SilentlyContinue).Count -eq 0) {
            return
        }
        Start-Sleep -Milliseconds 250
    }
    throw "Timed out waiting for the loopback listener to close"
}

function Wait-ForOwnedListener([int]$ProcessId, [int]$TimeoutSeconds) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        if ($null -eq (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) {
            throw "isolated server exited before opening a listener"
        }
        $listeners = @(Get-NetTCPConnection -State Listen -LocalPort 11434 -ErrorAction SilentlyContinue | Where-Object { $_.OwningProcess -eq $ProcessId -and $_.LocalAddress -eq "127.0.0.1" })
        if ($listeners.Count -eq 1) {
            return
        }
        Start-Sleep -Milliseconds 250
    }
    throw "Timed out waiting for the isolated server listener"
}

function Write-JsonFile([object]$Value, [string]$Path) {
    [IO.File]::WriteAllText($Path, (($Value | ConvertTo-Json -Depth 12) + [Environment]::NewLine), [Text.UTF8Encoding]::new($false))
}

function Invoke-ReadOnlyGet([string]$Path, [string]$Output) {
    if ($Path -notin @("/api/version", "/api/tags")) {
        throw "Only bounded read-only identity endpoints are permitted"
    }
    & curl.exe --silent --show-error --fail --connect-timeout 5 --max-time 20 --request GET --output $Output ($HostUrl + $Path)
    if ($LASTEXITCODE -ne 0) {
        throw "Read-only identity request failed: $Path"
    }
    if ((Get-Item -LiteralPath $Output).Length -gt $MaximumLogBytes) {
        throw "Identity response exceeded the fixed byte limit"
    }
}

function Assert-PostHocLogBound([string]$Path) {
    # Native PowerShell redirection cannot safely cap a running child stream without
    # replacing its exact argv/exit semantics or introducing a kill-polling loop.
    # The runner independently bounds all wire responses; this is a decisive
    # post-child disk guard and is intentionally not described as an active cap.
    if (Test-Path -LiteralPath $Path -PathType Leaf -and (Get-Item -LiteralPath $Path).Length -gt $MaximumLogBytes) {
        throw "Operation log exceeded the post-child byte guard: $Path"
    }
}

function Assert-IsolatedIdentity([object]$ExpectedRuntime, [string]$VersionPath, [string]$TagsPath) {
    $version = Get-CanonicalJson $VersionPath "isolated version"
    $tags = Get-CanonicalJson $TagsPath "isolated tags"
    if ($version.version -ne $ExpectedRuntime.version -or @($tags.models).Count -ne 2) {
        throw "isolated runtime identity differs"
    }
    $observed = @($tags.models | ForEach-Object { "$($_.name)|$($_.digest)" } | Sort-Object)
    $expected = @($ExpectedRuntime.models | ForEach-Object { "$($_.name)|$($_.digest)" } | Sort-Object)
    if (@(Compare-Object $observed $expected).Count -ne 0) {
        throw "isolated model inventory differs"
    }
}

function Restore-NormalOllama {
    if (-not $normalWasStopped) {
        return
    }
    Wait-ForFreeListener 20
    Start-Process -FilePath $ExpectedNormalApp -WindowStyle Hidden | Out-Null
    $deadline = [DateTime]::UtcNow.AddSeconds(60)
    $snapshot = $null
    $tree = $null
    while ([DateTime]::UtcNow -lt $deadline) {
        $snapshot = Get-OllamaProcessSnapshot
        try {
            $tree = Get-NormalTree $snapshot
            break
        }
        catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if ($null -eq $tree) {
        throw "normal Ollama did not restore the expected process chain"
    }
    Write-JsonFile $snapshot (Join-Path $OperationRoot "restored-processes.json")
    Invoke-ReadOnlyGet "/api/version" (Join-Path $OperationRoot "restored-version.response.json")
    Invoke-ReadOnlyGet "/api/tags" (Join-Path $OperationRoot "restored-tags.response.json")
    if ((Get-Sha256 (Join-Path $OperationRoot "restored-version.response.json")) -ne $baselineVersion -or (Get-Sha256 (Join-Path $OperationRoot "restored-tags.response.json")) -ne $baselineTags) {
        throw "restored normal identity bytes differ from baseline"
    }
    $script:restoreSucceeded = $true
}

$ProtocolRoot = Normalize-FullPath $ProtocolRoot
$MaterializationRoot = Normalize-FullPath $MaterializationRoot
$RuntimeIdentity = Normalize-FullPath $RuntimeIdentity
$MaterializationReceipt = Normalize-FullPath $MaterializationReceipt
$ConditionalGo = Normalize-FullPath $ConditionalGo
$EvidenceRoot = Normalize-FullPath $EvidenceRoot
$OperationRoot = Normalize-FullPath $OperationRoot

Assert-ExistingDirectory $ProtocolRoot "protocol checkout"
Assert-ExistingDirectory $MaterializationRoot "materialization root"
Assert-ExistingFile $RuntimeIdentity "runtime identity"
Assert-ExistingFile $MaterializationReceipt "materialization receipt"
Assert-ExistingFile $ConditionalGo "conditional GO"
Assert-ExternalCreateOnlyChild $EvidenceRoot "evidence root"
Assert-ExternalCreateOnlyChild $OperationRoot "operation root"
if ($EvidenceRoot -eq $OperationRoot) {
    throw "evidence and operation roots must differ"
}

$capturedRuntime = Get-CanonicalJson $RuntimeIdentity "runtime identity"
$go = Get-CanonicalJson $ConditionalGo "conditional GO"
$receipt = Get-CanonicalJson $MaterializationReceipt "materialization receipt"
$topology = Assert-MaterializationTopology
if ($MaterializationReceipt -ne (Normalize-FullPath $topology.receipt)) {
    throw "materialization receipt path differs"
}
$fullPlan = Get-CanonicalJson $topology.full "full plan"
$sourceManifest = Get-CanonicalJson $topology.source "source manifest"
$runtime = Get-CanonicalJson $topology.runtime "materialized runtime identity"

if ((Normalize-FullPath $PSCommandPath) -ne (Join-Path $ProtocolRoot "tools\run_v5_conditional_campaign.ps1")) {
    throw "wrapper must execute from the frozen protocol checkout"
}
Assert-Sha256 $PSCommandPath (Get-JsonString $fullPlan.component_sha256 "wrapper" "full plan") "wrapper"
Assert-Sha256 (Join-Path $ProtocolRoot "tools\run_v5_recovery.py") (Get-JsonString $fullPlan.component_sha256 "runner" "full plan") "runner"
Assert-Sha256 (Join-Path $ProtocolRoot "tools\analyze_v5_measurement.py") (Get-JsonString $fullPlan.component_sha256 "analyzer" "full plan") "analyzer"
if ($go.wrapper_sha256 -ne $fullPlan.component_sha256.wrapper -or $go.runner_sha256 -ne $fullPlan.component_sha256.runner -or $go.analyzer_sha256 -ne $fullPlan.component_sha256.analyzer) {
    throw "GO component bindings differ"
}
if ($go.decision -ne "GO" -or $receipt.schema_version -ne "anachron-v5-materialization-receipt-v3") {
    throw "GO or materialization receipt decision differs"
}
Assert-Sha256 $topology.full (Get-JsonString $go "full_plan_sha256" "conditional GO") "full plan"
Assert-Sha256 $topology.compatibility (Get-JsonString $go "compatibility_plan_sha256" "conditional GO") "compatibility plan"
Assert-Sha256 $topology.carry (Get-JsonString $go "carry_forward_sha256" "conditional GO") "carry-forward receipt"
Assert-Sha256 $topology.source (Get-JsonString $go "source_manifest_sha256" "conditional GO") "source manifest"
Assert-Sha256 $MaterializationReceipt (Get-JsonString $go "materialization_receipt_sha256" "conditional GO") "materialization receipt"
Assert-Sha256 $topology.runtime (Get-JsonString $receipt "runtime_identity_sha256" "materialization receipt") "materialized runtime identity"
Assert-Sha256 $RuntimeIdentity (Get-JsonString $receipt "runtime_identity_sha256" "materialization receipt") "captured runtime identity"
Assert-Sha256 $topology.schedule (Get-JsonString $receipt "schedule_sha256" "materialization receipt") "materialized schedule"
Assert-Sha256 $topology.full (Get-JsonString $receipt "full_plan_sha256" "materialization receipt") "materialized full plan"
Assert-Sha256 $topology.compatibility (Get-JsonString $receipt "compatibility_plan_sha256" "materialization receipt") "materialized compatibility plan"
Assert-Sha256 $topology.carry (Get-JsonString $receipt "carry_forward_sha256" "materialization receipt") "materialized carry-forward receipt"
Assert-Sha256 $topology.source (Get-JsonString $receipt "v5_source_manifest_sha256" "materialization receipt") "materialized source manifest"
Assert-Sha256 (Join-Path $ProtocolRoot "research\v5_measurement\authority_binding_contract.json") (Get-JsonString $go "authority_contract_sha256" "conditional GO") "authority contract"
Assert-Sha256 (Join-Path $ProtocolRoot "research\v5_measurement\ACCEPTANCE_MATRIX.md") (Get-JsonString $go "acceptance_matrix_sha256" "conditional GO") "acceptance matrix"
if ($fullPlan.expected_runtime.version -ne $runtime.version -or @(Compare-Object @($fullPlan.expected_runtime.models | ConvertTo-Json -Compress) @($runtime.models | ConvertTo-Json -Compress)).Count -ne 0) {
    throw "materialized runtime identity differs"
}
Assert-FrozenProtocol $go $sourceManifest

$runnerArguments = @("-m", "tools.run_v5_recovery", "--repository-root", $ProtocolRoot, "--full-plan", $topology.full, "--conditional-go", $ConditionalGo, "--output", $EvidenceRoot)
$analyzerArguments = @("-m", "tools.analyze_v5_measurement", $EvidenceRoot, "--repository-root", $ProtocolRoot, "--phase", "primary")
Push-Location $ProtocolRoot
try {
    & python.exe -B @runnerArguments "--preflight-only"
    if ($LASTEXITCODE -ne 0) {
        throw "fixed runner preflight rejected the frozen inputs"
    }
}
finally {
    Pop-Location
}

if ($ValidateOnly.IsPresent) {
    Write-Output "V5 wrapper validation passed without process, network, or filesystem mutation"
    return
}
if (-not $Execute.IsPresent) {
    throw "Refusing campaign execution without the explicit -Execute switch"
}

Assert-ExistingFile $ExpectedIsolatedExe "isolated executable"
Assert-Sha256 $ExpectedIsolatedExe $ExpectedIsolatedExeSha256 "isolated executable"
Assert-ExistingDirectory $ExpectedIsolatedModels "isolated model store"
Assert-SafeTree $ExpectedIsolatedModels "isolated model store"
Assert-ExistingFile $ExpectedNormalApp "normal Ollama app"
Assert-ExistingFile $ExpectedNormalServer "normal Ollama server"
Assert-ImmediateCreateOnlyChild $EvidenceRoot "evidence root"
Assert-ImmediateCreateOnlyChild $OperationRoot "operation root"

$before = Get-OllamaProcessSnapshot
$normalTree = Get-NormalTree $before
New-Item -ItemType Directory -Path $OperationRoot | Out-Null
Write-JsonFile $before (Join-Path $OperationRoot "baseline-processes.json")
Invoke-ReadOnlyGet "/api/version" (Join-Path $OperationRoot "baseline-version.response.json")
Invoke-ReadOnlyGet "/api/tags" (Join-Path $OperationRoot "baseline-tags.response.json")
$baselineVersion = Get-Sha256 (Join-Path $OperationRoot "baseline-version.response.json")
$baselineTags = Get-Sha256 (Join-Path $OperationRoot "baseline-tags.response.json")

try {
    Stop-Process -Id $normalTree.app.ProcessId
    $normalWasStopped = $true
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    while ([DateTime]::UtcNow -lt $deadline -and $null -ne (Get-Process -Id $normalTree.server.ProcessId -ErrorAction SilentlyContinue)) {
        Start-Sleep -Milliseconds 250
    }
    if ($null -ne (Get-Process -Id $normalTree.server.ProcessId -ErrorAction SilentlyContinue)) {
        Stop-Process -Id $normalTree.server.ProcessId
    }
    Wait-ForFreeListener 20

    $environmentNames = @("OLLAMA_HOST", "OLLAMA_MODELS", "OLLAMA_NO_CLOUD", "OLLAMA_NOPRUNE")
    $savedEnvironment = @{}
    foreach ($name in $environmentNames) {
        $savedEnvironment[$name] = Get-Item -Path ("Env:" + $name) -ErrorAction SilentlyContinue
    }
    try {
        $env:OLLAMA_HOST = "127.0.0.1:11434"
        $env:OLLAMA_MODELS = $ExpectedIsolatedModels
        $env:OLLAMA_NO_CLOUD = "1"
        $env:OLLAMA_NOPRUNE = "1"
        $isolatedProcess = Start-Process -FilePath $ExpectedIsolatedExe -ArgumentList "serve" -WindowStyle Hidden -RedirectStandardOutput (Join-Path $OperationRoot "isolated.stdout.log") -RedirectStandardError (Join-Path $OperationRoot "isolated.stderr.log") -PassThru
    }
    finally {
        foreach ($name in $environmentNames) {
            if ($null -eq $savedEnvironment[$name]) {
                Remove-Item -Path ("Env:" + $name) -ErrorAction SilentlyContinue
            }
            else {
                Set-Item -Path ("Env:" + $name) -Value $savedEnvironment[$name].Value
            }
        }
    }
    Wait-ForOwnedListener $isolatedProcess.Id 60
    $isolatedSnapshot = Get-OllamaProcessSnapshot
    Assert-IsolatedTree $isolatedSnapshot $isolatedProcess.Id
    Write-JsonFile $isolatedSnapshot (Join-Path $OperationRoot "isolated-processes.json")
    Invoke-ReadOnlyGet "/api/version" (Join-Path $OperationRoot "isolated-version.response.json")
    Invoke-ReadOnlyGet "/api/tags" (Join-Path $OperationRoot "isolated-tags.response.json")
    Assert-IsolatedIdentity $fullPlan.expected_runtime (Join-Path $OperationRoot "isolated-version.response.json") (Join-Path $OperationRoot "isolated-tags.response.json")
    Assert-PostHocLogBound (Join-Path $OperationRoot "isolated.stdout.log")
    Assert-PostHocLogBound (Join-Path $OperationRoot "isolated.stderr.log")

    Push-Location $ProtocolRoot
    try {
        $savedErrorAction = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            & python.exe @runnerArguments 1> (Join-Path $OperationRoot "runner.stdout.log") 2> (Join-Path $OperationRoot "runner.stderr.log")
            $runnerExitCode = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $savedErrorAction
        }
    }
    finally {
        Pop-Location
    }
    Assert-PostHocLogBound (Join-Path $OperationRoot "runner.stdout.log")
    Assert-PostHocLogBound (Join-Path $OperationRoot "runner.stderr.log")
    if ($runnerExitCode -ne 0) {
        $campaignError = "fixed runner exited with code $runnerExitCode"
    }
    else {
        Push-Location $ProtocolRoot
        try {
            $savedErrorAction = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            try {
                & python.exe @analyzerArguments 1> (Join-Path $OperationRoot "analyzer.stdout.log") 2> (Join-Path $OperationRoot "analyzer.stderr.log")
                $analyzerExitCode = $LASTEXITCODE
            }
            finally {
                $ErrorActionPreference = $savedErrorAction
            }
        }
        finally {
            Pop-Location
        }
        Assert-PostHocLogBound (Join-Path $OperationRoot "analyzer.stdout.log")
        Assert-PostHocLogBound (Join-Path $OperationRoot "analyzer.stderr.log")
        if ($analyzerExitCode -ne 0) {
            $campaignError = "fixed analyzer exited with code $analyzerExitCode"
        }
        else {
            $campaignSucceeded = $true
        }
    }
}
catch {
    $campaignError = $_.Exception.Message
}
finally {
    try {
        if ($null -ne $isolatedProcess -and $null -ne (Get-Process -Id $isolatedProcess.Id -ErrorAction SilentlyContinue)) {
            Stop-Process -Id $isolatedProcess.Id
        }
        if ($normalWasStopped) {
            Wait-ForFreeListener 20
        }
    }
    catch {
        $cleanupError = $_.Exception.Message
    }
    try {
        Restore-NormalOllama
    }
    catch {
        if ($null -eq $cleanupError) {
            $cleanupError = $_.Exception.Message
        }
    }
    $status = [pscustomobject]@{
        analyzer_exit_code = $analyzerExitCode
        analyzer_stderr_sha256 = if (Test-Path -LiteralPath (Join-Path $OperationRoot "analyzer.stderr.log")) { Get-Sha256 (Join-Path $OperationRoot "analyzer.stderr.log") } else { $null }
        analyzer_stdout_sha256 = if (Test-Path -LiteralPath (Join-Path $OperationRoot "analyzer.stdout.log")) { Get-Sha256 (Join-Path $OperationRoot "analyzer.stdout.log") } else { $null }
        campaign_error = $campaignError
        campaign_succeeded = $campaignSucceeded
        completed_at_utc = [DateTime]::UtcNow.ToString("o")
        conditional_go_sha256 = Get-Sha256 $ConditionalGo
        evidence_root_exists = Test-Path -LiteralPath $EvidenceRoot
        isolated_pid = if ($null -eq $isolatedProcess) { $null } else { $isolatedProcess.Id }
        materialization_receipt_sha256 = Get-Sha256 $MaterializationReceipt
        operation_script_sha256 = Get-Sha256 $PSCommandPath
        restore_error = $cleanupError
        restore_succeeded = $restoreSucceeded
        runner_exit_code = $runnerExitCode
        runner_stderr_sha256 = if (Test-Path -LiteralPath (Join-Path $OperationRoot "runner.stderr.log")) { Get-Sha256 (Join-Path $OperationRoot "runner.stderr.log") } else { $null }
        runner_stdout_sha256 = if (Test-Path -LiteralPath (Join-Path $OperationRoot "runner.stdout.log")) { Get-Sha256 (Join-Path $OperationRoot "runner.stdout.log") } else { $null }
    }
    Write-JsonFile $status (Join-Path $OperationRoot "operation-status.json")
}

if ($null -ne $cleanupError -or -not $restoreSucceeded) {
    throw "normal Ollama restoration verification failed: $cleanupError"
}
if (-not $campaignSucceeded) {
    throw "v5 campaign did not complete: $campaignError"
}
