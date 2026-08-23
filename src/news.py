"""뉴스·블로그 — 구글 뉴스 RSS 와 네이버 블로그 검색."""

from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import requests

from feeds import (  # noqa: F401
    Item, TIMEOUT, USER_AGENT, _canonical_url, _clean_title, _note, _parse_feed, _plain,
)

GNEWS_FEED = (
    "https://news.google.com/rss/search"
    "?q={query}&hl={lang}&gl={country}&ceid={country}:{lang}"
)

# 네이버 검색 API. 개발자센터에서 앱을 만들면 바로 나오는 ID/시크릿만 있으면
# 되고 심사가 없다. 하루 25,000회.
NAVER_BLOG_API = "https://openapi.naver.com/v1/search/blog.json"
NAVER_SORTS = ("date", "sim")
KST = ZoneInfo("Asia/Seoul")

NAVER_SCOPES_EMPTY = (
    "앱에 API 권한이 없습니다. 개발자센터 → 내 애플리케이션 → 해당 앱 → "
    "API 설정 → '사용 API' 에 '검색' 을 추가하세요. 키는 맞습니다."
)

NAVER_ERRORS = {
    "024": "Client ID 가 틀렸습니다. 개발자센터의 값과 다시 맞춰보세요.",
    "028": "Client Secret 이 틀렸습니다. 두 값이 서로 바뀌지 않았는지 보세요.",
    "101": "이 앱에 검색 API 권한이 없습니다. 앱 설정의 '사용 API' 에 검색을 추가하세요.",
    "012": "요청 헤더가 잘못됐습니다.",
    "429": "하루 호출 한도를 넘겼습니다.",
}


def _naver_error(response):
    """네이버 오류 본문에서 코드와 뜻을 뽑는다. 코드가 앞에 와야 잘려도 남는다."""
    if response is None:
        return ""
    try:
        body = response.json()
    except ValueError:
        return " ".join((response.text or "").split())[:120]
    code = str(body.get("errorCode", "")).strip()
    message = " ".join(str(body.get("errorMessage", "")).split())
    hint = NAVER_ERRORS.get(code, "")
    # 키는 맞는데 앱에 권한이 없는 경우도 024 로 온다. 고칠 곳이 아예 다르다.
    if "scopes are empty" in message.lower():
        hint = NAVER_SCOPES_EMPTY
    return " ".join(x for x in (f"[{code}]" if code else "", hint, message) if x)[:200]


def search_naver_blog(query, source_name, client_id, client_secret,
                      sort="date", display=20, problems=None):
    """네이버 블로그를 검색한다. 특정 블로그가 아니라 네이버 전체가 대상이다.

    블로그 하나만 구독하려면 sources.rss 에 rss.blog.naver.com/<아이디>.xml 을
    넣으면 된다 — 그건 키가 필요 없다. 이 함수는 주제로 넓게 훑는 쪽이다.
    """
    if sort not in NAVER_SORTS:
        print(f"[collect] '{source_name}' 정렬 '{sort}' 을 몰라 date 로 씁니다.")
        sort = "date"

    try:
        resp = requests.get(
            NAVER_BLOG_API,
            params={"query": query, "display": min(display, 100), "sort": sort},
            timeout=TIMEOUT,
            headers={
                "X-Naver-Client-Id": client_id,
                "X-Naver-Client-Secret": client_secret,
                "User-Agent": USER_AGENT,
            },
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.HTTPError as e:
        # 네이버는 본문에 errorCode 를 담아 준다. 상태 코드만 찍으면
        # 아이디가 틀린 건지 권한이 없는 건지 알 수 없다.
        print(f"[collect] '{source_name}' 네이버 블로그 검색 실패: {e}")
        reason = _naver_error(e.response)
        if reason:
            print(f"[collect]   네이버 응답: {reason}")
        _note(problems, source_name, f"네이버 블로그 검색 실패 ({reason or e})")
        return []
    except (requests.RequestException, ValueError) as e:
        print(f"[collect] '{source_name}' 네이버 블로그 검색 실패: {e}")
        _note(problems, source_name, "네이버 블로그 검색 실패")
        return []

    items = []
    for entry in data.get("items") or []:
        link = entry.get("link")
        title = _clean_title(_plain(entry.get("title")))
        if not link or not title:
            continue
        # postdate 는 YYYYMMDD 라 시각이 없다. 그날 0시(KST)로 둔다.
        try:
            when = datetime.strptime(entry.get("postdate", ""), "%Y%m%d").replace(tzinfo=KST)
        except ValueError:
            when = datetime.now(timezone.utc)
        url_ = _canonical_url(link)
        items.append(
            Item(
                id=f"naver:{url_}",
                title=title,
                url=url_,
                # 블로그 이름을 출처로 둬야 한 블로그가 회차를 독식하지 않는다
                source=_plain(entry.get("bloggername")) or source_name,
                kind="article",
                published=when,
                summary=_plain(entry.get("description")),
            )
        )
    print(f"[collect] '{source_name}' 네이버 블로그 {len(items)}건 ({sort})")
    return items

def _fresh_window(hours):
    """구글 뉴스 검색어에 붙일 기간 제한.

    when: 이 없으면 관련도순으로 몇 달 전 기사까지 섞여 온다. 그것들은
    lookback 에서 전부 잘려 나가 결국 기사가 한 건도 남지 않는다.
    """
    hours = max(1, int(hours))
    if hours < 24:
        return f"when:{hours}h"
    # 올림한다. 36시간을 1d 로 줄이면 열두 시간을 잃는다.
    return f"when:{-(-hours // 24)}d"


def _collect_google_news(src, hours, problems=None):
    """기간을 좁혀 부르고, 그래도 비면 제한 없이 한 번 더 부른다.

    when: 을 못 알아듣는 경우에도 지금보다 나빠지지 않게 하기 위함이다.
    """
    name = src["name"]
    lang = src.get("lang", "ko")
    country = src.get("country", "KR")

    def fetch(query):
        return _parse_feed(
            GNEWS_FEED.format(query=quote_plus(query), lang=lang, country=country),
            name, "article", problems,
        )

    def mark(found):
        for item in found:
            item.searched = True
        return found

    window = _fresh_window(hours)
    items = mark(fetch(f"{src['query']} {window}"))
    if items:
        return items
    print(f"[collect] '{name}' {window} 로는 결과가 없어 기간 제한 없이 다시 찾습니다.")
    return mark(fetch(src["query"]))
