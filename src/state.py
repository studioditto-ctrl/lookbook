"""발송 이력과 채널 ID 캐시. 레포의 state/ 디렉터리에 JSON으로 보관한다."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATE_DIR = Path(__file__).resolve().parent.parent / "state"


def _dir(namespace=None):
    """다이제스트별 상태 디렉터리. 기본(러닝)은 state/ 를 그대로 쓴다."""
    return STATE_DIR / namespace if namespace else STATE_DIR


def seen_path(namespace=None):
    return _dir(namespace) / "seen.json"


def channels_path(namespace=None):
    return _dir(namespace) / "channels.json"

# 이 기간이 지난 발송 이력은 버린다. 파일이 무한히 커지는 것을 막는다.
RETENTION_DAYS = 90


def _load(path, default):
    if not path.exists():
        return default
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[state] {path.name} 읽기 실패, 초기화합니다: {e}")
        return default


def _save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def load_seen(namespace=None):
    """{item_id: ISO8601 발송시각} 형태로 반환."""
    data = _load(seen_path(namespace), {})
    return data if isinstance(data, dict) else {}


def save_seen(seen, namespace=None):
    """오래된 기록을 정리한 뒤 저장."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    kept = {}
    for item_id, sent_at in seen.items():
        try:
            if datetime.fromisoformat(sent_at) >= cutoff:
                kept[item_id] = sent_at
        except (TypeError, ValueError):
            # 형식이 깨진 항목은 버린다
            continue
    _save(seen_path(namespace), kept)
    return kept


def mark_sent(seen, items):
    now = datetime.now(timezone.utc).isoformat()
    for item in items:
        seen[item.id] = now
    return seen


def load_channel_cache(namespace=None):
    data = _load(channels_path(namespace), {})
    return data if isinstance(data, dict) else {}


def save_channel_cache(cache, namespace=None):
    _save(channels_path(namespace), cache)
