"""유튜브 — 채널 업로드, 키워드 검색, 구독자·조회수 기준."""

import re
from datetime import datetime, timedelta, timezone

import requests

from feeds import (  # noqa: F401
    Item, TIMEOUT, USER_AGENT, _clean_title, _get_with_retry, _note, _parse_feed,
)

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
                searched=True,
            )
        )
    print(f"[collect] '{source_name}' 영상 검색 {len(items)}건 ({order})")
    return items


# 024 는 원인이 둘이다. 본문을 봐야 갈린다.

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
    # 구독자 수는 검색으로 들어온 모르는 채널에만 묻는다. 직접 적어둔 채널은
    # 크기를 따지려고 고른 게 아니다.
    unknown = [i for i in videos if not i.trusted and i.channel_id]
    subs, subs_ok = ({}, True)
    if min_subs and unknown:
        subs, subs_ok = channel_subscribers(
            [i.channel_id for i in unknown], key, cache, problems
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
        if (min_subs and not item.trusted and subs_ok
                and seen_subs is not None and seen_subs < min_subs):
            by_subs += 1
            continue
        kept.append(item)

    if by_views or by_subs:
        print(f"[collect] 기준 미달 영상 제외 {by_views + by_subs}건 "
              f"(조회수 {min_views:,} 미만 {by_views} · "
              f"구독자 {min_subs:,} 미만 {by_subs}, 직접 고른 채널은 제외 안 함)")
    return kept
