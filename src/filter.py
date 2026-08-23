"""중복 제거 · 점수 매기기 · 슬롯별 선별."""

import hashlib
import re
from datetime import datetime, timezone

# 같은 사건을 여러 매체가 쓰면 URL 도 id 도 달라서 그냥은 걸러지지 않는다.
# 제목을 정규화한 지문으로 한 번 더 접는다.
#
#   "서울마라톤 접수 시작 - 연합뉴스"  ->  매체명을 떼고
#   "서울마라톤 접수 시작"             ->  두 글자 이상 낱말만 남겨
#   {마라톤, 서울마라톤, 시작, 접수}    ->  정렬해 해시
#
# 완전히 같은 지문은 버리고, 많이 겹치면(자카드 0.7 이상) 점수 높은 쪽만 남긴다.
_PUBLISHER_TAIL = re.compile(r"\s[-–—|]\s[^-–—|]{1,40}$")
_NOT_WORD = re.compile(r"[^0-9a-z가-힣]+")
SIMILAR_ENOUGH = 0.7


def title_words(title):
    text = _PUBLISHER_TAIL.sub("", title or "").lower()
    return {w for w in _NOT_WORD.split(text) if len(w) >= 2}


def fingerprint(title):
    """발송 이력에 남길 제목 지문. 없으면 빈 문자열."""
    words = title_words(title)
    if not words:
        return ""
    joined = " ".join(sorted(words))
    return "t:" + hashlib.sha1(joined.encode("utf-8")).hexdigest()[:16]


def _overlap(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


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


def _has(haystack, word):
    """낱말이 여럿인 말은 붙어 있지 않아도 다 나오면 걸린 것으로 본다.

    '남성 피부' 를 문구 그대로 요구하면 '남성의 피부 관리' 가 떨어진다.
    """
    parts = [part for part in str(word).lower().split() if part]
    return bool(parts) and all(part in haystack for part in parts)


def _relevant(item, keywords, scope=()):
    """검색으로 찾아온 항목이 이 주제 이야기인지.

    구글 뉴스도 유튜브도 검색어를 느슨하게 해석해 상관없는 것을 섞어 준다.
    주제어(scope)가 있으면 그것만 본다 — '훈련·기록·페이스' 같은 키워드는
    한미연합훈련·기록적 폭염·페이스북 기사에도 그대로 들어 있어서,
    키워드로 거르면 정치 기사가 러닝 회차에 실려 나간다. 키워드는 점수를
    매기는 데 쓰고, 주제에 속하는지는 주제어로 판단한다.

    제목뿐 아니라 본문 발췌도 본다. 제목만 보면 '이번 주 정리' 같은
    맹숭한 제목의 알맹이 있는 글까지 떨어진다.
    """
    haystack = f"{item.title} {item.summary or ''}".lower()
    if scope:
        return any(_has(haystack, word) for word in scope)
    if not keywords:
        return True
    return any(_has(haystack, word) for word in keywords)


def _excluded(item, patterns):
    title = item.title.lower()
    return any(p.lower() in title for p in patterns)


def _fold_similar(items):
    """제목이 사실상 같은 것들을 한 건으로 줄인다. 점수 높은 쪽을 남긴다."""
    kept, kept_words = [], []
    dropped = 0
    for item in sorted(items, key=lambda i: (-i.score, -i.published.timestamp())):
        words = title_words(item.title)
        if any(_overlap(words, other) >= SIMILAR_ENOUGH for other in kept_words):
            dropped += 1
            continue
        kept.append(item)
        kept_words.append(words)
    return kept, dropped


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

    # 슬롯에 tags 가 있으면 그 분류의 소스만 쓴다.
    # 같은 채널 묶음에서 시간대별로 다른 주제를 보낼 때 쓴다.
    wanted = set(slot_config.get("tags") or [])

    # 검색으로 찾아온 항목에만 적용한다. 사람이 골라둔 채널·피드는
    # 채널을 믿고 담는 것이라 낱말이 안 걸려도 남긴다.
    require_keyword = config.get("require_keyword", True)
    scope = config.get("scope") or []

    fresh = []
    repeats = 0
    off_topic = 0
    for item in items:
        if item.id in seen:
            continue
        # 어제 다른 매체로 나간 같은 사건을 오늘 또 보내지 않는다
        if fingerprint(item.title) in seen:
            repeats += 1
            continue
        if wanted and not (wanted & set(item.tags)):
            continue
        if _excluded(item, exclude):
            continue
        if require_keyword and item.searched and not _relevant(item, keywords, scope):
            off_topic += 1
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

    if off_topic:
        print(f"[filter] 주제와 안 맞아 제외 {off_topic}건 (검색으로 찾아온 것 중)")

    fresh, folded = _fold_similar(fresh)
    if repeats or folded:
        print(f"[filter] 겹치는 항목 제외 {repeats + folded}건 "
              f"(지난 발송과 {repeats} · 이번 회차 안에서 {folded})")

    articles = _pick(
        [i for i in fresh if i.kind == "article"], slot_config.get("articles", 3)
    )
    videos = _pick(
        [i for i in fresh if i.kind == "video"], slot_config.get("videos", 2)
    )

    print(f"[filter] 후보 {len(fresh)}건 → 기사 {len(articles)}건, 영상 {len(videos)}건")
    return articles, videos
