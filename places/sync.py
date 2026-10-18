"""구글 지도 앱에서 직접 저장한 장소를 봇 기록에 가져온다 (/sync).

휴대폰 구글 지도에서 저장한 장소는 봇의 places.json 에 없어서 '내 주변 추천'(nearby.py)에 안 나온다.
저장됨 탭의 목록을 다시 읽어 **봇이 모르는 장소만** 찾아 places.json · coords.json · 옵시디언 노트에 넣는다.

  텔레그램 /sync            봇 안에서 (봇의 크롬을 쓴다)
  python -m places sync     봇을 끈 상태에서 (같은 크롬 프로필이라 동시에 못 연다)

방법: 목록마다 장소 줄의 이름만 읽어 이미 아는 이름과 견주고(전부 열면 수백 곳이라 오래 걸린다),
새 이름만 장소 페이지를 열어 이름·주소·좌표·cid 를 읽는다. 열어 보니 이미 아는 장소(cid·이름·좌표가
같음)면 새로 만들지 않고, 그 목록 이름을 별칭으로 기억해 다음부터 열지 않는다.
사용자가 저장한 목록은 그대로 두고 **구글 지도 목록·장소를 지우지 않는다**.
시 단위 목록(📍경기 성남시)에도 넣을지는 한 번만 묻고 답을 기억한다 (store.data["sync"]).
"""

import re
import time
from pathlib import Path

from . import coords, nearby, obsidian, region
from .namematch import address_match, name_matches, norm
from .reorg import ITEM, category_of
from .gmaps import NAV_TIMEOUT, NO_SAVE, UI_TIMEOUT
from .progress import Progress

STEPS = ("목록 읽기", "새 장소 찾기", "노트·좌표 저장")
HINTS = ("보통 30초", "목록마다 1~2초", "")
SAME_PLACE_M = 300  # 이름이 비슷하고 좌표가 이 안이면 같은 장소로 본다
YES = {"예", "네", "응", "y", "yes", "넣어", "넣기", "ㅇㅇ", "시목록"}
NO = {"아니", "아니오", "아니요", "n", "no", "안", "ㄴㄴ"}
RATING = re.compile(r"^(\d(?:\.\d)?)\(([\d,]+)\)$")


def parse_item(text):
    """목록의 장소 줄 → (이름, 평점 또는 None). 줄 모양은 '이름\\n4.5(1,234)\\n₩10,000~\\n· 카페'."""
    parts = [p.strip() for p in (text or "").split("\n") if p.strip()]
    if not parts:
        return "", None
    m = RATING.match(parts[1]) if len(parts) > 1 else None
    return parts[0], (float(m.group(1)) if m else None)


def answer_of(word):
    """'/sync 예' 의 예·아니오 → True/False, 그 밖이면 None."""
    w = (word or "").strip().lower()
    return True if w in YES else False if w in NO else None


class GmapsSource:
    """실제 구글 지도 화면 (Reorg 의 목록 읽기를 그대로 쓴다). 테스트에서는 가짜로 바꾼다."""

    def __init__(self, config, gm, log=print):
        from .reorg import Reorg
        self.gm = gm
        self.reader = Reorg(config, log, gm=gm)
        self.url = ""

    def start(self):
        self.gm.start()
        self.reader.page = self.gm.page

    def read_lists(self):
        return self.reader.read_lists()

    def read_texts(self, lst):
        """목록을 열어 장소 줄의 글을 모두 읽는다."""
        self.url = self.reader.open_list(lst)
        items = self.reader.load_items(lst["count"])
        return items.evaluate_all("els => els.map(e => e.innerText)")

    def open_list(self, lst, upto):
        """목록 화면을 열고 장소 줄을 upto 개까지 불러온다."""
        self.url = self.reader.open_list(lst)
        self.reader.load_items(upto)

    def open_item(self, lst, idx, name):
        """열어 둔 목록의 idx 번째 장소를 열어 place_info 를 돌려준다 (이름이 다르면 목록이 바뀐 것)."""
        page = self.reader.page
        items = page.locator(ITEM)
        if items.count() <= idx:
            items = self.reader.load_items(idx + 1)
        item = items.nth(idx)
        got, _ = parse_item(item.inner_text())
        if norm(got) != norm(name):  # 읽는 사이 목록에 장소가 추가·삭제됐다면 이름으로 다시 찾는다
            items = self.reader.load_items(lst["count"] + 5)
            names = [norm(parse_item(t)[0]) for t in items.evaluate_all("els => els.map(e => e.innerText)")]
            if norm(name) not in names:
                raise RuntimeError(f"목록이 바뀜 ({got})")
            item = items.nth(names.index(norm(name)))
        item.scroll_into_view_if_needed()
        item.click()
        page.locator("h1.DUwDvf").first.wait_for(timeout=UI_TIMEOUT)
        page.wait_for_timeout(1500)
        return self.gm.place_info()

    def back(self, lst, idx):
        self.reader.back_to_list(self.url, idx + 2)

    def open_url(self, url):
        page = self.reader.page
        page.goto(url, timeout=NAV_TIMEOUT)
        page.locator("h1.DUwDvf").first.wait_for(timeout=UI_TIMEOUT)
        page.wait_for_timeout(1500)

    def save_here(self, target):
        return self.gm.save_current(target)


