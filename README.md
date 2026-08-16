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

### 3. 한국어 요약 켜기 (선택)

영어 기사·영상의 제목을 한국어로 번역하고 1~2문장 요약을 붙이려면
Claude API 키를 Repository secrets 에 추가한다. 워크플로는
`ANTHROPIC_API_KEY` 와 `RUNNING_API` 두 이름을 모두 받는다 (앞의 것이
비어 있으면 뒤의 것을 쓴다).

키가 없으면 요약 단계를 건너뛰고 제목과 링크만 보낸다. 발송 자체는
실패하지 않는다. 요약 요청이 실패해도 마찬가지로 제목만 나간다.

`config.yaml` 의 `summary.enabled` 로 끄고 켤 수 있고, `summary.effort` 로
비용과 품질을 조절한다 (`low` 기본값).

### 4. 유튜브 영상 켜기 (선택)

채널 RSS(`youtube.com/feeds/videos.xml`)는 GitHub Actions 러너에서 404/500 을
자주 돌려준다. 같은 채널 ID가 실행마다 다른 오류를 내므로 ID 문제가 아니라
유튜브 쪽에서 러너를 막는 것으로 보인다. 영상을 안정적으로 받으려면
YouTube Data API 키를 쓴다.

1. Google Cloud Console 에서 프로젝트 생성 → **YouTube Data API v3** 사용 설정
2. 사용자 인증 정보 → API 키 만들기 (결제 등록 불필요)
3. Repository secrets 에 **`YOUTUBE_API_KEY`** 로 등록

호출은 채널당 1 유닛이고 일일 쿼터는 10,000 이라, 채널 8개 × 하루 2회면
16 유닛으로 여유가 크다. 키가 없으면 RSS 로 시도하고, 실패하면 영상 없이
기사만 보낸다.

### 5. 첫 발송 확인

**Actions → 러닝 다이제스트 → Run workflow** 에서 수동 실행.
`dry_run` 을 켜면 실제 전송 없이 로그로 메시지만 확인할 수 있다.

시크릿이 아직 없어도 실행은 된다. 전송만 건너뛰고 수집 결과와 완성된
메시지를 로그에 출력한 뒤, 무엇이 빠졌는지 에러로 알려준다. 소스가
제대로 동작하는지는 설정을 마치기 전에도 이렇게 확인할 수 있다.

## 소스 추가·변경

`config.yaml` 만 고치면 된다. 커밋하면 다음 실행부터 반영된다.

### 유튜브 채널 추가

세 가지 방법이 있고, 결과는 `state/channels.json` 에 캐시되므로 채널당 한 번만
조회한다.

```yaml
sources:
  youtube:
    - name: 채널 이름                                   # 이름만 (API 키 필요)
    - name: 채널 이름
      url: https://www.youtube.com/@handle              # 주소로
    - name: 채널 이름
      channel_id: UCxxxxxxxxxxxxxxxxxxxxxx              # ID를 알면 바로
```

**이름만 적는 방법**은 `YOUTUBE_API_KEY` 가 있어야 하고, 검색이라 동명이인
채널이 잡힐 수 있다. 로그에 `'런랜드' 검색 → '런랜드 RUNLAND' (UC...)` 처럼
찾은 채널 이름이 찍히니 확인하고, 틀렸으면 `state/channels.json` 에서 해당
줄을 지운 뒤 `search:` 로 검색어를 좁히거나 주소를 직접 넣는다.

검색은 호출당 100 유닛으로 비싸지만 캐시되므로 채널당 한 번만 든다
(10개 등록 = 1,000 유닛, 일일 쿼터 10,000).

### 뉴스 검색어 추가

```yaml
sources:
  google_news:
    - name: 트레일러닝
      query: 트레일러닝 OR 울트라마라톤
      lang: ko
      country: KR
```

### 썸네일 (링크 미리보기)

텔레그램은 메시지 하나에 미리보기를 **하나만** 붙일 수 있다. 그래서 첫 항목의
링크로만 썸네일을 띄운다.

```yaml
link_preview:
  mode: first        # none 이면 썸네일 없음
  prefer: video      # any 면 메시지 첫 항목을 그대로 사용
  large: true        # false 면 작은 썸네일
  above_text: true   # false 면 본문 아래
```

