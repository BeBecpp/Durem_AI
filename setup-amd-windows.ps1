$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
Write-Host "DUREM AMD / Radeon setup" -ForegroundColor Cyan
if (-not (Get-Command lemonade -ErrorAction SilentlyContinue)) { throw "Lemonade CLI олдсонгүй." }

Write-Host "Vulkan backend шалгаж/суулгаж байна..."
lemonade backends install llamacpp:vulkan
Write-Host "Qwen3 8B татаж байна..."
lemonade pull Qwen3-8B-GGUF
Write-Host "Qwen3 8B-г Vulkan тохиргоотой load хийж хадгалж байна..."
lemonade load Qwen3-8B-GGUF --llamacpp vulkan --save-options
Write-Host "Embedding model татаж байна..."
lemonade pull Qwen3-Embedding-0.6B-GGUF
Write-Host "Embedding model бэлэн." -ForegroundColor Green
