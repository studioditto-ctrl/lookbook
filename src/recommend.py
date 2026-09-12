"""테마 하나로 소스 후보(유튜브·매체·블로그)와 키워드를 추천한다.

Gemini 에게는 채널·매체 '이름'만 제안하게 하고, 실존 여부는 여기서 따로
확인한다. 모델이 라이브 웹 검색을 쓰지 않으므로 이름을 지어낼 수 있다 —
유튜브는 YouTube Data API 로 채널을 찾지 못하거나, 찾았어도 구독자가 너무
적으면(이름만으로 검색하다 보니 동명의 엉뚱한 소규모 채널이 잡히는 일이
있다) 후보에서 뺀다. 살아남은 채널은 구독자 많은 순으로 국내/해외 각각
순위를 매긴다. 매체/블로그는 RSS 가 실제로 살아 있는지 확인하고, 매체는
방문자 수까지 최선을 다해 확인한다 — 실패해도 버리지 않고 '확인 필요'로만
표시한다.

어드민 페이지는 정적 GitHub Pages라 이 스크립트를 직접 부를 수 없다(키가
브라우저에 노출된다). repository_dispatch 로 이 스크립트를 돌리는 워크플로가
결과를 state/recommend/<slug>.json 에 커밋하면, 페이지는 그 파일이 생기길
기다렸다가 읽는다. 그래서 어떤 예외가 나도 결과 파일은 반드시 남겨야
페이지가 무한정 기다리지 않는다 — main() 은 넓게 예외를 잡는다.
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# gemini-2.5-pro 는 신규 사용자에게 막혔고, 그다음 쓴 gemini-3.1-pro-preview
# 는 무료 등급 쿼터가 0(유료 결제가 있어야 쓸 수 있는 등급)이었다. flash 계열은
# 보통 무료 등급에도 쿼터가 있어 기본값으로 둔다. 계정 등급이 바뀌거나 모델이
# 또 바뀌면, GEMINI_MODEL 환경변수(워크플로 시크릿/변수)로 코드를 안 고치고
# 바꿀 수 있다.
MODEL = os.environ.get("GEMINI_MODEL") or "gemini-3.1-flash"
MAX_TOKENS = 12000
# 후보를 늘릴수록 응답이 길어져 max_tokens 에서 잘릴 위험이 커진다.

# 진짜 일시적인 쿼터 초과(순간적으로 몰렸을 때)라면 잠깐 기다리면 풀린다.
# 등급 자체에 쿼터가 0 인 경우(이번에 겪은 것)는 재시도해도 똑같이 막히지만,
# 몇 초 더 기다리는 비용이 크지 않아 구분하지 않고 한 번은 재시도해 본다.
RATE_LIMIT_RETRIES = 2
RATE_LIMIT_BACKOFF_SECONDS = 20

REPO = Path(__file__).resolve().parent.parent
RECOMMEND_DIR = REPO / "state" / "recommend"

KINDS = ("youtube", "media", "blog")
REGIONS = ("domestic", "international")

# Gemini 에게 한 번에 요청하는 원본 후보 상한. 검증·순위 매기기 전 단계라
# 실제로 화면에 보일 개수(유튜브 지역별 10위까지, 매체·블로그 지역별 10개까지)
# 보다 넉넉히 받아야, 검증에서 떨어져 나가도 각 칸이 비지 않는다.
RAW_LIMIT = 50
# 유튜브는 지역별로 구독자 많은 순 이만큼만 남긴다.
YOUTUBE_RANK_SIZE = 10
# 매체·블로그는 kind·지역 조합별로 이만큼만 남긴다.
OTHER_CAP = 10
# 채널 이름으로 검색하면 동명의 엉뚱한 소규모 채널이 잡히기도 한다.
# 이 미만이거나 비공개면(확인 불가) 순위에서 뺀다.
MIN_YOUTUBE_SUBSCRIBERS = 1000

SYSTEM = f"""당신은 텔레그램 다이제스트 구독 설정을 돕는 추천 어시스턴트입니다.

사용자가 새 주제(테마)를 입력하면, 그 주제를 다루는 소스 후보를 최대
{RAW_LIMIT}개 제안합니다. 이후 실존 여부·구독자 수·방문자 수를 별도로
확인해 추리므로, 여기서는 실제로 존재한다고 확신하는 것 위주로 넉넉히
제안하십시오.

규칙:
- 후보의 kind 는 다음 셋 중 하나입니다.
  - "youtube": 유튜브 채널
  - "media": 언론사·매체 — 자체 도메인을 가진 뉴스매체·전문지 등 방문자
    통계를 확인할 수 있을 법한 곳
  - "blog": 개인 블로그·커뮤니티 등 매체가 아닌 글 출처
