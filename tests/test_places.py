"""인스타 장소 아카이브 봇 테스트 — 네트워크·API 없이 돈다."""

import json
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from places import extract, geocode, instagram, region  # noqa: E402
from places.app import App  # noqa: E402
from places.telegram_bot import handle_update  # noqa: E402
from places.store import Store  # noqa: E402

EMBED = """<html><body><div class="EmbedCaption">
<div class="Caption"><a class="CaptionUsername" href="#">seoul_cafe</a><br/><br/>
성수 카페 추천 ☕<br/>📍어니언 성수 서울 성동구 아차산로9길 8<br/>
빵이 맛있어요 &amp; 넓어요<br/>#성수카페
<div class="CaptionComments"><a>댓글 12개 모두 보기</a></div></div></div></body></html>"""

OG = ('<meta property="og:description" content="좋아요 1,234개, 댓글 56개 - seoul_cafe님, '
      '2026년 10월 1일: &quot;연남 맛집 📍소금집 델리&quot;. " />')

CONFIG = {
    "region_alias": {"서교동": "홍대", "을지로3가": "을지로"},
    "metro": ["서울", "부산"],
    "gmaps": {"list_prefix": "📍"},
}


class InstagramTest(unittest.TestCase):
    def test_find_urls_normalizes_and_dedups(self):
        text = ("https://www.instagram.com/p/Ddxt9nOkeas/?utm_source=ig_web_copy_link&exln=x "
                "https://instagram.com/reel/AbC_1-2/ https://www.instagram.com/p/Ddxt9nOkeas/")
        self.assertEqual(instagram.find_urls(text), [
            ("Ddxt9nOkeas", "https://www.instagram.com/p/Ddxt9nOkeas/"),
            ("AbC_1-2", "https://www.instagram.com/reel/AbC_1-2/"),
        ])

    def test_parse_embed_keeps_lines_drops_comments(self):
        caption, user = instagram.parse_embed(EMBED)
        self.assertEqual(user, "seoul_cafe")
        self.assertIn("📍어니언 성수 서울 성동구 아차산로9길 8\n", caption)
        self.assertIn("빵이 맛있어요 & 넓어요", caption)
        self.assertNotIn("댓글", caption)
        self.assertNotIn("seoul_cafe", caption)

    def test_parse_og(self):
        caption, user = instagram.parse_og(OG)
        self.assertEqual(caption, "연남 맛집 📍소금집 델리")
        self.assertEqual(user, "seoul_cafe")

    def test_fetch_falls_back_to_og(self):
        pages = {"embed": SimpleNamespace(status_code=200, url="x", text="<html></html>"),
                 "post": SimpleNamespace(status_code=200, url="x", text=OG)}
        session = SimpleNamespace(get=lambda url, **kw: pages["embed" if "embed" in url else "post"])
        post = instagram.fetch("abc", "https://www.instagram.com/p/abc/", session=session)
        self.assertEqual((post.source, post.caption), ("og", "연남 맛집 📍소금집 델리"))

    def test_login_wall_is_failure(self):
        wall = SimpleNamespace(status_code=200, url="https://www.instagram.com/accounts/login/", text=OG)
        session = SimpleNamespace(get=lambda url, **kw: wall)
        self.assertEqual(instagram.fetch("abc", "u", session=session).caption, "")

    def test_blocked_request_falls_back_to_browser(self):
        calls = []

        def browser(code, url, channel):
            calls.append(channel)
            return instagram.Post(code, url, caption="성수 카페", source="browser")

        with unittest.mock.patch.object(instagram, "fetch", return_value=instagram.Post("abc", "u")):
            post = instagram.fetch_any("abc", "u", "chrome", browser=browser)
        self.assertEqual((post.source, calls), ("browser", ["chrome"]))


