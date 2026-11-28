"""addlist: 구글 지도에 아직 없는 신규 가게(주소 핀)와 잘못 저장된 기록 바로잡기."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from places import addlist  # noqa: E402
from places.store import Store  # noqa: E402


class FakeGM:
    def __init__(self, pages):
        self.pages, self.saved, self.q = pages, [], ""

    def start(self): pass
    def close(self): pass
    def _open_place(self, q): self.q = q
    def place_info(self): return self.pages[self.q]
    def save_current(self, target):
        self.saved.append(target)
        return True, "저장"


def _cfg(root):
    return {"metro": ["서울", "부산"], "gmaps": {"list_prefix": "📍"}, "data_dir": str(root / "data"),
            "obsidian": {"vault_path": str(root / "vault"), "folder": "장소", "index_note": "장소 목록"}}


class AddListPinTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cfg = _cfg(self.root)

    def test_address_pin_is_saved_under_the_shop_name(self):
        pages = {"서울 마포구 성미산로 183": {"name": "서울특별시 마포구 성미산로 183", "address": "", "category": "",
                                            "id": "", "url": "", "lat": 37.5634, "lng": 126.9260}}
        gm = FakeGM(pages)
        spec = {"source": "https://www.instagram.com/p/X/", "tags": ["신규오픈"],
                "places": [{"name": "홉하우스", "city": "서울", "pin": True, "title": "홉하우스",
                            "query": "서울 마포구 성미산로 183", "addr": "마포구 성미산로 183",
                            "category": "카페", "note": "가오픈 · @hobb.haus"}]}
        run = addlist.AddList(self.cfg, spec, log=lambda m: None, gm=gm)
        run.run(apply=True)
        self.assertEqual(gm.saved, ["📍서울"])
        note = (self.root / "vault" / "장소" / "서울" / "마포" / "홉하우스.md").read_text(encoding="utf-8")
        self.assertIn('address: "서울특별시 마포구 성미산로 183"', note)
        self.assertIn("location: [37.5634, 126.926]", note)
        self.assertIn("@hobb.haus", note)

    def test_pin_at_a_different_address_is_refused(self):
        pages = {"x": {"name": "서울특별시 마포구 성미산로17길 85", "address": "", "category": "", "id": "",
                       "url": "", "lat": 37.5, "lng": 126.9}}
        gm = FakeGM(pages)
        spec = {"places": [{"name": "홉하우스", "city": "서울", "pin": True, "query": "x",
                            "addr": "마포구 성미산로 183"}]}
        run = addlist.AddList(self.cfg, spec, log=lambda m: None, gm=gm)
        run.run(apply=True)
        self.assertEqual(gm.saved, [])  # 길 번호가 다르면 저장하지 않는다
        self.assertEqual(run.results[0]["status"], "불일치")

    def test_existing_wrong_entry_is_corrected_not_duplicated(self):
        store = Store(self.cfg["data_dir"])
        store.data["places"]["name:서울:남산:이디티로스터리남산점"] = {
            "name": "이디티로스터리 남산점", "category": "카페", "sido": "서울", "area": "남산",
            "address": "중구 남산동2가 25-26 1, 2층", "lat": None, "lng": None, "sources": ["https://ig/p/1"],
            "gmaps_saved": True, "gmaps_mismatch": True, "gmaps_list": "📍서울", "note": "", "tags": []}
        store.save()
        pages = {"edt": {"name": "EDTRoastery", "address": "서울특별시 중구 퇴계로20길 51 1층",
                         "category": "커피 로스터리", "id": "0x1:0x2", "url": "https://maps.google.com/?cid=2",
                         "lat": 37.5580, "lng": 126.9866}}
        spec = {"source": "https://ig/p/1", "places": [
            {"name": "이디티로스터리 남산점", "city": "서울", "query": "edt", "alt": ["EDTRoastery"],
             "title": "이디티로스터리 남산점", "category": "카페", "fix_location": True,
             "note": "신규 카페 · 3m 거리라 같은 가게"}]}
        run = addlist.AddList(self.cfg, spec, log=lambda m: None, gm=FakeGM(pages))
        run.run(apply=True)
        places = Store(self.cfg["data_dir"]).data["places"]
        self.assertEqual(len(places), 1)  # 새로 만들지 않고 고쳤다
        entry = next(iter(places.values()))
        self.assertNotIn("gmaps_mismatch", entry)
        note = next((self.root / "vault" / "장소").rglob("이디티로스터리 남산점.md")).read_text(encoding="utf-8")
        self.assertIn("신규 카페 · 3m 거리라 같은 가게", note)
        self.assertLess(note.index("3m 거리라"), note.index("## 메모"))  # 메모 칸 앞, 출처 쪽에 쓴다
        self.assertEqual(note.count("3m 거리라"), 1)
        self.assertEqual(entry["address"], "서울특별시 중구 퇴계로20길 51 1층")
        self.assertEqual((entry["lat"], entry["lng"]), (37.5580, 126.9866))


if __name__ == "__main__":
    unittest.main()
