# 제시각에 보내기 (Windows)

깃허브 예약 실행(크론)은 시각을 지키지 못한다. 부하가 크면 버리기 때문에
08:00 회차가 16:09 에 오거나 아예 빠지는 날이 있었다. 늘 켜둔 PC 가 제시각에
깨워 주는 쪽이 정확하다.

## 한 번만 하면 된다

1. 이 폴더(`scripts/windows`)를 PC 로 내려받는다
2. **`설정하기.bat` 을 더블클릭한다**

토큰을 물어보면 붙여넣는다. 08:00 과 18:00 로 작업이 등록되고, 그 자리에서
한 번 시험 발송까지 해 본다.

`설정하기.bat` 은 실행 정책 인자를 박아 두고 `install.ps1` 을 부르기만 한다.
손으로 명령을 붙여넣다 `powershell -` 가 떨어져 나가면
`Get-ExecutionPolicy : 'Scope' 매개 변수를 바인딩할 수 없습니다` 가 나는데,
그 사고를 없애려고 둔 것이다.

PowerShell 창에서 직접 하려면 세 줄이다. `-Scope Process` 는 그 창에서만
적용돼 관리자 권한이 필요 없고, `Unblock-File` 은 인터넷에서 받은 파일에
붙는 차단 표시를 뗀다 (이게 없으면 정책을 풀어도 막힌다).

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
Unblock-File .\wake.ps1, .\install.ps1
.\install.ps1
```

시각이 다르면:

```powershell
.\install.ps1 -Times 07:30,19:00
```

지우려면:

```powershell
.\install.ps1 -Uninstall
```

## 토큰

깃허브 → Settings → Developer settings → Personal access tokens →
**Fine-grained tokens** 에서 만든다.

- Repository access: **Only select repositories** → `studioditto-ctrl/lookbook`
- Repository permissions → **Contents: Read and write**

`%USERPROFILE%\.lookbook\token.txt` 에 저장되고 그 계정만 읽도록 잠근다.
저장소에는 올라가지 않는다.

## 잘 되는지 보기

- 기록: `%USERPROFILE%\.lookbook\wake.log`
- 작업: `taskschd.msc` → *Lookbook 다이제스트 08:00* / *18:00*
- 깃허브: Actions 탭에 `repository_dispatch` 로 실행이 뜬다

`wake.log` 에 `깨웠습니다 (HTTP 204)` 가 찍히면 성공이다.

## 알아둘 것

- 관리자 권한은 필요 없다. 로그인한 상태로 켜져 있으면 된다.
- 자거나 꺼져 있어 놓친 회차는 깨어난 뒤 곧바로 따라잡는다
  (`StartWhenAvailable`, `WakeToRun`).
- 두 번 깨워도 같은 날 두 번 발송되지 않는다. 보낼 회차 판단은 깃허브
  쪽 발송 이력이 한다.
- 깃허브 크론은 그대로 둔다. PC 가 꺼져 있는 날의 보험이다.
