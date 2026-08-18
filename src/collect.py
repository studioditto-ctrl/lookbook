"""RSS·유튜브 수집.

유튜브는 채널 RSS(`/feeds/videos.xml`)를 쓰기 때문에 Data API 키가 필요 없다.
채널당 최근 15개 영상을 주므로 하루 2회 폴링이면 놓치는 영상이 없다.
"""

import html
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus, urlparse, urlunparse, parse_qsl, urlencode

import feedparser
import requests

USER_AGENT = "running-digest/1.0 (+https://github.com/studioditto-ctrl/lookbook)"
TIMEOUT = 20

YT_FEED = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
YT_API = "https://www.googleapis.com/youtube/v3/playlistItems"
YT_SEARCH_API = "https://www.googleapis.com/youtube/v3/search"
YT_API_MAX = 15
GNEWS_FEED = (
    "https://news.google.com/rss/search"
    "?q={query}&hl={lang}&gl={country}&ceid={country}:{lang}"
)

# 추적용 쿼리 파라미터. 같은 기사가 다른 URL로 보이는 것을 막는다.
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "igshid", "ref", "ref_src",
}


@dataclass
class Item:
    id: str
    title: str
    url: str
    source: str
    kind: str  # "article" | "video"
    published: datetime
    summary: str = ""      # 피드에서 가져온 원문 발췌
    summary_ko: str = ""   # 한국어 요약 (요약 단계에서 채움)
    tags: tuple = ()       # 소스에 붙인 분류 (슬롯별 필터에 쓴다)
    score: float = 0.0


def _canonical_url(url):
    try:
        parts = urlparse(url)
    except ValueError:
        return url
    query = [(k, v) for k, v in parse_qsl(parts.query) if k not in TRACKING_PARAMS]
    return urlunparse(parts._replace(query=urlencode(query), fragment=""))


def _published(entry):
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            return datetime.fromtimestamp(time.mktime(parsed), tz=timezone.utc)
    return datetime.now(timezone.utc)


# 블록 태그는 공백으로 바꿔 문단을 분리하고, 인라인 태그는 지운다.
# 인라인까지 공백으로 바꾸면 "<b>부상</b>을"이 "부상 을"이 되어 조사가 떨어진다.
_BLOCK_TAG_RE = re.compile(r"</?(?:p|div|br|li|tr|h[1-6])\b[^>]*>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")


def _snippet(entry):
    """피드 항목의 본문 발췌를 평문으로. 없으면 빈 문자열."""
    raw = entry.get("summary") or entry.get("media_description") or ""
    if not raw:
        content = entry.get("content") or []
        if content:
            raw = content[0].get("value", "")
    text = html.unescape(_TAG_RE.sub("", _BLOCK_TAG_RE.sub(" ", raw)))
    return re.sub(r"\s+", " ", text).strip()


def _clean_title(title):
    title = re.sub(r"\s+", " ", title or "").strip()
    # 구글 뉴스는 제목 뒤에 " - 매체명"을 붙인다. 출처는 따로 표시하므로 떼어낸다.
    return re.sub(r"\s+-\s+[^-]{2,30}$", "", title)


def _get_with_retry(url, attempts=2, delay=2):
    """5xx 는 일시적인 경우가 많아 한 번 더 시도한다. 4xx 는 바로 포기한다."""
    for attempt in range(1, attempts + 1):
        resp = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
        if resp.status_code < 500 or attempt == attempts:
            resp.raise_for_status()
            return resp
        time.sleep(delay)
    raise requests.RequestException("재시도 후에도 실패")


def _note(problems, source_name, reason):
    if problems is not None:
        problems.append((source_name, reason))


def _tag(items, tags):
    for item in items:
        item.tags = tuple(tags or ())
    return items


def _parse_feed(url, source_name, kind, problems=None):
    """피드 하나를 항목 리스트로. 실패해도 예외를 올리지 않는다."""
    try:
        resp = _get_with_retry(url)
    except requests.RequestException as e:
        print(f"[collect] '{source_name}' 피드 요청 실패: {e}")
        _note(problems, source_name, "피드 요청 실패")
        return []

    parsed = feedparser.parse(resp.content)
    if parsed.bozo and not parsed.entries:
        print(f"[collect] '{source_name}' 피드 파싱 실패: {parsed.bozo_exception}")
        _note(problems, source_name, "피드 파싱 실패")
        return []

    items = []
    for entry in parsed.entries:
        link = entry.get("link")
        title = _clean_title(entry.get("title"))
        if not link or not title:
            continue
        url_ = _canonical_url(link)
        items.append(
            Item(
                id=entry.get("id") or url_,
                title=title,
                url=url_,
                source=source_name,
                kind=kind,
                published=_published(entry),
                summary=_snippet(entry),
            )
        )
    print(f"[collect] '{source_name}' {len(items)}건")
    return items


