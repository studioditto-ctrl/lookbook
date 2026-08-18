"""어드민 페이지가 저장하는 settings.yaml 을 읽어 config 위에 덮는다.

채널 목록은 config*.yaml 에 두고, 자주 바뀌는 값(발송 시간·항목 수·키워드·
제외어)만 여기서 관리한다. 페이지가 파일을 통째로 덮어쓰기 때문에 주석이
많은 config 를 건드리지 않아도 된다.
"""

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

REPO = Path(__file__).resolve().parent.parent
SETTINGS_PATH = REPO / "settings.yaml"

# 예정 시각을 이만큼 넘겨서 실행되면 그 회차는 건너뛴다.
# 크론이 밀리거나 실행이 걸러졌을 때, 한참 지난 회차를 밤중에 보내지 않기 위함.
MAX_LATE = timedelta(hours=3)

# config 파일 없이 페이지에서 만든 주제에 쓰이는 기본값
DEFAULTS = {
    "timezone": "Asia/Seoul",
    "lookback_hours": 48,
    "summary": {"enabled": True, "effort": "low"},
    "link_preview": {"mode": "first", "prefer": "video", "large": True, "above_text": True},
}


def load(path=None):
    path = Path(path) if path else SETTINGS_PATH
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def digest_key(digest):
    """상태 디렉터리 이름. config 파일이 없는 주제는 key 를 그대로 쓴다."""
    if digest.get("key"):
        return digest["key"]
    config = digest.get("config") or ""
    stem = Path(config).stem
    return stem.split(".", 1)[1] if "." in stem else None


def find(settings, digest_id):
    """config 파일명 또는 key 로 다이제스트를 찾는다."""
    for digest in settings.get("digests") or []:
        if digest.get("config") == digest_id or digest.get("key") == digest_id:
            return digest
    return None


def for_slot(settings, config_name, slot_name):
    """해당 다이제스트/슬롯의 설정을 찾는다. 없으면 None."""
    for digest in settings.get("digests") or []:
        if digest.get("config") != config_name and digest.get("key") != config_name:
            continue
        for slot in digest.get("slots") or []:
            if slot.get("slot") == slot_name:
                return digest, slot
    return None, None


def apply(config, settings, config_name, slot_name):
    """config 딕셔너리에 settings 값을 덮어쓴 사본을 돌려준다."""
    digest, slot = for_slot(settings, config_name, slot_name)
    if slot is None:
        return config

    merged = dict(config)
    slots = {k: dict(v) for k, v in (config.get("slots") or {}).items()}
    target = slots.setdefault(slot_name, {})
    for key in ("title", "articles", "videos"):
        if slot.get(key) is not None:
            target[key] = slot[key]
    merged["slots"] = slots

    if digest.get("keywords"):
        merged["keywords"] = digest["keywords"]
    if settings.get("exclude"):
        merged["exclude"] = settings["exclude"]

    # 어드민 페이지가 추가한 소스를 config 의 목록 뒤에 붙인다.
    # 검색어 하나는 뉴스와 유튜브 양쪽에 건다 — 구독 채널이 없는 새 주제도
    # 영상이 들어와야 텔레그램 썸네일이 뜬다.
    sources = {k: list(v or []) for k, v in (config.get("sources") or {}).items()}
    for query in digest.get("queries") or []:
        name = query.get("name") if isinstance(query, dict) else query
        text = query.get("query") if isinstance(query, dict) else query
        tags = query.get("tags") if isinstance(query, dict) else None
        entry = {"name": name, "query": text, "lang": "ko", "country": "KR"}
        if tags:
            entry["tags"] = tags
        sources.setdefault("google_news", []).append(entry)

        if isinstance(query, dict) and query.get("youtube") is False:
            continue
        video = {"name": name, "query": text}
        if tags:
            video["tags"] = tags
        sources.setdefault("youtube_search", []).append(video)
    for channel in digest.get("channels") or []:
        sources.setdefault("youtube", []).append(dict(channel))
    merged["sources"] = sources

    for key in ("timezone", "lookback_hours", "summary", "link_preview"):
        if key not in merged and key in DEFAULTS:
            merged[key] = DEFAULTS[key]
    return merged


def due(settings, now=None, state=None):
    """지금 보내야 할 (config, slot) 목록.

    같은 날 이미 보낸 회차는 건너뛴다 — 크론이 밀려도 중복 발송되지 않고,
    한 번 걸러져도 다음 실행에서 따라잡는다.
    """
    tz = ZoneInfo("Asia/Seoul")
    now = now or datetime.now(tz)
    state = state or {}
    today = now.date().isoformat()

    ready = []
    for digest in settings.get("digests") or []:
        config_name = digest.get("config")
        for slot in digest.get("slots") or []:
            if not slot.get("enabled", True):
                continue
            send_at = slot.get("send_at")
            if not send_at:
                continue
            try:
                hour, minute = (int(x) for x in send_at.split(":"))
            except (ValueError, AttributeError):
                print(f"[settings] '{send_at}' 시각을 읽을 수 없습니다. 건너뜁니다.")
                continue

            scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if now < scheduled or now - scheduled > MAX_LATE:
                continue
            key = f"{config_name or digest.get('key')}:{slot['slot']}"
            if state.get(key) == today:
                continue
            ready.append((config_name or digest.get("key"), slot["slot"], key, today))
    return ready
