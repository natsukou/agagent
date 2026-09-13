[CmdletBinding()]
param(
    [string]$Remote = "origin",
    [int]$Retries = 3,
    [string]$Proxy
)

$ErrorActionPreference = "Stop"

if (-not (git rev-parse --is-inside-work-tree 2>$null)) {
    throw "The current directory is not a Git repository."
}

$remoteUrl = git remote get-url $Remote 2>$null
if (-not $remoteUrl) {
    throw "Git remote '$Remote' is not configured."
}

Write-Host "Remote: $Remote ($remoteUrl)"

if (-not $Proxy -and $env:OS -eq "Windows_NT") {
    $internetSettings = Get-ItemProperty `
        "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings" `
        -ErrorAction SilentlyContinue

    if ($internetSettings.ProxyEnable -eq 1 -and $internetSettings.ProxyServer) {
        $proxyEntries = @{}
        foreach ($entry in ($internetSettings.ProxyServer -split ";")) {
            if ($entry -match "^(?<scheme>[^=]+)=(?<address>.+)$") {
                $proxyEntries[$Matches.scheme] = $Matches.address
            }
        }

        if ($proxyEntries.Count -gt 0) {
            $Proxy = $proxyEntries.https
            if (-not $Proxy) { $Proxy = $proxyEntries.http }
        } else {
            $Proxy = $internetSettings.ProxyServer
        }
    }
}

$gitNetworkArgs = @()
if ($Proxy) {
    if ($Proxy -notmatch "^[a-z]+://") {
        $Proxy = "http://$Proxy"
    }
    Write-Host "Using proxy discovered from Windows settings: $Proxy"
    $gitNetworkArgs = @(
        "-c", "http.proxy=$Proxy",
        "-c", "https.proxy=$Proxy"
    )
}

for ($attempt = 1; $attempt -le $Retries; $attempt++) {
    Write-Host "Publishing all branches (attempt $attempt/$Retries)..."
    & git @gitNetworkArgs push --all $Remote
    if ($LASTEXITCODE -eq 0) {
        break
    }

    if ($attempt -eq $Retries) {
        throw "Failed to publish branches after $Retries attempts."
    }

    Start-Sleep -Seconds ([Math]::Min(2 * $attempt, 5))
}

$localBranches = @(git for-each-ref --format="%(refname:short)" refs/heads/)
$remoteBranches = @(& git @gitNetworkArgs ls-remote --heads $Remote | ForEach-Object {
    ($_ -split "\s+")[1] -replace "^refs/heads/", ""
})

$missingBranches = @($localBranches | Where-Object { $_ -notin $remoteBranches })
if ($missingBranches.Count -gt 0) {
    throw "Remote verification failed. Missing branches: $($missingBranches -join ', ')"
}

Write-Host "Published and verified $($localBranches.Count) branches: $($localBranches -join ', ')"
