"""구글 지도 저장 목록을 시 단위 목록으로 재정리하고 옵시디언 장소 노트를 만든다.

  python -m places reorg --sample 2   목록마다 앞 2곳만 열어 주소·새 목록 이름을 보여준다 (아무것도 안 바꿈)
  python -m places reorg --apply      전체를 돌며 새 목록(📍경기 성남시 …)에 추가하고 노트를 쓴다

모든 목록의 장소를 하나씩 열어 주소와 고유 ID 를 읽고, 같은 장소는 한 번만 처리한다.
**기존 목록은 지우지 않는다** (영구 삭제라 사용자가 직접). 끝나면 지울 목록을 알려준다.
진행 기록은 %USERPROFILE%\\.lookbook\\reorg.json — 중간에 멈춰도 다시 실행하면 이어서 한다.
"""

import json
import re
import time
from pathlib import Path

from . import obsidian, region
from .gmaps import NO_SAVE, NAV_TIMEOUT, UI_TIMEOUT, GMaps
from .store import Store

ITEM = "button.SMP2wb"  # 목록 안의 장소 한 줄 (2026-10 화면)
LIST_JS = r"""() => [...document.querySelectorAll('button')]
  .map(e => (e.innerText || '').trim())
  .filter(t => /개 장소$/.test(t))"""
CATEGORY_RULES = [  # 구글 분류 → 노트 분류
    ("카페", ("카페", "커피", "찻집", "티 하우스", "디저트")),
    ("베이커리", ("제과", "베이커리", "빵", "도넛")),
    ("바", ("바", "술집", "이자카야", "와인", "펍", "칵테일", "주점", "양조")),
    ("숙소", ("호텔", "숙박", "펜션", "리조트", "게스트", "민박", "B&B", "료칸", "모텔", "빌라", "스테이")),
    ("전시/공간", ("박물관", "미술관", "갤러리", "전시", "기념관", "공연", "서점", "도서관")),
    ("자연/산책", ("공원", "해변", "산", "폭포", "숲", "정원", "섬", "호수", "전망대", "해수욕장", "트레일", "사원", "절", "신사")),
    ("쇼핑", ("상점", "숍", "가게", "쇼핑", "마켓", "시장", "백화점", "몰", "소품", "편집", "의류", "식료품")),
    ("식당", ("식당", "음식점", "레스토랑", "요리", "한식", "중식", "일식", "양식", "국밥", "고기", "회", "면", "라멘", "스시", "피자", "버거", "분식", "다이닝")),
]


def category_of(google_category):
    text = google_category or ""
    for name, words in CATEGORY_RULES:
        if any(w in text for w in words):
            return name
    return "기타"


def _norm(name):
    return re.sub(r"\s+", "", name or "").lower()


