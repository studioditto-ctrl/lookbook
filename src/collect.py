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
from zoneinfo import ZoneInfo
from urllib.parse import quote_plus, urlparse, urlunparse, parse_qsl, urlencode

import feedparser
import requests

USER_AGENT = "running-digest/1.0 (+https://github.com/studioditto-ctrl/lookbook)"
TIMEOUT = 20

YT_FEED = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
YT_API = "https://www.googleapis.com/youtube/v3/playlistItems"
YT_VIDEOS_API = "https://www.googleapis.com/youtube/v3/videos"
YT_CHANNELS_API = "https://www.googleapis.com/youtube/v3/channels"
# id 를 50개까지 한 번에 물어볼 수 있고 호출당 1유닛이다.
YT_BATCH = 50
# 구독자 수는 하루 사이에 크게 변하지 않는다. 캐시해 호출을 아낀다.
SUBS_CACHE_DAYS = 7
YT_SEARCH_API = "https://www.googleapis.com/youtube/v3/search"
YT_API_MAX = 15
# 네이버 검색 API. 개발자센터에서 앱을 만들면 바로 나오는 ID/시크릿만 있으면
# 되고 심사가 없다. 하루 25,000회.
NAVER_BLOG_API = "https://openapi.naver.com/v1/search/blog.json"
NAVER_SORTS = ("date", "sim")
KST = ZoneInfo("Asia/Seoul")

GNEWS_FEED = (
    "https://news.google.com/rss/search"
    "?q={query}&hl={lang}&gl={country}&ceid={country}:{lang}"
)

# 추적용 쿼리 파라미터. 같은 기사가 다른 URL로 보이는 것을 막는다.
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "igshid", "ref", "ref_src",
    # 네이버 검색이 붙이는 것들. 남겨두면 같은 글이 다른 URL 로 보인다.
    "from", "trackingCode", "proxyReferer", "fromRss",
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
    channel_id: str = ""   # 영상일 때 올린 채널 (구독자 수 확인에 쓴다)
    score: float = 0.0


_TAG_RE = re.compile(r"<[^>]+>")


def _plain(text):
    """네이버가 검색어에 <b> 를 씌워 돌려주고 엔티티도 섞여 온다."""
    return html.unescape(_TAG_RE.sub("", text or "")).strip()


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


# search.list 가 받는 정렬. viewCount 는 기간 안에서 조회수가 높은 순이라,
# publishedAfter 와 같이 쓰면 '요즘 많이 본 영상'이 된다.
SEARCH_ORDERS = ("date", "viewCount", "relevance", "rating")


def search_videos(query, source_name, key, hours=48, limit=YT_API_MAX,
                  order="date", lang=None, problems=None):
    """키워드로 최근 영상을 찾는다. 구독 여부와 무관하게 유튜브 전체가 대상이다.

    채널 목록만 보면 이미 아는 채널에서만 영상이 온다. 이 경로가 있어야
    주제를 새로 만들거나 모르는 채널이 올린 영상도 들어온다.

    search 는 1회 100 유닛이라 회차당 검색어 하나에 한 번만 부른다.
    """
    if order not in SEARCH_ORDERS:
        print(f"[collect] '{source_name}' 정렬 '{order}' 을 몰라 date 로 씁니다.")
        order = "date"

    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    params = {
        "part": "snippet",
        "type": "video",
        "order": order,
        "q": query,
        "maxResults": min(limit, 25),
        "publishedAfter": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "key": key,
    }
    if lang:
        params["relevanceLanguage"] = lang
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
                channel_id=snippet.get("channelId", ""),
            )
        )
    print(f"[collect] '{source_name}' 영상 검색 {len(items)}건 ({order})")
    return items


# 024 는 원인이 둘이다. 본문을 봐야 갈린다.
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
                channel_id=snippet.get("channelId", "") or channel_id,
            )
        )
    print(f"[collect] '{source_name}' {len(items)}건 (Data API)")
    return items


def _video_id(item):
    return item.id.split(":")[-1] if item.id.startswith("yt:video:") else ""


def _batched(values, size=YT_BATCH):
    values = list(values)
    for start in range(0, len(values), size):
        yield values[start:start + size]


def _stat(entry, name):
    try:
        return int((entry.get("statistics") or {}).get(name, 0))
    except (TypeError, ValueError):
        return 0


def video_views(video_ids, key, problems=None):
    """영상별 조회수. 50개당 1유닛."""
    views = {}
    for chunk in _batched(video_ids):
        try:
            resp = requests.get(
                YT_VIDEOS_API,
                params={"part": "statistics", "id": ",".join(chunk), "key": key},
                timeout=TIMEOUT, headers={"User-Agent": USER_AGENT},
            )
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as e:
            print(f"[collect] 조회수 확인 실패: {e}")
            _note(problems, "조회수 확인", "videos.list 요청 실패")
            return {}
        for entry in data.get("items") or []:
            views[entry.get("id")] = _stat(entry, "viewCount")
    return views