class Sync:
    def __init__(self, config, store, source, log=print, progress=None):
        self.config, self.store, self.source, self.log = config, store, source, log
        self.progress = progress or Progress(steps=STEPS, hints=HINTS)
        state = store.data.setdefault("sync", {})
        state.setdefault("city", None)         # None=아직 안 물음 · True/False=답
        state.setdefault("alias", {})          # 목록 줄 이름(norm) → 장소 key
        state.setdefault("pending_city", [])   # 시 목록에 아직 못 넣은 새 장소 key
        self.state = state
        self.data_dir = config["data_dir"]
        self.sidecar = coords.load(self.data_dir)

    # --- 아는 장소 ---
    def known_names(self):
        names = {norm(e["name"]) for e in self.store.all_places()}
        places = self.store.data["places"]  # /undo 로 지운 장소의 별칭은 빼야 다시 가져온다
        return names | {n for n, key in self.state["alias"].items() if key in places}

    def _existing(self, key, info):
        """열어 본 장소가 이미 기록에 있으면 그 key."""
        places = self.store.data["places"]
        if key in places:
            return key
        for k, e in places.items():
            if info["url"] and coords.cid_url(e) == info["url"]:
                return k
        sido = region.split_address(info["address"] or info["name"], self.config)[0]
        for k, e in places.items():
            if not name_matches(e["name"], info["name"]):
                continue
            ll = coords.of(k, e, self.sidecar)
            if ll and info.get("lat") is not None:  # 좌표가 있으면 그것으로 (지점이 다른 같은 이름 방지)
                near = nearby.distance_m(ll[0], ll[1], info["lat"], info["lng"]) <= SAME_PLACE_M
                if near:
                    return k
            elif address_match(e.get("address"), info["address"]) or e.get("sido") == sido:
                return k
        return None

    # --- 실행 ---
    def run(self, city=None):
        """목록을 읽어 새 장소를 가져온다. city: 시 목록에도 넣을지 (None 이면 기억한 답)."""
        if city is not None:
            self.set_city(city)
        result = {"lists": 0, "lines": 0, "new": [], "linked": 0, "failed": [], "bad_lists": [],
                  "city_saved": [], "city_failed": [], "ask_city": False}
        self.progress.start("구글 지도 동기화")
        self.progress.stage(0)
        self.source.start()
        lists = [lst for lst in self.source.read_lists() if lst["count"] > 0]
        result["lists"] = len(lists)
        found = self._scan(lists, result)
        known = self.known_names()
        todo = {n: f for n, f in found.items() if n not in known}
        self.log(f"장소 줄 {result['lines']}개 → 모르는 이름 {len(todo)}개")
        self.progress.stage(2, total=len(todo))
        by_list = {}
        for f in todo.values():
            by_list.setdefault(f["pos"][0], []).append(f)
        for li, group in by_list.items():
            self._resolve_list(lists[li], sorted(group, key=lambda f: f["pos"][1]), found, result)
        self._finish(result)
        return result

    def _scan(self, lists, result):
        """목록마다 장소 줄 이름을 읽는다 → {norm: {name, rating, lists, pos}}."""
        found = {}
        self.progress.stage(1)
        for li, lst in enumerate(lists):
            self.progress.stage(1, f"{li + 1}/{len(lists)} {lst['name']}")
            try:
                texts = self.source.read_texts(lst)
            except Exception as e:  # noqa: BLE001 — 목록 하나 실패로 멈추지 않게
                self.log(f"  ! 목록 '{lst['name']}' 못 읽음: {str(e)[:80]}")
                result["bad_lists"].append(lst["name"])
                continue
            self.log(f"  목록 {li + 1}/{len(lists)} {lst['name']}: {len(texts)}/{lst['count']}곳 읽음")
            for idx, text in enumerate(texts):
                name, rating = parse_item(text)
                if not name:
                    continue
                result["lines"] += 1
                f = found.setdefault(norm(name), {"name": name, "rating": rating, "lists": [],
                                                  "pos": (li, idx)})
                if lst["name"] not in f["lists"]:
                    f["lists"].append(lst["name"])
        return found

    def _resolve_list(self, lst, group, found, result):
        """한 목록에서 모르는 이름들을 열어 기록한다 (이름은 목록 안 순서로 연다)."""
        try:
            self.source.open_list(lst, group[-1]["pos"][1] + 1)
        except Exception as e:  # noqa: BLE001
            self.log(f"  ! 목록 '{lst['name']}' 못 열음: {str(e)[:80]}")
            result["bad_lists"].append(lst["name"])
            result["failed"].extend(f["name"] for f in group)
            self.progress.done += len(group)
            return
        for f in group:
            idx = f["pos"][1]
            if norm(f["name"]) in self.known_names():  # 앞서 열다가 같은 장소로 확인된 이름
                self.progress.advance(f["name"])
                continue
            try:
                info = self.source.open_item(lst, idx, f["name"])
                if not info["name"]:
                    raise RuntimeError("장소 정보를 못 읽음")
                self._record(f, info, result)
            except Exception as e:  # noqa: BLE001 — 한 곳 실패로 멈추지 않게, 다음 /sync 가 다시 시도
                self.log(f"  ! {f['name']}: {str(e)[:80]}")
                result["failed"].append(f["name"])
            self.progress.advance(f["name"])
            try:
                self.source.back(lst, idx)
            except Exception as e:  # noqa: BLE001
                self.log(f"  ! 목록으로 못 돌아옴: {str(e)[:80]}")
                break
            time.sleep(0.3)
        for name in (f["name"] for f in group if norm(f["name"]) not in self.known_names()
                     and f["name"] not in result["failed"]):
            result["failed"].append(name)  # 목록으로 못 돌아와 열지 못한 이름

    def _record(self, f, info, result):
        pid = info["id"]
        key = f"gmaps:{pid or 'name:' + norm(info['name']) + '|' + info['address']}"
        same = self._existing(key, info)
        if same:
            entry = self.store.data["places"][same]
            self._merge(same, entry, f, info)
            self.state["alias"][norm(f["name"])] = same
            result["linked"] += 1
            self.log(f"  = {f['name']} → 이미 아는 장소 ({entry['name']})")
            return
        sido, area = region.split_address(info["address"] or info["name"], self.config)
        category = info["category"]
        note = " · ".join(p for p in (category, f"구글 평점 {f['rating']}" if f["rating"] else "") if p)
        entry = {
            "name": info["name"], "category": category_of(category),
            "sido": sido, "area": area, "address": info["address"],
            "lat": info.get("lat"), "lng": info.get("lng"),
            "kakao_id": "", "kakao_url": "", "phone": "",
            "note": note, "confidence": 1.0, "checked": True,
            "sources": [info["url"] or f"https://www.google.com/maps/search/?api=1&query={info['name']}"],
            "gmaps_saved": True, "gmaps_list": f["lists"][0], "gmaps_origin": list(f["lists"]),
            "gmaps_synced": time.strftime("%Y-%m-%d"),
        }
        self.store.data["places"][key] = entry
        self.state["alias"][norm(f["name"])] = key
        if info.get("lat") is not None:
            self.sidecar[key] = [info["lat"], info["lng"]]
        obsidian.write_place(entry, entry["gmaps_list"], "구글 지도 목록 " + ", ".join(f["lists"]) + " 에서 가져옴",
                             self.config)
        result["new"].append(dict(entry, key=key))
        self.log(f"  ✅ {info['name']} → {sido} {area} | {info['address'][:40]}")
        if self.state["city"]:
            self._city_save(key, entry, result)
        elif self.state["city"] is None and self._city_target(entry):
            self.state["pending_city"].append(key)

    def _merge(self, key, entry, f, info):
        """이미 있는 장소에 목록 이름·좌표를 보탠다 (노트는 건드리지 않는다)."""
        origin = entry.setdefault("gmaps_origin", [])
        origin.extend(n for n in f["lists"] if n not in origin)
        entry["gmaps_saved"] = True
        if entry.get("lat") is None and key not in self.sidecar and info.get("lat") is not None:
            self.sidecar[key] = [info["lat"], info["lng"]]

    # --- 시 단위 목록 ---
    def _city_target(self, entry):
        label = region.city_label(entry.get("address") or "") or entry.get("sido")
        if not label:
            return ""
        target = region.list_name(entry.get("sido", ""), entry.get("area", ""), self.config, entry.get("address", ""))
        return "" if target in (entry.get("gmaps_origin") or []) else target

    def _city_save(self, key, entry, result):
        """지금 열려 있는 장소를 시 단위 목록에도 넣는다."""
        target = self._city_target(entry)
        if not target:
            return True
        ok, reason = self.source.save_here(target)
        if ok:
            entry["gmaps_list"] = target
            obsidian.write_place(entry, target, "", self.config)
            result["city_saved"].append(entry["name"])
            self.log(f"    → {target} ({reason})")
            return True
        result["city_failed"].append(entry["name"])
        self.log(f"    ⚠️ {target} 저장 실패: {reason}")
        if reason != NO_SAVE and key not in self.state["pending_city"]:
            self.state["pending_city"].append(key)
        return False

    def set_city(self, yes):
        self.state["city"] = bool(yes)
        if not yes:
            self.state["pending_city"] = []

    def apply_pending_city(self):
        """'예' 라고 답한 뒤, 그동안 못 넣은 새 장소를 시 단위 목록에 넣는다."""
        result = {"city_saved": [], "city_failed": []}
        keys = list(dict.fromkeys(self.state["pending_city"]))
        self.state["pending_city"] = []
        self.progress.start("시 단위 목록에 넣기")
        self.progress.stage(2, total=len(keys))
        if keys:
            self.source.start()
        for key in keys:
            entry = self.store.data["places"].get(key)
            url = coords.cid_url(entry) if entry else ""
            try:
                if not url:
                    raise RuntimeError("지도 링크 없음")
                self.source.open_url(url)
                self._city_save(key, entry, result)
            except Exception as e:  # noqa: BLE001
                self.log(f"  ! {entry['name'] if entry else key}: {str(e)[:80]}")
                result["city_failed"].append(entry["name"] if entry else key)
                self.state["pending_city"].append(key)
            self.progress.advance(entry["name"] if entry else "")
        self._finish(result)
        return result

    def _finish(self, result):
        if result.get("new") is not None:
            self.state["last"] = f"{time.strftime('%m-%d %H:%M')} · 새 장소 {len(result['new'])}곳"
        Path(self.data_dir).mkdir(parents=True, exist_ok=True)
        coords.save(self.data_dir, self.sidecar)
        obsidian.write_index(self.store.all_places(), self.config)
        self.store.save()
        result["ask_city"] = self.state["city"] is None and bool(self.state["pending_city"])


