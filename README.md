# 러닝 다이제스트

매일 출근길(08:00 KST)과 퇴근길(19:00 KST)에 러닝 관련 뉴스와 유튜브 영상을
텔레그램으로 보내주는 봇. GitHub Actions에서 돌기 때문에 서버가 필요 없다.

설계 배경과 단계별 계획은 [PLAN.md](PLAN.md) 참고.

## 설정하기

### 1. 봇 만들고 토큰 등록

1. 텔레그램에서 `@BotFather` 와 대화 → `/newbot` → 봇 토큰 발급
2. 레포 **Settings → Secrets and variables → Actions → New repository secret**
   에 `TELEGRAM_BOT_TOKEN` 으로 등록

워크플로는 `TELEGRAM_BOT_TOKEN` 과 `RUNNING1978XIANAO_BOT` 두 이름을 모두
받는다. 앞의 것이 비어 있으면 뒤의 것을 쓴다. 새로 만들 때는
`TELEGRAM_BOT_TOKEN` 을 권한다.

토큰은 절대 `config.yaml` 이나 코드에 적지 않는다. Secrets 에만 넣는다.
토큰이 노출됐다면 BotFather 에서 `/revoke` 로 폐기하고 새 토큰을 발급받아
같은 시크릿의 값을 갱신한다. 봇과 설정은 그대로 유지된다.

### 2. chat_id 찾기

`getUpdates` URL 을 직접 열 필요 없다. 워크플로가 대신 찾아준다.

1. 만든 봇에게 텔레그램에서 아무 메시지나 전송
   (getUpdates 는 최근 24시간 기록만 보관하므로 이 순서가 중요하다)
2. **Actions → chat_id 확인 → Run workflow** 실행
3. 로그에 나온 숫자를 `TELEGRAM_CHAT_ID` 로 등록

```
찾은 채팅방:
  TELEGRAM_CHAT_ID = 123456789    ← 홍길동 · private
```

**봇은 보낸 메시지에 답하지 않는다.** 수신 메시지를 처리하는 코드가 없는
단방향 봇이다. 1번에서 반응이 없는 것이 정상이고, 메시지가 전송되기만 하면
된다. 대신 2번을 실행하면 봇이 확인 메시지를 한 번 보내주므로 연결이
제대로 됐는지 텔레그램에서 눈으로 확인할 수 있다.

### 3. 첫 발송 확인

**Actions → 러닝 다이제스트 → Run workflow** 에서 수동 실행.
`dry_run` 을 켜면 실제 전송 없이 로그로 메시지만 확인할 수 있다.

## 소스 추가·변경

`config.yaml` 만 고치면 된다. 커밋하면 다음 실행부터 반영된다.

### 유튜브 채널 추가

채널 ID를 몰라도 된다. 채널 주소만 넣으면 자동으로 변환된다.

```yaml
sources:
  youtube:
    - name: 채널 이름
      url: https://www.youtube.com/@handle
```

변환 결과는 `state/channels.json` 에 캐시되므로 매번 다시 조회하지 않는다.

### 뉴스 검색어 추가

```yaml
sources:
  google_news:
    - name: 트레일러닝
      query: 트레일러닝 OR 울트라마라톤
      lang: ko
      country: KR
```

### 관심사 반영

`keywords` 의 가중치를 올리면 그 주제가 우선 선택되고, `exclude` 에 넣은
단어가 제목에 있으면 아예 빠진다.

## 로컬에서 돌려보기

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 전송 없이 메시지만 출력
.venv/bin/python src/main.py --slot morning --dry-run

# 실제 전송
TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... \
  .venv/bin/python src/main.py --slot evening
```

테스트는 외부 네트워크 없이 돈다.

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## 구조

```
.github/workflows/digest.yml   cron 스케줄 + 실행 + 상태 커밋
.github/workflows/setup.yml    chat_id 확인 (최초 1회)
.github/workflows/test.yml     푸시할 때마다 테스트
src/collect.py                 RSS·유튜브 수집, 채널 ID 변환
src/filter.py                  중복 제거, 키워드 점수, 슬롯별 선별
src/telegram.py                메시지 포맷팅, 전송 (재시도 포함)
src/state.py                   발송 이력 · 채널 캐시
src/whoami.py                  chat_id 조회 도우미
src/main.py                    진입점
config.yaml                    소스 · 키워드 · 항목 수
state/seen.json                발송 이력 (자동 갱신, 90일 보관)
```

## 잘 안 될 때

**`TELEGRAM_BOT_TOKEN 이 설정되어 있지 않습니다`**

시크릿이 등록되지 않았거나 워크플로가 읽을 수 없는 자리에 있다. 확인할 것:

- **Settings → Secrets and variables → Actions** 의 **Secrets** 탭인지.
  같은 화면의 **Variables** 탭에 넣으면 `secrets.` 로 읽히지 않는다.
- **Repository secrets** 목록에 있는지. Environment secrets 는 잡에
  `environment:` 를 지정해야 읽힌다.
- 이름이 정확히 `TELEGRAM_BOT_TOKEN` 인지 (오타, 앞뒤 공백 주의).
- 왼쪽 사이드바에서 Codespaces 나 Dependabot 시크릿 화면에 넣지 않았는지.

로그의 `env:` 블록에 `TELEGRAM_BOT_TOKEN:` 이 빈칸으로 보이면 못 찾은 것이고,
`***` 로 보이면 제대로 전달된 것이다.

**chat_id 확인** 워크플로는 첫 단계에서 등록된 시크릿 **이름**을 출력한다.
값은 GitHub 이 가려주므로 이름만 나온다. 여기에 `TELEGRAM_BOT_TOKEN` 이
없으면 등록이 안 됐거나 이름이 다른 것이다. `github_token` 하나만 보이면
아직 아무것도 등록되지 않은 상태다 (그건 GitHub 이 자동으로 넣어준다).

등록 화면 바로가기:
`https://github.com/<소유자>/<레포>/settings/secrets/actions`

**`최근 대화 기록이 없습니다`**

봇에게 보낸 메시지가 없거나 24시간이 지났다. BotFather 가 알려준 `@사용자명`
이 맞는지 확인하고 메시지를 다시 보낸 뒤 워크플로를 재실행한다.

## 알아둘 점

- **크론은 정시를 보장하지 않는다.** 러너 부하에 따라 5~15분 밀린다.
  이를 감안해 크론을 목표 시각보다 10분 앞에 잡아뒀다.
- **60일간 커밋이 없으면 스케줄 워크플로가 자동 비활성화된다.**
  매 발송마다 `state/seen.json` 을 커밋하므로 이 문제는 자연히 피해간다.
- 피드 하나가 죽어도 나머지는 정상 발송된다. 실패한 소스는 로그에 남는다.
- 보낼 새 항목이 없으면 메시지를 보내지 않고 조용히 넘어간다.
