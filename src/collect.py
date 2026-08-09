"""RSS·유튜브 수집.

유튜브는 채널 RSS(`/feeds/videos.xml`)를 쓰기 때문에 Data API 키가 필요 없다.
채널당 최근 15개 영상을 주므로 하루 2회 폴링이면 놓치는 영상이 없다.
"""

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


def _clean_title(title):
    title = re.sub(r"\s+", " ", title or "").strip()
    # 구글 뉴스는 제목 뒤에 " - 매체명"을 붙인다. 출처는 따로 표시하므로 떼어낸다.
    return re.sub(r"\s+-\s+[^-]{2,30}$", "", title)


def _parse_feed(url, source_name, kind):
    """피드 하나를 항목 리스트로. 실패해도 예외를 올리지 않는다."""
    try:
        resp = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[collect] '{source_name}' 피드 요청 실패: {e}")
        return []

    parsed = feedparser.parse(resp.content)
    if parsed.bozo and not parsed.entries:
        print(f"[collect] '{source_name}' 피드 파싱 실패: {parsed.bozo_exception}")
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
            )
        )
    print(f"[collect] '{source_name}' {len(items)}건")
    return items


_CHANNEL_ID_RE = re.compile(r'"(?:channelId|externalId)":"(UC[\w-]{22})"')


def resolve_channel_id(url, cache):
    """유튜브 채널 주소(@핸들 포함)를 channel_id 로 바꾼다. 결과는 캐시한다."""
    if url in cache:
        return cache[url]

    match = re.search(r"/channel/(UC[\w-]{22})", url)
    if match:
        cache[url] = match.group(1)
        return cache[url]

    try:
        resp = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[collect] 채널 ID 확인 실패 ({url}): {e}")
        return None

    match = _CHANNEL_ID_RE.search(resp.text)
    if not match:
        print(f"[collect] 채널 ID를 페이지에서 찾지 못했습니다: {url}")
        return None

    cache[url] = match.group(1)
    return cache[url]


def collect(config, channel_cache):
    """설정에 있는 모든 소스에서 항목을 모아 lookback 기간 내 것만 반환."""
    sources = config.get("sources") or {}
    items = []

    for src in sources.get("google_news") or []:
        url = GNEWS_FEED.format(
            query=quote_plus(src["query"]),
            lang=src.get("lang", "ko"),
            country=src.get("country", "KR"),
        )
        items += _parse_feed(url, src["name"], "article")

    for src in sources.get("rss") or []:
        items += _parse_feed(src["url"], src["name"], "article")

    for src in sources.get("youtube") or []:
        channel_id = src.get("channel_id")
        if not channel_id and src.get("url"):
            channel_id = resolve_channel_id(src["url"], channel_cache)
        if not channel_id:
            print(f"[collect] '{src.get('name', '?')}' 채널을 건너뜁니다 (ID 없음)")
            continue
        items += _parse_feed(YT_FEED.format(channel_id=channel_id), src["name"], "video")

    cutoff = datetime.now(timezone.utc) - timedelta(
        hours=config.get("lookback_hours", 36)
    )
    fresh = [i for i in items if i.published >= cutoff]
    print(f"[collect] 전체 {len(items)}건 중 최근 {len(fresh)}건")
    return fresh