# --- 보고 ---
def report(result, esc=lambda s: s, limit=12):
    """사람이 읽는 요약. 모바일이라 짧게 (새 장소는 limit 곳까지만 이름을 보여 준다)."""
    lines = []
    if "lists" in result:
        new = result["new"]
        head = f"🔄 구글 지도 동기화 · 목록 {result['lists']}개 · 장소 {result['lines']}곳 확인"
        lines.append(head)
        if new:
            lines.append(f"🆕 봇이 몰랐던 새 장소 {len(new)}곳 저장")
            for i, e in enumerate(new[:limit], 1):
                where = " ".join(p for p in (e["sido"], e["area"]) if p)
                lines.append(f"{i}. <b>{esc(e['name'])}</b> · {esc(e['category'])} · {esc(where)} "
                             f"({esc(', '.join(e['gmaps_origin']))})")
            if len(new) > limit:
                lines.append(f"… 외 {len(new) - limit}곳")
        else:
            lines.append("✅ 새로 가져올 장소가 없어요.")
        if result["linked"]:
            lines.append(f"= 이름만 달랐던 아는 장소 {result['linked']}곳은 연결만 했어요")
        if result["failed"]:
            lines.append("⚠️ 못 연 장소 (다음 /sync 에서 다시): " + esc(", ".join(result["failed"][:8])))
        if result["bad_lists"]:
            lines.append("⚠️ 못 읽은 목록: " + esc(", ".join(result["bad_lists"])))
    if result["city_saved"]:
        lines.append(f"📍 시 단위 목록에도 {len(result['city_saved'])}곳 넣었어요")
    if result["city_failed"]:
        lines.append("⚠️ 시 단위 목록 저장 실패: " + esc(", ".join(result["city_failed"][:8])))
    if result.get("ask_city"):
        lines.append("\n새 장소를 시 단위 목록(📍경기 성남시 …)에도 넣을까요? (한 번만 물어요)\n"
                     "/sync 예 · /sync 아니오")
    return "\n".join(lines)
