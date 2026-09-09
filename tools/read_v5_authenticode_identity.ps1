[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$LiteralPath
)

$securityModule = Join-Path $PSHOME "Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1"
$null = Import-Module -Name $securityModule -ErrorAction Stop
$signature = Get-AuthenticodeSignature -LiteralPath $LiteralPath -ErrorAction Stop
if ($signature.Status -ne "Valid" -or $null -eq $signature.SignerCertificate) {
    exit 7
}

[ordered]@{
    subject = $signature.SignerCertificate.Subject
    thumbprint = $signature.SignerCertificate.Thumbprint
} | ConvertTo-Json -Compress
