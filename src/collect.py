"""RSS·유튜브 수집.

유튜브는 채널 RSS(`/feeds/videos.xml`)를 쓰기 때문에 Data API 키가 필요 없다.
채널당 최근 15개 영상을 주므로 하루 2회 폴링이면 놓치는 영상이 없다.
"""

import html
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
    summary: str = ""      # 피드에서 가져온 원문 발췌
    summary_ko: str = ""   # 한국어 요약 (요약 단계에서 채움)
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


def _note(problems, source_name, reason):
    if problems is not None:
        problems.append((source_name, reason))


def _parse_feed(url, source_name, kind, problems=None):
    """피드 하나를 항목 리스트로. 실패해도 예외를 올리지 않는다."""
    try:
        resp = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
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


_CHANNEL_ID_RE = re.compile(r'"(?:channelId|externalId)":"(UC[\w-]{22})"')


def resolve_channel_id(url, cache, problems=None, source_name=None):
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
        _note(problems, source_name or url, "채널 주소를 열 수 없음 (핸들이 바뀌었을 수 있음)")
        return None

    match = _CHANNEL_ID_RE.search(resp.text)
    if not match:
        print(f"[collect] 채널 ID를 페이지에서 찾지 못했습니다: {url}")
        _note(problems, source_name or url, "페이지에서 channel_id 를 찾지 못함")
        return None

    cache[url] = match.group(1)
    return cache[url]


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
        items += _parse_feed(url, src["name"], "article", problems)

    for src in sources.get("rss") or []:
        items += _parse_feed(src["url"], src["name"], "article", problems)

    for src in sources.get("youtube") or []:
        name = src.get("name", "?")
        channel_id = src.get("channel_id")
        if not channel_id and src.get("url"):
            channel_id = resolve_channel_id(src["url"], channel_cache, problems, name)
        if not channel_id:
            print(f"[collect] '{name}' 채널을 건너뜁니다 (ID 없음)")
            continue
        items += _parse_feed(
            YT_FEED.format(channel_id=channel_id), src["name"], "video", problems
        )

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