`prefer: video` 는 메시지 순서와 무관하게 **영상 링크**로 썸네일을 만든다.
유튜브 링크는 썸네일이 확실히 잡히는 반면, Google 뉴스 링크
(`news.google.com/rss/articles/...`)는 리다이렉트라 미리보기가 비는 일이
잦기 때문이다. 그 회차에 영상이 없으면 기사 링크로 넘어간다.

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
src/summarize.py               한국어 번역·요약 (Claude API)
src/telegram.py                메시지 포맷팅, 전송 (재시도 포함)
src/state.py                   발송 이력 · 채널 캐시
src/whoami.py                  chat_id 조회 도우미
src/main.py                    진입점
config.yaml                    러닝 — 소스 · 키워드 · 항목 수
config.food.yaml               음식 — 같은 형식
config.fashion.yaml            패션 — 같은 형식
settings.yaml                  어드민 페이지가 덮어쓰는 값 (시각 · 개수 · 키워드)
docs/index.html                모바일 어드민 페이지
state/seen.json                발송 이력 (자동 갱신, 90일 보관)
```

## 지금 바로 한 번 보내기

Actions 화면을 열 수 없을 때는 `.trigger` 파일을 건드려 푸시하면 발송이 돈다.

```bash
# 첫 줄이 회차다. 슬롯 이름만 쓰거나 <설정>:<슬롯> 으로 적는다
echo "evening"    > .trigger && git commit -am "발송" && git push
echo "food:lunch" > .trigger && git commit -am "발송" && git push

# 둘째 줄에 force 를 넣으면 이미 보낸 항목도 다시 보낸다 (--ignore-seen)
printf 'food:lunch\nforce\n' > .trigger && git commit -am "테스트 발송" && git push
```

경로 필터가 걸려 있어 다른 파일만 바꾼 푸시로는 발송되지 않는다. 이 경로가
필요 없으면 `digest.yml` 의 `push:` 블록을 지운다.

## 모바일 어드민 페이지

발송 시각·항목 수·키워드·제외어를 휴대폰에서 고친다. 채널 목록은
`config*.yaml` 에 그대로 두고, 페이지는 `settings.yaml` 만 덮어쓴다.

### 켜기 (한 번)

1. 저장소 **Settings → Pages → Source: GitHub Actions**
2. **Actions → 어드민 페이지 배포 → Run workflow** (이후로는 `docs/` 가
   바뀔 때마다 저절로 배포된다)
3. `https://<사용자>.github.io/lookbook/` 접속 후 맨 위
   **먼저 GitHub 토큰을 넣어주세요** 카드에 fine-grained PAT 입력
   (토큰이 저장되면 이 카드는 한 줄로 접힌다)
   - 이 저장소 하나만, **Contents: Read and write** 권한만
   - 브라우저 localStorage 에만 저장되고 커밋되지 않는다

### 쓰기

화면은 위에서부터 **주제 추가 → 주제별 카드 → 고급 설정** 순이다.

- **주제 추가** — 맨 위. 말 하나를 넣으면 그 말로 구글 뉴스를 찾는 검색어가
  잡히고, 불러온 구독 목록에서 이름이 맞는 채널이 붙는다.
- **주제 카드** — 슬롯마다 발송 스위치·시각·기사 수·영상 수, 그리고
  **지금 테스트 발송**. 키워드·검색어·채널은 카드 아래 접힌
  줄(`키워드 n · 검색어 n · 채널 n`)을 펴야 나온다.
- **고급 설정** — 구독 CSV 불러오기, 공통 제외어.

**바꾸면 알아서 저장된다.** 값을 건드리고 1.2초 뒤 `settings.yaml` 이
커밋되고, 화면 오른쪽 위가 `바뀜 → 저장하는 중… → 저장됨 09:41` 로 바뀐다.
화면 아래 알림도 뜨므로 어디까지 스크롤했든 결과가 보인다. 저장을 앞당기려면
**저장**, 저장소 쪽 값을 다시 가져오려면 **새로고침** 을 누른다.

다른 곳에서 먼저 `settings.yaml` 이 바뀌어 `sha` 가 어긋나면 최신 `sha` 를
받아 한 번 다시 민다. 편집하던 값이 조용히 사라지지 않는다.

### 테스트 발송

슬롯의 **지금 테스트 발송** 을 누르면 저장하지 않은 값을 먼저 저장하고,
`.trigger` 에 `<설정>:<슬롯>` 과 `force` 를 써서 커밋한다. 워크플로의 푸시
경로가 걸려 그 회차만 즉시 돈다. `force` 가 있으므로 **이미 보낸 항목도
다시 온다** — 바꾼 설정이 어떻게 나오는지 바로 볼 수 있다.

토큰 권한은 `Contents: Read and write` 하나면 된다. Actions 권한은
필요 없다 — 파일을 커밋해서 워크플로를 깨우는 방식이기 때문이다.

### 새 주제 만들기

페이지 맨 위 **주제 추가** 에 말을 넣으면 그 말로 구글 뉴스를 검색하는
검색어가 잡히고, 불러온 구독 목록에서 이름이 맞는 채널이 자동으로 붙는다.
채널은 검색창에서 더 넣거나 뺄 수 있고, 발송 시각·개수도 바로 조정된다.

