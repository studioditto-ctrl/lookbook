"""소스를 모아 한 묶음으로 돌려준다.

세부 구현은 갈라 두었다.
    feeds.py    항목 모양·HTTP·피드 파싱
    youtube.py  채널·검색·구독자와 조회수 기준
    news.py     구글 뉴스·네이버 블로그

바깥에서 쓰던 이름은 여기서 그대로 다시 내보낸다.
"""

import os
from datetime import datetime, timedelta, timezone

from feeds import (  # noqa: F401
    Item, TIMEOUT, TRACKING_PARAMS, USER_AGENT,
    _canonical_url, _clean_title, _get_with_retry, _note, _parse_feed, _plain,
    _published, _snippet, _tag,
)
import news
import youtube

from news import (  # noqa: F401
    GNEWS_FEED, KST, NAVER_BLOG_API, NAVER_ERRORS, NAVER_SCOPES_EMPTY, NAVER_SORTS,
    _collect_google_news, _fresh_window, _naver_error, search_naver_blog,
)
from youtube import (  # noqa: F401
    SEARCH_ORDERS, SUBS_CACHE_DAYS, YT_API, YT_API_MAX, YT_BATCH, YT_CHANNELS_API,
    YT_FEED, YT_SEARCH_API, YT_VIDEOS_API,
    _batched, _channel_id_from_page, _collect_via_api, _stat, _uploads_playlist,
    _video_id, channel_subscribers, filter_youtube, resolve_channel_id,
    search_channel_id, search_videos, video_views,
)

def collect(config, channel_cache):
    """설정에 있는 모든 소스에서 항목을 모아 lookback 기간 내 것만 반환."""
    sources = config.get("sources") or {}
    items = []
    problems = []

    lookback = config.get("lookback_hours", 36)
    for src in sources.get("google_news") or []:
        items += _tag(news._collect_google_news(src, lookback, problems), src.get("tags"))

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
        found = news.search_naver_blog(
            src["query"], src.get("name") or src["query"], naver_id, naver_secret,
            sort=src.get("sort", "date"), display=src.get("display", 20),
            problems=problems,
        )
        items += _tag(found, src.get("tags"))

    api_key = os.environ.get("YOUTUBE_API_KEY")
    if (sources.get("youtube_search") or []) and not api_key:
        print("[collect] YOUTUBE_API_KEY 가 없어 영상 검색을 건너뜁니다.")
    for src in sources.get("youtube_search") or [] if api_key else []:
        found = youtube.search_videos(
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
            channel_id = youtube.resolve_channel_id(src["url"], channel_cache, problems, name)
        # url 도 channel_id 도 없으면 이름(또는 search)으로 찾는다. API 키가 필요하다.
        if not channel_id and api_key:
            channel_id = youtube.search_channel_id(
                src.get("search") or name, channel_cache, api_key, problems, name
            )
        if not channel_id:
            print(f"[collect] '{name}' 채널을 건너뜁니다 (ID 없음)")
            continue
        if api_key:
            fetched = youtube._collect_via_api(channel_id, src["name"], api_key, problems)
        else:
            fetched = _parse_feed(
                YT_FEED.format(channel_id=channel_id), src["name"], "video", problems
            )
        # 사람이 골라 적어둔 채널이다. 구독자 수로 되묻지 않는다.
        for item in fetched:
            item.trusted = True
        items += _tag(fetched, src.get("tags"))

    if problems:
        print("\n[collect] 문제가 있는 소스 (config.yaml 에서 고치거나 지우세요):")
        for source_name, reason in problems:
            print(f"  - {source_name}: {reason}")
        print()

    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback)
    fresh = [i for i in items if i.published >= cutoff]
    # 기간을 좁힌 뒤에 확인한다. 어차피 버릴 것까지 물어볼 이유가 없다.
    fresh = youtube.filter_youtube(fresh, config, api_key, channel_cache, problems)
    # 종류별로 나눠 찍는다. 합계만 보면 기사가 통째로 잘려도 눈에 띄지 않는다.
    articles = sum(1 for i in fresh if i.kind == "article")
    print(f"[collect] 전체 {len(items)}건 중 최근 {len(fresh)}건 "
          f"(기사 {articles} · 영상 {len(fresh) - articles}, 최근 {lookback}시간)")
    return fresh