- region 은 "domestic"(국내) 또는 "international"(해외)입니다. kind ×
  region 조합(유튜브×국내, 유튜브×해외, 매체×국내, 매체×해외, 블로그×국내,
  블로그×해외) 여섯 칸에 골고루 후보를 채우십시오 — 한쪽에 쏠리면 검증
  후 그 칸이 빌 수 있습니다. 각 칸 최소 5개 이상을 목표로 하되, 실제로
  존재한다고 확신할 수 있는 만큼만 채우십시오.
- name 에는 실제로 존재한다고 확신하는 채널명/매체명만 씁니다. 실존 여부는
  이후 별도로 확인하니, 떠오르지 않으면 억지로 채우지 말고 후보 수를
  줄이십시오. 지어낸 이름을 자신 있게 제시하지 마십시오.
- kind 가 "media" 나 "blog" 인데 정확한 URL을 모르면 url 을 빈 문자열로
  둡니다. 틀린 URL을 지어내지 마십시오. kind 가 "youtube" 면 url 은 항상
  빈 문자열로 둡니다 (채널 검색은 이름으로 합니다).
- reason 은 이 후보가 왜 이 주제와 맞는지 한국어 1문장으로 씁니다.
- keywords 는 이 주제의 다이제스트를 걸러낼 때 쓸 낱말 약 20개. 한국어와
  영어를 섞어서(예: 주제가 "러닝"이면 "러닝", "훈련", "running", "training"
  처럼 같은 개념의 한국어·영어 표기를 같이) 채웁니다.
