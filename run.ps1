# Start the Migration Agent on Windows: creates the virtual environment, installs dependencies, starts the app.
#   powershell -ExecutionPolicy Bypass -File run.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path .venv)) {
    Write-Host "Creating virtual environment..." -ForegroundColor Cyan
    python -m venv .venv
    & .\.venv\Scripts\python.exe -m pip install --upgrade pip -q
    & .\.venv\Scripts\python.exe -m pip install -r requirements.txt
}
if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host "Created .env - add a free Groq key for AI suggestions (the app also runs without one)." -ForegroundColor Yellow
}
Write-Host "`nMigration Agent is starting on http://localhost:8000  (Ctrl+C to stop)`n" -ForegroundColor Green
& .\.venv\Scripts\python.exe -m uvicorn backend.main:app --port 8000
