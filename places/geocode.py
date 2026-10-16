"""장소 검증 — 국내는 카카오 로컬 키워드 검색, 해외는 추출값 그대로.

구글 Places API 는 결제 계정이 필요해 쓰지 않는다. 구글 지도 저장은
브라우저 자동화가 이름+주소로 검색해 처리한다.
"""

import os
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import requests

from . import region

KAKAO_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"
TIMEOUT = 15
MIN_CONFIDENCE = 0.55


@dataclass
class Place:
    name: str
    category: str
    sido: str            # 서울 / 제주 / 일본 …
    area: str            # 성수 / 애월 / 도쿄 …
    address: str = ""
    lat: float = None
    lng: float = None
    kakao_id: str = ""
    kakao_url: str = ""
    phone: str = ""
    note: str = ""
    confidence: float = 0.0
    checked: bool = False  # 카카오로 확인을 시도했는지 (키가 없으면 False)
    sources: list = field(default_factory=list)

    @property
    def key(self):
        if self.kakao_id:
            return f"kakao:{self.kakao_id}"
        # 카카오 ID 가 없으면 이름으로 합친다. 띄어쓰기 차이는 같은 곳으로 본다
        return f"name:{self.sido}:{self.area}:{self.name}".lower().replace(" ", "")

    @property
    def gmaps_query(self):
        return " ".join(p for p in (self.name, self.address or self.area) if p)

    @property
    def gmaps_url(self):
        from urllib.parse import quote
        return f"https://www.google.com/maps/search/?api=1&query={quote(self.gmaps_query)}"


def _similar(a, b):
    a, b = a.replace(" ", "").lower(), b.replace(" ", "").lower()
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 0.9
    return SequenceMatcher(None, a, b).ratio()


def score(candidate, doc):
    """이름 유사도 + 지역 힌트가 주소에 들어 있으면 가산."""
    s = _similar(candidate.query, doc.get("place_name", ""))
    s = max(s, _similar(candidate.name, doc.get("place_name", "")))
    addr = doc.get("address_name", "") + " " + doc.get("road_address_name", "")
    for hint in (candidate.area_hint, candidate.city):
        if hint and hint in addr:
            s += 0.1
    return min(s, 1.0)


def kakao_search(query, key, session=None):
    session = session or requests
    r = session.get(
        KAKAO_URL,
        headers={"Authorization": f"KakaoAK {key}"},
        params={"query": query, "size": 5},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.json().get("documents") or []


def _queries(c):
    yield " ".join(p for p in (c.area_hint or c.city, c.query) if p)
    yield c.query
    if c.address_hint:
        yield f"{c.address_hint} {c.name}"


def resolve(candidate, config, search=None):
    """Candidate → Place. 카카오에서 못 찾으면 추출값으로 만든 저신뢰 Place."""
    if candidate.domestic:
        key = os.environ.get("KAKAO_REST_API_KEY")
        if search is None and key:
            search = lambda q: kakao_search(q, key)  # noqa: E731
        best, best_score = None, 0.0
        for q in (_queries(candidate) if search else ()):
            try:
                docs = search(q)
            except requests.RequestException as e:
                print(f"[geocode] 카카오 검색 실패 ({q}): {e}")
                continue
            for doc in docs:
                s = score(candidate, doc)
                if s > best_score:
                    best, best_score = doc, s
            if best_score >= 0.85:
                break
        if best and best_score >= MIN_CONFIDENCE:
            sido, area = region.from_address(best.get("address_name", ""), config)
            return Place(
                name=best.get("place_name", candidate.name),
                category=candidate.category,
                sido=sido or candidate.city,
                area=area or candidate.area_hint,
                address=best.get("road_address_name") or best.get("address_name", ""),
                lat=float(best["y"]) if best.get("y") else None,
                lng=float(best["x"]) if best.get("x") else None,
                kakao_id=str(best.get("id", "")),
                kakao_url=best.get("place_url", ""),
                phone=best.get("phone", ""),
                note=candidate.note,
                confidence=round(best_score, 2),
                checked=True,
            )
        sido = region.short_sido(candidate.city)
        checked = search is not None
    else:
        sido = candidate.country
        checked = False

    area = candidate.area_hint if candidate.domestic else (candidate.city or candidate.area_hint)
    return Place(
        name=candidate.query,
        category=candidate.category,
        sido=sido,
        area=area,
        address=candidate.address_hint,
        note=candidate.note,
        confidence=0.0 if candidate.domestic else 0.5,
        checked=checked,
    )
