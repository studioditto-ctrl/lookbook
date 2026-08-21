"""주제로 인기 유튜브 채널을 찾아 config 에 붙일 형태로 출력한다.

채널 목록만 보면 이미 아는 채널에서만 영상이 온다. 여기서 찾은 채널을
config*.yaml 의 sources.youtube 에 옮기면 목록 자체가 넓어진다.
(영상 단위 확장은 sources.youtube_search 가 이미 하고 있다.)

    python scripts/discover_channels.py 러닝 마라톤
    python scripts/discover_channels.py --all        # settings.yaml 의 모든 주제

아무것도 전송하지 않고 로그에만 출력한다. 무엇을 넣을지는 사람이 고른다.

쿼터: 검색어당 search 100 유닛 + channels 1 유닛. 일일 10,000 안에서
한 번에 여남은 개는 넉넉하다.
"""

import argparse
import os
import sys
from pathlib import Path

import requests
import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from collect import USER_AGENT, YT_SEARCH_API, TIMEOUT  # noqa: E402

CHANNELS_API = "https://www.googleapis.com/youtube/v3/channels"

# 이보다 적으면 후보에서 뺀다. 하루 두 번 보내는 다이제스트에는
# 꾸준히 올리는 채널이 맞다.
MIN_SUBSCRIBERS = 20_000
MIN_VIDEOS = 30


def _get(url, params):
    resp = requests.get(
        url, params=params, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}
    )
    resp.raise_for_status()
    return resp.json()


def search_channels(query, key, limit=25):
    """검색어로 채널 후보의 id 를 모은다."""
    data = _get(YT_SEARCH_API, {
        "part": "snippet", "type": "channel", "q": query,
        "maxResults": min(limit, 50), "key": key,
    })
    return [
        (entry.get("id") or {}).get("channelId")
        for entry in data.get("items") or []
        if (entry.get("id") or {}).get("channelId")
    ]


def channel_details(ids, key):
    """구독자 수·영상 수를 붙인다. 50개까지 한 번에 묻는다 (1 유닛)."""
    out = []
    for start in range(0, len(ids), 50):
        data = _get(CHANNELS_API, {
            "part": "snippet,statistics",
            "id": ",".join(ids[start:start + 50]),
            "key": key,
        })
        for entry in data.get("items") or []:
            stats = entry.get("statistics") or {}
            if stats.get("hiddenSubscriberCount"):
                continue
            out.append({
                "id": entry["id"],
                "name": (entry.get("snippet") or {}).get("title", "?"),
                "subs": int(stats.get("subscriberCount", 0)),
                "videos": int(stats.get("videoCount", 0)),
            })
    return out


def known_channel_ids():
    """이미 쓰고 있는 채널. config*.yaml 과 settings.yaml 을 모두 본다."""
    known = set()
    for path in list(REPO.glob("config*.yaml")) + [REPO / "settings.yaml"]:
        if not path.exists():
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for src in ((data.get("sources") or {}).get("youtube") or []):
            if src.get("channel_id"):
                known.add(src["channel_id"])
        for digest in data.get("digests") or []:
            for src in digest.get("channels") or []:
                if src.get("channel_id"):
                    known.add(src["channel_id"])
    return known


def topics_from_settings():
    """settings.yaml 의 주제 이름과 검색어를 검색어로 쓴다."""
    path = REPO / "settings.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    topics = []
    for digest in data.get("digests") or []:
        queries = [q.get("query") if isinstance(q, dict) else q
                   for q in digest.get("queries") or []]
        topics.append((digest.get("label") or "?", queries or [digest.get("label")]))
    return topics


def report(label, queries, key, known):
    print(f"\n=== {label} ===")
    found = {}
    for query in queries:
        if not query:
            continue
        try:
            ids = search_channels(query, key)
        except (requests.RequestException, ValueError) as e:
            print(f"  '{query}' 검색 실패: {e}")
            continue
        for channel in channel_details(ids, key):
            found[channel["id"]] = channel

    fresh = [
        c for c in found.values()
        if c["id"] not in known
        and c["subs"] >= MIN_SUBSCRIBERS
        and c["videos"] >= MIN_VIDEOS
    ]
    fresh.sort(key=lambda c: -c["subs"])

    skipped = len(found) - len(fresh)
    if not fresh:
        print(f"  새로 넣을 만한 채널이 없습니다. (후보 {len(found)}개 검토)")
        return
    print(f"  후보 {len(found)}개 중 {len(fresh)}개 "
          f"(이미 쓰는 채널·구독자 {MIN_SUBSCRIBERS:,} 미만 등 {skipped}개 제외)")
    print("  아래를 config 의 sources.youtube 에 붙여넣으세요:\n")
    for c in fresh[:15]:
        print(f"    - name: {c['name']}")
        print(f"      channel_id: {c['id']}   # 구독자 {c['subs']:,} · 영상 {c['videos']:,}")


def main():
    parser = argparse.ArgumentParser(description="주제로 인기 유튜브 채널 찾기")
    parser.add_argument("queries", nargs="*", help="검색어")
    parser.add_argument("--all", action="store_true",
                        help="settings.yaml 의 모든 주제로 찾는다")
    args = parser.parse_args()

    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        print("YOUTUBE_API_KEY 가 없습니다.", file=sys.stderr)
        return 1
    if not args.queries and not args.all:
        parser.print_help()
        return 1

    known = known_channel_ids()
    print(f"이미 쓰고 있는 채널 {len(known)}개는 빼고 보여줍니다.")

    if args.all:
        for label, queries in topics_from_settings():
            report(label, queries, key, known)
    else:
        report(" ".join(args.queries), args.queries, key, known)

    print("\n전송은 하지 않았습니다. 넣을 채널만 골라 config 에 옮기세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