class Reorg:
    def __init__(self, config, log=print):
        self.config = config
        self.log = log
        self.data_dir = Path(config["data_dir"])
        self.state_path = self.data_dir / "reorg.json"
        self.state = {"lists": None, "progress": {}, "places": {}}
        if self.state_path.exists():
            self.state.update(json.loads(self.state_path.read_text(encoding="utf-8")))
        self.gm = GMaps(config, config["data_dir"])
        self.page = None

    def save_state(self):
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.state_path)

    # --- 화면 ---
    def open_saved(self):
        page = self.page
        page.goto("https://www.google.com/maps?hl=ko", timeout=NAV_TIMEOUT)
        page.wait_for_timeout(2500)
        page.get_by_role("button", name="저장됨").first.click()
        page.wait_for_timeout(2500)

    def read_lists(self):
        """저장됨 패널의 목록 [{name, count, nth}] (같은 이름 목록은 nth 로 구분)."""
        page = self.page
        self.open_saved()
        seen = []
        for _ in range(15):
            texts = page.evaluate(LIST_JS)
            if len(texts) > len(seen):
                seen = texts
            page.mouse.move(280, 600)
            page.mouse.wheel(0, 1500)
            page.wait_for_timeout(1000)
        lists, counts = [], {}
        for t in seen:
            parts = [p.strip() for p in t.split("\n") if p.strip()]
            m = re.search(r"([\d,]+)개 장소$", parts[-1])
            if len(parts) < 2 or not m:
                continue
            name = parts[-2]
            nth = counts.get(name, 0)
            counts[name] = nth + 1
            lists.append({"name": name, "count": int(m.group(1).replace(",", "")), "nth": nth})
        return lists

    def open_list(self, lst):
        page = self.page
        self.open_saved()
        target = page.get_by_text(lst["name"], exact=True).nth(lst["nth"])
        for _ in range(15):
            if target.count() and target.is_visible():
                break
            page.mouse.move(280, 600)
            page.mouse.wheel(0, 800)
            page.wait_for_timeout(700)
        target.click()
        page.wait_for_timeout(2500)
        return page.url

    def load_items(self, upto):
        """목록 화면에서 장소 줄을 upto 개 이상 불러올 때까지 내린다."""
        page = self.page
        items = page.locator(ITEM)
        items.first.wait_for(timeout=UI_TIMEOUT)
        stable = 0
        while items.count() < upto and stable < 4:
            before = items.count()
            items.last.scroll_into_view_if_needed()
            page.wait_for_timeout(900)
            stable = stable + 1 if items.count() == before else 0
        return items

    def back_to_list(self, url, upto):
        page = self.page
        page.go_back()
        page.wait_for_timeout(1500)
        if page.locator(ITEM).count() == 0:  # 뒤로가기가 목록으로 안 돌아오면 주소로
            page.goto(url, timeout=NAV_TIMEOUT)
            page.wait_for_timeout(2500)
        return self.load_items(upto)

    # --- 실행 ---
    def run(self, apply=False, sample=0):
        self.gm.start()
        self.page = self.gm.page
        try:
            if not self.state["lists"] or not apply:
                self.state["lists"] = self.read_lists()
                self.save_state()
            lists = [lst for lst in self.state["lists"] if lst["count"] > 0]
            total = sum(lst["count"] for lst in lists)
            self.log(f"목록 {len(lists)}개 · 항목 {total}개 (중복 포함)")
            for lst in lists:
                self._run_list(lst, apply, sample)
        finally:
            self.gm.close()
        if apply:
            self.write_notes()
        return self.report(apply)

    def _run_list(self, lst, apply, sample):
        key = f"{lst['name']}#{lst['nth']}"
        start = self.state["progress"].get(key, 0) if apply else 0
        n = min(lst["count"], sample) if sample else lst["count"]
        if start >= n:
            return
        self.log(f"\n== {lst['name']} ({lst['count']}곳) {start}번째부터")
        url = self.open_list(lst)
        items = self.load_items(n)
        n = min(n, items.count())
        for i in range(start, n):
            try:
                self._run_item(lst, items.nth(i), apply)
            except Exception as e:  # noqa: BLE001 — 한 곳 실패로 전체가 멈추지 않게
                self.log(f"  ! {i + 1}번째 실패: {e}")
                if apply:  # --retry 로 이 자리만 다시
                    self.state.setdefault("failed", []).append({"list": key, "i": i})
            if apply:
                self.state["progress"][key] = i + 1
                self.save_state()
            items = self.back_to_list(url, i + 2)
            time.sleep(0.5)

    def retry_failed(self):
        """전체 실행 중 실패한 자리(목록, 순번)만 다시 연다."""
        failed = self.state.get("failed") or []
        self.log(f"다시 할 자리 {len(failed)}곳 + 지역 미정 중 위치 핀")
        lists = {f"{lst['name']}#{lst['nth']}": lst for lst in self.state["lists"]}
        self.state["failed"] = []
        self.gm.start()
        self.page = self.gm.page
        try:
            for key in dict.fromkeys(f["list"] for f in failed):
                lst = lists[key]
                idx = sorted(f["i"] for f in failed if f["list"] == key)
                self.log(f"\n== 다시: {lst['name']} {[i + 1 for i in idx]}번째")
                url = self.open_list(lst)
                for i in idx:
                    items = self.load_items(i + 1)
                    try:
                        self._run_item(lst, items.nth(i), True)
                    except Exception as e:  # noqa: BLE001
                        self.log(f"  ! {i + 1}번째 또 실패: {e}")
                        self.state["failed"].append({"list": key, "i": i})
                    self.save_state()
                    self.back_to_list(url, i + 2)
            self._retry_unassigned()
        finally:
            self.gm.close()
        self.write_notes()
        return self.report(True)

    def _retry_unassigned(self):
        """지역을 못 정했던 곳을 (규칙이 늘어난 뒤) 다시 판정해 저장한다.
        지도에 찍은 위치는 이름이 곧 주소다."""
        for rec in self.state["places"].values():
            where = rec["address"] or rec["name"]
            if rec.get("target") or not region.city_label(where) or not rec.get("url"):
                continue
            if rec["name"] in region.CITY_SIDO:  # '거제시' 처럼 도시 자체가 저장된 항목은 가게가 아니라 건너뛴다
                continue
            rec["target"] = region.list_name("", "", self.config, where)
            self.page.goto(rec["url"], timeout=NAV_TIMEOUT)
            self.page.locator("h1.DUwDvf").first.wait_for(timeout=UI_TIMEOUT)
            self.page.wait_for_timeout(1500)
            ok, reason = self.gm.save_current(rec["target"])
            rec["saved"], rec["reason"] = ok, reason
            self.log(f"  {'✅' if ok else '⚠️'} {rec['name']} → {rec['target']} {reason}")
            self.save_state()

    def _run_item(self, lst, item, apply):
        page = self.page
        item.scroll_into_view_if_needed()
        item.click()
        page.locator("h1.DUwDvf").first.wait_for(timeout=UI_TIMEOUT)
        page.wait_for_timeout(1500)
        info = self.gm.place_info()
        pid = info["id"] or f"name:{_norm(info['name'])}|{info['address']}"
        places = self.state["places"]
        if pid in places:
            if lst["name"] not in places[pid]["lists"]:
                places[pid]["lists"].append(lst["name"])
            self.log(f"  = {info['name']} (이미 처리)")
            return
        # 지도에 직접 찍은 위치는 주소가 곧 이름이다 ('제주특별자치도 제주시 구좌읍 …')
        where = info["address"] or info["name"]
        label = region.city_label(where)
        target = region.list_name("", "", self.config, where) if label else ""
        rec = dict(info, lists=[lst["name"]], target=target, saved=False, reason="")
        if not target:
            rec["reason"] = "주소로 지역을 못 정함"
        elif apply:
            ok, reason = self.gm.save_current(target)
            rec["saved"], rec["reason"] = ok, reason
        places[pid] = rec
        mark = "✅" if rec["saved"] else ("·" if not apply and target else "⚠️")
        self.log(f"  {mark} {info['name']} → {target or '?'} {rec['reason']} | {info['address'][:40]}")

    # --- 옵시디언 ---
    def write_notes(self):
        store = Store(self.config["data_dir"])
        existing = {_norm(e["name"]): e for e in store.all_places()}
        made = updated = 0
        for pid, rec in self.state["places"].items():
            if not rec.get("target"):
                continue
            summary = "구글 지도 목록 " + ", ".join(rec["lists"]) + " 에서 가져옴"
            entry = existing.get(_norm(rec["name"]))
            if entry:  # 봇이 이미 저장한 장소 — 주소·목록만 채운다 (노트 위치는 그대로)
                entry["address"] = entry.get("address") or rec["address"]
                entry["gmaps_list"] = rec["target"]
                entry["gmaps_saved"] = entry.get("gmaps_saved") or rec["saved"]
                obsidian.write_place(entry, rec["target"], "", self.config)
                updated += 1
                continue
            sido, area = region.split_address(rec["address"] or rec["name"], self.config)
            entry = {
                "name": rec["name"], "category": category_of(rec["category"]),
                "sido": sido, "area": area, "address": rec["address"],
                "lat": None, "lng": None, "kakao_id": "", "kakao_url": "", "phone": "",
                "note": rec["category"], "confidence": 0.0, "checked": False,
                "sources": [rec["url"]] if rec["url"] else [],
                "gmaps_saved": rec["saved"] or rec["reason"] == "이미 저장됨",
                "gmaps_list": rec["target"], "gmaps_origin": rec["lists"],
            }
            if not entry["sources"]:
                continue
            store.data["places"][f"gmaps:{pid}"] = entry
            existing[_norm(rec["name"])] = entry
            obsidian.write_place(entry, rec["target"], summary, self.config)
            made += 1
        obsidian.write_index(store.all_places(), self.config)
        store.save()
        self.log(f"\n옵시디언: 새 노트 {made}개, 기존 노트 갱신 {updated}개")

    def report(self, apply):
        places = list(self.state["places"].values())
        by_target = {}
        for p in places:
            by_target.setdefault(p["target"] or "(지역 미정)", []).append(p)
        lines = [f"\n고유 장소 {len(places)}곳 → 새 목록 {len([t for t in by_target if t != '(지역 미정)'])}개"]
        for t in sorted(by_target):
            ps = by_target[t]
            failed = [p["name"] for p in ps if apply and p["target"] and not p["saved"]
                      and p["reason"] != "이미 저장됨"]
            lines.append(f"  {t}: {len(ps)}곳" + (f" (실패 {len(failed)}: {', '.join(failed)})" if failed else ""))
        if apply:
            targets = set(by_target)
            old = [lst["name"] for lst in self.state["lists"] if lst["name"] not in targets]
            lines.append("\n직접 지울 기존 목록: " + ", ".join(dict.fromkeys(old)))
        text = "\n".join(lines)
        self.log(text)
        return text
