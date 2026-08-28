<#
  다이제스트를 지금 보내라고 깃허브에 알린다.

  깃허브 예약 실행(크론)은 시각을 지키지 못한다. 부하가 크면 버리기
  때문에, 08:00 회차가 16:09 에 오거나 아예 빠지는 날이 있었다. 늘 켜져
  있는 PC 가 제시각에 깨워 주는 쪽이 훨씬 정확하다.

  보낼 회차 판단은 깃허브 쪽에서 한다. 이 스크립트는 '지금 확인해 봐'
  라고만 하므로, 두 번 돌아도 같은 날 두 번 발송되지 않는다.

  토큰은 저장소에 두지 않는다. 아래 파일에서 읽는다.
    %USERPROFILE%\.lookbook\token.txt
#>

$ErrorActionPreference = "Stop"

$Repo     = "studioditto-ctrl/lookbook"
$HomeDir  = Join-Path $env:USERPROFILE ".lookbook"
$TokenPath= Join-Path $HomeDir "token.txt"
$LogPath  = Join-Path $HomeDir "wake.log"

function Write-Log([string]$Message){
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Write-Host $line
    try{
        if (-not (Test-Path $HomeDir)){ New-Item -ItemType Directory -Path $HomeDir | Out-Null }
        Add-Content -Path $LogPath -Value $line -Encoding UTF8
        # 로그가 한없이 자라지 않게 뒤쪽만 남긴다
        $lines = @(Get-Content -Path $LogPath -Encoding UTF8)
        if ($lines.Count -gt 500){
            Set-Content -Path $LogPath -Value $lines[-300..-1] -Encoding UTF8
        }
    }catch{ }
}

if (-not (Test-Path $TokenPath)){
    Write-Log "토큰 파일이 없습니다: $TokenPath"
    Write-Log "install.ps1 을 먼저 실행하세요."
    exit 1
}

# 빈 파일이면 Get-Content -Raw 가 $null 을 준다. [string] 로 캐스팅해도
# $null 그대로라서, 바로 .Trim() 을 걸면 안내 대신 PowerShell 예외가
# 튀어나온다. 캐스팅에 기대지 말고 먼저 있는지 본다.
$token = Get-Content -Path $TokenPath -Raw -ErrorAction SilentlyContinue
if ($null -eq $token){ Write-Log "토큰 파일이 비어 있습니다: $TokenPath"; exit 1 }
$token = $token.Trim()
if (-not $token){ Write-Log "토큰 파일이 비어 있습니다: $TokenPath"; exit 1 }

# TLS 1.2 미만으로 붙는 구형 설정이 남아 있으면 깃허브가 끊는다
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

try{
    $response = Invoke-WebRequest -Method Post `
        -Uri "https://api.github.com/repos/$Repo/dispatches" `
        -Headers @{
            Authorization = "Bearer $token"
            Accept        = "application/vnd.github+json"
            "User-Agent"  = "lookbook-waker"
        } `
        -ContentType "application/json" `
        -Body '{"event_type":"digest"}' `
        -UseBasicParsing -TimeoutSec 30
    Write-Log ("깨웠습니다 (HTTP {0})" -f [int]$response.StatusCode)
    exit 0
}catch{
    # switch 안에서는 $_ 가 에러 레코드가 아니라 switch 의 대상값으로 바뀐다.
    # 먼저 붙잡아 두지 않으면 아래 메시지가 빈 채로 찍힌다.
    $err    = $_
    $detail = $err.Exception.Message
    $status = 0
    if ($err.Exception.Response){ $status = [int]$err.Exception.Response.StatusCode }
    switch ($status){
        401 { Write-Log "401 — 토큰이 틀렸거나 만료됐습니다. token.txt 를 새 토큰으로 바꾸세요." }
        403 { Write-Log "403 — 토큰에 Contents: Read and write 권한이 없습니다." }
        404 { Write-Log "404 — 토큰의 Repository access 목록에 $Repo 가 없습니다." }
        0   { Write-Log ("네트워크 오류: {0}" -f $detail) }
        default { Write-Log ("실패 (HTTP {0}): {1}" -f $status, $detail) }
    }
    exit 1
}