- scope 는 검색·필터링에서 항상 주제로 인정할 낱말 5~10개로, 넉넉하게
  잡습니다. 표준 명칭뿐 아니라 줄임말·구어체·영문 표기·자주 쓰이는 오기까지
  포함해 동의어를 폭넓게 채웁니다. scope 가 너무 좁으면 실제로는 주제와
  맞는 글도 걸러져 버려지니, 좁히기보다 넓히는 쪽을 택하십시오."""

SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "region": {"type": "string", "enum": list(REGIONS)},
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


# Gemini 가 요청을 막는 사유들. 안전 관련 사유는 전부 '거부'로 취급한다.
_REFUSAL_FINISH_REASONS = {
    "SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "RECITATION", "SPII",
}


def _request(client, theme, exclude_names=None):
    from google.genai import types

    content = f"주제: {theme}"
    if exclude_names:
        content += "\n\n이미 추천했던 이름입니다. 다시 제안하지 말고 새로운 것만 주세요: " \
            + ", ".join(exclude_names)

    response = client.models.generate_content(
        model=MODEL,
        contents=content,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM,
            response_mime_type="application/json",
            # response_schema(OpenAPI 방언)이 아니라 response_json_schema 를
            # 쓴다 — 우리 SCHEMA 는 표준 JSON Schema 라 이쪽이 변환 없이
            # 그대로 통한다(엔진이 지원하는 필드만 쓴다: type/enum/items/
            # properties/additionalProperties/required 등, SCHEMA 는 이
            # 안에 들어간다).
            response_json_schema=SCHEMA,
            max_output_tokens=MAX_TOKENS,
        ),
    )

    # 프롬프트 자체가 막히면 candidates 가 아예 비어 있다.
    # FinishReason 은 str 을 상속하는 열거형이라 그냥 문자열과 비교해도 된다.
    candidates = response.candidates or []
    finish = candidates[0].finish_reason if candidates else None
    if finish in _REFUSAL_FINISH_REASONS:
        raise RuntimeError("모델이 요청을 거부했습니다")
    if finish == "MAX_TOKENS":
        raise RuntimeError("응답이 max_tokens 에서 잘렸습니다")

    text = response.text
    if not text:
        raise RuntimeError("응답에 텍스트 블록이 없습니다")
    return json.loads(text)


def _ask_gemini(theme, exclude_names=None):
    if not os.environ.get("GEMINI_API_KEY"):
        raise RuntimeError("GEMINI_API_KEY 가 없습니다")

    import httpx
    from google import genai
    from google.genai import errors

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    attempt = 0
    while True:
        try:
            return _request(client, theme, exclude_names=exclude_names)
        except errors.APIError as e:
            if e.code == 429 and attempt < RATE_LIMIT_RETRIES:
                attempt += 1
                print(f"[recommend] 요청 한도 초과, {RATE_LIMIT_BACKOFF_SECONDS}초 후 재시도"
                      f" ({attempt}/{RATE_LIMIT_RETRIES}): {e.message}")
                time.sleep(RATE_LIMIT_BACKOFF_SECONDS)
                continue
            if e.code == 429:
                raise RuntimeError(f"요청 한도 초과: {e.message}") from e
            raise RuntimeError(f"API 오류 {e.code}: {e.message}") from e
        except httpx.HTTPError as e:
            raise RuntimeError(f"API 연결 실패: {e}") from e


def _verify_youtube(name, key, cache):
    """채널 이름으로 실제 channel_id 를 찾는다. 못 찾으면 None."""
    from youtube import channel_subscribers, search_channel_id

    channel_id = search_channel_id(name, cache, key)
    if not channel_id:
        return None
    subs, _ok = channel_subscribers([channel_id], key, cache)
    return channel_id, subs.get(channel_id)


FEED_UA = "Mozilla/5.0 (compatible; DigestBot/1.0)"
FEED_TIMEOUT = 8
# Gemini 가 주는 url 은 사람이 보는 블로그 홈 주소인 경우가 많다. 실제 피드는
# 보통 이 중 하나에 있다 — 직접 파싱이 안 되면 자동 발견을 시도하고, 그것도
# 안 되면 이 후보 경로들을 하나씩 열어본다. 오래 걸려도(요청이 여럿 나가도)
# 죽은 채널을 살아있다고 잘못 등록하는 것보다 낫다.
FEED_SUFFIXES = ["feed/", "feed", "rss/", "rss.xml", "atom.xml", "feed.xml", "?feed=rss2"]

_LINK_ALT_RE = re.compile(r'<link\b[^>]*rel=["\']alternate["\'][^>]*>', re.I)
_TYPE_FEED_RE = re.compile(r'type=["\']application/(?:rss|atom)\+xml["\']', re.I)
_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']')


def _fetch(url):
    import requests

    try:
        resp = requests.get(url, timeout=FEED_TIMEOUT, headers={"User-Agent": FEED_UA})
        resp.raise_for_status()
        return resp
    except requests.RequestException:
        return None


def _looks_like_feed(text):
    """문자열 앞부분만 보고 판단하지 않는다 — feedparser 로 실제 구조를 본다."""
    import feedparser

    try:
        parsed = feedparser.parse(text)
    except Exception:
        return False
    return bool(parsed.entries) or bool((parsed.feed or {}).get("title"))


def _discover_feed_link(html, base_url):
    """<link rel="alternate" type="application/rss+xml" href="..."> 자동 발견."""
    from urllib.parse import urljoin

    for tag in _LINK_ALT_RE.findall(html):
        if not _TYPE_FEED_RE.search(tag):
            continue
        href = _HREF_RE.search(tag)
        if href:
            return urljoin(base_url, href.group(1))
    return None


def _verify_feed(url):
    """실제로 살아 있는 RSS/Atom 인지 확인한다.

    준 주소 자체가 피드가 아니어도(사람이 보는 블로그 홈일 수 있다) 페이지에
    자동 발견되는 진짜 피드 주소가 있으면, 또는 흔한 피드 경로 중 하나가
    맞으면 그 주소로 바꿔 돌려준다. 시간이 걸리더라도(요청을 여러 번 시도)
    실제로 살아 있는지 정확히 확인하는 쪽을 택한다. 다 실패하면 None —
    후보에서 빼지는 않고 '확인 필요'로만 남긴다."""
    resp = _fetch(url)
    if resp and _looks_like_feed(resp.text):
        return url

    if resp:
        discovered = _discover_feed_link(resp.text[:20000], url)
        if discovered:
            found = _fetch(discovered)
            if found and _looks_like_feed(found.text):
                return discovered

    from urllib.parse import urljoin

    # 표준 상대주소 규칙대로면 '/'로 끝나지 않는 주소(예: .../blog)는 마지막
    # 조각이 파일처럼 취급돼 대체돼 버린다. 그게 사실 디렉터리 주소일 수도
    # 있어 두 가지 다 시도한다 — 느리더라도 놓치는 것보다 낫다.
    bases = {url}
    if not url.endswith("/"):
        bases.add(url + "/")

    tried = set()
    for base in bases:
        for suffix in FEED_SUFFIXES:
            candidate = urljoin(base, suffix)
            if candidate in tried:
                continue
            tried.add(candidate)
            found = _fetch(candidate)
            if found and _looks_like_feed(found.text):
                return candidate

    return None


def _check_media_traffic(url):
    """월간 방문(추정치)을 최선을 다해 확인한다.

    공식 API 키가 필요 없는 비공식 공개 엔드포인트(SimilarWeb)를 쓴다 —
    문서화된 계약이 아니라서 언제든 막히거나 모양이 바뀔 수 있다. 실패하면
    조용히 None 을 돌려준다. 확인 안 된 숫자를 지어내는 것보다 '모름'이 낫다.
    """
    import requests
    from urllib.parse import urlparse

    domain = urlparse(url).netloc.removeprefix("www.")
    if not domain:
        return None
    try:
        resp = requests.get(
            "https://data.similarweb.com/api/v1/data",
            params={"domain": domain}, timeout=FEED_TIMEOUT,
            headers={"User-Agent": FEED_UA},
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        return None
    visits = (data.get("Engagments") or data.get("EstimatedMonthlyVisits") or {})
    value = visits.get("Visits") if isinstance(visits, dict) else visits
    try:
        return int(float(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def _collect_raw(theme, youtube_key, cache, exclude_names):
    """Gemini 제안 → 검증까지 마친 원본 후보 목록 (아직 순위·상한 적용 전)."""
    parsed = _ask_gemini(theme, exclude_names=exclude_names)
    out = []
    for raw in (parsed.get("candidates") or [])[:RAW_LIMIT]:
        name = (raw.get("name") or "").strip()
        kind = raw.get("kind")
        region = raw.get("region")
        if not name or kind not in KINDS or region not in REGIONS:
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
            if subs is None or subs < MIN_YOUTUBE_SUBSCRIBERS:
                print(f"[recommend] '{name}' 구독자 {subs} 명이라 제외합니다"
                      f" (최소 {MIN_YOUTUBE_SUBSCRIBERS:,}명, 이름으로 검색해"
                      f" 동명의 다른 채널이 잡혔을 수 있습니다).")
                continue
            out.append({
                "name": name, "kind": kind, "region": region, "reason": reason,
                "channel_id": channel_id, "subscribers": subs, "verified": True,
            })
        else:
            # 확인되면 실제로 살아 있는 피드 주소로 바꿔서 저장한다 — Gemini 가
            # 준 주소가 사람이 보는 페이지였어도, 자동 발견/추정으로 찾은
            # 진짜 피드 주소를 쓴다. 못 찾으면 원래 주소를 그대로 두고
            # '확인 필요'로만 남긴다 (봇 차단으로 확인만 실패했을 수 있다).
            resolved = _verify_feed(url) if url else None
            entry = {
                "name": name, "kind": kind, "region": region, "reason": reason,
                "url": resolved or url, "verified": bool(resolved),
            }
            if kind == "media":
                entry["monthly_visits"] = (
                    _check_media_traffic(resolved or url) if (resolved or url) else None
                )
            out.append(entry)
    return parsed, out


def _rank_youtube(candidates):
    """지역별 구독자 많은 순 top N. 살아남은 것만 순위(rank)를 매긴다."""
    ranked = []
    youtube = [c for c in candidates if c["kind"] == "youtube"]
    for region in REGIONS:
        group = sorted(
            (c for c in youtube if c["region"] == region),
            key=lambda c: c["subscribers"], reverse=True,
        )[:YOUTUBE_RANK_SIZE]
        for i, c in enumerate(group, start=1):
            c["rank"] = i
        ranked += group
    return ranked


def _cap_others(candidates):
    """매체·블로그는 kind·지역 조합별로 상한을 둔다.

    매체는 방문자 수(확인된 것 우선)로, 블로그는 RSS 확인 여부로 정렬한다.
    """
    out = []
    for kind in ("media", "blog"):
        for region in REGIONS:
            group = [c for c in candidates if c["kind"] == kind and c["region"] == region]
            if kind == "media":
                group.sort(key=lambda c: (c.get("monthly_visits") is None,
                                           -(c.get("monthly_visits") or 0)))
            else:
                group.sort(key=lambda c: not c["verified"])
            out += group[:OTHER_CAP]
    return out


def recommend(theme, youtube_key=None, cache=None, exclude_names=None):
    """테마 -> 검증·순위까지 마친 후보 목록.

    {"theme","candidates","keywords","scope","generated_at"}. candidates 는
    유튜브(지역별 구독자 순위 rank 포함) + 매체(방문자 수 우선) + 블로그
    (RSS 확인 우선) 순으로 담겨 있다.
    """
    cache = cache if cache is not None else {}
    parsed, raw = _collect_raw(theme, youtube_key, cache, exclude_names)

    candidates = _rank_youtube(raw) + _cap_others(raw)
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
    parser.add_argument(
        "--exclude", default="",
        help="쉼표로 구분한, 이미 추천받은 이름 목록 — '추가 검색'에서 중복을 피하려고 쓴다.",
    )
    args = parser.parse_args(argv)
    exclude_names = [s.strip() for s in args.exclude.split(",") if s.strip()]

    # 페이지는 이 파일이 생기기를 기다린다. 어떤 예외가 나도 반드시 뭔가는
    # 써야 폴링이 영원히 끝나지 않는 사태를 막는다 — summarize.py 처럼 좁게
    # 잡지 않고 여기서만 의도적으로 넓게 잡는다.
    try:
        result = recommend(
            args.theme, youtube_key=os.environ.get("YOUTUBE_API_KEY"),
            exclude_names=exclude_names,
        )
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