def channel_subscribers(channel_ids, key, cache, problems=None):
    """채널별 구독자 수. 캐시에 남겨 매 회차 다시 묻지 않는다."""
    now = datetime.now(timezone.utc)
    subs, missing = {}, []
    for channel_id in set(channel_ids):
        hit = (cache or {}).get(f"subs:{channel_id}")
        if isinstance(hit, list) and len(hit) == 2:
            try:
                when = datetime.fromisoformat(hit[1])
            except ValueError:
                when = None
            if when and now - when < timedelta(days=SUBS_CACHE_DAYS):
                subs[channel_id] = int(hit[0])
                continue
        missing.append(channel_id)

    for chunk in _batched(missing):
        try:
            resp = requests.get(
                YT_CHANNELS_API,
                params={"part": "statistics", "id": ",".join(chunk), "key": key},
                timeout=TIMEOUT, headers={"User-Agent": USER_AGENT},
            )
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as e:
            print(f"[collect] 구독자 수 확인 실패: {e}")
            _note(problems, "구독자 확인", "channels.list 요청 실패")
            return subs, False
        for entry in data.get("items") or []:
            # 구독자 수를 숨긴 채널은 0 이 아니라 '모름'이다. 걸러내지 않는다.
            if (entry.get("statistics") or {}).get("hiddenSubscriberCount"):
                continue
            count = _stat(entry, "subscriberCount")
            subs[entry["id"]] = count
            if cache is not None:
                cache[f"subs:{entry['id']}"] = [count, now.isoformat()]
    return subs, True


def filter_youtube(items, config, key, cache, problems=None):
    """구독자·조회수가 기준에 못 미치는 영상을 뺀다.

    확인하지 못한 값(요청 실패, 구독자 비공개)은 통과시킨다. 모르는 것을
    이유로 버리면 API 가 한 번 흔들릴 때 영상이 통째로 사라진다.
    """
    limits = config.get("youtube_filter") or {}
    min_subs = int(limits.get("min_subscribers", 0) or 0)
    min_views = int(limits.get("min_views", 0) or 0)
    if not (min_subs or min_views) or not key:
        return items

    videos = [i for i in items if i.kind == "video" and _video_id(i)]
    if not videos:
        return items

    views = video_views([_video_id(i) for i in videos], key, problems) if min_views else {}
    subs, subs_ok = ({}, True)
    if min_subs:
        subs, subs_ok = channel_subscribers(
            [i.channel_id for i in videos if i.channel_id], key, cache, problems
        )

    kept, by_views, by_subs = [], 0, 0
    for item in items:
        if item.kind != "video" or not _video_id(item):
            kept.append(item)
            continue
        seen_views = views.get(_video_id(item))
        if min_views and seen_views is not None and seen_views < min_views:
            by_views += 1
            continue
        seen_subs = subs.get(item.channel_id)
        if min_subs and subs_ok and seen_subs is not None and seen_subs < min_subs:
            by_subs += 1
            continue
        kept.append(item)

    if by_views or by_subs:
        print(f"[collect] 기준 미달 영상 제외 {by_views + by_subs}건 "
              f"(조회수 {min_views:,} 미만 {by_views} · 구독자 {min_subs:,} 미만 {by_subs})")
    return kept


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

    window = _fresh_window(hours)
    items = fetch(f"{src['query']} {window}")
    if items:
        return items
    print(f"[collect] '{name}' {window} 로는 결과가 없어 기간 제한 없이 다시 찾습니다.")
    return fetch(src["query"])


def collect(config, channel_cache):
    """설정에 있는 모든 소스에서 항목을 모아 lookback 기간 내 것만 반환."""
    sources = config.get("sources") or {}
    items = []
    problems = []

    lookback = config.get("lookback_hours", 36)
    for src in sources.get("google_news") or []:
        items += _tag(_collect_google_news(src, lookback, problems), src.get("tags"))

    for src in sources.get("rss") or []:
        items += _tag(
            _parse_feed(src["url"], src["name"], "article", problems), src.get("tags")
        )

    naver_id = os.environ.get("NAVER_CLIENT_ID")
    naver_secret = os.environ.get("NAVER_CLIENT_SECRET")
    naver_sources = sources.get("naver_blog") or []
    if naver_sources and not (naver_id and naver_secret):
        print("[collect] NAVER_CLIENT_ID/SECRET 이 없어 블로그 검색을 건너뜁니다.")
    for src in naver_sources if (naver_id and naver_secret) else []:
        found = search_naver_blog(
            src["query"], src.get("name") or src["query"], naver_id, naver_secret,
            sort=src.get("sort", "date"), display=src.get("display", 20),
            problems=problems,
        )
        items += _tag(found, src.get("tags"))

    api_key = os.environ.get("YOUTUBE_API_KEY")
    if (sources.get("youtube_search") or []) and not api_key:
        print("[collect] YOUTUBE_API_KEY 가 없어 영상 검색을 건너뜁니다.")
    for src in sources.get("youtube_search") or [] if api_key else []:
        found = search_videos(
            src["query"], src.get("name") or src["query"], api_key,
            hours=config.get("lookback_hours", 36),
            order=src.get("order", "date"),
            lang=src.get("lang"),
            problems=problems,
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

    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback)
    fresh = [i for i in items if i.published >= cutoff]
    # 기간을 좁힌 뒤에 확인한다. 어차피 버릴 것까지 물어볼 이유가 없다.
    fresh = filter_youtube(fresh, config, api_key, channel_cache, problems)
    # 종류별로 나눠 찍는다. 합계만 보면 기사가 통째로 잘려도 눈에 띄지 않는다.
    articles = sum(1 for i in fresh if i.kind == "article")
    print(f"[collect] 전체 {len(items)}건 중 최근 {len(fresh)}건 "
          f"(기사 {articles} · 영상 {len(fresh) - articles}, 최근 {lookback}시간)")
    return fresh
