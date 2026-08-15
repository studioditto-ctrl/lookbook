"""구독 채널 목록을 뽑아 러닝·운동 관련 채널을 골라낸다.

구독 목록은 기본이 비공개다. 비공개면 OAuth 사용자 인증이 필요해 API 키로는
읽을 수 없다. YouTube 설정에서 '모든 구독 정보 공개'로 바꾸면 API 키만으로
조회된다 — 확인이 끝나면 다시 비공개로 되돌리면 된다.

    python scripts/list_subscriptions.py @핸들
"""

import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from collect import USER_AGENT, resolve_channel_id, search_channel_id  # noqa: E402

SUBS_API = "https://www.googleapis.com/youtube/v3/subscriptions"

# 이 중 하나라도 채널 이름이나 설명에 있으면 후보로 표시한다.
KEYWORDS = [
    "러닝", "런닝", "달리기", "마라톤", "런", "run", "running", "marathon",
    "트레일", "trail", "조깅", "jogging", "페이스", "pace",
    "운동", "헬스", "workout", "fitness", "training", "트레이닝",
    "크로스핏", "crossfit", "요가", "yoga", "스트레칭", "재활",
]


def fetch_subscriptions(channel_id, key):
    subs, page = [], None
    while True:
        params = {
            "part": "snippet", "channelId": channel_id,
            "maxResults": 50, "key": key,
        }
        if page:
            params["pageToken"] = page
        resp = requests.get(
            SUBS_API, params=params, timeout=20, headers={"User-Agent": USER_AGENT}
        )
        if resp.status_code == 403:
            print("구독 목록이 비공개입니다. YouTube 설정 → 개인정보 보호에서")
            print("'모든 구독 정보 비공개' 를 끄고 다시 실행하세요.", file=sys.stderr)
            return None
        resp.raise_for_status()
        data = resp.json()
        subs += data.get("items") or []
        page = data.get("nextPageToken")
        if not page:
            return subs


def looks_relevant(title, description):
    text = f"{title} {description}".lower()
    return [k for k in KEYWORDS if k.lower() in text]


def main():
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        print("YOUTUBE_API_KEY 가 필요합니다.", file=sys.stderr)
        return 1
    if len(sys.argv) < 2:
        print("사용법: python scripts/list_subscriptions.py @핸들", file=sys.stderr)
        return 1

    target = sys.argv[1].strip()
    cache = {}
    if target.startswith("UC") and len(target) == 24:
        channel_id = target
    elif target.startswith("http"):
        channel_id = resolve_channel_id(target, cache)
    else:
        handle = target.lstrip("@")
        channel_id = resolve_channel_id(f"https://www.youtube.com/@{handle}", cache)
        if not channel_id:
            channel_id = search_channel_id(handle, cache, key)
    if not channel_id:
        print(f"'{target}' 의 channel_id 를 찾지 못했습니다.", file=sys.stderr)
        return 1

    print(f"대상 채널: {channel_id}\n")
    subs = fetch_subscriptions(channel_id, key)
    if subs is None:
        return 1

    matched, others = [], []
    for entry in subs:
        snippet = entry.get("snippet") or {}
        title = snippet.get("title", "?")
        cid = ((snippet.get("resourceId") or {}).get("channelId")) or "?"
        hits = looks_relevant(title, snippet.get("description", ""))
        (matched if hits else others).append((title, cid, hits))

    print(f"구독 {len(subs)}개 중 러닝·운동 후보 {len(matched)}개\n")
    print("=== 후보 (config.yaml 에 넣을 채널) ===")
    for title, cid, hits in matched:
        print(f"    - name: {title}")
        print(f"      channel_id: {cid}    # 매칭: {', '.join(hits[:3])}")
    print(f"\n=== 나머지 {len(others)}개 ===")
    for title, cid, _ in others:
        print(f"  {title}  ({cid})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
