<#
  봇을 계속 띄워 둔다. 죽으면 30초 뒤 다시 실행. 작업 스케줄러가 로그인 때 부른다.
#>

$Repo    = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$HomeDir = Join-Path $env:USERPROFILE ".lookbook"
$VPy     = Join-Path $HomeDir "places-venv\Scripts\python.exe"
$Log     = Join-Path $HomeDir "places.log"
$env:PYTHONIOENCODING = "utf-8"

Set-Location $Repo
while ($true){
    if ((Test-Path $Log) -and (Get-Item $Log).Length -gt 5MB){
        Move-Item $Log "$Log.1" -Force
    }
    Add-Content -Path $Log -Value "=== $(Get-Date -Format s) 시작" -Encoding UTF8
    & $VPy -u -m places run *>> $Log
    Add-Content -Path $Log -Value "=== $(Get-Date -Format s) 종료 (코드 $LASTEXITCODE), 30초 뒤 재시작" -Encoding UTF8
    Start-Sleep -Seconds 30
}
