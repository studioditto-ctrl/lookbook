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
from places.gmaps import MISMATCH  # noqa: E402
from places.namematch import address_match, name_matches  # noqa: E402
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

    def test_logged_in_session_comes_first(self):
        slides = instagram.Post("abc", "u", caption="소개팅 성지", source="session", images=[b"img"])
        session = SimpleNamespace(configured=True, fetch=lambda code, url: slides)
        with unittest.mock.patch.object(instagram, "fetch", side_effect=AssertionError("호출되면 안 됨")):
            self.assertIs(instagram.fetch_any("abc", "u", session=session), slides)

    def test_session_failure_falls_back(self):
        session = SimpleNamespace(configured=True, fetch=lambda code, url: None)
        plain = instagram.Post("abc", "u", caption="캡션", source="og")
        with unittest.mock.patch.object(instagram, "fetch", return_value=plain):
            self.assertIs(instagram.fetch_any("abc", "u", session=session), plain)

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
        self.assertEqual(region.list_name("서울", "성수", CONFIG), "📍서울")
        self.assertEqual(region.list_name("", "", CONFIG), "📍기타")
        city = {"gmaps": {"list_prefix": "📍"}}  # 기본은 시 단위
        cases = {
            "경기도 성남시 분당구 판교로 255번길 9-22": "📍경기 성남시",
            "경기 성남시 분당구 삼평동 647": "📍경기 성남시",  # 카카오 표기
            "강원특별자치도 평창군 대관령면": "📍강원",
            "서울특별시 성동구 아차산로9길 8": "📍서울",
            "인천광역시 옹진군 영흥면": "📍인천",
            "제주특별자치도 서귀포시 안덕면": "📍제주 서귀포시",
            "Jl. Raya Ubud, Ubud, Kabupaten Gianyar, Bali 80571 인도네시아": "📍인도네시아",
            "日本、〒604-8005 京都府京都市": "📍일본",
            "2F, 13 한남대로27가길 용산구 서울특별시": "📍서울",  # 거꾸로 적힌 주소
            "1층 장승포동 거제시 경상남도": "📍경남 거제시",
            "서귀포시 남원읍 서성로652번길 199": "📍제주 서귀포시",  # 도가 빠진 주소
            "광주시 오포읍 문형산길 1": "📍경기 광주시",
        }
        for address, expected in cases.items():
            self.assertEqual(region.list_name("", "", city, address), expected, address)
        self.assertEqual(region.list_name("경남", "거제", city), "📍경남")  # 주소가 없으면 시도
        grouped = {"gmaps": {"list_prefix": "📍", "list_unit": "region",
                             "list_groups": {"인천": "서울·경기"}}}
        self.assertEqual(region.list_name("인천", "영흥도", grouped), "📍서울·경기")
        self.assertEqual(region.list_name("일본", "도쿄", grouped), "📍일본")
        by_area = {"gmaps": {"list_prefix": "📍", "list_unit": "area"}}
        self.assertEqual(region.list_name("서울", "성수", by_area), "📍서울 성수")

    def test_split_address_for_note_folders(self):
        cfg = dict(CONFIG, metro=["서울", "부산", "인천"])
        cases = {
            "경기도 성남시 분당구 서현로494번길 9": ("경기", "성남"),
            "2F, 13 한남대로27가길 용산구 서울특별시": ("서울", "용산"),
            "성남시": ("경기", "성남"),
            "서귀포시 남원읍 서성로652번길 199": ("제주", "서귀포"),
            "일본 〒606-8406 Kyoto": ("일본", ""),
        }
        for address, expected in cases.items():
            self.assertEqual(region.split_address(address, cfg), expected, address)

    def test_gmaps_query_completes_partial_address(self):
        place = geocode.Place(name="카페굿웨더", category="카페", sido="경기", area="분당",
                              address="황새울로342번길 19")
        self.assertEqual(place.gmaps_query, "카페굿웨더 경기 분당 황새울로342번길 19")

    def test_long_reply_splits_on_lines(self):
        from places.telegram_bot import chunks
        text = "\n".join(f"{i}. <b>가게{i}</b> · 카페" for i in range(400))
        parts = chunks(text, 1000)
        self.assertTrue(all(len(p) <= 1000 for p in parts))
        self.assertTrue(all(p.count("<b>") == p.count("</b>") for p in parts))
        self.assertEqual("\n".join(parts), text)

    def test_gmaps_query_adds_region_without_address(self):
        place = geocode.Place(name="미소식", category="식당", sido="경남", area="거제")
        self.assertEqual(place.gmaps_query, "미소식 경남 거제")
        dup = geocode.Place(name="스타벅스 강릉강문해변", category="카페", sido="강원", area="강문해변")
        self.assertEqual(dup.gmaps_query, "스타벅스 강릉강문해변 강원")


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

    def test_overload_is_retried_but_other_errors_are_not(self):
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise RuntimeError("API 오류 503: This model is currently experiencing high demand.")
            return "ok"

        self.assertEqual(extract._with_retry(flaky, sleep=lambda s: None), "ok")
        self.assertEqual(len(calls), 3)

        def broken():
            calls.append(1)
            raise RuntimeError("API 오류 400: bad")

        calls.clear()
        with self.assertRaises(RuntimeError):
            extract._with_retry(broken, sleep=lambda s: None)
        self.assertEqual(len(calls), 1)

    def test_gemini_is_default(self):
        seen = {}

        def ask(system, content, schema, **kw):
            seen.update(schema=schema, content=content)
            return {"summary": "요약", "places": [{"name": "어니언", "category": "카페"}]}

        result = extract.extract("캡션", {}, ask=ask)
        self.assertEqual(seen["schema"], extract.SCHEMA)
        self.assertIn("캡션", seen["content"])
        self.assertEqual(result.places[0].name, "어니언")

    def test_secret_key_follows_provider(self):
        from places import config as cfg
        with unittest.mock.patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1"},
                                      clear=True):
            self.assertEqual(cfg.missing_secrets({}), ["GEMINI_API_KEY"])
            self.assertEqual(cfg.missing_secrets({"extract": {"provider": "claude"}}),
                             ["ANTHROPIC_API_KEY"])

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
    def __init__(self, ok=True, reason="", address="", opened_name=""):
        self.ok, self.reason, self.address, self.calls = ok, reason, address, []
        self.opened_name = opened_name  # 비워 두면 항상 찾던 가게가 열린다

    def save(self, query, list_name, expect=None, expect_addr=""):
        if self.opened_name and expect and not (
                name_matches(expect, self.opened_name) or address_match(expect_addr, self.address)):
            self.last = {"name": self.opened_name, "address": self.address, "list": ""}
            return False, MISMATCH  # 실제 GMaps 처럼 다른 가게가 열리면 저장하지 않는다
        if callable(list_name):  # 실제 GMaps 처럼 연 장소의 주소로 목록을 정한다
            list_name = list_name({"address": self.address})
        self.last = {"address": self.address, "list": list_name}
        self.calls.append((query, list_name))
        return self.ok, self.reason


