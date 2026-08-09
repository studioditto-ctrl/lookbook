# 러닝 다이제스트

매일 출근길(08:00 KST)과 퇴근길(19:00 KST)에 러닝 관련 뉴스와 유튜브 영상을
텔레그램으로 보내주는 봇. GitHub Actions에서 돌기 때문에 서버가 필요 없다.

설계 배경과 단계별 계획은 [PLAN.md](PLAN.md) 참고.

## 설정하기

### 1. 봇 만들고 토큰 등록

1. 텔레그램에서 `@BotFather` 와 대화 → `/newbot` → 봇 토큰 발급
2. 레포 **Settings → Secrets and variables → Actions → New repository secret**
   에 `TELEGRAM_BOT_TOKEN` 으로 등록

토큰은 절대 `config.yaml` 이나 코드에 적지 않는다. Secrets 에만 넣는다.

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

## 알아둘 점

- **크론은 정시를 보장하지 않는다.** 러너 부하에 따라 5~15분 밀린다.
  이를 감안해 크론을 목표 시각보다 10분 앞에 잡아뒀다.
- **60일간 커밋이 없으면 스케줄 워크플로가 자동 비활성화된다.**
  매 발송마다 `state/seen.json` 을 커밋하므로 이 문제는 자연히 피해간다.
- 피드 하나가 죽어도 나머지는 정상 발송된다. 실패한 소스는 로그에 남는다.
- 보낼 새 항목이 없으면 메시지를 보내지 않고 조용히 넘어간다.
