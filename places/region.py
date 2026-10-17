"""주소 → 동네 상권 이름.

'서울 성동구 성수동2가 315-57' → ('서울', '성수')
'제주특별자치도 제주시 애월읍 ...' → ('제주', '애월')
'강원특별자치도 강릉시 교동 ...' → ('강원', '강릉')
"""

import re

SIDO_SHORT = {
    "서울특별시": "서울", "부산광역시": "부산", "대구광역시": "대구", "인천광역시": "인천",
    "광주광역시": "광주", "대전광역시": "대전", "울산광역시": "울산",
    "세종특별자치시": "세종", "경기도": "경기", "강원특별자치도": "강원", "강원도": "강원",
    "충청북도": "충북", "충청남도": "충남", "전북특별자치도": "전북", "전라북도": "전북",
    "전라남도": "전남", "경상북도": "경북", "경상남도": "경남",
    "제주특별자치도": "제주", "제주도": "제주",
}


def short_sido(name):
    return SIDO_SHORT.get(name, name)


def neighborhood(dong, aliases=None):
    """'성수동2가' → '성수', '연남동' → '연남', '애월읍' → '애월'."""
    aliases = aliases or {}
    if dong in aliases:
        return aliases[dong]
    base = re.sub(r"\d+가$", "", dong)          # 성수동2가 → 성수동, 을지로3가 → 을지로
    base = re.sub(r"\d+동$", "동", base)         # 역삼1동 → 역삼동
    if base in aliases:
        return aliases[base]
    stripped = re.sub(r"(동|읍|면|리)$", "", base)
    return stripped if len(stripped) >= 2 else base


def from_address(address, config):
    """카카오 address_name → (시도, 동네). 실패하면 (시도 or '', '')."""
    tokens = (address or "").split()
    if not tokens:
        return "", ""
    sido = short_sido(tokens[0])
    aliases = config.get("region_alias") or {}
    metro = set(config.get("metro") or [])

    if sido in metro or sido == "제주":
        # 구/시 다음의 첫 동·읍·면·가 토큰을 동네로
        for tok in tokens[1:]:
            if re.search(r"(동|읍|면|가)$", tok) and not tok.endswith("구"):
                return sido, neighborhood(tok, aliases)
        return sido, ""
    # 도 지역은 시·군 단위
    if len(tokens) > 1:
        return sido, re.sub(r"(시|군)$", "", tokens[1]) or tokens[1]
    return sido, ""


def list_name(sido, area, config):
    """구글 지도 저장 목록 이름.

    list_unit 이 region(기본)이면 시도·국가 단위로 묶고 list_groups 로 합친다
    (서울·경기·인천 → '서울·경기'). area 면 '서울 성수' 처럼 동네까지.
    """
    settings = config.get("gmaps") or {}
    prefix = settings.get("list_prefix", "")
    if settings.get("list_unit", "region") == "area":
        label = " ".join(p for p in (sido, area) if p)
    else:
        label = (settings.get("list_groups") or {}).get(sido, sido)
    return f"{prefix}{label or '기타'}"
