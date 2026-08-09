"""중복 제거 · 점수 매기기 · 슬롯별 선별."""

from datetime import datetime, timezone


def _score(item, keywords):
    """제목 키워드 가중치 + 최신성 보너스."""
    title = item.title.lower()
    score = sum(weight for word, weight in keywords.items() if word.lower() in title)

    age_hours = (datetime.now(timezone.utc) - item.published).total_seconds() / 3600
    if age_hours < 6:
        score += 2
    elif age_hours < 18:
        score += 1

    return score


def _excluded(item, patterns):
    title = item.title.lower()
    return any(p.lower() in title for p in patterns)


def _pick(candidates, limit):
    """점수 높은 순으로 뽑되, 한 출처가 연달아 독식하지 않게 분산한다."""
    picked, used = [], {}
    pool = sorted(candidates, key=lambda i: (-i.score, -i.published.timestamp()))

    # 1순위: 출처당 1건씩
    for item in pool:
        if len(picked) >= limit:
            break
        if used.get(item.source):
            continue
        picked.append(item)
        used[item.source] = 1

    # 자리가 남으면 점수순으로 채운다
    for item in pool:
        if len(picked) >= limit:
            break
        if item in picked:
            continue
        picked.append(item)

    return picked


def select(items, seen, config, slot):
    """발송할 항목을 고른다. (기사 리스트, 영상 리스트) 반환."""
    keywords = config.get("keywords") or {}
    exclude = config.get("exclude") or []
    slot_config = (config.get("slots") or {}).get(slot) or {}

    fresh = []
    for item in items:
        if item.id in seen:
            continue
        if _excluded(item, exclude):
            continue
        item.score = _score(item, keywords)
        fresh.append(item)

    # 같은 기사가 여러 소스에 잡히는 경우가 있어 URL 기준으로 한 번 더 접는다
    by_url = {}
    for item in fresh:
        existing = by_url.get(item.url)
        if existing is None or item.score > existing.score:
            by_url[item.url] = item
    fresh = list(by_url.values())

    articles = _pick(
        [i for i in fresh if i.kind == "article"], slot_config.get("articles", 3)
    )
    videos = _pick(
        [i for i in fresh if i.kind == "video"], slot_config.get("videos", 2)
    )

    print(f"[filter] 후보 {len(fresh)}건 → 기사 {len(articles)}건, 영상 {len(videos)}건")
    return articles, videos
