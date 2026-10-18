"""내 주변 추천: 텔레그램으로 받은 GPS 위치에서 가까운 저장 장소를 고른다.

기준은 봇이 아는 저장 장소(places.json — 구글 지도 저장 목록 + 인스타·미쉐린으로 넣은 것)다.
거리는 직선거리. 위치는 이 계산에만 쓰고 기록하지 않는다 (로그에도 좌표를 남기지 않는다).

  근처 카페          → 카페만       근처 미쉐린 3     → 미쉐린 3곳
  /near 맛집 10      → 식당 10곳    (위치를 아직 안 보냈으면 위치 보내기 버튼을 보여 준다)
"""

import html
import math
import re
from urllib.parse import quote

from . import coords

DEFAULT_N = 20
MAX_N = 50
FAR_M = 30_000  # 이보다 멀면 '근처에는 없어요' 라고 알린다

NEAR_WORDS = ("/near", "근처", "주변", "가까운", "nearby")
STOP = {"/near", "근처", "주변", "가까운", "nearby", "추천", "추천해줘", "추천해", "해줘", "알려줘", "보여줘",
        "에서", "좀", "장소", "곳", "있는", "내", "나", "의", "을", "를", "곳으로", "찾아줘", "찾아봐"}
CATEGORIES = {"식당", "카페", "베이커리", "바", "전시/공간", "숙소", "쇼핑", "자연/산책", "기타"}
ALIASES = {
    "맛집": "식당", "밥": "식당", "식사": "식당", "레스토랑": "식당", "점심": "식당", "저녁": "식당",
    "커피": "카페", "디저트": "카페", "술": "바", "와인": "바", "술집": "바", "빵": "베이커리",
    "호텔": "숙소", "펜션": "숙소", "전시": "전시/공간", "미술관": "전시/공간", "박물관": "전시/공간",
    "산책": "자연/산책", "자연": "자연/산책", "공원": "자연/산책", "별": "미쉐린", "스타": "미쉐린",
    "미슐랭": "미쉐린", "가볼만한": "명소", "가볼만한곳": "명소", "관광": "명소", "볼거리": "명소",
}
# 글자로 보낸 명령 (텔레그램 메뉴 목록에는 한글 명령을 못 올려서 버튼 글자가 이 명령을 보낸다)
COMMAND_TERMS = {"/근처카페": "카페", "/근처맛집": "식당", "/근처장소": "명소", "/근처명소": "명소"}
SIGHT_CATEGORIES = {"전시/공간", "자연/산책"}  # '명소' = 가볼 만한 곳


def wants_nearby(text):
    t = (text or "").strip().lower()
    return bool(t) and "instagram.com" not in t and any(w in t for w in NEAR_WORDS)


def parse_request(text):
    """'근처 카페 3 추천해줘' → (['카페'], 3)."""
    terms, n = [], DEFAULT_N
    for tok in re.split(r"[\s,]+", (text or "").lower()):
        if tok.startswith("/"):
            tok = tok.split("@")[0]  # /near@봇이름
        if tok in COMMAND_TERMS:
            terms.append(COMMAND_TERMS[tok])
            continue
        if not tok or tok in STOP:
            continue
        if tok.isdigit():
            n = max(1, min(MAX_N, int(tok)))
            continue
        terms.append(ALIASES.get(tok, tok))
    return list(dict.fromkeys(terms)), n


def distance_m(lat1, lng1, lat2, lng2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2)
    return 2 * 6_371_000 * math.asin(math.sqrt(a))


def fmt_distance(m):
    if m < 1000:
        return f"{int(round(m, -1)) or 10}m"
    return f"{m / 1000:.1f}km"


def _matches(entry, term):
    if term == "명소":
        return entry.get("category") in SIGHT_CATEGORIES
    if term in CATEGORIES:  # '카페' 가 설명 글에 든 식당까지 걸리지 않게 분류는 정확히
        return entry.get("category") == term
    hay = " ".join([entry.get("name", ""), entry.get("category", ""), entry.get("note", ""),
                    entry.get("gmaps_list", ""), entry.get("area", ""), entry.get("address", ""),
                    *(entry.get("tags") or [])]).lower()
    return term.lower() in hay


def candidates(store_places, sidecar):
    """좌표가 있고 구글 지도에 저장된 장소. (좌표 없는 수도 함께 돌려준다)"""
    out, missing = [], 0
    for key, e in store_places.items():
        if not (e.get("gmaps_saved") or e.get("gmaps_no_save")):
            continue
        ll = coords.of(key, e, sidecar)
        if not ll:
            if key not in sidecar:  # coords.json 에 null 로 적힌 곳은 '알 수 없다고 확인한' 곳이라 세지 않는다
                missing += 1
            continue
        out.append(dict(e, lat=ll[0], lng=ll[1], _order=len(out)))  # places.json 은 저장한 순서라 _order 가 클수록 최근
    return out, missing


def nearest(places, lat, lng, terms=(), n=DEFAULT_N, recent=False):
    """[(거리 m, 장소)] 가까운 순. terms 는 모두 맞아야 한다.
    recent 면 가까운 n곳을 뽑은 뒤 최근 저장한 것부터 보여 준다 (저장일이 있으면 그 날짜, 없으면 저장한 순서)."""
    hits = [(distance_m(lat, lng, p["lat"], p["lng"]), p) for p in places
            if all(_matches(p, t) for t in terms)]
    hits.sort(key=lambda h: h[0])
    hits = hits[:n]
    if recent:
        hits.sort(key=lambda h: h[1].get("_order", 0), reverse=True)
    return hits


def _place_link(p):
    cid = coords.cid_url(p)
    if cid:
        return cid
    q = " ".join(x for x in (p.get("name"), p.get("address") or p.get("area")) if x)
    return f"https://www.google.com/maps/search/?api=1&query={quote(q)}"


def _directions(p):
    return f"https://www.google.com/maps/dir/?api=1&destination={p['lat']},{p['lng']}&travelmode=walking"


def render(hits, terms, missing=0, recent=False):
    label = " ".join(terms)
    if not hits:
        what = f"'{html.escape(label)}'에 맞는 " if terms else ""
        return f"📍 {what}저장 장소가 없어요."
    head = f"📍 내 주변 저장 장소{' · ' + html.escape(label) if label else ''}"
    if hits[0][0] > FAR_M:
        head += f"\n근처 {FAR_M // 1000}km 안에는 없어요. 가장 가까운 곳이에요."
    lines = [head, ""]
    for i, (m, p) in enumerate(hits, 1):
        star = " ⭐미쉐린" if "미쉐린" in (p.get("tags") or []) else ""
        lines.append(f"{i}. <b>{html.escape(p['name'])}</b> · {html.escape(p.get('category', ''))} · "
                     f"{fmt_distance(m)}{star}")
        saved = f" · {p['saved_at'][5:]} 저장" if recent and p.get("saved_at") else ""
        lines.append(f"   {html.escape(p.get('gmaps_list') or '')}{saved} · "
                     f"<a href=\"{html.escape(_directions(p))}\">길찾기</a> · "
                     f"<a href=\"{html.escape(_place_link(p))}\">지도</a>")
    foot = f"가까운 {len(hits)}곳 · 최근 저장한 순 · 직선거리 기준" if recent else "직선거리 기준"
    if missing:
        foot += f" · 좌표를 아직 모르는 {missing}곳은 빠졌어요"
    lines += ["", f"<i>{foot}</i>"]
    return "\n".join(lines)
