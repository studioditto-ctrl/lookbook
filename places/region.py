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


# 해외 주소에서 나라를 찾는 이름 (한국어 화면의 구글 지도는 현지 표기·영문이 섞인다)
COUNTRIES = {
    # 〒(우편번호)·chōme(丁目)는 일본 주소에만 쓰인다 — 나라 이름 없이 찍힌 위치용
    "일본": ("일본", "Japan", "日本", "〒", "chōme", "Chōme", "Chome", "chome"),
    "중국": ("중국", "China", "中国"),
    "대만": ("대만", "Taiwan", "台灣", "台湾"),
    "홍콩": ("홍콩", "Hong Kong", "香港"),
    "마카오": ("마카오", "Macao", "Macau", "澳門"),
    "인도네시아": ("인도네시아", "Indonesia"),
    "태국": ("태국", "Thailand", "ประเทศไทย"),
    "베트남": ("베트남", "Vietnam", "Việt Nam"),
    "싱가포르": ("싱가포르", "Singapore"),
    "필리핀": ("필리핀", "Philippines"),
    "말레이시아": ("말레이시아", "Malaysia"),
    "미국": ("미국", "United States", "USA"),
    "캐나다": ("캐나다", "Canada"),
    "영국": ("영국", "United Kingdom", "UK"),
    "프랑스": ("프랑스", "France"),
    "이탈리아": ("이탈리아", "Italy", "Italia"),
    "스페인": ("스페인", "Spain", "España"),
    "독일": ("독일", "Germany", "Deutschland"),
    "호주": ("호주", "Australia"),
    "괌": ("괌", "Guam"),
}
METRO = {"서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종"}
# 도를 빼고 '서귀포시 남원읍 …' 처럼 시부터 적힌 주소용 (광주시는 경기 광주시)
CITY_SIDO = {city + "시": sido for sido, cities in {
    "경기": "수원 성남 의정부 안양 부천 광명 평택 동두천 안산 고양 과천 구리 남양주 오산 시흥 군포 "
            "의왕 하남 용인 파주 이천 안성 김포 화성 광주 양주 포천 여주",
    "강원": "춘천 원주 강릉 동해 태백 속초 삼척",
    "충북": "청주 충주 제천",
    "충남": "천안 공주 보령 아산 서산 논산 계룡 당진",
    "전북": "전주 군산 익산 정읍 남원 김제",
    "전남": "목포 여수 순천 나주 광양",
    "경북": "포항 경주 김천 안동 구미 영주 영천 상주 문경 경산",
    "경남": "창원 진주 통영 사천 김해 밀양 거제 양산",
    "제주": "제주 서귀포",
}.items() for city in cities.split()}


def parse_address(address):
    """구글·카카오 주소 → (나라, 시도 약칭, 시 이름 또는 '').

    '경기도 성남시 분당구 …' → ('대한민국', '경기', '성남시')
    '강원특별자치도 평창군 …' → ('대한민국', '강원', '')
    '서울특별시 성동구 …'     → ('대한민국', '서울', '')
    해외면 (나라 또는 '', '', '').
    """
    text = re.sub(r"^\s*(주소:\s*)?(대한민국\s*)?", "", address or "").strip()
    tokens = [t.strip(",") for t in text.split()]

    def is_sido(tok):  # 구글은 '경기도', 카카오는 '경기' 처럼 쓴다
        return tok in SIDO_SHORT or tok in SIDO_SHORT.values()

    # 영문식 순서로 거꾸로 적힌 주소도 있다: '2F, 13 한남대로27가길 용산구 서울특별시'
    for seq in (tokens, tokens[::-1]):
        if seq and is_sido(seq[0]):
            sido = SIDO_SHORT.get(seq[0], seq[0])
            city = ""
            if sido not in METRO and len(seq) > 1 and seq[1].endswith("시"):
                city = seq[1]
            return "대한민국", sido, city
    for seq in (tokens, tokens[::-1]):
        if seq and seq[0] in CITY_SIDO:
            return "대한민국", CITY_SIDO[seq[0]], seq[0]
    for country, names in COUNTRIES.items():
        if any(re.search(rf"(?<![A-Za-z]){re.escape(n)}(?![A-Za-z])", text) for n in names):
            return country, "", ""
    return "", "", ""


def split_address(address, config):
    """주소 → 노트 폴더용 (시도, 동네). 해외면 (나라, '').

    도가 빠졌거나 거꾸로 적힌 주소도 시도는 parse_address 로 정하고,
    동네는 정상 순서 주소에서만 from_address 로, 아니면 시 이름에서 '시' 를 뗀다.
    """
    country, sido, city = parse_address(address)
    if country != "대한민국":
        return country, ""
    text = re.sub(r"^\s*(주소:\s*)?(대한민국\s*)?", "", address or "").strip()
    first = text.split()[0] if text else ""
    if SIDO_SHORT.get(first, first) == sido:
        return from_address(text, config)
    return sido, re.sub(r"시$", "", city)


def city_label(address):
    """'경기 성남시' / '강원' / '서울' / '일본'. 알 수 없으면 ''."""
    country, sido, city = parse_address(address)
    if country == "대한민국":
        return f"{sido} {city}".strip()
    return country


def list_name(sido, area, config, address=""):
    """구글 지도 저장 목록 이름.

    list_unit
      city (기본): 시 단위. 주소로 정한다 — '📍경기 성남시', 군이면 '📍강원',
                   특별·광역시는 '📍서울', 해외는 나라 '📍일본'. 주소가 없으면 시도·나라.
      region: 시도·국가 단위, list_groups 로 합친다 (서울·경기·인천 → '서울·경기')
      area:   '서울 성수' 처럼 동네까지
    """
    settings = config.get("gmaps") or {}
    prefix = settings.get("list_prefix", "")
    unit = settings.get("list_unit", "city")
    if unit == "area":
        label = " ".join(p for p in (sido, area) if p)
    elif unit == "region":
        label = (settings.get("list_groups") or {}).get(sido, sido)
    else:
        label = city_label(address) or sido
    return f"{prefix}{label or '기타'}"