`config` 파일 없이 `settings.yaml` 만으로 도는 주제이므로 저장소를
직접 건드릴 일이 없다. 상태는 `state/<key>/` 에 자동으로 생긴다.

### 구독 목록은 커밋되지 않는다

Takeout 의 `구독정보.csv` 는 페이지에서 읽어 **브라우저 localStorage 에만**
둔다. 이 저장소는 공개이므로 목록 전체를 올리지 않고, 주제에 실제로 넣은
채널만 `settings.yaml` 에 커밋된다. 기기를 바꾸면 CSV 를 다시 불러오면 된다.

### 발송 시각이 데이터인 이유

크론에 시각을 박아두면 페이지에서 바꿀 수 없다. 그래서 워크플로는
30분마다 깨어나 `settings.yaml` 을 보고 **지금 보낼 회차가 있는지**만
판단한다(`--due`). 이미 보낸 회차는 `state/schedule.json` 의 날짜로
걸러지므로 중복 발송되지 않고, 크론이 밀려도 3시간 안이면 따라잡는다.

## 다이제스트 여러 개 운영하기

설정 파일 하나가 다이제스트 하나다. 파이프라인·요약·썸네일은 공유하고
발송 이력만 분리된다.

| 설정 파일 | 주제 | 상태 | 슬롯 (KST) |
|---|---|---|---|
| `config.yaml` | 러닝 | `state/` | `morning` 08:00, `evening` 19:00 |
| `config.food.yaml` | 음식 | `state/food/` | `lunch` 12:30 |
| `config.fashion.yaml` | 패션 | `state/fashion/` | `night` 21:30 |

텔레그램 채팅방은 하나를 같이 쓴다.

**한 파일 안에서 슬롯마다 다른 소스를 쓰려면** 소스에 `tags` 를 달고
슬롯에서 고른다. 슬롯에 `tags` 가 없으면 모든 소스를 쓴다. 주제가
확실히 갈리면 태그 대신 파일을 나누는 편이 읽기 쉽다 — 패션과 음식도
그래서 각자 파일을 쓴다.

```yaml
sources:
  youtube:
    - name: 어느 맛집 채널
      channel_id: UC...
      tags: [food]
slots:
  lunch:
    tags: [food]     # 이 슬롯은 food 소스만 본다
```

새 다이제스트를 추가하려면 `config.<이름>.yaml` 을 만들고
`digest.yml` 의 크론과 `case` 문에 한 줄씩 넣는다. 상태는
`state/<이름>/` 에 자동으로 생긴다.

## 구독 채널에서 골라 담기

구독 목록에서 러닝·운동 채널만 뽑아 `config.yaml` 에 넣을 형태로 출력한다.

1. YouTube 설정 → 개인정보 보호 → **'모든 구독 정보 비공개'를 끈다**
   (구독 목록은 기본이 비공개이고, 비공개면 API 키로 읽을 수 없다)
2. `.subs-trigger` 에 본인 핸들을 적고 푸시

   ```bash
   echo "@myhandle" > .subs-trigger && git commit -am "구독 확인" && git push
   ```

3. Actions 로그에서 후보 목록을 확인하고 `config.yaml` 에 옮긴다
4. 끝나면 구독 정보를 다시 비공개로 되돌린다

아무것도 전송하지 않고 로그에만 출력한다.

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

등록 화면 바로가기:
`https://github.com/<소유자>/<레포>/settings/secrets/actions`

**시크릿 이름을 로그에 찍어 확인하고 싶더라도 `toJSON(secrets)` 는 쓰지
않는다.** 시크릿 컨텍스트 전체를 읽는 워크플로는 GitHub 이 승인 대상으로
분류해서, 실행할 때마다 "Approve and run" 을 눌러야 한다. 이름이 의심되면
프리플라이트 단계가 어느 시크릿이 비었는지 알려주므로 그걸 보면 된다.

**영상이 하나도 안 올 때**

실행 로그 끝의 `문제가 있는 소스` 목록을 본다. 로그에는 채널마다
`channel_id=UC... (canonical)` 처럼 어느 표지에서 ID를 뽑았는지도 찍힌다.

- 피드가 **404** 면 channel_id 가 그 채널의 것이 아닐 가능성이 높다.
  `state/channels.json` 에서 해당 항목을 지우면 다음 실행에서 다시 찾는다.
- **500** 은 대개 일시적이라 자동으로 한 번 재시도한다. 계속되면 유튜브 쪽
  문제일 수 있으니 다음 회차를 기다려 본다.
- 채널 주인이 핸들을 바꿨다면 `config.yaml` 의 주소를 고친다.

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
- **요약은 선택한 항목에만, 한 번의 요청으로 처리한다.** 걸러낸 항목은
  번역하지 않고, 항목마다 따로 호출하지도 않는다.
