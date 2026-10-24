"""구글 지도가 연 장소가 찾던 가게인지 가려내는 비교. 이름과 주소로 본다.

구글 지도 검색은 이름이 안 맞아도 1순위 결과를 연다 (새 가게·작은 가게는 다른 곳이 열린다).
그대로 저장하면 엉뚱한 가게가 내 목록에 들어가서, 저장 전에 이 비교를 통과해야 한다.
"""

import re
from difflib import SequenceMatcher


def norm(text):
    return re.sub(r"[\s·\-_.()（）,]+", "", text or "").lower()


def name_matches(want, got):
    """같은 가게 이름인가 (영문 병기·지점명이 붙어도 통과)."""
    a, b = norm(want), norm(got)
    if not a or not b:
        return False
    return a in b or b in a or SequenceMatcher(None, a, b).ratio() >= 0.7


FLOOR = re.compile(r"^(지하)?\d*층$|^b?\d+f$|^\d+호$", re.I)
FLOOR_LIST = re.compile(r"\b\d+,\s*(?=\d*층)")  # '4, 5층' 처럼 층을 여러 개 적은 앞부분


def address_match(want, got):
    """글에 적힌 주소(지번·일부만 있음)와 구글 주소가 같은 곳인가.
    층·호 같은 말은 빼고 남은 말(구·도로·번지)이 구글 주소에 모두 들어 있어야 한다 (2개 이상)."""
    text = re.sub(r"^주소:\s*", "", got or "").lower().replace(" ", "")
    want = FLOOR_LIST.sub("", want or "")
    tokens = [t for t in re.split(r"[\s,]+", want.strip()) if t and not FLOOR.match(t)]
    tokens = [t for t in tokens if not t.startswith("(")]
    if len(tokens) < 2:
        return False
    return all(t.lower().replace(" ", "") in text for t in tokens)
