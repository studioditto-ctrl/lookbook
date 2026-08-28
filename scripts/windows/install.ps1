<#
  발송 시각마다 깨우는 작업을 윈도우 작업 스케줄러에 등록한다.

  관리자 권한은 필요 없다. 로그인한 사용자로만 돌면 되고, 늘 켜둔 PC
  라면 그것으로 충분하다.

  사용법 (PowerShell 창에서):
    .\install.ps1                      토큰을 물어보고 08:00, 18:00 로 등록
    .\install.ps1 -Times 08:00,18:00   시각을 직접 지정
    .\install.ps1 -Uninstall           등록한 작업을 지운다
#>

param(
    [string[]]$Times = @("08:00", "18:00"),
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

$TaskPrefix = "Lookbook 다이제스트"
$HomeDir    = Join-Path $env:USERPROFILE ".lookbook"
$TokenPath  = Join-Path $HomeDir "token.txt"
$WakeScript = Join-Path $PSScriptRoot "wake.ps1"

function Remove-Existing {
    Get-ScheduledTask | Where-Object { $_.TaskName -like "$TaskPrefix*" } | ForEach-Object {
        Unregister-ScheduledTask -TaskName $_.TaskName -Confirm:$false
        Write-Host ("지웠습니다: {0}" -f $_.TaskName)
    }
}

if ($Uninstall){ Remove-Existing; Write-Host "끝났습니다."; exit 0 }

if (-not (Test-Path $WakeScript)){
    Write-Host "wake.ps1 을 찾을 수 없습니다: $WakeScript" -ForegroundColor Red
    exit 1
}

# --- 토큰 ---
if (-not (Test-Path $HomeDir)){ New-Item -ItemType Directory -Path $HomeDir | Out-Null }
if (-not (Test-Path $TokenPath)){
    Write-Host ""
    Write-Host "깃허브 토큰이 필요합니다 (Contents: Read and write)."
    Write-Host "https://github.com/settings/personal-access-tokens 에서 만드세요."
    Write-Host ""
    $secure = Read-Host "토큰을 붙여넣으세요" -AsSecureString
    $plain  = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
                [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
    if (-not $plain.Trim()){ Write-Host "빈 값입니다. 그만둡니다." -ForegroundColor Red; exit 1 }
    Set-Content -Path $TokenPath -Value $plain.Trim() -Encoding ASCII -NoNewline
    # 이 계정만 읽게 잠근다
    icacls $TokenPath /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null
    Write-Host "토큰을 저장했습니다: $TokenPath"
}else{
    Write-Host "이미 있는 토큰을 씁니다: $TokenPath"
}

# --- 작업 등록 ---
Remove-Existing

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $WakeScript)

# 자거나 꺼져 있어서 놓친 회차는 깨어난 뒤 곧바로 따라잡는다
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -WakeToRun `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

foreach ($t in $Times){
    if ($t -notmatch '^\d{1,2}:\d{2}$'){
        Write-Host ("시각을 읽을 수 없습니다: {0} — 건너뜁니다" -f $t) -ForegroundColor Yellow
        continue
    }
    $name    = "$TaskPrefix $t"
    $trigger = New-ScheduledTaskTrigger -Daily -At $t
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger `
        -Settings $settings -Description "발송 시각에 깃허브를 깨웁니다 ($t)" | Out-Null
    Write-Host ("등록했습니다: {0}" -f $name)
}

Write-Host ""
Write-Host "지금 한 번 시험해 봅니다…"
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $WakeScript
Write-Host ""
Write-Host "기록은 여기에 쌓입니다: $(Join-Path $HomeDir 'wake.log')"
Write-Host "작업을 보려면 taskschd.msc 를 열고 '$TaskPrefix' 을 찾으세요."