_ID = r"(UC[\w-]{22})"
_LINK_TAG_RE = re.compile(r"<link\b[^>]*>", re.I)
_CANONICAL_HREF_RE = re.compile(r'href="[^"]*/channel/' + _ID)
_ITEMPROP_RE = re.compile(r'<meta\b[^>]*itemprop="identifier"[^>]*content="' + _ID, re.I)
_EXTERNAL_ID_RE = re.compile(r'"externalId":"' + _ID)
_CHANNEL_ID_RE = re.compile(r'"channelId":"' + _ID)


def _channel_id_from_page(page):
    """채널 페이지에서 그 페이지 '자신'의 channel_id 를 뽑는다.

    "channelId" 는 추천 채널이나 영상 소유자 정보로도 페이지에 여러 번 나온다.
    먼저 매칭되는 것을 집으면 남의 채널 ID를 가져올 수 있으므로, 페이지 소유자를
    가리키는 것이 확실한 표지부터 순서대로 확인한다.
    """
    for tag in _LINK_TAG_RE.findall(page):
        if 'rel="canonical"' in tag.lower():
            match = _CANONICAL_HREF_RE.search(tag)
            if match:
                return match.group(1), "canonical"

    for regex, label in (
        (_ITEMPROP_RE, "itemprop"),
        (_EXTERNAL_ID_RE, "externalId"),
        (_CHANNEL_ID_RE, "channelId"),
    ):
        match = regex.search(page)
        if match:
            return match.group(1), label
    return None, None


def resolve_channel_id(url, cache, problems=None, source_name=None):
    """유튜브 채널 주소(@핸들 포함)를 channel_id 로 바꾼다. 결과는 캐시한다."""
    if url in cache:
        return cache[url]

    match = re.search(r"/channel/" + _ID, url)
    if match:
        cache[url] = match.group(1)
        return cache[url]

    try:
        resp = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[collect] 채널 ID 확인 실패 ({url}): {e}")
        _note(problems, source_name or url, "채널 주소를 열 수 없음 (핸들이 바뀌었을 수 있음)")
        return None

    channel_id, label = _channel_id_from_page(resp.text)
    if not channel_id:
        print(f"[collect] 채널 ID를 페이지에서 찾지 못했습니다: {url}")
        _note(problems, source_name or url, "페이지에서 channel_id 를 찾지 못함")
        return None

    print(f"[collect] '{source_name or url}' channel_id={channel_id} ({label})")
    cache[url] = channel_id
    return channel_id


