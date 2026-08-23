"""소스가 공유하는 뼈대 — 항목 모양, HTTP, 피드 파싱.

유튜브·뉴스·블로그가 모두 이 Item 으로 모인다.
"""

import html
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import feedparser
import requests

USER_AGENT = "running-digest/1.0 (+https://github.com/studioditto-ctrl/lookbook)"
TIMEOUT = 20

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
    trusted: bool = False  # 사람이 골라 config 에 적어둔 채널에서 온 것
    searched: bool = False # 검색으로 찾아온 것 (주제와 무관할 수 있다)
    score: float = 0.0


_TAG_RE = re.compile(r"<[^>]+>")

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
    trusted: bool = False  # 사람이 골라 config 에 적어둔 채널에서 온 것
    searched: bool = False # 검색으로 찾아온 것 (주제와 무관할 수 있다)
    score: float = 0.0

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
