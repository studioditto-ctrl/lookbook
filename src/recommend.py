"""테마 하나로 소스 후보(유튜브·미디어/블로그)와 키워드를 추천한다.

Claude 에게는 채널·매체 '이름'만 제안하게 하고, 실존 여부는 여기서 따로
확인한다. 모델이 라이브 웹 검색을 쓰지 않으므로 이름을 지어낼 수 있다 —
유튜브는 YouTube Data API 로 채널을 찾지 못하면 후보에서 뺀다. 블로그/RSS
는 봇 차단으로 확인이 실패할 수 있어, 실패해도 버리지 않고 '확인 필요'로
표시만 한다.

어드민 페이지는 정적 GitHub Pages라 이 스크립트를 직접 부를 수 없다(키가
브라우저에 노출된다). repository_dispatch 로 이 스크립트를 돌리는 워크플로가
결과를 state/recommend/<slug>.json 에 커밋하면, 페이지는 그 파일이 생기길
기다렸다가 읽는다. 그래서 어떤 예외가 나도 결과 파일은 반드시 남겨야
페이지가 무한정 기다리지 않는다 — main() 은 넓게 예외를 잡는다.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

MODEL = "claude-opus-5"
MAX_TOKENS = 4000

REPO = Path(__file__).resolve().parent.parent
RECOMMEND_DIR = REPO / "state" / "recommend"

SYSTEM = """당신은 텔레그램 다이제스트 구독 설정을 돕는 추천 어시스턴트입니다.

사용자가 새 주제(테마)를 입력하면, 그 주제를 다루는 소스 후보를 최대 10개
제안합니다.

규칙:
- 후보의 kind 는 "youtube"(유튜브 채널) 또는 "blog"(뉴스매체·블로그·RSS)
  중 하나입니다.
- region 은 "domestic"(국내) 또는 "international"(해외)이며, 전체 후보를
  대략 절반씩 나눕니다.
- name 에는 실제로 존재한다고 확신하는 채널명/매체명만 씁니다. 실존 여부는
  이후 별도로 확인하니, 떠오르지 않으면 억지로 채우지 말고 후보 수를
  줄이십시오. 지어낸 이름을 자신 있게 제시하지 마십시오.
- kind 가 "blog" 인데 정확한 URL을 모르면 url 을 빈 문자열로 둡니다.
  틀린 URL을 지어내지 마십시오. kind 가 "youtube" 면 url 은 항상 빈
  문자열로 둡니다 (채널 검색은 이름으로 합니다).