def search_channel_id(query, cache, key, problems=None, source_name=None):
    """채널 이름으로 channel_id 를 찾는다. URL이나 핸들을 몰라도 등록할 수 있게.

    search 호출은 100 유닛으로 비싸지만, 찾은 결과를 state/channels.json 에
    캐시하므로 채널당 한 번만 든다. 검색이라 다른 채널이 잡힐 수 있어
    찾은 채널 이름을 로그에 남긴다 — 틀렸으면 캐시에서 지우고 고치면 된다.
    """
    cache_key = f"search:{query}"
    if cache_key in cache:
        return cache[cache_key]

    params = {"part": "snippet", "type": "channel", "q": query, "maxResults": 1, "key": key}
    try:
        resp = requests.get(
            YT_SEARCH_API, params=params, timeout=TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        print(f"[collect] '{source_name or query}' 채널 검색 실패: {e}")
        _note(problems, source_name or query, "채널 검색 실패")
        return None

    results = data.get("items") or []
    if not results:
        print(f"[collect] '{query}' 검색 결과가 없습니다.")
        _note(problems, source_name or query, "검색 결과 없음")
        return None

    top = results[0]
    channel_id = (top.get("id") or {}).get("channelId")
    if not channel_id:
        _note(problems, source_name or query, "검색 결과에 channel_id 없음")
        return None

    found = (top.get("snippet") or {}).get("title", "?")
    print(f"[collect] '{query}' 검색 → '{found}' ({channel_id})")
    cache[cache_key] = channel_id
    return channel_id


def search_videos(query, source_name, key, hours=48, limit=YT_API_MAX, problems=None):
    """키워드로 최근 영상을 찾는다.

    구독 채널이 없는 새 주제는 이 경로로만 영상이 들어온다. 뉴스 링크는
    구글 리다이렉트라 텔레그램 썸네일이 비는데, 유튜브 링크는 확실히 잡힌다.

    search 는 1회 100 유닛이라 회차당 검색어 하나에 한 번만 부른다.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    params = {
        "part": "snippet",
        "type": "video",
        "order": "date",
        "q": query,
        "maxResults": min(limit, 25),
        "publishedAfter": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "key": key,
    }
    try:
        resp = requests.get(
            YT_SEARCH_API, params=params, timeout=TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        print(f"[collect] '{source_name}' 영상 검색 실패: {e}")
        _note(problems, source_name, "유튜브 영상 검색 실패")
        return []

    items = []
    for entry in data.get("items") or []:
        video_id = (entry.get("id") or {}).get("videoId")
        snippet = entry.get("snippet") or {}
        title = _clean_title(snippet.get("title"))
        if not video_id or not title:
            continue
        try:
            when = datetime.fromisoformat(
                (snippet.get("publishedAt") or "").replace("Z", "+00:00")
            )
        except ValueError:
            when = datetime.now(timezone.utc)
        items.append(
            Item(
                id=f"yt:video:{video_id}",
                title=title,
                # 검색은 채널을 가리지 않으므로 출처에 실제 채널 이름을 쓴다
                source=snippet.get("channelTitle") or source_name,
                url=f"https://www.youtube.com/watch?v={video_id}",
                kind="video",
                published=when,
                summary=re.sub(r"\s+", " ", snippet.get("description") or "").strip(),
            )
        )
    print(f"[collect] '{source_name}' 영상 검색 {len(items)}건")
    return items


def _uploads_playlist(channel_id):
    """채널의 '업로드' 재생목록 ID. UC... -> UU... 로 앞 두 글자만 바뀐다."""
    return "UU" + channel_id[2:]


def _collect_via_api(channel_id, source_name, key, problems=None):
    """YouTube Data API 로 최근 업로드를 가져온다.

    RSS 엔드포인트(youtube.com/feeds/videos.xml)는 러너에서 404/500 을 돌려주고
    같은 채널 ID에도 실행마다 결과가 달라, 안정적으로 쓸 수 없다. Data API 는
    playlistItems 호출당 1 유닛이라 하루 두 번 × 채널 몇 개로는 쿼터에 여유가 많다.
    """
    params = {
        "part": "snippet",
        "playlistId": _uploads_playlist(channel_id),
        "maxResults": YT_API_MAX,
        "key": key,
    }
    try:
        resp = requests.get(
            YT_API, params=params, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        print(f"[collect] '{source_name}' Data API 요청 실패: {e}")
        _note(problems, source_name, "YouTube Data API 요청 실패")
        return []

    items = []
    for entry in data.get("items") or []:
        snippet = entry.get("snippet") or {}
        video_id = (snippet.get("resourceId") or {}).get("videoId")
        title = _clean_title(snippet.get("title"))
        if not video_id or not title:
            continue
        published = snippet.get("publishedAt") or ""
        try:
            when = datetime.fromisoformat(published.replace("Z", "+00:00"))
        except ValueError:
            when = datetime.now(timezone.utc)
        items.append(
            Item(
                id=f"yt:video:{video_id}",
                title=title,
                url=f"https://www.youtube.com/watch?v={video_id}",
                source=source_name,
                kind="video",
                published=when,
                summary=re.sub(r"\s+", " ", snippet.get("description") or "").strip(),
            )
        )
    print(f"[collect] '{source_name}' {len(items)}건 (Data API)")
    return items


def collect(config, channel_cache):
    """설정에 있는 모든 소스에서 항목을 모아 lookback 기간 내 것만 반환."""
    sources = config.get("sources") or {}
    items = []
    problems = []

    for src in sources.get("google_news") or []:
        url = GNEWS_FEED.format(
            query=quote_plus(src["query"]),
            lang=src.get("lang", "ko"),
            country=src.get("country", "KR"),
        )
        items += _tag(_parse_feed(url, src["name"], "article", problems), src.get("tags"))

    for src in sources.get("rss") or []:
        items += _tag(
            _parse_feed(src["url"], src["name"], "article", problems), src.get("tags")
        )

    api_key = os.environ.get("YOUTUBE_API_KEY")
    if (sources.get("youtube_search") or []) and not api_key:
        print("[collect] YOUTUBE_API_KEY 가 없어 영상 검색을 건너뜁니다.")
    for src in sources.get("youtube_search") or [] if api_key else []:
        found = search_videos(
            src["query"], src.get("name") or src["query"], api_key,
            hours=config.get("lookback_hours", 36), problems=problems,
        )
        items += _tag(found, src.get("tags"))

    if (sources.get("youtube") or []) and not api_key:
        print("[collect] YOUTUBE_API_KEY 가 없어 RSS 로 시도합니다 (404 가 잦습니다).")

    for src in sources.get("youtube") or []:
        name = src.get("name", "?")
        channel_id = src.get("channel_id")
        if not channel_id and src.get("url"):
            channel_id = resolve_channel_id(src["url"], channel_cache, problems, name)
        # url 도 channel_id 도 없으면 이름(또는 search)으로 찾는다. API 키가 필요하다.
        if not channel_id and api_key:
            channel_id = search_channel_id(
                src.get("search") or name, channel_cache, api_key, problems, name
            )
        if not channel_id:
            print(f"[collect] '{name}' 채널을 건너뜁니다 (ID 없음)")
            continue
        if api_key:
            fetched = _collect_via_api(channel_id, src["name"], api_key, problems)
        else:
            fetched = _parse_feed(
                YT_FEED.format(channel_id=channel_id), src["name"], "video", problems
            )
        items += _tag(fetched, src.get("tags"))

    if problems:
        print("\n[collect] 문제가 있는 소스 (config.yaml 에서 고치거나 지우세요):")
        for source_name, reason in problems:
            print(f"  - {source_name}: {reason}")
        print()

    cutoff = datetime.now(timezone.utc) - timedelta(
        hours=config.get("lookback_hours", 36)
    )
    fresh = [i for i in items if i.published >= cutoff]
    print(f"[collect] 전체 {len(items)}건 중 최근 {len(fresh)}건")
    return fresh
