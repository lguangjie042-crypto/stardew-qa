$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$py = Join-Path $root ".venv\Scripts\python.exe"
$cpolar = Join-Path $root "tools\cpolar-win\cpolar.exe"
$envFile = Join-Path $root ".env"

if (-not (Test-Path $cpolar)) {
    New-Item -ItemType Directory -Force -Path (Join-Path $root "tools") | Out-Null
    $zip = Join-Path $root "tools\cpolar.zip"
    Invoke-WebRequest -Uri "https://www.cpolar.com/static/downloads/cpolar-stable-windows-amd64.zip" -OutFile $zip
    Expand-Archive -Path $zip -DestinationPath (Join-Path $root "tools\cpolar-win") -Force
}

$token = ""
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^CPOLAR_AUTHTOKEN=(.+)$') {
            $token = $Matches[1].Trim()
        }
    }
}

$busy = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if (-not $busy) {
    Start-Process -FilePath $py -ArgumentList "-m","uvicorn","app.main:app","--host","127.0.0.1","--port","8000" -WorkingDirectory $root -WindowStyle Hidden
    Start-Sleep -Seconds 2
}

if (-not $token) {
    Write-Host "本机网站在 http://127.0.0.1:8000"
    Write-Host "国内公网还差 authtoken：打开 https://www.cpolar.com/ 注册免费账号，在后台复制 authtoken。"
    Write-Host "写到 .env 里这一行：CPOLAR_AUTHTOKEN=你的token"
    Write-Host "然后重新运行 .\scripts\share.ps1"
    exit 1
}

Write-Host "本机仍是 http://127.0.0.1:8000"
Write-Host "窗口里出现的 cpolar 地址可以发给国内各地。关掉这个窗口后，外网就打不开了。"
& $cpolar authtoken $token | Out-Null
& $cpolar http 8000 -region cn -log stdout -log-level INFO
