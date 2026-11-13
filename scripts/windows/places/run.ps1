<#
  봇을 계속 띄워 둔다. 죽으면 30초 뒤 다시 실행. 작업 스케줄러가 로그인 때 부른다.
#>

$Repo    = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$HomeDir = Join-Path $env:USERPROFILE ".lookbook"
$VPy     = Join-Path $HomeDir "places-venv\Scripts\python.exe"
$Log     = Join-Path $HomeDir "places.log"
$env:PYTHONIOENCODING = "utf-8"

Set-Location $Repo
# 이전 실행의 로그는 따로 둔다 (예전 버전이 남긴 깨진 인코딩 로그 포함)
if (Test-Path $Log){ Move-Item $Log "$Log.old" -Force }
while ($true){
    if ((Test-Path $Log) -and (Get-Item $Log).Length -gt 5MB){
        Move-Item $Log "$Log.1" -Force
    }
    # 파이썬이 로그를 UTF-8 로 직접 쓴다 (PowerShell 리디렉션은 인코딩이 섞여 깨진다)
    [IO.File]::AppendAllText($Log, "=== $(Get-Date -Format s) 시작`r`n", [Text.UTF8Encoding]::new($false))
    & $VPy -u -m places run --log $Log
    [IO.File]::AppendAllText($Log, "=== $(Get-Date -Format s) 종료 (코드 $LASTEXITCODE), 30초 뒤 재시작`r`n", [Text.UTF8Encoding]::new($false))
    Start-Sleep -Seconds 30
}
