"""장소 목록(JSON)을 구글 지도 시 단위 목록과 옵시디언에 넣는다. 미쉐린 가이드 같은 목록용.

  python -m places addlist LIST.json            확인만: 이름마다 구글 지도에서 열어 이름·주소를 보여준다
  python -m places addlist LIST.json --apply    확인을 통과한 장소를 지도 목록과 옵시디언에 저장

LIST.json (레포 밖 %USERPROFILE%\\.lookbook\\lists\\ 에 둔다):
  {"source": "https://…", "tags": ["미쉐린"], "category": "식당",
   "places": [{"name": "밍글스", "city": "서울", "note": "미쉐린 3스타",
               "query": "(선택) 검색어", "alt": ["(선택) 구글이 보여 주는 다른 이름"],
               "title": "(선택) 노트에 쓸 가게 이름", "fix_location": true}]}

가오픈·신규 가게처럼 구글 지도에 아직 없으면 주소 핀으로 저장한다:
   {"name": "홉하우스", "city": "서울", "query": "서울 마포구 성미산로 183", "pin": true,
    "addr": "서울 마포구 성미산로 183", "title": "홉하우스", "note": "…"}
(fix_location: 이미 있는 기록의 주소·좌표를 새로 연 장소의 것으로 바꾼다.)

같은 이름으로 다른 가게가 열리는 일을 막으려고 구글 지도가 연 장소의 이름과 주소의 시·도를
목록의 값과 견준다. 맞지 않으면 다른 검색어로 다시 열어 보고, 그래도 안 맞으면 저장하지 않고
알려 준다 (query 를 고쳐 다시 실행). 이미 저장된 장소는 '이미 저장됨' 으로 넘어간다.
"""

import json
from pathlib import Path

from . import obsidian, region
from .gmaps import NO_SAVE, GMaps
from .namematch import address_match, name_matches  # noqa: F401 — 다른 모듈·테스트가 여기서 가져다 쓴다
from .namematch import norm as _norm
from .store import Store


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
            if item.get("pin"):
                # 가오픈·신규 가게는 구글 지도에 아직 없어서 글의 주소 위치(주소 핀)로 저장한다.
                # 열린 곳의 이름이 곧 주소이므로 글의 주소(addr)와 맞는지로 확인한다
                if not info["address"] and address_match(item.get("addr", ""), info["name"]):
                    info = dict(info, address=info["name"])
                    last = info
                ok_name = address_match(item.get("addr", ""), info["address"])
            else:
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
            title = item.get("title") or info["name"]  # 노트 이름 (주소 핀·영문 표기는 가게 이름으로)
            entry = ((info["id"] and store.data["places"].get(f"gmaps:{info['id']}"))
                     or existing.get(_norm(title)) or existing.get(_norm(info["name"])))
            tags = list(self.spec.get("tags") or []) + list(item.get("tags") or [])
            if entry:
                if source and source not in entry["sources"]:
                    entry["sources"].append(source)
                entry["gmaps_saved"] = entry.get("gmaps_saved") or saved
                entry.pop("gmaps_mismatch", None)  # 엉뚱한 가게가 저장됐던 기록을 바로잡는다
                entry["gmaps_list"] = r["target"]
                if info.get("address") and (not entry.get("address") or item.get("pin")
                                            or item.get("fix_location")):
                    entry["address"] = info["address"]
                if info.get("lat") is not None and (entry.get("lat") is None or item.get("fix_location")):
                    entry["lat"], entry["lng"] = info["lat"], info["lng"]
                entry["tags"] = list(dict.fromkeys((entry.get("tags") or []) + tags))
                path = obsidian.write_place(entry, r["target"], item.get("note", ""), self.config)
                add_note_line(path, item.get("note", ""))  # 이미 있는 노트에는 출처 줄이 안 붙을 수 있다
                updated += 1
                continue
            sido, area = region.split_address(info["address"], self.config)
            entry = {
                "name": title, "category": item.get("category") or self.spec.get("category", "식당"),
                "sido": sido, "area": area, "address": info["address"],
                "lat": info.get("lat"), "lng": info.get("lng"),
                "kakao_id": "", "kakao_url": "", "phone": "",
                "note": item.get("note", ""), "confidence": 1.0, "checked": True,
                "sources": [s for s in (source, info["url"]) if s],
                "gmaps_saved": saved, "gmaps_no_save": not saved, "gmaps_list": r["target"],
                "tags": tags,
            }
            store.data["places"][f"gmaps:{info['id'] or _norm(title)}"] = entry
            existing[_norm(title)] = entry
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


def add_note_line(path, text, today=None):
    """노트의 '출처' 목록 끝(## 메모 앞)에 한 줄을 더한다. 이미 있으면 그대로. 메모 칸은 건드리지 않는다."""
    from datetime import date
    path = Path(path)
    body = path.read_text(encoding="utf-8")
    if not text or text in body:
        return False
    line = f"- {today or date.today().isoformat()} {text}"
    marker = "\n## 메모"
    if marker in body:
        head, tail = body.split(marker, 1)
        body = head.rstrip() + "\n" + line + "\n" + marker + tail
    else:
        body = body.rstrip() + "\n" + line + "\n"
    path.write_text(body, encoding="utf-8")
    return True


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))
