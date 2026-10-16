# 인스타 장소 아카이브 봇

텔레그램에 인스타그램 게시물 URL 을 보내면 장소를 찾아
**구글 지도 저장 목록**(`📍서울 성수` 처럼 동네 상권별)과 **옵시디언**에 저장한다.
집 Windows PC 에서 상시 실행한다. 설계와 결정 배경은 [PLAN_PLACE_ARCHIVE.md](../PLAN_PLACE_ARCHIVE.md).

```
URL → 캡션 수집 → Claude 로 장소 추출 → 카카오 로컬로 주소·좌표 확인
    → 구글 지도 '저장' 자동 클릭 → 옵시디언 노트 + 지역별 목록 노트 → 텔레그램 답장
```

## 준비물 (키 3개)

비밀번호는 필요 없다. 아래 키만 설치 중에 PC 에 직접 입력한다.
**채팅이나 레포에 붙여넣지 않는다.**

| 키 | 받는 곳 |
|---|---|
| 텔레그램 봇 토큰 | 텔레그램 `@BotFather` → `/newbot` → 이름 지정 → `123456:ABC...` 형태 토큰. 러닝 다이제스트 봇과 **다른 새 봇**을 만든다 |
| Claude API 키 | console.anthropic.com → API Keys (러닝 다이제스트 키 재사용 가능) |
| 카카오 REST API 키 | developers.kakao.com → 내 애플리케이션 → 애플리케이션 추가 → 앱 키의 **REST API 키**. 앱 설정에 '카카오맵' 사용 설정이 있으면 켠다 |

## 설치 (한 번)

1. 파이썬 3.10 이상, 구글 드라이브 데스크톱 앱(vault 동기화 중), 크롬이 있는 PC
2. 이 레포 전체를 PC 에 받는다 (GitHub → Code → Download ZIP 후 압축 풀기, 또는 `git clone`)
3. `scripts\windows\places\설치하기.bat` 더블클릭
   - 키 3개 입력 → vault 경로 입력 (예: `G:\내 드라이브\Obsidian\MyVault`)
   - 크롬 창이 뜨면 **직접 구글 로그인** → 콘솔로 돌아와 Enter
4. 텔레그램에서 새 봇에게 아무 말이나 보내면 채팅방 ID 를 답한다
5. 그 숫자를 `%USERPROFILE%\.lookbook\places.env` 의 `TELEGRAM_CHAT_ID=` 뒤에 적고
   `설치하기.bat` 을 한 번 더 실행 (봇 재시작)

이후에는 PC 에 로그인할 때마다 자동으로 켜진다.
이 채팅방 외의 메시지는 무시한다.

## 먼저 샘플로 시험하기

설치 3단계 후, 레포 폴더에서 PowerShell 로 (저장 없이 분석만):

```powershell
$py = "$env:USERPROFILE\.lookbook\places-venv\Scripts\python.exe"
& $py -m places try https://www.instagram.com/p/Ddxt9nOkeas/ https://www.instagram.com/p/DejKUQwk6NU/ https://www.instagram.com/p/Dei7h14Gh-c/
```

수집 경로(`embed`/`og`/실패), 캡션 원문, 찾은 장소가 출력된다.
`--save` 를 붙이면 구글 지도·옵시디언에 실제로 저장한다.

## 쓰는 법

| 보내는 것 | 동작 |
|---|---|
| 인스타 URL (여러 개 가능) | 분석 후 바로 저장, 결과 답장 |
| URL + 캡션 텍스트 | 수집을 건너뛰고 보낸 캡션으로 분석 |
| 캡션 텍스트만 | 직전에 캡션 수집이 실패한 게시물에 이어서 적용 |
| `/recent` `/stats` | 최근 저장 · 지역별 개수 |
| `/undo` | 마지막 게시물 저장 취소 (구글 지도 목록에서는 직접 지운다) |

답장 표시: `✅지도` 자동 저장 성공 · `⚠️자동저장 실패 · 지도에서 저장` 링크를 눌러 직접 저장 ·
`⚠️위치 확인 필요` 카카오에서 확실한 매장을 못 찾음

## 저장 위치

| 무엇 | 어디 |
|---|---|
| 장소 노트 | `vault\Places\서울\성수\<장소명>.md` (frontmatter: 주소·좌표·지도 링크·출처 게시물) |
| 지역별 목록 | `vault\Places\장소 목록.md` (지도 미저장 장소 표시) |
| 처리 기록 | `%USERPROFILE%\.lookbook\places.json` — 공개 레포라서 기록은 레포에 올리지 않는다 |
| 로그 | `%USERPROFILE%\.lookbook\places.log`, 지도 저장 실패 스크린샷 `gmaps_fail_*.png` |

노트 아래쪽 `## 메모` 에 쓴 내용은 같은 장소가 다시 저장돼도 지워지지 않는다.
동네 이름 보정(서교동 → 홍대 등)은 `places.yaml` 의 `region_alias`.

## 알아둘 위험

- **구글 지도 자동 저장은 비공식 방식이다.** 구글 화면이 바뀌면 실패하고(그때는 링크로 안내),
  자동화 탐지로 계정에 보안 확인이 뜰 수 있다. 로그인이 풀리면 봇을 끄고
  `& $py -m places login` 으로 다시 로그인한다 (`설치하기.bat` 을 다시 실행해도 된다).
- 저장할 때 크롬 창이 잠깐 뜬다 (`gmaps.headless: true` 로 숨길 수 있지만 구글이 막을 가능성이 커진다).
- 인스타 캡션 수집은 로그인 없이 공개 페이지만 읽는다. 막히면 캡션을 복사해 보내면 된다.

## 끄기

`scripts\windows\places` 에서 PowerShell: `.\install.ps1 -Uninstall`
