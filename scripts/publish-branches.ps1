[CmdletBinding()]
param(
    [string]$Remote = "origin",
    [int]$Retries = 3
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

$repoHttpProxy = git config --local --get http.proxy
$repoHttpsProxy = git config --local --get https.proxy
if ($repoHttpProxy -or $repoHttpsProxy) {
    Write-Warning "Repository-local Git proxy is configured. Verify that it is reachable before publishing."
}

for ($attempt = 1; $attempt -le $Retries; $attempt++) {
    Write-Host "Publishing all branches (attempt $attempt/$Retries)..."
    git push --all $Remote
    if ($LASTEXITCODE -eq 0) {
        break
    }

    if ($attempt -eq $Retries) {
        throw "Failed to publish branches after $Retries attempts."
    }

    Start-Sleep -Seconds ([Math]::Min(2 * $attempt, 5))
}

$localBranches = @(git for-each-ref --format="%(refname:short)" refs/heads/)
$remoteBranches = @(git ls-remote --heads $Remote | ForEach-Object {
    ($_ -split "\s+")[1] -replace "^refs/heads/", ""
})

$missingBranches = @($localBranches | Where-Object { $_ -notin $remoteBranches })
if ($missingBranches.Count -gt 0) {
    throw "Remote verification failed. Missing branches: $($missingBranches -join ', ')"
}

Write-Host "Published and verified $($localBranches.Count) branches: $($localBranches -join ', ')"