class GMapsButtonTest(unittest.TestCase):
    def test_saved_label_still_matches(self):
        from places.gmaps import SAVE_BUTTON
        for label in ("저장", "저장됨", "Save", "Saved"):
            self.assertTrue(SAVE_BUTTON.search(label), label)
        self.assertFalse(SAVE_BUTTON.search("저장소"))


def _candidates(*names):
    return lambda caption, images=(): extract.Extraction(summary="요약", places=[
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

    def test_status_reports_counts(self):
        self.app.handle("https://www.instagram.com/p/AAA/")
        [reply] = self.app.handle("/status")
        self.assertIn("🟢 작동 중", reply)
        self.assertIn("저장 장소 1곳 · 지도 미저장 0곳", reply)
        self.assertIn("instagram.com/p/AAA", reply)

    def test_retry_saves_unsaved_places(self):
        self.app.gmaps = FakeGMaps(ok=False)
        self.app.handle("https://www.instagram.com/p/AAA/")
        self.assertFalse(Store(self.config["data_dir"]).all_places()[0]["gmaps_saved"])
        self.app.gmaps = FakeGMaps(ok=True, address="서울특별시 성동구 아차산로9길 8")
        [reply] = self.app.handle("/retry")
        self.assertIn("1/1곳 완료", reply)
        entry = Store(self.config["data_dir"]).all_places()[0]
        self.assertTrue(entry["gmaps_saved"])
        self.assertIn("✅ 지도에 저장 안 된", self.app.handle("/retry")[0])

    def test_wrong_shop_is_not_saved_and_not_retried(self):
        self.app.gmaps = FakeGMaps(opened_name="거제시", address="경상남도 거제시")
        [reply] = self.app.handle("https://www.instagram.com/p/AAA/")
        self.assertIn("같은 가게를 못 찾았어요 (열린 곳: 거제시)", reply)
        self.assertEqual(self.app.gmaps.calls, [])  # 저장 안 함
        entry = Store(self.config["data_dir"]).all_places()[0]
        self.assertFalse(entry["gmaps_saved"])
        self.assertTrue(entry["gmaps_mismatch"])
        self.assertIn("✅ 지도에 저장 안 된", self.app.handle("/retry")[0])  # 다시 시도 대상이 아니다
        self.assertIn("같은 가게를 못 찾은 1곳", self.app.handle("/status")[0])

    def test_place_without_save_button_is_explained(self):
        from places.gmaps import NO_SAVE
        self.app.gmaps = FakeGMaps(ok=False, reason=NO_SAVE)
        [reply] = self.app.handle("https://www.instagram.com/p/AAA/")
        self.assertIn("저장 기능이 없는 장소", reply)
        self.assertNotIn("자동저장 실패", reply)

    def test_url_saves_everywhere(self):
        [reply] = self.app.handle("https://www.instagram.com/p/AAA/?utm_source=x")
        self.assertIn("📍 1곳 저장", reply)
        self.assertIn("✅지도", reply)
        self.assertEqual(self.gmaps.calls, [("어니언 성수 서울 성동구 아차산로9길 8", "📍서울")])
        note = (self.vault / "서울" / "성수" / "어니언 성수.md").read_text(encoding="utf-8")
        self.assertIn("location: [37.5446, 127.0581]", note)
        self.assertIn("gmaps_saved: true", note)
        index = (self.vault / "장소 목록.md").read_text(encoding="utf-8")
        self.assertIn("[[Places/서울/성수/어니언 성수|어니언 성수]]", index)
        # 기록은 레포가 아니라 data_dir 에
        self.assertTrue(Store(self.config["data_dir"]).seen("AAA"))

    def test_low_credit_is_explained(self):
        def broke(caption, images=()):
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
        self.assertEqual(self.gmaps.calls, [("어니언 서울 성수", "📍서울")])
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


class NearbyTest(unittest.TestCase):
    PLACES = {
        "a": {"name": "리사르커피 성수", "category": "카페", "gmaps_saved": True, "gmaps_list": "📍서울",
              "lat": 37.5446, "lng": 127.0558, "sources": ["https://maps.google.com/?cid=111"], "tags": []},
        "b": {"name": "모수", "category": "식당", "gmaps_saved": True, "gmaps_list": "📍서울",
              "lat": 37.5330, "lng": 126.9990, "sources": [], "tags": ["미쉐린"], "note": "카페 같은 분위기"},
        "c": {"name": "먼 곳 카페", "category": "카페", "gmaps_saved": True, "gmaps_list": "📍제주 제주시",
              "lat": 33.5, "lng": 126.5, "sources": [], "tags": []},
        "d": {"name": "좌표 모름", "category": "카페", "gmaps_saved": True, "sources": []},
        "e": {"name": "저장 안 됨", "category": "카페", "gmaps_saved": False, "lat": 37.5446, "lng": 127.0558},
    }

    def test_parse_request(self):
        from places import nearby
        self.assertEqual(nearby.parse_request("근처 카페 3 추천해줘"), (["카페"], 3))
        self.assertEqual(nearby.parse_request("/near 맛집 99"), (["식당"], 50))
        self.assertEqual(nearby.parse_request("주변 미슐랭"), (["미쉐린"], 20))
        self.assertEqual(nearby.parse_request("/near"), ([], 20))
        self.assertEqual(nearby.parse_request("/근처카페"), (["카페"], 20))
        self.assertEqual(nearby.parse_request("/근처맛집@ditto_bot 3"), (["식당"], 3))
        self.assertEqual(nearby.parse_request("/근처장소"), (["명소"], 20))
        self.assertEqual(nearby.parse_request("근처 가볼만한 곳"), (["명소"], 20))
        self.assertTrue(nearby.wants_nearby("근처에 뭐 있어?"))
        self.assertFalse(nearby.wants_nearby("https://www.instagram.com/p/AAA/ 근처 카페"))

    def test_nearest_filters_and_sorts(self):
        from places import nearby
        places, missing = nearby.candidates(self.PLACES, {})
        self.assertEqual(missing, 1)  # 좌표 모름. 저장 안 된 곳은 세지도 않는다
        hits = nearby.nearest(places, 37.5450, 127.0560, [], 5)
        self.assertEqual([p["name"] for _, p in hits], ["리사르커피 성수", "모수", "먼 곳 카페"])
        cafes = nearby.nearest(places, 37.5450, 127.0560, ["카페"], 5)
        self.assertEqual([p["name"] for _, p in cafes], ["리사르커피 성수", "먼 곳 카페"])  # 설명 글의 '카페' 는 제외
        star = nearby.nearest(places, 37.5450, 127.0560, ["미쉐린"], 5)
        self.assertEqual([p["name"] for _, p in star], ["모수"])

    def test_recent_ordering_picks_nearest_then_shows_newest_first(self):
        from places import nearby
        raw = {f"p{i}": {"name": f"집{i}", "category": "카페", "gmaps_saved": True, "sources": [],
                         "lat": 37.5 + i * 0.001, "lng": 127.0, "saved_at": f"2026-10-{10 + i:02d}"}
               for i in range(5)}  # 집0 이 가장 가깝고 집4 가 가장 최근에 저장됨
        places, _ = nearby.candidates(raw, {})
        near3 = nearby.nearest(places, 37.5, 127.0, [], 3, recent=True)
        self.assertEqual([p["name"] for _, p in near3], ["집2", "집1", "집0"])  # 먼 집3·4 는 가까운 3곳에 못 든다
        text = nearby.render(near3, [], 0, recent=True)
        self.assertIn("10-12 저장", text)
        self.assertIn("최근 저장한 순", text)
        self.assertEqual([p["name"] for _, p in nearby.nearest(places, 37.5, 127.0, [], 3)], ["집0", "집1", "집2"])

    def test_default_shows_twenty(self):
        from places import nearby
        raw = {f"p{i}": {"name": f"집{i}", "category": "카페", "gmaps_saved": True, "sources": [],
                         "lat": 37.5 + i * 0.0001, "lng": 127.0} for i in range(30)}
        places, _ = nearby.candidates(raw, {})
        self.assertEqual(len(nearby.nearest(places, 37.5, 127.0, [], nearby.DEFAULT_N)), 20)

    def test_sights_are_exhibition_and_nature_places(self):
        from places import nearby
        places = [dict(self.PLACES["a"], category=c, name=c) for c in ("카페", "식당", "전시/공간", "자연/산책")]
        for p in places:
            p.update(lat=37.5446, lng=127.0558)
        hits = nearby.nearest(places, 37.5446, 127.0558, ["명소"], 5)
        self.assertEqual(sorted(p["name"] for _, p in hits), ["자연/산책", "전시/공간"])

    def test_sidecar_coords_fill_in(self):
        from places import nearby
        places, missing = nearby.candidates(self.PLACES, {"d": [37.54, 127.05]})
        self.assertEqual(missing, 0)
        self.assertIn("좌표 모름", [p["name"] for p in places])

    def test_confirmed_unknown_is_not_counted_as_missing(self):
        from places import nearby
        places, missing = nearby.candidates(self.PLACES, {"d": None})
        self.assertEqual(missing, 0)
        self.assertNotIn("좌표 모름", [p["name"] for p in places])

    def test_render(self):
        from places import nearby
        places, missing = nearby.candidates(self.PLACES, {})
        text = nearby.render(nearby.nearest(places, 37.5450, 127.0560, ["카페"], 2), ["카페"], missing)
        self.assertIn("<b>리사르커피 성수</b> · 카페 · 50m", text)
        self.assertIn("api=1&amp;destination=37.5446,127.0558", text)  # HTML 안이라 & 를 이스케이프
        self.assertIn("https://maps.google.com/?cid=111", text)
        self.assertIn("좌표를 아직 모르는 1곳", text)
        self.assertIn("근처 30km 안에는 없어요", nearby.render(
            nearby.nearest(places, 35.0, 129.0, [], 1), [], 0))  # 부산 에서는 제일 가까워도 멀다
        self.assertIn("맞는 저장 장소가 없어요", nearby.render([], ["숙소"], 0))

    def test_app_remembers_request_then_answers_location(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = dict(CONFIG, data_dir=str(Path(tmp.name) / "data"))
        store = Store(cfg["data_dir"])
        store.data["places"].update(self.PLACES)
        app = App(cfg, store=store)
        self.assertIn("카페 · 3곳", app.near_request(7, "근처 카페 3"))
        reply = app.nearby_reply(7, 37.5450, 127.0560)
        self.assertIn("리사르커피 성수", reply)
        self.assertNotIn("모수", reply)  # 카페만
        again = app.nearby_reply(7, 37.5450, 127.0560)  # 요청은 한 번 쓰면 잊는다 → 전체
        self.assertIn("모수", again)

    def test_plus_code_decodes_to_coordinates(self):
        from places import coords
        lat, lng = coords.decode_plus_code("849VCWC8+R9")  # 공개된 예시 코드 (구글 본사)
        self.assertAlmostEqual(lat, 37.42206, places=4)
        self.assertAlmostEqual(lng, -122.08406, places=4)
        # 로그인 없는 장소 주소에는 좌표 대신 플러스 코드가 든다
        url = "https://www.google.com/maps/place/x/data=!3m1!4b1!4m4!3m3!1s0x1:0x2!16s%2Fg%2F1!20s8Q58FHF2%2B6JF?hl=ko"
        lat, lng = coords.from_url(url)
        self.assertTrue(33.0 < lat < 34.0 and 126.0 < lng < 127.0)  # 제주

    def test_address_match_ignores_floors_and_needs_every_part(self):
        ok = address_match("인천광역시 옹진군 영흥면 영흥로 109-12", "주소: 인천광역시 옹진군 영흥면 영흥로 109-12 영흥수협회센터 34호")
        self.assertTrue(ok)
        # 같은 가게: 글은 'YMCA 2층', 구글은 '서울YMCA 2층'. 'B1F'·'2F' 같은 층 표기는 안 따진다
        self.assertTrue(address_match("종로구 종로 69 YMCA 2층", "서울특별시 종로구 종로 69 서울YMCA 2층"))
        self.assertTrue(address_match("용산구 소월로20길 42 2층", "2F, 42 소월로20길 용산구 서울특별시"))
        # 다른 가게: 길 번호가 다르다
        self.assertFalse(address_match("마포구 성미산로 183 4, 5층", "서울특별시 마포구 성미산로17길 85 1층"))
        self.assertFalse(address_match("종로구 대학로14길 24 1층", "서울특별시 종로구 대학로12길 22 아트포레스트 2층"))
        self.assertFalse(address_match("", "서울"))
        self.assertFalse(address_match("서울", "서울특별시"))  # 한 조각만으로는 믿지 않는다

    def test_coords_from_url(self):
        from places import coords
        url = "https://www.google.com/maps/place/x/@37.5,127.0,17z/data=!3m1!8m2!3d37.5446!4d127.0558"
        self.assertEqual(coords.from_url(url), (37.5446, 127.0558))  # 핀이 화면 중심보다 우선
        self.assertEqual(coords.from_url("https://www.google.com/maps/@37.5,127.0,15z"), (37.5, 127.0))
        self.assertIsNone(coords.from_url("https://www.google.com/maps/search/x"))


class AddListTest(unittest.TestCase):
    def test_name_match_tolerates_branch_and_english(self):
        from places.addlist import name_matches
        self.assertTrue(name_matches("밍글스", "밍글스 Mingles"))
        self.assertTrue(name_matches("스시 마츠모토", "스시마츠모토"))
        self.assertTrue(name_matches("가겐 바이 최준호", "가겐"))
        self.assertFalse(name_matches("소울", "홍대 곱창"))
        self.assertFalse(name_matches("이스트", ""))

    def test_check_then_apply_writes_list_and_note(self):
        from places import addlist
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        cfg = dict(CONFIG, data_dir=str(root / "data"),
                   obsidian={"vault_path": str(root / "vault"), "folder": "장소", "index_note": "장소 목록"})
        opened = {}

        class Fake:
            def start(self): pass
            def close(self): pass
            def _open_place(self, q): opened["q"] = q
            def place_info(self):
                if "레스토랑" not in opened["q"]:  # 이름만 검색하면 엉뚱한 가게가 열린다
                    return {"name": "홍대 곱창", "address": "서울특별시 마포구 어딘가 1", "category": "곱창", "id": "0x1:0x1", "url": ""}
                return {"name": "소울 Soul", "address": "서울특별시 용산구 한남대로20길 47", "category": "레스토랑",
                        "id": "0x2:0x2", "url": "https://maps.google.com/?cid=2"}
            def save_current(self, target):
                saved.append(target); return True, "저장"

        saved = []
        spec = {"source": "https://guide.michelin.com/x", "tags": ["미쉐린"], "category": "식당",
                "places": [{"name": "소울", "city": "서울", "note": "미쉐린 1스타"}]}
        dry = addlist.AddList(cfg, spec, log=lambda m: None, gm=Fake())
        dry.run(apply=False)
        self.assertEqual(saved, [])  # 확인만 하면 저장하지 않는다
        self.assertEqual(dry.results[0]["status"], "확인")
        self.assertIn("레스토랑", opened["q"])  # 첫 검색이 안 맞아 다른 검색어로 다시 열었다

        run = addlist.AddList(cfg, spec, log=lambda m: None, gm=Fake())
        run.run(apply=True)
        self.assertEqual(saved, ["📍서울"])
        note = (root / "vault" / "장소" / "서울" / "용산" / "소울 Soul.md").read_text(encoding="utf-8")
        self.assertIn('"미쉐린"', note)
        self.assertIn("미쉐린 1스타", note)
        self.assertIn("gmaps_saved: true", note)

    def test_scrambled_address_finds_province_anywhere(self):
        addr = "한남동 한남대로20길 21 18 KR 서울특별시 용산구 B동 지하 1층"
        self.assertEqual(region.parse_address(addr)[:2], ("대한민국", "서울"))
        self.assertEqual(region.city_label("1층 장승포동 KR 경상남도 거제시 어딘가"), "경남 거제시")
        cfg = dict(CONFIG, metro=["서울", "부산"])
        self.assertEqual(region.split_address(addr, cfg), ("서울", "용산"))

    def test_kr_prefix_in_address(self):
        cfg = dict(CONFIG, metro=["서울", "부산"])
        self.assertEqual(region.parse_address("KR 서울특별시 강남구 테헤란로 231 EAST 2층")[:2], ("대한민국", "서울"))
        self.assertEqual(region.split_address("KR 서울특별시 강남구 테헤란로 231", cfg), ("서울", "강남"))

    def test_seoul_area_falls_back_to_gu(self):
        cfg = dict(CONFIG, metro=["서울", "부산"])
        self.assertEqual(region.split_address("서울특별시 용산구 한남대로20길 47", cfg), ("서울", "용산"))
        self.assertEqual(region.split_address("서울특별시 중구 명동8가길 58", cfg), ("서울", "중구"))
        self.assertEqual(region.split_address("부산광역시 해운대구 달맞이길 30", cfg), ("부산", "해운대"))


class ProgressTest(unittest.TestCase):
    def setUp(self):
        from places.progress import Progress
        self.now = [100.0]
        self.shown = []
        self.p = Progress(show=self.shown.append, clock=lambda: self.now[0])

    def test_steps_and_spinner_change(self):
        self.p.stage(0)
        self.now[0] += 12
        self.p.stage(1, "슬라이드 9장")
        text = self.shown[-1]
        self.assertIn("12초", text)
        self.assertIn("✅ 게시물 읽기", text)
        self.assertIn("장소 찾기 · Gemini", text)
        self.assertIn("▫️ 지도·노트 저장", text)
        self.assertNotEqual(self.shown[-1][0], self.shown[-2][0])  # ⏳ ↔ ⌛

    def test_save_bar_and_eta(self):
        self.p.stage(2, total=4)
        self.assertIn("░░░░░░░░░░ 0/4 · 약 32초 남음", self.shown[-1])  # 기본 1곳 8초
        self.now[0] += 10
        self.p.advance("어니언")
        self.assertIn("▓▓░░░░░░░░ 1/4 · 약 30초 남음 · 어니언", self.shown[-1])

    def test_finish_and_silent_without_show(self):
        from places.progress import Progress
        self.now[0] += 52
        self.p.finish()
        self.assertEqual(self.shown[-1], "✅ 완료 · 52초")
        quiet = Progress()
        quiet.stage(2, total=1)
        quiet.advance()
        quiet.finish()  # show 가 없으면 아무 일도 없다


class FakeSource:
    """구글 지도 화면 대신 — lists: {목록 이름: [장소 줄 글]}, infos: {장소 이름: place_info}."""

    def __init__(self, lists, infos):
        self.lists, self.infos = lists, infos
        self.opened, self.saved, self.here, self.fail, self.current = [], [], None, set(), None

    def start(self): pass

    def read_lists(self):
        return [{"name": n, "count": len(t), "nth": 0} for n, t in self.lists.items()]

    def read_texts(self, lst):
        return self.lists[lst["name"]]

    def open_list(self, lst, upto):
        self.current = lst["name"]

    def open_item(self, lst, idx, name):
        # 실제 화면처럼 목록을 먼저 열어 둔 상태에서만 장소를 열 수 있다
        assert self.current == lst["name"], "목록을 열지 않고 장소를 열었다"
        self.opened.append(name)
        if name in self.fail:
            raise RuntimeError("화면이 안 열림")
        self.here = self.infos[name]
        return self.here

    def back(self, lst, idx): pass

    def open_url(self, url):
        self.here = next(i for i in self.infos.values() if i["url"] == url)

    def save_here(self, target):
        self.saved.append((self.here["name"], target))
        return True, "저장"


def _info(name, address, lat, lng, cid, category="베이커리"):
    return {"name": name, "address": address, "category": category, "id": f"0x1:0x{cid:x}",
            "url": f"https://maps.google.com/?cid={cid}", "lat": lat, "lng": lng}


class SyncTest(unittest.TestCase):
    LINES = {
        "Saved places": ["어니언 성수\n4.5(1,234)\n₩10,000~20,000\n· 카페", "새집 소금빵\n4.7(30)\n· 베이커리"],
        "가고 싶은 장소": ["새집 소금빵\n4.7(30)\n· 베이커리", "블루보틀 성수 카페\n4.4(900)\n· 카페"],
    }

    def setUp(self):
        from places import sync
        self.sync = sync
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.config = dict(CONFIG, data_dir=str(root / "data"),
                           obsidian={"vault_path": str(root / "vault"), "folder": "Places",
                                     "index_note": "장소 목록"})
        self.vault = root / "vault" / "Places"
        self.store = Store(self.config["data_dir"])
        self.store.data["places"].update({
            "k1": {"name": "어니언 성수", "category": "카페", "sido": "서울", "area": "성수", "address": "",
                   "sources": ["https://www.instagram.com/p/AAA/"], "gmaps_saved": True},
            # 봇이 인스타 글에서 만든 이름 — 구글 목록에는 '블루보틀 성수 카페' 로 보인다
            "k2": {"name": "블루보틀 성수", "category": "카페", "sido": "서울", "area": "성수", "address": "",
                   "lat": 37.5483, "lng": 127.0440, "sources": ["https://www.instagram.com/p/BBB/"],
                   "gmaps_saved": True},
        })
        self.infos = {
            "새집 소금빵": _info("새집 소금빵", "경기도 성남시 분당구 판교로 1", 37.40, 127.11, 101),
            "블루보틀 성수 카페": _info("블루보틀 성수 카페", "서울특별시 성동구 아차산로 7", 37.5484, 127.0441, 102,
                                  "카페"),
        }
        self.source = FakeSource(self.LINES, self.infos)

    def engine(self, source=None):
        return self.sync.Sync(self.config, self.store, source or self.source, log=lambda m: None)

    def test_parse_item_and_answers(self):
        self.assertEqual(self.sync.parse_item("어니언 성수\n4.5(1,234)\n₩10,000~20,000\n· 카페"), ("어니언 성수", 4.5))
        self.assertEqual(self.sync.parse_item("서울 성동구 아차산로 7"), ("서울 성동구 아차산로 7", None))
        self.assertEqual(self.sync.parse_item(""), ("", None))
        self.assertEqual([self.sync.answer_of(w) for w in ("예", "아니오", "음")], [True, False, None])

    def test_only_unknown_names_are_opened_and_recorded(self):
        result = self.engine().run()
        # 이미 아는 '어니언 성수' 는 열지 않고, 두 목록에 든 '새집 소금빵' 은 한 번만 연다
        self.assertEqual(self.source.opened, ["새집 소금빵", "블루보틀 성수 카페"])
        self.assertEqual((result["lists"], result["lines"]), (2, 4))
        [new] = result["new"]
        self.assertEqual((new["name"], new["category"], new["sido"], new["area"]),
                         ("새집 소금빵", "베이커리", "경기", "성남"))
        self.assertEqual(new["gmaps_origin"], ["Saved places", "가고 싶은 장소"])
        # places.json · coords.json · 옵시디언 노트에 모두 들어간다
        saved = Store(self.config["data_dir"]).data["places"]["gmaps:0x1:0x65"]
        self.assertEqual((saved["lat"], saved["gmaps_saved"], saved["gmaps_list"]), (37.40, True, "Saved places"))
        self.assertEqual(coords_load(self.config["data_dir"])["gmaps:0x1:0x65"], [37.40, 127.11])
        note = (self.vault / "경기" / "성남" / "새집 소금빵.md").read_text(encoding="utf-8")
        self.assertIn("gmaps_saved: true", note)
        self.assertIn("구글 평점 4.7", note)
        # '내 주변 추천' 후보가 되고 가장 가깝다
        from places import nearby
        places, missing = nearby.candidates(saved_places(self.config), coords_load(self.config["data_dir"]))
        self.assertEqual(nearby.nearest(places, 37.4001, 127.1101, [], 1)[0][1]["name"], "새집 소금빵")

    def test_same_place_with_other_name_is_linked_not_duplicated(self):
        result = self.engine().run()
        self.assertEqual(result["linked"], 1)  # 블루보틀: 이름이 비슷하고 300m 안이라 같은 장소
        self.assertEqual(len(self.store.data["places"]), 3)  # k1, k2 + 새집 소금빵
        self.assertIn("가고 싶은 장소", self.store.data["places"]["k2"]["gmaps_origin"])
        self.assertFalse((self.vault / "서울" / "성동").exists())
        again = FakeSource(self.LINES, self.infos)
        self.engine(again).run()
        self.assertEqual(again.opened, [])  # 두 번째부터는 아무것도 열지 않는다 (별칭 기억)

    def test_far_same_name_is_a_different_branch(self):
        self.infos["블루보틀 성수 카페"] = _info("블루보틀 성수 카페", "부산광역시 해운대구 1", 35.16, 129.16, 103, "카페")
        result = self.engine().run()
        self.assertEqual(result["linked"], 0)
        self.assertEqual(sorted(e["name"] for e in result["new"]), ["블루보틀 성수 카페", "새집 소금빵"])

    def test_failed_open_is_reported_and_retried_next_time(self):
        self.source.fail = {"새집 소금빵"}
        result = self.engine().run()
        self.assertEqual(result["failed"], ["새집 소금빵"])
        self.assertNotIn("gmaps:0x1:0x65", self.store.data["places"])
        self.source.fail = set()
        retry = FakeSource(self.LINES, self.infos)
        self.assertEqual([e["name"] for e in self.engine(retry).run()["new"]], ["새집 소금빵"])

    def test_city_list_is_asked_once_and_never_deletes(self):
        first = self.engine().run()
        self.assertTrue(first["ask_city"])
        self.assertEqual(self.source.saved, [])  # 답하기 전에는 시 단위 목록에 넣지 않는다
        engine = self.engine()
        engine.set_city(True)
        done = engine.apply_pending_city()
        self.assertEqual(self.source.saved, [("새집 소금빵", "📍경기 성남시")])
        self.assertEqual(done["city_saved"], ["새집 소금빵"])
        self.assertEqual(self.store.data["places"]["gmaps:0x1:0x65"]["gmaps_list"], "📍경기 성남시")
        # 이후 동기화는 묻지 않고, 새 장소는 연 자리에서 바로 시 단위 목록에 넣는다
        self.lines_extra = dict(self.LINES, **{"여행 계획": ["연남 빵집\n4.2(10)\n· 베이커리"]})
        self.infos["연남 빵집"] = _info("연남 빵집", "서울특별시 마포구 월드컵북로 1", 37.56, 126.92, 104)
        later = FakeSource(self.lines_extra, self.infos)
        result = self.engine(later).run()
        self.assertFalse(result["ask_city"])
        self.assertEqual(later.saved, [("연남 빵집", "📍서울")])

    def test_no_to_city_list_clears_pending(self):
        self.engine().run()
        engine = self.engine()
        engine.set_city(False)
        self.assertEqual(self.store.data["sync"]["pending_city"], [])
        self.assertEqual(self.source.saved, [])

    def test_list_already_named_like_a_city_list_is_not_duplicated(self):
        source = FakeSource({"📍경기 성남시": ["새집 소금빵\n4.7(30)"]}, self.infos)
        eng = self.engine(source)
        eng.set_city(True)
        eng.run()
        self.assertEqual(source.saved, [])

    def test_report_is_short_and_lists_new_places(self):
        text = self.sync.report(self.engine().run())
        self.assertIn("목록 2개 · 장소 4곳 확인", text)
        self.assertIn("<b>새집 소금빵</b> · 베이커리 · 경기 성남", text)
        self.assertIn("/sync 예 · /sync 아니오", text)

    def test_app_sync_command_and_city_answer(self):
        app = App(self.config, store=self.store, gmaps=FakeGMaps())
        with unittest.mock.patch("places.sync.GmapsSource", lambda config, gm: self.source):
            [first] = app.handle("/sync")
            self.assertIn("새 장소 1곳", first)
            self.assertIn("/sync 예", first)
            [yes] = app.handle("/sync 예")
            [bad] = app.handle("/sync 글쎄")
            [no] = App(self.config, store=Store(self.config["data_dir"]), gmaps=FakeGMaps()).handle("/sync 아니오")
        self.assertIn("시 단위 목록에도 1곳", yes)
        self.assertIn("사용법", bad)
        self.assertIn("넣지 않을게요", no)
        self.assertEqual(self.source.saved, [("새집 소금빵", "📍경기 성남시")])

    def test_app_sync_off_without_gmaps(self):
        self.assertIn("꺼져", App(self.config, store=self.store).handle("/sync")[0])

    def test_sync_gets_its_own_progress_steps(self):
        from places.progress import Progress
        shown = []
        progress = Progress(show=shown.append)
        app = App(self.config, store=self.store, gmaps=FakeGMaps())
        with unittest.mock.patch("places.sync.GmapsSource", lambda config, gm: self.source):
            app.handle("/sync", progress=progress)
        self.assertIn("노트·좌표 저장", progress.render())
        self.assertNotIn("게시물 읽기", progress.render())


def coords_load(data_dir):
    from places import coords
    return coords.load(data_dir)


def saved_places(config):
    return Store(config["data_dir"]).data["places"]


class BotTest(unittest.TestCase):
    def test_sync_command_gets_progress_message(self):
        calls = []
        progress = SimpleNamespace(finish=lambda text=None: calls.append("finish"), elapsed=lambda: 1)
        app = SimpleNamespace(handle=lambda text, progress=None: calls.append(("handle", text, progress)) or ["끝"])
        update = {"message": {"chat": {"id": 7}, "text": "/sync 예"}}
        sent = []
        handle_update(app, update, "7", lambda c, t: sent.append(t), set(), lambda chat: progress)
        self.assertEqual(calls, [("handle", "/sync 예", progress), "finish"])
        self.assertEqual(sent, ["끝"])

    def test_location_message_gets_nearby_reply_without_logging_coordinates(self):
        seen = []
        app = SimpleNamespace(nearby_reply=lambda chat, lat, lng: seen.append((lat, lng)) or "가까운 곳")
        update = {"message": {"chat": {"id": 7}, "location": {"latitude": 37.5, "longitude": 127.0}}}
        sent = []
        with unittest.mock.patch("builtins.print") as out:
            handle_update(app, update, "7", lambda c, t: sent.append(t), set())
        self.assertEqual((seen, sent), ([(37.5, 127.0)], ["가까운 곳"]))
        logged = " ".join(str(c.args[0]) for c in out.call_args_list)
        self.assertIn("위치 수신", logged)
        self.assertNotIn("37.5", logged)  # 좌표는 로그에 안 남는다

    def test_menu_is_persistent_and_every_button_is_understood(self):
        from places.telegram_bot import MAIN_MENU, MENU_ACTIONS, COMMANDS
        self.assertTrue(MAIN_MENU["is_persistent"])
        self.assertNotIn("one_time_keyboard", MAIN_MENU)  # 한 번 쓰면 접히는 키보드가 아니다
        labels = [b["text"] for row in MAIN_MENU["keyboard"] for b in row]
        self.assertEqual([l for l in labels if l not in MENU_ACTIONS], [])  # 모든 버튼이 글자→명령
        self.assertFalse(any(b.get("request_location") for row in MAIN_MENU["keyboard"] for b in row))
        self.assertEqual(len(MAIN_MENU["keyboard"]), 2)  # 2줄
        self.assertEqual([MENU_ACTIONS[b["text"]] for b in MAIN_MENU["keyboard"][0]], ["/근처카페", "/근처맛집", "/근처장소"])
        self.assertEqual([MENU_ACTIONS[b["text"]] for b in MAIN_MENU["keyboard"][1]], ["/sync", "/recent", "/undo"])
        self.assertIn("sync", [c for c, _ in COMMANDS])

    def test_menu_button_text_runs_the_command(self):
        got = []
        app = SimpleNamespace(handle=lambda text, progress=None: got.append(text) or ["끝"],
                              near_request=lambda chat, text: got.append(text) or "위치를 보내 주세요")
        sent = []
        reply = lambda c, t, markup=None: sent.append(t)  # noqa: E731
        for label in ("🔄 동기화", "🕘 최근저장", "☕ 근처카페", "↩️ 저장취소"):
            handle_update(app, {"message": {"chat": {"id": 7}, "text": label}}, "7", reply, set(),
                          lambda chat: None)
        self.assertEqual(got, ["/sync", "/recent", "/근처카페", "/undo"])
        # '근처 …' 버튼은 위치 버튼 키보드를 띄운다 (쓰면 접히고, 다음 답장에서 메뉴가 돌아온다)
        from places.telegram_bot import LOCATION_KEYBOARD
        shown = []
        handle_update(app, {"message": {"chat": {"id": 7}, "text": "🏛 근처장소"}}, "7",
                      lambda c, t, markup=None: shown.append(markup), set())
        self.assertEqual(shown, [LOCATION_KEYBOARD])
        self.assertTrue(LOCATION_KEYBOARD["keyboard"][0][0]["request_location"])
        self.assertTrue(LOCATION_KEYBOARD["one_time_keyboard"])

    def test_every_reply_carries_the_menu(self):
        from places import telegram_bot
        calls = []
        with unittest.mock.patch.object(telegram_bot, "call", lambda token, method, **kw: calls.append(kw)):
            telegram_bot.send("T", 7, "안녕")
        self.assertEqual(calls[0]["reply_markup"], telegram_bot.MAIN_MENU)

    def test_near_request_shows_location_button(self):
        from places.telegram_bot import LOCATION_KEYBOARD as MAIN_MENU
        app = SimpleNamespace(near_request=lambda chat, text: "위치를 보내 주세요")
        update = {"message": {"chat": {"id": 7}, "text": "근처 카페 추천해줘"}}
        sent = []
        handle_update(app, update, "7", lambda c, t, markup=None: sent.append((t, markup)), set())
        self.assertEqual(sent, [("위치를 보내 주세요", MAIN_MENU)])
        button = MAIN_MENU["keyboard"][0][0]
        self.assertTrue(button["request_location"])

    def test_progress_message_wraps_url_processing(self):
        calls = []
        progress = SimpleNamespace(finish=lambda text=None: calls.append(("finish", text)),
                                   elapsed=lambda: 3)
        app = SimpleNamespace(handle=lambda text, progress=None: calls.append(("handle", progress)) or ["결과"])
        update = {"message": {"chat": {"id": 7}, "text": "https://www.instagram.com/p/AAA/"}}
        sent = []
        handle_update(app, update, "7", lambda c, t: sent.append(t), set(), lambda chat: progress)
        self.assertEqual(calls, [("handle", progress), ("finish", None)])
        self.assertEqual(sent, ["결과"])  # '분석 중' 별도 메시지 대신 진행 메시지를 고쳐 쓴다

    def setUp(self):
        self.sent = []
        self.reply = lambda chat_id, text: self.sent.append((chat_id, text))
        self.app = SimpleNamespace(handle=lambda text: ["결과"])

    def test_log_hides_token(self):
        from places import telegram_bot
        with unittest.mock.patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "123:SECRET"}), \
                unittest.mock.patch("builtins.print") as out:
            telegram_bot.log("502 for url: https://api.telegram.org/bot123:SECRET/getUpdates")
        self.assertNotIn("SECRET", out.call_args.args[0])

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
