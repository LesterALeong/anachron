[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateRange(1, [int]::MaxValue)]
    [int]$ProcessId
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
Import-Module (Join-Path $PSHOME "Modules\CimCmdlets\CimCmdlets.psd1") -Force
Import-Module (Join-Path $PSHOME "Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1") -Force

$processes = @(
    Get-CimInstance -Namespace "root/cimv2" -ClassName "Win32_Process" -Filter ("ProcessId = " + $ProcessId)
)
if ($processes.Count -ne 1 -or [int]$processes[0].ProcessId -ne $ProcessId) {
    throw "CIM process identity is unavailable"
}

$process = $processes[0]
$name = [string]$process.Name
if ([string]::IsNullOrWhiteSpace($name)) {
    throw "CIM process name is unavailable"
}
[ordered]@{
    pid = [int]$process.ProcessId
    ppid = [int]$process.ParentProcessId
    name = $name
} | ConvertTo-Json -Compress