class RegionTest(unittest.TestCase):
    def test_metro_neighborhood(self):
        cases = {
            "서울 성동구 성수동2가 315-57": ("서울", "성수"),
            "서울 마포구 연남동 239-49": ("서울", "연남"),
            "서울 마포구 서교동 1-1": ("서울", "홍대"),
            "서울 중구 을지로3가 1": ("서울", "을지로"),
            "서울특별시 강남구 역삼1동 1": ("서울", "역삼"),
            "제주특별자치도 제주시 애월읍 고내리 1": ("제주", "애월"),
            "강원특별자치도 강릉시 교동 1": ("강원", "강릉"),
        }
        for address, expected in cases.items():
            self.assertEqual(region.from_address(address, CONFIG), expected, address)

    def test_list_name(self):
        self.assertEqual(region.list_name("서울", "성수", CONFIG), "📍서울 성수")
        self.assertEqual(region.list_name("", "", CONFIG), "📍기타")


class ExtractTest(unittest.TestCase):
    def test_extract_parses_structured_output(self):
        payload = {"summary": "성수 카페 소개", "places": [
            {"name": "어니언", "branch": "성수", "category": "카페", "country": "대한민국",
             "city": "서울", "area_hint": "성수", "address_hint": "아차산로9길 8", "note": ""},
            {"name": " ", "branch": "", "category": "기타", "country": "", "city": "",
             "area_hint": "", "address_hint": "", "note": ""},
        ]}
        seen = {}

        def create(**kwargs):
            seen.update(kwargs)
            return SimpleNamespace(stop_reason="end_turn", content=[
                SimpleNamespace(type="thinking"),
                SimpleNamespace(type="text", text=json.dumps(payload, ensure_ascii=False))])

        client = SimpleNamespace(messages=SimpleNamespace(create=create))
        result = extract.extract("캡션", {"extract": {"model": "m", "effort": "low"}}, client)
        self.assertEqual(seen["output_config"]["format"]["schema"], extract.SCHEMA)
        self.assertEqual(len(result.places), 1)
        self.assertEqual(result.places[0].query, "어니언 성수")
        self.assertTrue(result.places[0].domestic)

    def test_refusal_raises(self):
        client = SimpleNamespace(messages=SimpleNamespace(
            create=lambda **kw: SimpleNamespace(stop_reason="refusal", content=[])))
        with self.assertRaises(RuntimeError):
            extract.extract("캡션", {}, client)


DOC = {"id": "123", "place_name": "어니언 성수", "address_name": "서울 성동구 성수동2가 277-135",
       "road_address_name": "서울 성동구 아차산로9길 8", "x": "127.0581", "y": "37.5446",
       "place_url": "http://place.map.kakao.com/123", "phone": ""}


class GeocodeTest(unittest.TestCase):
    def test_domestic_resolves_via_kakao(self):
        cand = extract.Candidate(name="어니언", branch="성수", category="카페",
                                 country="대한민국", city="서울", area_hint="성수")
        place = geocode.resolve(cand, CONFIG, search=lambda q: [DOC])
        self.assertEqual((place.name, place.sido, place.area), ("어니언 성수", "서울", "성수"))
        self.assertEqual(place.key, "kakao:123")
        self.assertGreaterEqual(place.confidence, geocode.MIN_CONFIDENCE)

    def test_no_match_is_low_confidence(self):
        cand = extract.Candidate(name="없는가게", category="식당", country="대한민국", city="서울",
                                 area_hint="연남")
        place = geocode.resolve(cand, CONFIG, search=lambda q: [DOC])
        self.assertEqual((place.area, place.confidence), ("연남", 0.0))

    def test_without_kakao_key_uses_caption_hints(self):
        cand = extract.Candidate(name="어니언", branch="성수", category="카페",
                                 country="대한민국", city="서울특별시", area_hint="성수")
        with unittest.mock.patch.dict("os.environ", {"KAKAO_REST_API_KEY": ""}):
            place = geocode.resolve(cand, CONFIG)
        self.assertEqual((place.name, place.sido, place.area), ("어니언 성수", "서울", "성수"))
        self.assertFalse(place.checked)
        # 띄어쓰기만 다른 이름은 같은 장소로 합친다
        other = geocode.Place(name="어니언성수", category="카페", sido="서울", area="성수")
        self.assertEqual(place.key, other.key)

    def test_overseas_uses_country_city(self):
        cand = extract.Candidate(name="Blue Bottle", category="카페", country="일본", city="도쿄")
        place = geocode.resolve(cand, CONFIG, search=lambda q: self.fail("국내 검색 호출됨"))
        self.assertEqual((place.sido, place.area), ("일본", "도쿄"))


