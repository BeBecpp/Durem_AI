$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

Write-Host ""
Write-Host "DUREM AI 2.2 Diagnostics" -ForegroundColor Cyan
Write-Host "========================" -ForegroundColor DarkGray

function Result([string]$Name, [bool]$Ok, [string]$Detail) {
  $mark = if ($Ok) { "OK" } else { "CHECK" }
  $color = if ($Ok) { "Green" } else { "Yellow" }
  Write-Host ("[{0}] {1} - {2}" -f $mark, $Name, $Detail) -ForegroundColor $color
}

$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if ($pythonCmd) {
  $pv = (& python -c "import sys; print('.'.join(map(str,sys.version_info[:3])))" 2>$null).Trim()
  & python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" *> $null
  $pok = ($LASTEXITCODE -eq 0)
  Result "Python" $pok $pv
} else { Result "Python" $false "not found" }

$py = if (Test-Path ".venv\Scripts\python.exe") { ".\.venv\Scripts\python.exe" } else { "python" }
if ((Get-Command $py -ErrorAction SilentlyContinue) -or (Test-Path $py)) {
  try {
    $json = & $py -c "import json; from app.config import settings; print(json.dumps({'host':settings.host,'port':settings.port,'company':settings.company_name,'model':settings.llm_model,'data':str(settings.data_dir),'secret_strong':settings.secret_is_strong,'ai_local':settings.llm_endpoint_is_local,'allowed_hosts':settings.allowed_hosts}, ensure_ascii=False))"
    $cfg = $json | ConvertFrom-Json
    Result "Config" $true ("{0} @ {1}:{2}" -f $cfg.company,$cfg.host,$cfg.port)
    Result "Secret" ([bool]$cfg.secret_strong) "strong random application secret"
    Result "AI boundary" ([bool]$cfg.ai_local) "local/private endpoint"
    Result "Trusted hosts" (-not ($cfg.allowed_hosts -contains "*")) (($cfg.allowed_hosts) -join ", ")
    Result "Data directory" (Test-Path $cfg.data) $cfg.data
  } catch { Result "DUREM config" $false $_.Exception.Message }
}

if (Get-Command lemonade -ErrorAction SilentlyContinue) {
  Result "Lemonade CLI" $true "detected"
} else { Result "Lemonade CLI" $false "not on PATH" }

try {
  $models = Invoke-RestMethod -Uri "http://127.0.0.1:13305/v1/models" -TimeoutSec 4
  $names = @($models.data | ForEach-Object { $_.id }) -join ", "
  Result "Lemonade API" $true $(if ($names) { $names } else { "reachable" })
} catch { Result "Lemonade API" $false "http://127.0.0.1:13305 not reachable" }

try {
  $resp = Invoke-WebRequest -Uri "http://127.0.0.1:8080/login" -UseBasicParsing -TimeoutSec 4
  Result "DUREM web" ($resp.StatusCode -eq 200) "http://127.0.0.1:8080"
} catch { Result "DUREM web" $false "start.ps1 ажиллуулаагүй байж болно" }

Write-Host ""
Write-Host "Security Center: Admin -> Security" -ForegroundColor DarkGray
