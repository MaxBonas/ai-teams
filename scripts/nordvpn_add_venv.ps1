[CmdletBinding()]
param(
    [string]$SettingsFile = "",
    [switch]$Apply
)

# Alias conservado para instalaciones anteriores. Delega en la utilidad
# portable, que hace dry-run salvo que el operador indique -Apply.
$Arguments = @{}
if ($SettingsFile) {
    $Arguments.SettingsFile = $SettingsFile
}
if ($Apply) {
    $Arguments.Apply = $true
}

& (Join-Path $PSScriptRoot "nordvpn_split_tunnel.ps1") @Arguments
exit $LASTEXITCODE