class FakeGMaps:
    def __init__(self, ok=True):
        self.ok, self.calls = ok, []

    def save(self, query, list_name):
        self.calls.append((query, list_name))
        return self.ok, ""


def _candidates(*names):
    return lambda caption: extract.Extraction(summary="요약", places=[
        extract.Candidate(name=n, category="카페", country="대한민국", city="서울", area_hint="성수")
        for n in names])


class AppTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.config = dict(CONFIG, data_dir=str(root / "data"),
                           obsidian={"vault_path": str(root / "vault"), "folder": "Places",
                                     "index_note": "장소 목록"})
        self.gmaps = FakeGMaps()
        self.app = App(self.config, gmaps=self.gmaps,
                       fetch=lambda code, url: instagram.Post(code, url, caption="캡션 " * 10),
                       extractor=_candidates("어니언"),
                       resolver=lambda c: geocode.resolve(c, self.config, search=lambda q: [DOC]))
        self.vault = root / "vault" / "Places"

    def tearDown(self):
        self.tmp.cleanup()

    def test_url_saves_everywhere(self):
        [reply] = self.app.handle("https://www.instagram.com/p/AAA/?utm_source=x")
        self.assertIn("📍 1곳 저장", reply)
        self.assertIn("✅지도", reply)
        self.assertEqual(self.gmaps.calls, [("어니언 성수 서울 성동구 아차산로9길 8", "📍서울 성수")])
        note = (self.vault / "서울" / "성수" / "어니언 성수.md").read_text(encoding="utf-8")
        self.assertIn("location: [37.5446, 127.0581]", note)
        self.assertIn("gmaps_saved: true", note)
        index = (self.vault / "장소 목록.md").read_text(encoding="utf-8")
        self.assertIn("[[Places/서울/성수/어니언 성수|어니언 성수]]", index)
        # 기록은 레포가 아니라 data_dir 에
        self.assertTrue(Store(self.config["data_dir"]).seen("AAA"))

    def test_low_credit_is_explained(self):
        def broke(caption):
            raise RuntimeError("Your credit balance is too low to access the Anthropic API.")
        self.app.extractor = broke
        [reply] = self.app.handle("https://www.instagram.com/p/AAA/")
        self.assertIn("크레딧이 부족", reply)

    def test_same_place_from_second_post_merges(self):
        self.app.handle("https://www.instagram.com/p/AAA/")
        note_path = self.vault / "서울" / "성수" / "어니언 성수.md"
        note_path.write_text(note_path.read_text(encoding="utf-8") + "내 메모\n", encoding="utf-8")
        self.app.handle("https://www.instagram.com/p/BBB/")
        self.assertEqual(len(self.gmaps.calls), 1)  # 이미 저장한 장소는 다시 누르지 않는다
        note = note_path.read_text(encoding="utf-8")
        self.assertIn("p/BBB/", note)
        self.assertIn("내 메모", note)
        self.assertEqual(len(self.app.store.all_places()), 1)

    def test_seen_post_is_skipped(self):
        self.app.handle("https://www.instagram.com/p/AAA/")
        [reply] = self.app.handle("https://www.instagram.com/p/AAA/")
        self.assertIn("이미 저장한 게시물", reply)

    def test_gmaps_failure_gives_link(self):
        self.gmaps.ok = False
        [reply] = self.app.handle("https://www.instagram.com/p/AAA/")
        self.assertIn("자동저장 실패", reply)
        self.assertIn("google.com/maps/search", reply)
        self.assertIn("⚠️지도 미저장", (self.vault / "장소 목록.md").read_text(encoding="utf-8"))

    def test_caption_fallback_then_paste(self):
        self.app.fetch = lambda code, url: instagram.Post(code, url)
        [reply] = self.app.handle("https://www.instagram.com/p/CCC/")
        self.assertIn("캡션을 가져오지 못했어요", reply)
        [reply] = self.app.handle("성수 카페 추천 📍어니언 성수 빵 맛집입니다 꼭 가보세요")
        self.assertIn("📍 1곳 저장", reply)
        self.assertTrue(self.app.store.seen("CCC"))

    def test_caption_sent_with_url_skips_fetch(self):
        self.app.fetch = lambda code, url: self.fail("캡션이 있는데 수집 시도")
        [reply] = self.app.handle("https://www.instagram.com/p/DDD/ 성수 카페 추천 📍어니언 성수 빵 맛집")
        self.assertIn("1곳 저장", reply)

    def test_undo_removes_new_places_only(self):
        self.app.handle("https://www.instagram.com/p/AAA/")
        self.app.extractor = _candidates("어니언", "새가게")
        self.app.resolver = lambda c: geocode.resolve(
            c, self.config, search=lambda q: [DOC] if "어니언" in q else [])
        self.app.handle("https://www.instagram.com/p/BBB/")
        self.assertEqual(len(self.app.store.all_places()), 2)
        reply = self.app.command("/undo")
        self.assertIn("새가게", reply)
        self.assertEqual([p["name"] for p in self.app.store.all_places()], ["어니언 성수"])
        self.assertEqual(self.app.store.all_places()[0]["sources"], ["https://www.instagram.com/p/AAA/"])
        self.assertFalse(self.app.store.seen("BBB"))
        note = (self.vault / "서울" / "성수" / "어니언 성수.md").read_text(encoding="utf-8")
        self.assertNotIn("  - \"https://www.instagram.com/p/BBB/\"", note)
        self.assertFalse((self.vault / "서울" / "성수" / "새가게.md").exists())

    def test_without_kakao_no_per_place_warning(self):
        self.app.resolver = lambda c: geocode.resolve(c, self.config, search=None)
        with unittest.mock.patch.dict("os.environ", {"KAKAO_REST_API_KEY": ""}):
            [reply] = self.app.handle("https://www.instagram.com/p/AAA/")
        self.assertIn("📍 1곳 저장", reply)
        self.assertNotIn("위치 확인 필요", reply)
        self.assertIn("카카오 키가 없어", reply)
        self.assertEqual(self.gmaps.calls, [("어니언 성수", "📍서울 성수")])
        self.assertTrue((self.vault / "서울" / "성수" / "어니언.md").exists())

    def test_kakao_miss_warns_per_place(self):
        self.app.resolver = lambda c: geocode.resolve(c, self.config, search=lambda q: [])
        [reply] = self.app.handle("https://www.instagram.com/p/AAA/")
        self.assertIn("⚠️위치 확인 필요", reply)
        self.assertNotIn("카카오 키가 없어", reply)

    def test_stats_and_recent(self):
        self.app.handle("https://www.instagram.com/p/AAA/")
        self.assertIn("서울 성수: 1", self.app.command("/stats"))
        self.assertIn("어니언 성수", self.app.command("/recent"))


