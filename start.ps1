$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path ".venv")) { & .\setup.ps1 }
if (-not (Test-Path ".env")) { & .\setup.ps1 }
$python = ".\.venv\Scripts\python.exe"
$hostValue = (& $python -c "from app.config import settings; print(settings.host)").Trim()
$portValue = (& $python -c "from app.config import settings; print(settings.port)").Trim()
$openHost = if ($hostValue -eq "0.0.0.0") { "127.0.0.1" } else { $hostValue }
$url = "http://${openHost}:${portValue}"
$modelValue = (& $python -c "from app.config import settings; print(settings.llm_model)").Trim()
if (Get-Command lemonade -ErrorAction SilentlyContinue) {
  try {
    Write-Host "Local AI model шалгаж байна: $modelValue" -ForegroundColor DarkGray
    lemonade load $modelValue | Out-Host
  } catch {
    Write-Host "Lemonade model автоматаар load хийж чадсангүй. Lemonade app ажиллаж байгаа эсэхийг шалгана уу." -ForegroundColor Yellow
  }
}
Write-Host "DUREM AI 2.2 -> $url" -ForegroundColor Cyan
Start-Job -ScriptBlock { param($u) Start-Sleep -Seconds 2; Start-Process $u } -ArgumentList $url | Out-Null
& $python -m uvicorn app.main:app --host $hostValue --port $portValue --no-server-header
