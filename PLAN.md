# 러닝 뉴스·영상 텔레그램 다이제스트 — 기본 계획

매일 출근길(08:00 KST)과 퇴근길(19:00 KST)에 러닝 관련 뉴스와 유튜브 영상을
텔레그램으로 받는 자동화 봇.

## 확정된 결정사항

| 항목 | 결정 |
|---|---|
| 실행 환경 | GitHub Actions (cron 스케줄) |
| 전송 시각 | 08:00 / 19:00 KST, 하루 2회 |
| 콘텐츠 언어 | 한국어 위주 + 선별된 영어 소스 |
| 요약 기능 | 3단계에서 Claude API로 추가 (MVP는 제목+링크) |

## 아키텍처

```
[GitHub Actions cron]
        │
        ▼
   collect.py ──── Google News RSS (한국어 검색)
        │     ├── 해외 러닝 매체 RSS
        │     └── YouTube 채널 RSS (API 키 불필요)
        ▼
   filter.py ───── state/seen.json 대조 → 중복 제거
        │     └── 키워드 스코어링 → 상위 N건 선별
        ▼
   telegram.py ─── Bot API sendMessage (HTML)
        │
        ▼
   state/seen.json 갱신 → 레포에 커밋
```

서버·DB 없이 GitHub Actions와 레포 파일만으로 완결됩니다.

## 데이터 소스

### 뉴스·아티클 (RSS)

- **Google News RSS** — 한국어 기사 커버리지의 핵심
  `https://news.google.com/rss/search?q=러닝+OR+마라톤+OR+달리기&hl=ko&gl=KR&ceid=KR:ko`
  검색어를 여러 개 두고 결과를 합칩니다.
- **해외 매체 RSS** — 훈련법·부상예방·연구 기반 아티클
  Runner's World, Running Magazine, iRunFar 등

### 유튜브 (채널 RSS)

`https://www.youtube.com/feeds/videos.xml?channel_id=<CHANNEL_ID>`

**YouTube Data API 키가 필요 없습니다.** 채널당 최근 15개 영상을 제공하므로
하루 2회 폴링이면 놓치는 영상이 없습니다. 구독할 채널 ID를 `config.yaml`에
나열하는 방식이라 추가·제거가 쉽습니다.

키워드 검색(특정 채널이 아니라 주제로 찾기)까지 원하면 그때 Data API 키를
추가합니다. 일일 쿼터 10,000 유닛 / 검색 1회당 100 유닛이라 하루 2회
호출은 여유롭습니다.

## 중복 제거

`state/seen.json`에 발송한 URL과 영상 ID를 저장하고, 워크플로 마지막에
레포로 커밋해 되돌립니다. 별도 DB가 필요 없고 발송 이력도 git log에
그대로 남습니다. 항목이 무한정 쌓이지 않도록 90일 지난 기록은 정리합니다.

## 메시지 구성

**아침 08:00 — 읽을거리 위주**
- 뉴스·아티클 3건
- 영상 2건

**저녁 19:00 — 볼거리 위주**
- 영상 3~4건
- 팁 아티클 1건

제목 · 출처 · 링크를 Telegram HTML로 포맷. 링크 미리보기는 첫 항목만
활성화해 메시지가 지나치게 길어지는 것을 막습니다.

## 단계별 진행

### 0단계 — 사전 준비 (사용자 작업)

1. 텔레그램에서 `@BotFather` 대화 → `/newbot` → 봇 이름 지정 → **토큰** 발급
2. 만든 봇에게 아무 메시지나 전송
3. `https://api.telegram.org/bot<TOKEN>/getUpdates` 접속 → `chat.id` 확인
4. 레포 Settings → Secrets and variables → Actions 에 등록
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`

### 1단계 — MVP

- RSS 수집 → 텔레그램 전송 → cron 연결
- 목표: "매일 정해진 시간에 일단 온다"
- 중복 제거·스코어링 없이 최신순 상위 N건

### 2단계 — 품질

- `state/seen.json` 기반 중복 제거
- 관심 키워드 가중치 스코어링 (부상 예방, 훈련법, 기록 단축, 러닝화 등)
- 소스 밸런싱 — 한 매체가 메시지를 독식하지 않도록
- 아침/저녁 구성 분리

### 3단계 — 개인화

- Claude API로 한국어 3줄 요약 첨부
- 봇 명령어: 채널 추가/제외, 전송 시각 변경, 즉시 발송
- 관심 없는 항목 피드백 반영

## 레포 구조 (예정)

```
.github/workflows/digest.yml   # cron 스케줄 + 실행
src/collect.py                 # RSS·YouTube 수집
src/filter.py                  # 중복 제거 + 스코어링
src/telegram.py                # 전송
src/main.py                    # 진입점 (--slot morning|evening)
config.yaml                    # 소스 목록, 관심 키워드, 항목 수
state/seen.json                # 발송 이력
requirements.txt               # feedparser, requests, PyYAML
```

## 알아둘 점

**GitHub Actions cron은 정시를 보장하지 않습니다.** 러너 부하에 따라
5~15분, 드물게 그 이상 밀립니다. 이를 감안해 크론을 목표 시각보다
10분 앞에 잡습니다.

- 08:00 KST → `50 22 * * *` (UTC, 전날 22:50)
- 19:00 KST → `50 9 * * *` (UTC 09:50)

또한 **60일간 레포에 커밋 활동이 없으면 스케줄 워크플로가 자동 비활성화**
됩니다. `state/seen.json`을 매번 커밋하는 구조라 이 문제는 자연히 해결됩니다.