def _update(chat_id, text):
    return {"update_id": 1, "message": {"chat": {"id": int(chat_id)}, "text": text}}


class BotTest(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.reply = lambda chat_id, text: self.sent.append((chat_id, text))
        self.app = SimpleNamespace(handle=lambda text: ["결과"])

    def test_no_chat_id_configured_tells_id(self):
        handle_update(self.app, _update("111", "/start"), "", self.reply, set())
        self.assertEqual(len(self.sent), 1)
        self.assertIn("111", self.sent[0][1])

    def test_other_chat_is_told_once(self):
        notified = set()
        handle_update(self.app, _update("222", "hi"), "111", self.reply, notified)
        handle_update(self.app, _update("222", "hi"), "111", self.reply, notified)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("다른 채팅방 전용", self.sent[0][1])
        self.assertIn("222", self.sent[0][1])

    def test_url_gets_ack_then_result(self):
        handle_update(self.app, _update("111", "https://www.instagram.com/p/AAA/"), "111",
                      self.reply, set())
        self.assertTrue(self.sent[0][1].startswith("🔎"))
        self.assertEqual(self.sent[-1], ("111", "결과"))

    def test_handler_crash_is_reported(self):
        def boom(text):
            raise ValueError("x")
        self.app.handle = boom
        handle_update(self.app, _update("111", "/start"), "111", self.reply, set())
        self.assertIn("오류", self.sent[-1][1])


if __name__ == "__main__":
    unittest.main()
