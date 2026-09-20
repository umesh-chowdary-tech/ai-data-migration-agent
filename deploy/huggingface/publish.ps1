# Publish the committed state of this repo to a Hugging Face Space (Docker SDK).
#
#   powershell -ExecutionPolicy Bypass -File deploy\huggingface\publish.ps1 -Space your-username/migration-agent
#
# Git will ask for your Hugging Face username and an access token with WRITE permission
# (huggingface.co -> Settings -> Access Tokens). Use the token as the password.
param(
    [Parameter(Mandatory = $true)][string]$Space,
    [string]$Message = "Deploy migration agent"
)
$ErrorActionPreference = "Stop"
$repo = (Resolve-Path "$PSScriptRoot\..\..").Path
Set-Location $repo

if ((git status --porcelain) -ne $null) {
    Write-Host "Uncommitted changes - only committed files are published." -ForegroundColor Yellow
}

$work = Join-Path ([System.IO.Path]::GetTempPath()) "hf-space-$(Get-Random)"
Write-Host "Cloning https://huggingface.co/spaces/$Space ..." -ForegroundColor Cyan
git clone "https://huggingface.co/spaces/$Space" $work

# Replace the Space's contents with this repo's committed files
Get-ChildItem $work -Force | Where-Object { $_.Name -ne ".git" } | Remove-Item -Recurse -Force
$zip = Join-Path $work "_src.zip"
git archive HEAD --format=zip -o $zip
Expand-Archive -Path $zip -DestinationPath $work -Force
Remove-Item $zip

# The Space needs its own Dockerfile (port 7860, non-root) and README (the Space card)
Copy-Item "$repo\deploy\huggingface\Dockerfile" "$work\Dockerfile" -Force
Copy-Item "$repo\deploy\huggingface\README.md" "$work\README.md" -Force

git -C $work add -A
git -C $work commit -q -m $Message
Write-Host "Pushing (username + write token when asked)..." -ForegroundColor Cyan
git -C $work push
Write-Host "`nDone. The Space builds in a few minutes: https://huggingface.co/spaces/$Space" -ForegroundColor Green
Write-Host "Add GROQ_API_KEY and OPENROUTER_API_KEY under Settings -> Variables and secrets." -ForegroundColor Yellow
