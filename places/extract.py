"""캡션에서 장소 목록 추출 (Gemini 또는 Claude, 구조화 출력)."""

import json
from dataclasses import dataclass, field

MAX_TOKENS = 16000
CATEGORIES = ["식당", "카페", "베이커리", "바", "전시/공간", "숙소", "쇼핑", "자연/산책", "기타"]

SYSTEM = """인스타그램 장소 추천 게시물의 캡션에서 방문할 수 있는 장소를 뽑습니다.

각 장소에 대해:
- name: 가게·장소의 정식 이름. 지점명은 branch 로 분리 (예: name "블루보틀", branch "성수").
- category: 주어진 목록 중 하나.
- country: 국가. 국내면 "대한민국".
- city: 시/도 또는 해외 도시 (예: "서울", "제주", "도쿄").
- area_hint: 동네·상권 (예: "성수", "연남", "애월"). 캡션에 없으면 빈 문자열.
- address_hint: 캡션에 적힌 주소 그대로 (📍 뒤 등). 없으면 빈 문자열.
- note: 캡션에 나온 추천 메뉴·영업 정보·특징을 한 문장으로. 없으면 빈 문자열.

summary 에는 게시물 전체를 한국어 한 문장으로 요약합니다.

규칙:
- 캡션에 명시된 장소만 씁니다. 해시태그만 보고 지어내거나, 언급만 된 프랜차이즈
  브랜드 전체를 장소로 만들지 마십시오.
- 모르는 칸은 빈 문자열로 둡니다. 추측하지 마십시오.
- 장소가 없으면 places 를 빈 배열로 둡니다.
"""

_PLACE = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "branch": {"type": "string"},
        "category": {"type": "string", "enum": CATEGORIES},
        "country": {"type": "string"},
        "city": {"type": "string"},
        "area_hint": {"type": "string"},
        "address_hint": {"type": "string"},
        "note": {"type": "string"},
    },
    "required": ["name", "branch", "category", "country", "city",
                 "area_hint", "address_hint", "note"],
    "additionalProperties": False,
}
SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "places": {"type": "array", "items": _PLACE},
    },
    "required": ["summary", "places"],
    "additionalProperties": False,
}


@dataclass
class Candidate:
    name: str
    branch: str = ""
    category: str = "기타"
    country: str = ""
    city: str = ""
    area_hint: str = ""
    address_hint: str = ""
    note: str = ""

    @property
    def query(self):
        return " ".join(p for p in (self.name, self.branch) if p)

    @property
    def domestic(self):
        return self.country in ("", "대한민국", "한국", "South Korea", "Korea")


@dataclass
class Extraction:
    summary: str = ""
    places: list = field(default_factory=list)


KEYS = {"gemini": "GEMINI_API_KEY", "claude": "ANTHROPIC_API_KEY"}


def provider(config):
    name = ((config.get("extract") or {}).get("provider") or "gemini").lower()
    return name if name in KEYS else "gemini"


def parse(data):
    places = []
    for p in data.get("places") or []:
        name = (p.get("name") or "").strip()
        if not name:
            continue
        places.append(Candidate(**{k: (p.get(k) or "").strip() for k in _PLACE["properties"]}))
    return Extraction(summary=(data.get("summary") or "").strip(), places=places)


def extract(caption, config, client=None, ask=None):
    """캡션 → Extraction. 실패하면 예외를 올린다 (봇이 사용자에게 알린다).

    extract.provider 가 gemini(기본)면 러닝 다이제스트와 같은 Gemini 클라이언트,
    claude 면 Claude. client 를 넘기면 Claude 로 본다 (테스트용).
    """
    settings = config.get("extract") or {}
    content = f"<caption>\n{caption}\n</caption>"
    if client is None and provider(config) == "gemini":
        if ask is None:
            import os

            from src import gemini_client
            # 코드 기본 모델이 막혀 매번 404 → 대체 모델로 도는 시간을 줄인다. GEMINI_MODEL 환경변수가 우선
            if settings.get("gemini_model") and not os.environ.get("GEMINI_MODEL"):
                gemini_client.MODEL = settings["gemini_model"]
            ask = gemini_client.ask_gemini
        return parse(ask(SYSTEM, content, SCHEMA, max_tokens=MAX_TOKENS, log_prefix="places"))
    if client is None:
        import anthropic
        client = anthropic.Anthropic()

    response = client.messages.create(
        model=settings.get("model", "claude-opus-5-5"),
        max_tokens=MAX_TOKENS,
        system=SYSTEM,
        output_config={
            "effort": settings.get("effort", "low"),
            "format": {"type": "json_schema", "schema": SCHEMA},
        },
        messages=[{"role": "user", "content": content}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("모델이 요청을 거부했습니다")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("응답이 max_tokens 에서 잘렸습니다")
    text = next((b.text for b in response.content if b.type == "text"), None)
    if not text:
        raise RuntimeError("응답에 텍스트 블록이 없습니다")
    return parse(json.loads(text))