- reason 은 이 후보가 왜 이 주제와 맞는지 한국어 1문장으로 씁니다.
- keywords 는 이 주제의 다이제스트를 걸러낼 때 쓸 낱말 6~10개,
  scope 는 검색어 앞에 항상 붙일, 주제를 가리키는 낱말 2~5개입니다
  (예: 주제가 "러닝"이면 scope 는 ["러닝", "달리기"] 같은 것)."""

SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "kind": {"type": "string", "enum": ["youtube", "blog"]},
                    "region": {"type": "string", "enum": ["domestic", "international"]},
                    "reason": {"type": "string"},
                    "url": {"type": "string"},
                },
                "required": ["name", "kind", "region", "reason", "url"],
                "additionalProperties": False,
            },
        },
        "keywords": {"type": "array", "items": {"type": "string"}},
        "scope": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["candidates", "keywords", "scope"],
    "additionalProperties": False,
}


def _request(client, theme, effort="low"):
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM,
        output_config={
            "effort": effort,
            "format": {"type": "json_schema", "schema": SCHEMA},
        },
        messages=[{"role": "user", "content": f"주제: {theme}"}],
    )

    if response.stop_reason == "refusal":
        raise RuntimeError("모델이 요청을 거부했습니다")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("응답이 max_tokens 에서 잘렸습니다")

    text = next((b.text for b in response.content if b.type == "text"), None)
    if not text:
        raise RuntimeError("응답에 텍스트 블록이 없습니다")
    return json.loads(text)


def _ask_claude(theme):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY 가 없습니다")

    import anthropic

    client = anthropic.Anthropic()
    try:
        return _request(client, theme)
    except anthropic.RateLimitError as e:
        raise RuntimeError(f"요청 한도 초과: {e}") from e
    except anthropic.APIConnectionError as e:
        raise RuntimeError(f"API 연결 실패: {e}") from e
    except anthropic.APIStatusError as e:
        raise RuntimeError(f"API 오류 {e.status_code}: {e.message}") from e


def _verify_youtube(name, key, cache):
    """채널 이름으로 실제 channel_id 를 찾는다. 못 찾으면 None."""
    from youtube import channel_subscribers, search_channel_id

    channel_id = search_channel_id(name, cache, key)
    if not channel_id:
        return None
    subs, _ok = channel_subscribers([channel_id], key, cache)
    return channel_id, subs.get(channel_id)


def _verify_feed(url):
    """RSS/Atom 으로 보이는지 최소한으로 확인한다. 실패해도 존재 안 함이 아니다."""
    import requests

    try:
        resp = requests.get(
            url, timeout=8, headers={"User-Agent": "Mozilla/5.0 (compatible; DigestBot/1.0)"}
        )
        resp.raise_for_status()
    except requests.RequestException:
        return False
    head = resp.text[:2000].lower()
    return "<rss" in head or "<feed" in head or head.lstrip().startswith("<?xml")


def recommend(theme, youtube_key=None, cache=None):
    """테마 -> 검증된 후보 목록. {"theme","candidates","keywords","scope","generated_at"}."""
    cache = cache if cache is not None else {}
    parsed = _ask_claude(theme)

    candidates = []
    for raw in (parsed.get("candidates") or [])[:20]:
        name = (raw.get("name") or "").strip()
        kind = raw.get("kind")
        region = raw.get("region")
        if not name or kind not in ("youtube", "blog") or region not in (
            "domestic", "international",
        ):
            continue
        reason = (raw.get("reason") or "").strip()
        url = (raw.get("url") or "").strip()

        if kind == "youtube":
            if not youtube_key:
                # 키가 없으면 검증할 수 없다 — 잘못 심을 위험을 감수하느니 뺀다.
                continue
            found = _verify_youtube(name, youtube_key, cache)
            if not found:
                print(f"[recommend] '{name}' 유튜브 채널을 찾지 못해 제외합니다.")
                continue
            channel_id, subs = found
            candidates.append({
                "name": name, "kind": kind, "region": region, "reason": reason,
                "channel_id": channel_id, "subscribers": subs, "verified": True,
            })
        else:
            verified = bool(url) and _verify_feed(url)
            candidates.append({
                "name": name, "kind": kind, "region": region, "reason": reason,
                "url": url, "verified": verified,
            })

    candidates = candidates[:10]
    return {
        "theme": theme,
        "candidates": candidates,
        "keywords": [str(w).strip() for w in (parsed.get("keywords") or []) if str(w).strip()],
        "scope": [str(w).strip() for w in (parsed.get("scope") or []) if str(w).strip()],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _write_result(slug, result):
    RECOMMEND_DIR.mkdir(parents=True, exist_ok=True)
    path = RECOMMEND_DIR / f"{slug}.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description="테마로 소스 추천 후보를 만든다")
    parser.add_argument("--theme", required=True)
    parser.add_argument("--slug", required=True)
    args = parser.parse_args(argv)

    # 페이지는 이 파일이 생기기를 기다린다. 어떤 예외가 나도 반드시 뭔가는
    # 써야 폴링이 영원히 끝나지 않는 사태를 막는다 — summarize.py 처럼 좁게
    # 잡지 않고 여기서만 의도적으로 넓게 잡는다.
    try:
        result = recommend(args.theme, youtube_key=os.environ.get("YOUTUBE_API_KEY"))
    except Exception as e:
        print(f"[recommend] 실패: {e}", file=sys.stderr)
        result = {
            "theme": args.theme,
            "error": str(e),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    path = _write_result(args.slug, result)
    print(f"[recommend] {path} 에 저장했습니다.")


if __name__ == "__main__":
    main()
