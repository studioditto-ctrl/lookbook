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
            "2F, 13 한남대로27가길 용산구 서울특별시": ("서울", ""),
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
    def __init__(self, ok=True, reason="", address=""):
        self.ok, self.reason, self.address, self.calls = ok, reason, address, []

    def save(self, query, list_name):
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


class BotTest(unittest.TestCase):
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
