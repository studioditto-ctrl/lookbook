<#
  인스타 장소 아카이브 봇을 이 PC 에 설치한다.

  하는 일
    1. 파이썬 가상환경 (%USERPROFILE%\.lookbook\places-venv) 과 패키지 설치
    2. 비밀값 입력 → %USERPROFILE%\.lookbook\places.env (이 계정만 읽기)
    3. 옵시디언 vault 경로 → %USERPROFILE%\.lookbook\places.yaml
    4. 구글 로그인 (창이 뜨면 직접 로그인)
    5. 로그인할 때 봇이 자동 실행되도록 시작프로그램에 바로가기 등록 (관리자 권한 불필요)

  사용법 (레포 전체를 PC 에 받은 뒤):
    설치하기.bat 더블클릭, 또는 PowerShell 에서 .\install.ps1
    .\install.ps1 -Uninstall    자동 실행 해제·봇 종료 (기록·설정은 남긴다)
#>

param([switch]$Uninstall)

$ErrorActionPreference = "Stop"
$Shortcut = Join-Path ([Environment]::GetFolderPath("Startup")) "Lookbook 장소봇.lnk"
$Repo     = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$HomeDir  = Join-Path $env:USERPROFILE ".lookbook"
$Venv     = Join-Path $HomeDir "places-venv"
$VPy      = Join-Path $Venv "Scripts\python.exe"
$EnvPath  = Join-Path $HomeDir "places.env"
$YamlPath = Join-Path $HomeDir "places.yaml"
$RunScript = Join-Path $PSScriptRoot "run.ps1"

function Stop-Bot {
    Get-CimInstance Win32_Process |
        Where-Object { $_.CommandLine -and ($_.CommandLine -like "*places\run.ps1*" -or $_.CommandLine -like "*-m places run*") } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

if ($Uninstall){
    Stop-Bot
    Remove-Item $Shortcut -ErrorAction SilentlyContinue
    Write-Host "봇을 끄고 자동 실행을 해제했습니다. 기록과 설정은 $HomeDir 에 남아 있습니다."
    exit 0
}

if (-not (Test-Path (Join-Path $Repo "places\__main__.py"))){
    Write-Host "레포 전체가 필요합니다. places 폴더를 찾지 못했습니다: $Repo" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $HomeDir)){ New-Item -ItemType Directory -Path $HomeDir | Out-Null }

# --- 1. 파이썬 ---
$py = $null
foreach ($cand in @(@("py", "-3"), @("python"))){
    if (-not (Get-Command $cand[0] -ErrorAction SilentlyContinue)){ continue }
    $pre = @($cand | Select-Object -Skip 1)
    try {
        $ver = & $cand[0] @pre -c "import sys;print(sys.version_info >= (3,10))" 2>$null
        if ($ver -eq "True"){ $py = $cand; break }
    } catch {}
}
if (-not $py){
    Write-Host "파이썬 3.10 이상이 필요합니다. https://www.python.org/downloads/ 에서 설치하세요." -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $VPy)){
    Write-Host "가상환경을 만듭니다..."
    $pre = @($py | Select-Object -Skip 1)
    & $py[0] @pre -m venv $Venv
}
Write-Host "패키지를 설치합니다..."
& $VPy -m pip install --quiet --upgrade pip
& $VPy -m pip install --quiet -r (Join-Path $Repo "places\requirements.txt")

$chrome = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
$channel = "chrome"
if (-not $chrome){
    Write-Host "크롬이 없어 Playwright 크로미움을 받습니다..."
    & $VPy -m playwright install chromium
    $channel = ""
}

# --- 2. 비밀값 ---
function Read-Secret($label){
    $s = Read-Host $label -AsSecureString
    return [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)).Trim()
}
if (-not (Test-Path $EnvPath)){
    Write-Host ""
    Write-Host "비밀값을 입력합니다. 화면에 표시되지 않고 이 PC 에만 저장됩니다."
    $tg  = Read-Secret "텔레그램 봇 토큰 (BotFather 에서 받은 값)"
    $ant = Read-Secret "Claude API 키 (sk-ant-...)"
    $kko = Read-Secret "카카오 REST API 키 (선택 — 없으면 그냥 Enter)"
    @(
        "TELEGRAM_BOT_TOKEN=$tg",
        "TELEGRAM_CHAT_ID=",
        "ANTHROPIC_API_KEY=$ant",
        "KAKAO_REST_API_KEY=$kko"
    ) | Set-Content -Path $EnvPath -Encoding UTF8
    icacls $EnvPath /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null
    Write-Host "저장했습니다: $EnvPath"
} else {
    Write-Host "이미 있는 비밀값 파일을 씁니다: $EnvPath"
    Write-Host "(카카오 키를 나중에 넣으려면 그 파일의 KAKAO_REST_API_KEY= 뒤에 적고 이 스크립트를 다시 실행)"
}

# --- 3. vault 경로 ---
if (-not (Test-Path $YamlPath)){
    Write-Host ""
    Write-Host "옵시디언 vault 폴더 경로를 입력하세요."
    Write-Host "구글 드라이브 앱 기준 예: G:\내 드라이브\Obsidian\MyVault"
    do {
        $vault = (Read-Host "vault 경로").Trim('"', ' ')
        $okVault = $vault -and (Test-Path $vault -PathType Container)
        if (-not $okVault){ Write-Host "폴더를 찾을 수 없습니다. 다시 입력하세요." -ForegroundColor Yellow }
    } until ($okVault)
    $vaultYaml = $vault.Replace("'", "''")
    @(
        "obsidian:",
        "  vault_path: '$vaultYaml'",
        "gmaps:",
        "  browser_channel: '$channel'"
    ) | Set-Content -Path $YamlPath -Encoding UTF8
    Write-Host "저장했습니다: $YamlPath"
}

# --- 4. 구글 로그인 ---
Push-Location $Repo
try {
    if (-not (Test-Path (Join-Path $HomeDir "gmaps-profile"))){
        Write-Host ""
        Write-Host "구글 지도 저장용 로그인 창을 엽니다. 직접 로그인하세요 (비밀번호는 저장하지 않습니다)."
        Stop-Bot  # 같은 브라우저 프로필을 봇이 쓰고 있으면 열리지 않는다
        & $VPy -m places login
    }
    & $VPy -m places check
} finally { Pop-Location }

# --- 5. 자동 실행 ---
$args5 = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$RunScript`""
$wsh = New-Object -ComObject WScript.Shell
$lnk = $wsh.CreateShortcut($Shortcut)
$lnk.TargetPath = "powershell.exe"
$lnk.Arguments = $args5
$lnk.WorkingDirectory = $Repo
$lnk.WindowStyle = 7
$lnk.Save()
Stop-Bot
Start-Process powershell.exe -ArgumentList $args5 -WindowStyle Hidden

Write-Host ""
Write-Host "설치 끝. 봇을 시작했습니다. 기록: $HomeDir\places.log"
Write-Host "텔레그램에서 봇에게 아무 말이나 보내면 채팅방 ID 를 알려줍니다."
Write-Host "그 숫자를 $EnvPath 의 TELEGRAM_CHAT_ID= 뒤에 적고 설치하기.bat 을 한 번 더 실행하세요."
Write-Host "(이미 입력한 값은 다시 묻지 않고 봇만 재시작합니다.)"
