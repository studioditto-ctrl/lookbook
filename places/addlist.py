"""장소 목록(JSON)을 구글 지도 시 단위 목록과 옵시디언에 넣는다. 미쉐린 가이드 같은 목록용.

  python -m places addlist LIST.json            확인만: 이름마다 구글 지도에서 열어 이름·주소를 보여준다
  python -m places addlist LIST.json --apply    확인을 통과한 장소를 지도 목록과 옵시디언에 저장

LIST.json (레포 밖 %USERPROFILE%\\.lookbook\\lists\\ 에 둔다):
  {"source": "https://…", "tags": ["미쉐린"], "category": "식당",
   "places": [{"name": "밍글스", "city": "서울", "note": "미쉐린 3스타",
               "query": "(선택) 검색어", "alt": ["(선택) 구글이 보여 주는 다른 이름"]}]}

같은 이름으로 다른 가게가 열리는 일을 막으려고 구글 지도가 연 장소의 이름과 주소의 시·도를
목록의 값과 견준다. 맞지 않으면 다른 검색어로 다시 열어 보고, 그래도 안 맞으면 저장하지 않고
알려 준다 (query 를 고쳐 다시 실행). 이미 저장된 장소는 '이미 저장됨' 으로 넘어간다.
"""

import json
import re
from difflib import SequenceMatcher
from pathlib import Path

from . import obsidian, region
from .gmaps import NO_SAVE, GMaps
from .store import Store


def _norm(text):
    return re.sub(r"[\s·\-_.()（）,]+", "", text or "").lower()


def name_matches(want, got):
    """구글이 연 이름이 찾던 이름과 같은 가게인가 (영문 병기·지점명이 붙어도 통과)."""
    a, b = _norm(want), _norm(got)
    if not a or not b:
        return False
    return a in b or b in a or SequenceMatcher(None, a, b).ratio() >= 0.7


def queries(item):
    city = item.get("city", "")
    name = item["name"]
    if item.get("query"):
        return [item["query"]]
    return [f"{name} {city}".strip(), f"{name} 레스토랑 {city}".strip(), f"{name} 파인다이닝 {city}".strip()]


class AddList:
    def __init__(self, config, spec, log=print, gm=None):
        self.config, self.spec, self.log = config, spec, log
        self.gm = gm or GMaps(config, config["data_dir"])
        self.results = []

    # --- 확인 ---
    def find(self, item):
        """검색어를 바꿔 가며 열어 이름·시도가 맞는 장소 정보를 돌려준다. 못 찾으면 (None, 마지막 정보)."""
        last = None
        for q in queries(item):
            try:
                self.gm._open_place(q)
            except Exception:  # noqa: BLE001 — 검색 결과가 없으면 다음 검색어
                continue
            info = self.gm.place_info()
            last = info
            # 구글이 영문(Restaurant Allen)으로 보여 주는 가게는 alt 에 그 이름을 적는다
            ok_name = any(name_matches(n, info["name"]) for n in [item["name"], *item.get("alt", [])])
            want_city = item.get("city")
            ok_city = not want_city or region.parse_address(info["address"] or "")[1] == want_city
            if ok_name and ok_city:
                return info, last
        return None, last

    def run(self, apply=False):
        self.gm.start()
        try:
            for item in self.spec["places"]:
                try:
                    self._one(item, apply)
                except Exception as e:  # noqa: BLE001 — 한 곳 실패로 전체가 멈추지 않게
                    self.log(f"  ! {item['name']}: {str(e)[:100]}")
                    self.results.append(dict(item, status="오류", reason=str(e)[:100]))
        finally:
            self.gm.close()
        if apply:
            self.write_notes()
        return self.summary(apply)

    def _one(self, item, apply):
        info, last = self.find(item)
        if not info:
            got = f"{last['name']} | {last['address']}" if last else "열리지 않음"
            self.log(f"  ⚠️ {item['name']} → 이름·지역이 안 맞음: {got}")
            self.results.append(dict(item, status="불일치", got=got))
            return
        target = region.list_name("", "", self.config, info["address"])
        line = f"{item['name']} → {info['name']} | {info['category']} | {info['address'][:36]} → {target}"
        if not apply:
            self.log(f"  · {line}")
            self.results.append(dict(item, status="확인", info=info, target=target))
            return
        ok, reason = self.gm.save_current(target)
        status = "저장" if ok else ("저장불가" if reason == NO_SAVE else "실패")
        self.log(f"  {'✅' if ok else '⚠️'} {line} [{reason}]")
        self.results.append(dict(item, status=status, reason=reason, info=info, target=target))

    # --- 옵시디언 ---
    def write_notes(self):
        store = Store(self.config["data_dir"])
        existing = {_norm(e["name"]): e for e in store.all_places()}
        source = self.spec.get("source", "")
        made = updated = 0
        for r in self.results:
            if r["status"] not in ("저장", "저장불가") or not r.get("info"):
                continue
            info, item = r["info"], r
            saved = r["status"] == "저장"
            entry = store.data["places"].get(f"gmaps:{info['id']}") or existing.get(_norm(info["name"]))
            tags = list(self.spec.get("tags") or []) + list(item.get("tags") or [])
            if entry:
                if source and source not in entry["sources"]:
                    entry["sources"].append(source)
                entry["gmaps_saved"] = entry.get("gmaps_saved") or saved
                entry["gmaps_list"] = r["target"]
                entry["tags"] = list(dict.fromkeys((entry.get("tags") or []) + tags))
                obsidian.write_place(entry, r["target"], item.get("note", ""), self.config)
                updated += 1
                continue
            sido, area = region.split_address(info["address"], self.config)
            entry = {
                "name": info["name"], "category": item.get("category") or self.spec.get("category", "식당"),
                "sido": sido, "area": area, "address": info["address"],
                "lat": None, "lng": None, "kakao_id": "", "kakao_url": "", "phone": "",
                "note": item.get("note", ""), "confidence": 1.0, "checked": True,
                "sources": [s for s in (source, info["url"]) if s],
                "gmaps_saved": saved, "gmaps_no_save": not saved, "gmaps_list": r["target"],
                "tags": tags,
            }
            store.data["places"][f"gmaps:{info['id'] or _norm(info['name'])}"] = entry
            existing[_norm(info["name"])] = entry
            obsidian.write_place(entry, r["target"], item.get("note", ""), self.config)
            made += 1
        obsidian.write_index(store.all_places(), self.config)
        store.save()
        self.log(f"\n옵시디언: 새 노트 {made}개, 기존 노트 갱신 {updated}개")

    def summary(self, apply):
        count = {}
        for r in self.results:
            count[r["status"]] = count.get(r["status"], 0) + 1
        text = f"\n총 {len(self.results)}곳 · " + " · ".join(f"{k} {v}" for k, v in count.items())
        bad = [r["name"] for r in self.results if r["status"] in ("불일치", "오류", "실패")]
        if bad:
            text += "\n확인 필요: " + ", ".join(bad)
        self.log(text)
        return text


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))
