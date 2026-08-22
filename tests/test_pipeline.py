"""수집→선별→포맷 전 구간 테스트.

외부 네트워크 없이 돌도록 고정 피드를 로컬 HTTP 서버로 띄운다.
"""

import contextlib
import functools
import http.server
import io
import json
import os
import socketserver
import sys
import tempfile
import threading
import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from collect import Item, _canonical_url, _clean_title, _parse_feed  # noqa: E402
from filter import select  # noqa: E402
from telegram import build_message  # noqa: E402

NOW = datetime.now(timezone.utc)


def rss_feed(entries):
    items = "\n".join(
        f"""<item>
      <title>{title}</title>
      <link>{link}</link>
      <guid>{link}</guid>
      <pubDate>{format_datetime(published)}</pubDate>
    </item>"""
        for title, link, published in entries
    )
    return f"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>fixture</title>
{items}
</channel></rss>"""


def atom_feed(entries):
    items = "\n".join(
        f"""<entry>
    <id>yt:video:{vid}</id>
    <title>{title}</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v={vid}"/>
    <published>{published.isoformat()}</published>
  </entry>"""
        for vid, title, published in entries
    )
    return f"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>fixture</title>
{items}
</feed>"""


class FeedServer:
    """테스트용 정적 파일 서버."""

    def __init__(self, files, statuses=None):
        self.dir = tempfile.TemporaryDirectory()
        for name, content in files.items():
            (Path(self.dir.name) / name).write_text(content, encoding="utf-8")
        # 오류 응답은 본문까지 흉내 내야 한다. 상태 코드만으로는 부족한 경우가 있다.
        errors = statuses or {}

        class QuietHandler(http.server.SimpleHTTPRequestHandler):
            # 요청 로그로 테스트 출력이 지저분해지지 않게 막는다
            def log_message(self, *args):
                pass

            def do_GET(self):
                name = self.path.lstrip("/").split("?", 1)[0]
                if name in errors:
                    code, body = errors[name]
                    encoded = body.encode("utf-8")
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)
                    return
                super().do_GET()

        handler = functools.partial(QuietHandler, directory=self.dir.name)
        self.httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def url(self, name):
        return f"http://127.0.0.1:{self.port}/{name}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.dir.cleanup()


CONFIG = {
    "timezone": "Asia/Seoul",
    "lookback_hours": 36,
    "slots": {
        "morning": {"title": "🏃 오늘 아침 러닝 브리핑", "articles": 3, "videos": 2},
        "evening": {"title": "🌙 퇴근길 러닝 브리핑", "articles": 1, "videos": 4},
    },
    "keywords": {"부상": 3, "훈련": 2},
    "exclude": ["채용"],
}


class TestHelpers(unittest.TestCase):
    def test_canonical_url_strips_tracking(self):
        url = _canonical_url("https://ex.com/a?utm_source=x&id=7#frag")
        self.assertEqual(url, "https://ex.com/a?id=7")

    def test_clean_title_removes_source_suffix(self):
        self.assertEqual(_clean_title("무릎 부상 예방법 - 러닝월드"), "무릎 부상 예방법")

    def test_clean_title_keeps_plain_title(self):
        self.assertEqual(_clean_title("  10km  기록  단축 "), "10km 기록 단축")


class TestPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = FeedServer(
            {
                "news.xml": rss_feed(
                    [
                        ("무릎 부상 예방 스트레칭 - 러닝월드", "https://ex.com/1", NOW - timedelta(hours=2)),
                        ("주말 훈련 루틴 정리", "https://ex.com/2", NOW - timedelta(hours=10)),
                        ("러닝 코치 채용 공고", "https://ex.com/3", NOW - timedelta(hours=1)),
                        ("신형 러닝화 리뷰", "https://ex.com/4", NOW - timedelta(hours=20)),
                    ]
                ),
                "videos.xml": atom_feed(
                    [
                        ("vid0000001", "초보자를 위한 훈련 영상", NOW - timedelta(hours=3)),
                        ("vid0000002", "10km 페이스 잡는 법", NOW - timedelta(hours=25)),
                    ]
                ),
                "broken.xml": "not xml at all",
            }
        )

    @classmethod
    def tearDownClass(cls):
        cls.server.close()

    def collect_fixtures(self):
        return _parse_feed(self.server.url("news.xml"), "샘플뉴스", "article") + _parse_feed(
            self.server.url("videos.xml"), "샘플채널", "video"
        )

    def test_parses_both_feed_formats(self):
        items = self.collect_fixtures()
        self.assertEqual(len([i for i in items if i.kind == "article"]), 4)
        self.assertEqual(len([i for i in items if i.kind == "video"]), 2)
        video = next(i for i in items if i.kind == "video")
        self.assertTrue(video.url.startswith("https://www.youtube.com/watch?v="))

    def test_broken_feed_returns_empty_without_raising(self):
        self.assertEqual(_parse_feed(self.server.url("broken.xml"), "깨진피드", "article"), [])

    def test_missing_feed_returns_empty_without_raising(self):
        self.assertEqual(_parse_feed(self.server.url("nope.xml"), "없는피드", "article"), [])

    def test_excluded_titles_are_dropped(self):
        articles, _ = select(self.collect_fixtures(), {}, CONFIG, "morning")
        self.assertNotIn("러닝 코치 채용 공고", [a.title for a in articles])

    def test_keyword_scoring_orders_results(self):
        articles, _ = select(self.collect_fixtures(), {}, CONFIG, "morning")
        self.assertEqual(articles[0].title, "무릎 부상 예방 스트레칭")

    def test_seen_items_are_skipped(self):
        items = self.collect_fixtures()
        articles, _ = select(items, {}, CONFIG, "morning")
        seen = {a.id: NOW.isoformat() for a in articles}
        again, _ = select(items, seen, CONFIG, "morning")
        self.assertFalse(set(a.id for a in again) & set(seen))

    def test_slot_limits_are_respected(self):
        items = self.collect_fixtures()
        articles, videos = select(items, {}, CONFIG, "evening")
        self.assertEqual(len(articles), 1)
        self.assertEqual(len(videos), 2)  # 후보가 2건뿐이라 상한 4보다 적다

    def test_duplicate_urls_are_folded(self):
        dup = Item(
            id="other-id",
            title="무릎 부상 예방 스트레칭",
            url="https://ex.com/1",
            source="다른매체",
            kind="article",
            published=NOW,
        )
        articles, _ = select(self.collect_fixtures() + [dup], {}, CONFIG, "morning")
        self.assertEqual(len([a for a in articles if a.url == "https://ex.com/1"]), 1)


class TestMessage(unittest.TestCase):
    def make_items(self, count, kind="article"):
        return [
            Item(
                id=f"id{n}",
                title=f"제목 {n}",
                url=f"https://ex.com/{n}",
                source=f"매체{n}",
                kind=kind,
                published=NOW,
            )
            for n in range(count)
        ]

    def test_message_has_header_and_links(self):
        message = build_message(self.make_items(2), self.make_items(1, "video"), CONFIG, "morning")
        self.assertIn("🏃 오늘 아침 러닝 브리핑", message)
        self.assertIn('<a href="https://ex.com/0">', message)
        self.assertIn("📰 <b>읽을거리</b>", message)
        self.assertIn("🎬 <b>영상</b>", message)

    def test_video_numbering_continues_after_articles(self):
        message = build_message(self.make_items(2), self.make_items(1, "video"), CONFIG, "morning")
        self.assertIn("3. <a", message)

    def test_html_special_characters_are_escaped(self):
        item = Item(
            id="x",
            title="러닝 & <b>기록</b>",
            url="https://ex.com/a?x=1&y=2",
            source="매체",
            kind="article",
            published=NOW,
        )
        message = build_message([item], [], CONFIG, "morning")
        self.assertIn("러닝 &amp; &lt;b&gt;기록&lt;/b&gt;", message)
        self.assertIn("https://ex.com/a?x=1&amp;y=2", message)

    def test_long_message_is_trimmed_under_limit(self):
        long_items = [
            Item(
                id=f"id{n}",
                title="아주 긴 제목 " * 30,
                url=f"https://ex.com/{n}",
                source="매체",
                kind="article",
                published=NOW,
            )
            for n in range(120)
        ]
        message = build_message(long_items, [], CONFIG, "morning")
        self.assertLessEqual(len(message), 4096)
        self.assertIn("🏃 오늘 아침 러닝 브리핑", message)

    def test_empty_sections_are_omitted(self):
        message = build_message(self.make_items(1), [], CONFIG, "morning")
        self.assertNotIn("🎬", message)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestWhoami(unittest.TestCase):
    """chat_id 추출은 실제 getUpdates 응답 모양으로 검증한다."""

    def setUp(self):
        from whoami import chats_from_updates, describe

        self.chats_from_updates = chats_from_updates
        self.describe = describe

    def test_extracts_chat_from_message(self):
        payload = {
            "ok": True,
            "result": [
                {
                    "update_id": 1,
                    "message": {
                        "message_id": 1,
                        "chat": {"id": 123456789, "type": "private", "first_name": "지훈"},
                        "text": "안녕",
                    },
                }
            ],
        }
        chats = self.chats_from_updates(payload)
        self.assertEqual([c["id"] for c in chats], [123456789])

    def test_deduplicates_repeated_chats(self):
        message = {
            "message_id": 1,
            "chat": {"id": 7, "type": "private", "first_name": "A"},
        }
        payload = {"result": [{"message": message}, {"message": message}]}
        self.assertEqual(len(self.chats_from_updates(payload)), 1)

    def test_finds_chat_in_channel_post(self):
        payload = {
            "result": [
                {
                    "update_id": 2,
                    "channel_post": {
                        "chat": {"id": -100123, "type": "channel", "title": "러닝방"}
                    },
                }
            ]
        }
        chats = self.chats_from_updates(payload)
        self.assertEqual(chats[0]["id"], -100123)
        self.assertIn("러닝방", self.describe(chats[0]))

    def test_empty_result_returns_nothing(self):
        self.assertEqual(self.chats_from_updates({"ok": True, "result": []}), [])
        self.assertEqual(self.chats_from_updates({}), [])

    def test_describe_falls_back_to_username(self):
        self.assertIn("runner", self.describe({"id": 1, "type": "private", "username": "runner"}))


class TestProblemReporting(unittest.TestCase):
    """소스가 죽었을 때 조용히 사라지지 않고 목록에 남는지."""

    @classmethod
    def setUpClass(cls):
        cls.server = FeedServer({"ok.xml": rss_feed([("제목", "https://ex.com/1", NOW)])})

    @classmethod
    def tearDownClass(cls):
        cls.server.close()

    def test_failed_feed_is_recorded(self):
        problems = []
        _parse_feed(self.server.url("gone.xml"), "없는피드", "article", problems)
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0][0], "없는피드")

    def test_healthy_feed_records_nothing(self):
        problems = []
        items = _parse_feed(self.server.url("ok.xml"), "정상피드", "article", problems)
        self.assertEqual(problems, [])
        self.assertEqual(len(items), 1)

    def test_unreachable_channel_is_recorded(self):
        from collect import resolve_channel_id

        problems = []
        result = resolve_channel_id(
            self.server.url("no-such-channel"), {}, problems, "가짜채널"
        )
        self.assertIsNone(result)
        self.assertEqual(problems[0][0], "가짜채널")

    def test_channel_url_with_explicit_id_needs_no_network(self):
        from collect import resolve_channel_id

        cache = {}
        url = "https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv"
        self.assertEqual(resolve_channel_id(url, cache), "UCabcdefghijklmnopqrstuv")
        self.assertEqual(cache[url], "UCabcdefghijklmnopqrstuv")


class TestSummarize(unittest.TestCase):
    """요약은 실패해도 다이제스트를 막지 않아야 한다."""

    def setUp(self):
        import summarize as summarize_module

        self.mod = summarize_module

    def items(self):
        return [
            Item(
                id=f"id{n}",
                title=f"English Title {n}",
                url=f"https://ex.com/{n}",
                source="Runner's World",
                kind="article",
                published=NOW,
                summary=f"snippet {n}",
            )
            for n in range(3)
        ]

    def fake_client(self, text=None, stop_reason="end_turn", blocks=None):
        from types import SimpleNamespace

        if blocks is None:
            blocks = [SimpleNamespace(type="text", text=text)]
        response = SimpleNamespace(stop_reason=stop_reason, content=blocks)
        captured = {}

        def create(**kwargs):
            captured.update(kwargs)
            return response

        return SimpleNamespace(messages=SimpleNamespace(create=create)), captured

    def test_payload_carries_title_source_and_snippet(self):
        payload = self.mod._payload(self.items())
        self.assertEqual(payload[0]["index"], 0)
        self.assertEqual(payload[0]["title"], "English Title 0")
        self.assertEqual(payload[0]["snippet"], "snippet 0")
        self.assertEqual(payload[0]["kind"], "기사")

    def test_snippet_is_truncated(self):
        items = self.items()
        items[0].summary = "가" * 5000
        self.assertEqual(len(self.mod._payload(items)[0]["snippet"]), self.mod.SNIPPET_MAX)

    def test_apply_replaces_title_and_sets_summary(self):
        items = self.items()
        applied = self.mod._apply(
            items,
            {"items": [{"index": 0, "title_ko": "한국어 제목", "summary_ko": "요약."}]},
        )
        self.assertEqual(applied, 1)
        self.assertEqual(items[0].title, "한국어 제목")
        self.assertEqual(items[0].summary_ko, "요약.")
        self.assertEqual(items[1].title, "English Title 1")  # 안 온 항목은 원문 유지

    def test_apply_ignores_out_of_range_and_bad_indices(self):
        items = self.items()
        applied = self.mod._apply(
            items,
            {"items": [
                {"index": 99, "title_ko": "x", "summary_ko": "y"},
                {"index": "0", "title_ko": "x", "summary_ko": "y"},
                {"title_ko": "x", "summary_ko": "y"},
            ]},
        )
        self.assertEqual(applied, 0)
        self.assertEqual(items[0].title, "English Title 0")

    def test_apply_keeps_original_title_when_blank(self):
        items = self.items()
        self.mod._apply(items, {"items": [{"index": 0, "title_ko": "  ", "summary_ko": "요약."}]})
        self.assertEqual(items[0].title, "English Title 0")
        self.assertEqual(items[0].summary_ko, "요약.")

    def test_request_sends_schema_and_effort(self):
        client, captured = self.fake_client(text='{"items": []}')
        self.mod._request(client, self.items(), "low")
        self.assertEqual(captured["model"], "claude-opus-5")
        self.assertEqual(captured["output_config"]["effort"], "low")
        self.assertEqual(
            captured["output_config"]["format"]["schema"]["required"], ["items"]
        )

    def test_request_raises_on_refusal(self):
        client, _ = self.fake_client(text="{}", stop_reason="refusal")
        with self.assertRaises(RuntimeError):
            self.mod._request(client, self.items(), "low")

    def test_request_raises_on_truncation(self):
        client, _ = self.fake_client(text='{"items": []}', stop_reason="max_tokens")
        with self.assertRaises(RuntimeError):
            self.mod._request(client, self.items(), "low")

    def test_request_skips_non_text_blocks(self):
        from types import SimpleNamespace

        blocks = [
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text='{"items": [{"index": 0, "title_ko": "제목", "summary_ko": "요약"}]}'),
        ]
        client, _ = self.fake_client(blocks=blocks)
        parsed = self.mod._request(client, self.items(), "low")
        self.assertEqual(parsed["items"][0]["title_ko"], "제목")

    def test_disabled_in_config_skips(self):
        items = self.items()
        self.assertFalse(self.mod.summarize(items, {"summary": {"enabled": False}}))
        self.assertEqual(items[0].title, "English Title 0")

    def test_missing_api_key_skips_without_raising(self):
        import os

        saved = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            items = self.items()
            self.assertFalse(self.mod.summarize(items, {}))
            self.assertEqual(items[0].title, "English Title 0")
        finally:
            if saved is not None:
                os.environ["ANTHROPIC_API_KEY"] = saved

    def test_empty_item_list_skips(self):
        self.assertFalse(self.mod.summarize([], {}))


class TestMessageWithSummaries(unittest.TestCase):
    def item(self, n, summary_ko=""):
        return Item(
            id=f"id{n}",
            title=f"제목 {n}",
            url=f"https://ex.com/{n}",
            source=f"매체{n}",
            kind="article",
            published=NOW,
            summary_ko=summary_ko,
        )

    def test_summary_line_is_rendered(self):
        message = build_message([self.item(0, "무릎 부상을 예방하는 스트레칭 세 가지.")], [], CONFIG, "morning")
        self.assertIn("무릎 부상을 예방하는 스트레칭 세 가지.", message)

    def test_item_without_summary_still_renders(self):
        message = build_message([self.item(0)], [], CONFIG, "morning")
        self.assertIn("제목 0", message)
        self.assertIn("매체0", message)

    def test_summary_is_html_escaped(self):
        message = build_message([self.item(0, "러닝 & <b>기록</b>")], [], CONFIG, "morning")
        self.assertIn("러닝 &amp; &lt;b&gt;기록&lt;/b&gt;", message)

    def test_long_summaries_trim_whole_items(self):
        items = [self.item(n, "긴 요약 문장. " * 40) for n in range(60)]
        message = build_message(items, [], CONFIG, "morning")
        self.assertLessEqual(len(message), 4096)
        # 잘려도 항목이 줄 단위로 쪼개지지 않는다
        self.assertFalse(message.endswith("   "))
        self.assertIn("🏃 오늘 아침 러닝 브리핑", message)


class TestChannelIdExtraction(unittest.TestCase):
    """채널 페이지에는 남의 channel_id 도 여러 번 등장한다."""

    def setUp(self):
        from collect import _channel_id_from_page

        self.extract = _channel_id_from_page

    def test_canonical_link_wins_over_other_ids(self):
        page = """
        <script>{"channelId":"UCwrongwrongwrongwrongw1"}</script>
        <link rel="canonical" href="https://www.youtube.com/channel/UCrightrightrightrightrr">
        <script>{"channelId":"UCwrongwrongwrongwrongw2"}</script>
        """
        self.assertEqual(self.extract(page), ("UCrightrightrightrightrr", "canonical"))

    def test_canonical_with_reversed_attribute_order(self):
        page = '<link href="https://www.youtube.com/channel/UCrightrightrightrightrr" rel="canonical">'
        self.assertEqual(self.extract(page)[0], "UCrightrightrightrightrr")

    def test_falls_back_to_itemprop(self):
        page = """
        <script>{"channelId":"UCwrongwrongwrongwrongw1"}</script>
        <meta itemprop="identifier" content="UCrightrightrightrightrr">
        """
        self.assertEqual(self.extract(page), ("UCrightrightrightrightrr", "itemprop"))

    def test_falls_back_to_external_id_before_channel_id(self):
        page = '{"channelId":"UCwrongwrongwrongwrongw1","externalId":"UCrightrightrightrightrr"}'
        self.assertEqual(self.extract(page), ("UCrightrightrightrightrr", "externalId"))

    def test_channel_id_is_last_resort(self):
        page = '{"channelId":"UClastlastlastlastlastlr"}'
        self.assertEqual(self.extract(page), ("UClastlastlastlastlastlr", "channelId"))

    def test_no_id_returns_none(self):
        self.assertEqual(self.extract("<html>아무것도 없음</html>"), (None, None))

    def test_canonical_pointing_elsewhere_is_ignored(self):
        # /channel/ 이 아닌 canonical (예: /@handle) 은 무시하고 다음 표지로 넘어간다
        page = """
        <link rel="canonical" href="https://www.youtube.com/@handle">
        <meta itemprop="identifier" content="UCrightrightrightrightrr">
        """
        self.assertEqual(self.extract(page), ("UCrightrightrightrightrr", "itemprop"))


class TestYouTubeDataAPI(unittest.TestCase):
    """RSS 대신 Data API 로 업로드를 가져오는 경로."""

    @classmethod
    def setUpClass(cls):
        import collect as collect_module

        cls.collect = collect_module
        payload = {
            "items": [
                {"snippet": {
                    "title": "Marathon pacing explained",
                    "description": "How to hold  goal pace\nover 42km.",
                    "publishedAt": "2026-08-14T02:00:00Z",
                    "resourceId": {"videoId": "vid123"},
                }},
                {"snippet": {  # videoId 없음 → 건너뛴다
                    "title": "Broken entry",
                    "publishedAt": "2026-08-14T02:00:00Z",
                    "resourceId": {},
                }},
            ]
        }
        cls.server = FeedServer({"api.json": json.dumps(payload), "bad.json": "not json"})
        cls.saved_api = collect_module.YT_API

    @classmethod
    def tearDownClass(cls):
        cls.collect.YT_API = cls.saved_api
        cls.server.close()

    def test_uploads_playlist_id(self):
        self.assertEqual(
            self.collect._uploads_playlist("UCL2AIZN201G3V3jhLIheeeg"),
            "UUL2AIZN201G3V3jhLIheeeg",
        )

    def test_parses_videos(self):
        self.collect.YT_API = self.server.url("api.json")
        items = self.collect._collect_via_api("UCxxxxxxxxxxxxxxxxxxxxxx", "채널", "key")
        self.assertEqual(len(items), 1)  # videoId 없는 항목은 제외
        item = items[0]
        self.assertEqual(item.url, "https://www.youtube.com/watch?v=vid123")
        self.assertEqual(item.id, "yt:video:vid123")
        self.assertEqual(item.kind, "video")
        self.assertEqual(item.source, "채널")
        self.assertEqual(item.summary, "How to hold goal pace over 42km.")
        self.assertEqual(item.published.year, 2026)

    def test_bad_json_is_recorded_not_raised(self):
        self.collect.YT_API = self.server.url("bad.json")
        problems = []
        items = self.collect._collect_via_api("UCx", "채널", "key", problems)
        self.assertEqual(items, [])
        self.assertEqual(problems[0][0], "채널")

    def test_http_error_is_recorded_not_raised(self):
        self.collect.YT_API = self.server.url("missing.json")
        problems = []
        self.assertEqual(self.collect._collect_via_api("UCx", "채널", "key", problems), [])
        self.assertEqual(len(problems), 1)


class TestChannelSearch(unittest.TestCase):
    """URL 없이 채널 이름만으로 등록하는 경로."""

    @classmethod
    def setUpClass(cls):
        import collect as collect_module

        cls.collect = collect_module
        hit = {"items": [{"id": {"channelId": "UCkoreanrunnerkoreanrun"},
                          "snippet": {"title": "런랜드"}}]}
        cls.server = FeedServer({
            "hit.json": json.dumps(hit, ensure_ascii=False),
            "empty.json": json.dumps({"items": []}),
            "noid.json": json.dumps({"items": [{"snippet": {"title": "x"}}]}),
        })
        cls.saved = collect_module.YT_SEARCH_API

    @classmethod
    def tearDownClass(cls):
        cls.collect.YT_SEARCH_API = cls.saved
        cls.server.close()

    def test_finds_and_caches_channel_id(self):
        self.collect.YT_SEARCH_API = self.server.url("hit.json")
        cache = {}
        found = self.collect.search_channel_id("런랜드", cache, "key")
        self.assertEqual(found, "UCkoreanrunnerkoreanrun")
        self.assertEqual(cache["search:런랜드"], "UCkoreanrunnerkoreanrun")

    def test_cache_hit_makes_no_request(self):
        self.collect.YT_SEARCH_API = self.server.url("missing.json")  # 404 를 낼 주소
        cache = {"search:런랜드": "UCcachedcachedcachedcac"}
        self.assertEqual(
            self.collect.search_channel_id("런랜드", cache, "key"), "UCcachedcachedcachedcac"
        )

    def test_empty_result_is_recorded(self):
        self.collect.YT_SEARCH_API = self.server.url("empty.json")
        problems = []
        self.assertIsNone(self.collect.search_channel_id("없는채널", {}, "key", problems))
        self.assertEqual(problems[0][0], "없는채널")

    def test_result_without_channel_id_is_recorded(self):
        self.collect.YT_SEARCH_API = self.server.url("noid.json")
        problems = []
        self.assertIsNone(self.collect.search_channel_id("x", {}, "key", problems))
        self.assertEqual(len(problems), 1)

    def test_request_failure_is_recorded_not_raised(self):
        self.collect.YT_SEARCH_API = self.server.url("missing.json")
        problems = []
        self.assertIsNone(self.collect.search_channel_id("x", {}, "key", problems))
        self.assertEqual(len(problems), 1)


class TestNameOnlySourceWiring(unittest.TestCase):
    """이름만 적힌 채널이 collect() 에서 검색 경로를 타는지."""

    @classmethod
    def setUpClass(cls):
        import collect as collect_module

        cls.collect = collect_module
        search_hit = {"items": [{"id": {"channelId": "UCkoreanrunnerkoreanrun"},
                                 "snippet": {"title": "런랜드"}}]}
        uploads = {"items": [{"snippet": {
            "title": "10km 페이스 잡기",
            "description": "설명",
            "publishedAt": NOW.isoformat().replace("+00:00", "Z"),
            "resourceId": {"videoId": "vid999"},
        }}]}
        cls.server = FeedServer({
            "search.json": json.dumps(search_hit, ensure_ascii=False),
            "uploads.json": json.dumps(uploads, ensure_ascii=False),
        })
        cls.saved = (collect_module.YT_SEARCH_API, collect_module.YT_API)
        collect_module.YT_SEARCH_API = cls.server.url("search.json")
        collect_module.YT_API = cls.server.url("uploads.json")

    @classmethod
    def tearDownClass(cls):
        cls.collect.YT_SEARCH_API, cls.collect.YT_API = cls.saved
        cls.server.close()

    def config(self):
        return {"lookback_hours": 36, "sources": {"youtube": [{"name": "런랜드"}]}}

    def test_name_only_channel_is_resolved_and_collected(self):
        os.environ["YOUTUBE_API_KEY"] = "key"
        try:
            cache = {}
            items = self.collect.collect(self.config(), cache)
        finally:
            os.environ.pop("YOUTUBE_API_KEY", None)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].kind, "video")
        self.assertEqual(items[0].source, "런랜드")
        self.assertEqual(cache["search:런랜드"], "UCkoreanrunnerkoreanrun")

    def test_name_only_channel_skipped_without_api_key(self):
        os.environ.pop("YOUTUBE_API_KEY", None)
        self.assertEqual(self.collect.collect(self.config(), {}), [])

    def test_search_field_overrides_name_as_query(self):
        os.environ["YOUTUBE_API_KEY"] = "key"
        try:
            cache = {}
            config = self.config()
            config["sources"]["youtube"][0]["search"] = "런랜드 러닝"
            self.collect.collect(config, cache)
        finally:
            os.environ.pop("YOUTUBE_API_KEY", None)
        self.assertIn("search:런랜드 러닝", cache)
        self.assertNotIn("search:런랜드", cache)


class TestLinkPreview(unittest.TestCase):
    """썸네일은 첫 항목 하나만 — 텔레그램이 메시지당 하나만 허용한다."""

    def setUp(self):
        from telegram import _link_preview_options

        self.options = _link_preview_options

    def items(self, n, kind="article"):
        return [
            Item(id=f"id{i}", title=f"제목 {i}", url=f"https://ex.com/{i}",
                 source="매체", kind=kind, published=NOW)
            for i in range(n)
        ]

    def test_preview_is_large_and_above_text(self):
        opts = self.options({"link_preview": {"mode": "first"}}, self.items(3), [])
        self.assertEqual(opts["url"], "https://ex.com/0")
        self.assertTrue(opts["prefer_large_media"])
        self.assertTrue(opts["show_above_text"])

    def test_falls_back_to_first_video_when_no_articles(self):
        videos = self.items(2, "video")
        opts = self.options({"link_preview": {"mode": "first"}}, [], videos)
        self.assertEqual(opts["url"], videos[0].url)

    def test_small_and_inline_when_configured(self):
        setting = {"mode": "first", "large": False, "above_text": False}
        opts = self.options({"link_preview": setting}, self.items(1), [])
        self.assertTrue(opts["prefer_small_media"])
        self.assertNotIn("prefer_large_media", opts)
        self.assertNotIn("show_above_text", opts)

    def test_none_disables_preview(self):
        opts = self.options({"link_preview": {"mode": "none"}}, self.items(1), [])
        self.assertEqual(opts, {"is_disabled": True})

    def test_string_form_still_works(self):
        self.assertEqual(self.options({"link_preview": "none"}, self.items(1), []),
                         {"is_disabled": True})
        self.assertEqual(self.options({"link_preview": "first"}, self.items(1), [])["url"],
                         "https://ex.com/0")

    def test_missing_key_defaults_to_disabled(self):
        self.assertEqual(self.options({}, self.items(1), []), {"is_disabled": True})

    def test_no_items_disables_preview(self):
        self.assertEqual(self.options({"link_preview": {"mode": "first"}}, [], []),
                         {"is_disabled": True})


class TestLinkPreviewPrefersVideo(unittest.TestCase):
    """유튜브 링크는 썸네일이 확실히 뜬다 — 미리보기는 영상을 먼저 쓴다."""

    def setUp(self):
        from telegram import _link_preview_options

        self.options = _link_preview_options
        self.setting = {"link_preview": {"mode": "first", "prefer": "video"}}

    def items(self, n, kind, prefix):
        return [
            Item(id=f"{prefix}{i}", title=f"제목 {i}", url=f"https://{prefix}.com/{i}",
                 source="매체", kind=kind, published=NOW)
            for i in range(n)
        ]

    def test_video_wins_even_when_articles_come_first(self):
        articles = self.items(3, "article", "news")
        videos = self.items(2, "video", "youtu")
        self.assertEqual(self.options(self.setting, articles, videos)["url"], "https://youtu.com/0")

    def test_falls_back_to_article_when_no_videos(self):
        articles = self.items(2, "article", "news")
        self.assertEqual(self.options(self.setting, articles, [])["url"], "https://news.com/0")

    def test_prefer_any_uses_message_order(self):
        setting = {"link_preview": {"mode": "first", "prefer": "any"}}
        articles = self.items(1, "article", "news")
        videos = self.items(1, "video", "youtu")
        self.assertEqual(self.options(setting, articles, videos)["url"], "https://news.com/0")

    def test_video_preference_is_the_default(self):
        setting = {"link_preview": {"mode": "first"}}
        articles = self.items(1, "article", "news")
        videos = self.items(1, "video", "youtu")
        self.assertEqual(self.options(setting, articles, videos)["url"], "https://youtu.com/0")


class TestSubscriptionKeywordMatch(unittest.TestCase):
    """구독 목록에서 러닝·운동 채널을 골라내는 규칙."""

    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from list_subscriptions import looks_relevant

        self.match = looks_relevant

    def test_korean_running_channel_matches(self):
        self.assertTrue(self.match("마라닉TV", "러닝 브이로그"))

    def test_english_running_channel_matches(self):
        self.assertTrue(self.match("Ben Parkes", "Marathon training and racing"))

    def test_workout_channel_matches(self):
        self.assertTrue(self.match("피지컬갤러리", "홈트레이닝과 운동 과학"))

    def test_description_alone_can_match(self):
        self.assertTrue(self.match("어떤채널", "매일 달리기 기록을 남깁니다"))

    def test_unrelated_channel_does_not_match(self):
        self.assertFalse(self.match("먹방TV", "맛집 탐방과 리뷰"))

    def test_match_is_case_insensitive(self):
        self.assertTrue(self.match("RUNNING CHANNEL", ""))


class TestSlotTagFiltering(unittest.TestCase):
    """한 채널 묶음에서 시간대별로 다른 주제를 보내는 규칙."""

    def items(self):
        def make(n, tag, kind="video"):
            return Item(id=f"{tag}{n}", title=f"{tag} 제목 {n}",
                        url=f"https://ex.com/{tag}{n}", source=f"{tag}채널",
                        kind=kind, published=NOW, tags=(tag,))
        return [make(n, "food") for n in range(3)] + [make(n, "fashion") for n in range(3)]

    def config(self, tags):
        slot = {"title": "t", "articles": 0, "videos": 3}
        if tags is not None:
            slot["tags"] = tags
        return {"slots": {"s": slot}, "keywords": {}, "exclude": []}

    def test_slot_keeps_only_its_tag(self):
        _, videos = select(self.items(), {}, self.config(["food"]), "s")
        self.assertTrue(videos)
        self.assertTrue(all("food" in v.tags for v in videos))

    def test_other_slot_gets_the_other_tag(self):
        _, videos = select(self.items(), {}, self.config(["fashion"]), "s")
        self.assertTrue(all("fashion" in v.tags for v in videos))

    def test_no_tags_on_slot_keeps_everything(self):
        _, videos = select(self.items(), {}, self.config(None), "s")
        self.assertEqual(len({v.tags[0] for v in videos}), 2)

    def test_untagged_item_is_dropped_by_a_tagged_slot(self):
        items = self.items() + [
            Item(id="x", title="분류 없음", url="https://ex.com/x", source="기타",
                 kind="video", published=NOW)
        ]
        _, videos = select(items, {}, self.config(["food"]), "s")
        self.assertNotIn("분류 없음", [v.title for v in videos])


class TestStateNamespace(unittest.TestCase):
    """다이제스트마다 발송 이력이 섞이지 않아야 한다."""

    def setUp(self):
        import state as state_module

        self.state = state_module

    def test_default_namespace_keeps_original_paths(self):
        self.assertEqual(self.state.seen_path().name, "seen.json")
        self.assertEqual(self.state.seen_path().parent.name, "state")

    def test_named_namespace_gets_its_own_directory(self):
        self.assertEqual(self.state.seen_path("lifestyle").parent.name, "lifestyle")
        self.assertEqual(self.state.channels_path("lifestyle").parent.name, "lifestyle")

    def test_namespaces_do_not_collide(self):
        self.assertNotEqual(self.state.seen_path(), self.state.seen_path("lifestyle"))


class TestConfigNamespaceDerivation(unittest.TestCase):
    def setUp(self):
        from main import namespace_for

        self.derive = namespace_for

    def test_default_config_has_no_namespace(self):
        self.assertIsNone(self.derive("config.yaml"))

    def test_named_config_derives_its_namespace(self):
        self.assertEqual(self.derive("config.lifestyle.yaml"), "lifestyle")

    def test_path_prefix_is_ignored(self):
        self.assertEqual(self.derive("/repo/config.lifestyle.yaml"), "lifestyle")


class TestVideoSearch(unittest.TestCase):
    """구독 채널이 없는 주제도 검색으로 영상을 얻어야 한다."""

    @classmethod
    def setUpClass(cls):
        import collect as collect_module

        cls.collect = collect_module
        payload = {"items": [
            {"id": {"videoId": "abc123"},
             "snippet": {"title": "IT 뉴스 정리", "channelTitle": "테크채널",
                         "publishedAt": "2026-08-16T02:00:00Z",
                         "description": "이번 주  요약"}},
            {"id": {},  # videoId 없음 → 건너뛴다
             "snippet": {"title": "깨진 항목", "publishedAt": "2026-08-16T02:00:00Z"}},
        ]}
        cls.server = FeedServer({
            "search.json": json.dumps(payload, ensure_ascii=False),
            "bad.json": "not json",
        })
        cls.saved = collect_module.YT_SEARCH_API

    @classmethod
    def tearDownClass(cls):
        cls.collect.YT_SEARCH_API = cls.saved
        cls.server.close()

    def test_parses_videos(self):
        self.collect.YT_SEARCH_API = self.server.url("search.json")
        items = self.collect.search_videos("IT", "IT", "key")
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.url, "https://www.youtube.com/watch?v=abc123")
        self.assertEqual(item.id, "yt:video:abc123")
        self.assertEqual(item.kind, "video")
        # 검색은 채널을 가리지 않으므로 출처는 실제 채널 이름이어야 한다
        self.assertEqual(item.source, "테크채널")
        self.assertEqual(item.summary, "이번 주 요약")

    def test_failure_is_reported_not_raised(self):
        self.collect.YT_SEARCH_API = self.server.url("bad.json")
        problems = []
        self.assertEqual(self.collect.search_videos("IT", "IT", "key", problems=problems), [])
        self.assertEqual(problems[0][0], "IT")


class TestNaverBlogSearch(unittest.TestCase):
    """네이버 블로그는 RSS 없이 검색 API 로 넓게 훑는다."""

    @classmethod
    def setUpClass(cls):
        import collect as collect_module

        cls.collect = collect_module
        payload = {"items": [
            {"title": "서브3 <b>마라톤</b> 훈련 &amp; 후기",
             "link": "https://blog.naver.com/runner/223456789?from=search",
             "description": "인터벌 <b>훈련</b>을 12주간",
             "bloggername": "달리는 사람", "postdate": "20260819"},
            {"title": "링크 없음", "link": "", "postdate": "20260819"},
        ]}
        cls.server = FeedServer({
            "blog.json": json.dumps(payload, ensure_ascii=False),
            "bad.json": "not json",
        }, statuses={"denied.json": (401, json.dumps(
            {"errorMessage": "Not Exist Client ID : Authentication failed.",
             "errorCode": "024"}))})
        cls.saved = collect_module.NAVER_BLOG_API

    @classmethod
    def tearDownClass(cls):
        cls.collect.NAVER_BLOG_API = cls.saved
        cls.server.close()

    def test_parses_and_cleans(self):
        self.collect.NAVER_BLOG_API = self.server.url("blog.json")
        items = self.collect.search_naver_blog("마라톤", "러닝 블로그", "id", "secret")
        self.assertEqual(len(items), 1)  # link 없는 항목은 제외
        item = items[0]
        # <b> 와 엔티티가 그대로 텔레그램에 나가면 안 된다
        self.assertEqual(item.title, "서브3 마라톤 훈련 & 후기")
        self.assertEqual(item.summary, "인터벌 훈련을 12주간")
        self.assertEqual(item.kind, "article")
        # 한 블로그가 회차를 독식하지 않도록 출처는 블로그 이름
        self.assertEqual(item.source, "달리는 사람")
        # 추적 파라미터는 떨어져야 같은 글이 두 번 안 온다
        self.assertEqual(item.url, "https://blog.naver.com/runner/223456789")
        self.assertEqual(item.id, "naver:https://blog.naver.com/runner/223456789")

    def test_postdate_is_read_as_kst(self):
        self.collect.NAVER_BLOG_API = self.server.url("blog.json")
        item = self.collect.search_naver_blog("마라톤", "블로그", "id", "secret")[0]
        self.assertEqual(item.published.astimezone(self.collect.KST).date().isoformat(),
                         "2026-08-19")

    def test_failure_is_reported_not_raised(self):
        self.collect.NAVER_BLOG_API = self.server.url("bad.json")
        problems = []
        got = self.collect.search_naver_blog("마라톤", "블로그", "id", "s", problems=problems)
        self.assertEqual(got, [])
        self.assertEqual(problems[0][0], "블로그")

    def test_scopes_empty_points_at_the_app_not_the_key(self):
        """024 는 원인이 둘이다. 키를 다시 넣으라고 하면 영영 못 고친다."""
        import collect as collect_module

        class Response:
            def json(self):
                return {"errorCode": "024",
                        "errorMessage": "Scopes are Empty : Authentication failed."}

        reason = collect_module._naver_error(Response())
        self.assertIn("사용 API", reason)
        self.assertNotIn("Client ID 가 틀렸습니다", reason)

    def test_naver_error_body_is_surfaced(self):
        """상태 코드만으로는 무엇을 고쳐야 할지 알 수 없다."""
        self.collect.NAVER_BLOG_API = self.server.url("denied.json")  # 401 을 낼 주소
        problems = []
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            self.collect.search_naver_blog("마라톤", "블로그", "id", "s", problems=problems)
        self.assertIn("네이버 응답", buffer.getvalue())
        self.assertIn("024", buffer.getvalue())
        self.assertIn("024", problems[0][1])

    def test_unknown_sort_falls_back(self):
        self.collect.NAVER_BLOG_API = self.server.url("blog.json")
        got = self.collect.search_naver_blog("마라톤", "블로그", "id", "s", sort="best")
        self.assertEqual(len(got), 1)


class TestNaverNeedsCredentials(unittest.TestCase):
    """키가 없으면 그 소스만 건너뛰고 발송은 계속돼야 한다."""

    def setUp(self):
        import collect as collect_module

        self.collect = collect_module
        self.saved = {k: os.environ.pop(k, None)
                      for k in ("NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET", "YOUTUBE_API_KEY")}

    def tearDown(self):
        for k, v in self.saved.items():
            if v is not None:
                os.environ[k] = v

    def test_missing_keys_skip_the_source(self):
        config = {"sources": {"naver_blog": [{"name": "블로그", "query": "러닝"}]},
                  "lookback_hours": 48}
        self.assertEqual(self.collect.collect(config, {}), [])


class TestPageQueriesFanOut(unittest.TestCase):
    """페이지 검색어 하나가 뉴스와 유튜브에 걸린다.

    네이버 검색은 API 가 NAVER API HUB 로 이관돼 기존 키가 통하지 않는다.
    블로그는 검색 대신 feeds(블로그별 RSS)로 받는다.
    """

    def setUp(self):
        import settings as settings_module

        self.apply = settings_module.apply
        self.data = {"digests": [{
            "key": "it", "label": "IT",
            "slots": [{"slot": "daily", "title": "IT", "articles": 2, "videos": 3}],
            "queries": [{"name": "IT", "query": "IT 뉴스"}],
        }]}

    def test_news_and_video(self):
        cfg = self.apply({}, self.data, "it", "daily")
        for key in ("google_news", "youtube_search"):
            self.assertEqual([s["query"] for s in cfg["sources"][key]], ["IT 뉴스"], key)

    def test_youtube_can_be_turned_off_per_query(self):
        self.data["digests"][0]["queries"][0]["youtube"] = False
        cfg = self.apply({}, self.data, "it", "daily")
        self.assertIsNone(cfg["sources"].get("youtube_search"))
        self.assertEqual(len(cfg["sources"]["google_news"]), 1)


class TestPageFeeds(unittest.TestCase):
    """네이버 블로그는 검색 API 대신 블로그별 RSS 로 받는다."""

    def setUp(self):
        import settings as settings_module

        self.apply = settings_module.apply
        self.data = {"digests": [{
            "key": "run", "label": "러닝",
            "slots": [{"slot": "daily", "title": "러닝", "articles": 2, "videos": 3}],
            "feeds": [{"name": "달리는회계사",
                       "url": "https://rss.blog.naver.com/runner.xml"}],
        }]}

    def test_feed_lands_in_rss(self):
        cfg = self.apply({}, self.data, "run", "daily")
        self.assertEqual(cfg["sources"]["rss"],
                         [{"name": "달리는회계사", "url": "https://rss.blog.naver.com/runner.xml"}])

    def test_config_feeds_are_kept(self):
        base = {"sources": {"rss": [{"name": "기존", "url": "https://x.test/f.xml"}]}}
        cfg = self.apply(base, self.data, "run", "daily")
        self.assertEqual([s["name"] for s in cfg["sources"]["rss"]], ["기존", "달리는회계사"])
        # 원본은 건드리지 않는다
        self.assertEqual(len(base["sources"]["rss"]), 1)

    def test_page_queries_no_longer_hit_naver_search(self):
        """검색 API 가 이관돼 키가 안 먹는다. 매 회차 401 을 만들지 않아야 한다."""
        data = {"digests": [{
            "key": "it", "label": "IT",
            "slots": [{"slot": "daily", "title": "IT", "articles": 1, "videos": 1}],
            "queries": [{"name": "IT", "query": "IT"}],
        }]}
        cfg = self.apply({}, data, "it", "daily")
        self.assertIsNone(cfg["sources"].get("naver_blog"))
        self.assertEqual(len(cfg["sources"]["google_news"]), 1)


class TestRepeatedStories(unittest.TestCase):
    """같은 사건을 여러 매체가 쓰면 URL 도 id 도 달라 그냥은 안 걸러진다."""

    def setUp(self):
        from collect import Item
        from filter import fingerprint, select

        self.Item = Item
        self.fingerprint = fingerprint
        self.select = select
        self.now = datetime.now(timezone.utc)
        self.config = {"slots": {"m": {"articles": 5, "videos": 0}}}

    def article(self, ident, title):
        return self.Item(id=ident, title=title, url=f"https://x.test/{ident}",
                         source=ident, kind="article", published=self.now)

    def test_publisher_tail_is_ignored(self):
        self.assertEqual(self.fingerprint("서울마라톤 접수 시작 - 연합뉴스"),
                         self.fingerprint("서울마라톤 접수 시작 | 뉴시스"))

    def test_different_stories_keep_different_marks(self):
        self.assertNotEqual(self.fingerprint("마라톤 완주 비결"),
                            self.fingerprint("김치찌개 끓이는 법"))

    def test_untitled_has_no_mark(self):
        self.assertEqual(self.fingerprint(""), "")
        self.assertEqual(self.fingerprint("!!! ???"), "")

    def test_same_story_folds_within_one_run(self):
        items = [
            self.article("a", "서울마라톤 접수 시작 - 연합뉴스"),
            self.article("b", "서울마라톤 접수 시작 | 뉴시스"),
            self.article("c", "러닝화 고르는 법"),
        ]
        articles, _ = self.select(items, {}, self.config, "m")
        self.assertEqual(len(articles), 2)

    def test_story_sent_before_does_not_return(self):
        sent = self.article("a", "서울마라톤 접수 시작 - 연합뉴스")
        seen = {self.fingerprint(sent.title): "2026-08-21"}
        again = self.article("b", "서울마라톤 접수 시작 | 뉴시스")
        articles, _ = self.select([again], seen, self.config, "m")
        self.assertEqual(articles, [])

    def test_mark_sent_records_both(self):
        import state

        item = self.article("a", "서울마라톤 접수 시작")
        seen = state.mark_sent({}, [item])
        self.assertIn("a", seen)
        self.assertIn(self.fingerprint(item.title), seen)


class TestYoutubeThresholds(unittest.TestCase):
    """구독자·조회수가 기준에 못 미치는 영상은 뺀다."""

    def setUp(self):
        import collect as collect_module
        from collect import Item

        self.collect = collect_module
        self.Item = Item
        self.config = {"youtube_filter": {"min_subscribers": 100000, "min_views": 10000}}
        self.saved = (collect_module.video_views, collect_module.channel_subscribers)

    def tearDown(self):
        self.collect.video_views, self.collect.channel_subscribers = self.saved

    def video(self, vid, channel):
        return self.Item(id=f"yt:video:{vid}", title=vid, url="u", source="s",
                         kind="video", published=datetime.now(timezone.utc),
                         channel_id=channel)

    def stub(self, views, subs, subs_ok=True):
        self.collect.video_views = lambda ids, key, problems=None: views
        self.collect.channel_subscribers = lambda ids, key, cache, problems=None: (subs, subs_ok)

    def test_curated_channels_skip_the_subscriber_floor(self):
        """직접 적어둔 채널은 크기를 따지려고 고른 게 아니다."""
        mine = self.video("mine", "UC2")
        mine.trusted = True
        found = self.video("found", "UC2")
        self.stub({"mine": 50000, "found": 50000}, {"UC2": 1200})
        kept = self.collect.filter_youtube([mine, found], self.config, "key", {})
        self.assertEqual([i.id for i in kept], ["yt:video:mine"])

    def test_curated_channels_still_need_views(self):
        mine = self.video("mine", "UC2")
        mine.trusted = True
        self.stub({"mine": 900}, {})
        self.assertEqual(self.collect.filter_youtube([mine], self.config, "key", {}), [])

    def test_subscribers_are_only_asked_for_unknown_channels(self):
        asked = []
        self.collect.video_views = lambda ids, key, problems=None: {"mine": 50000}
        self.collect.channel_subscribers = (
            lambda ids, key, cache, problems=None: (asked.extend(ids), ({}, True))[1])
        mine = self.video("mine", "UC2")
        mine.trusted = True
        self.collect.filter_youtube([mine], self.config, "key", {})
        self.assertEqual(asked, [])

    def test_low_views_and_small_channels_are_dropped(self):
        items = [self.video("big", "UC1"), self.video("few", "UC1"), self.video("small", "UC2")]
        self.stub({"big": 50000, "few": 900, "small": 80000}, {"UC1": 500000, "UC2": 1200})
        kept = self.collect.filter_youtube(items, self.config, "key", {})
        self.assertEqual([i.id for i in kept], ["yt:video:big"])

    def test_unknown_numbers_pass(self):
        """모르는 것을 이유로 버리면 API 가 흔들릴 때 영상이 통째로 사라진다."""
        items = [self.video("x", "UC9")]
        self.stub({}, {})
        self.assertEqual(self.collect.filter_youtube(items, self.config, "key", {}), items)

    def test_articles_are_untouched(self):
        article = self.Item(id="a", title="t", url="u", source="s", kind="article",
                            published=datetime.now(timezone.utc))
        self.stub({}, {})
        self.assertIn(article, self.collect.filter_youtube([article], self.config, "key", {}))

    def test_no_limits_means_no_api_calls(self):
        called = []
        self.collect.video_views = lambda *a, **k: called.append(1) or {}
        items = [self.video("x", "UC1")]
        self.assertEqual(self.collect.filter_youtube(items, {}, "key", {}), items)
        self.assertEqual(called, [])

    def test_subscriber_counts_are_cached(self):
        from datetime import timedelta as td

        fresh = (datetime.now(timezone.utc) - td(days=1)).isoformat()
        cache = {"subs:UC1": [500000, fresh]}
        subs, ok = self.saved[1](["UC1"], "key", cache)
        self.assertEqual(subs, {"UC1": 500000})
        self.assertTrue(ok)


class TestGoogleNewsWindow(unittest.TestCase):
    """기간을 좁히지 않으면 관련도순으로 오래된 기사가 와서 전부 잘려 나간다."""

    def setUp(self):
        import collect as collect_module

        self.collect = collect_module

    def test_window_never_narrows_below_lookback(self):
        self.assertEqual(self.collect._fresh_window(6), "when:6h")
        self.assertEqual(self.collect._fresh_window(23), "when:23h")
        self.assertEqual(self.collect._fresh_window(24), "when:1d")
        # 36시간을 1d 로 줄이면 열두 시간을 잃는다
        self.assertEqual(self.collect._fresh_window(36), "when:2d")
        self.assertEqual(self.collect._fresh_window(48), "when:2d")
        self.assertEqual(self.collect._fresh_window(49), "when:3d")

    def test_zero_and_negative_do_not_crash(self):
        self.assertEqual(self.collect._fresh_window(0), "when:1h")
        self.assertEqual(self.collect._fresh_window(-5), "when:1h")


class TestGoogleNewsFallback(unittest.TestCase):
    """when: 을 붙여 빈손이면 제한 없이 한 번 더 부른다."""

    def setUp(self):
        import collect as collect_module

        self.collect = collect_module
        self.calls = []
        self.saved = collect_module._parse_feed

    def tearDown(self):
        self.collect._parse_feed = self.saved

    def _stub(self, results):
        def fake(url, name, kind, problems=None):
            self.calls.append(url)
            return results.pop(0)
        self.collect._parse_feed = fake

    def test_window_is_added_to_the_query(self):
        from collect import Item

        item = Item(id="a", title="t", url="u", source="s", kind="article",
                    published=datetime.now(timezone.utc))
        self._stub([[item]])
        got = self.collect._collect_google_news({"name": "뉴스", "query": "러닝"}, 48)
        self.assertEqual(got, [item])
        self.assertEqual(len(self.calls), 1)
        self.assertIn("when%3A2d", self.calls[0])

    def test_empty_result_retries_without_the_window(self):
        self._stub([[], []])
        self.collect._collect_google_news({"name": "뉴스", "query": "러닝"}, 48)
        self.assertEqual(len(self.calls), 2)
        self.assertIn("when%3A2d", self.calls[0])
        self.assertNotIn("when", self.calls[1])


class TestSearchOrder(unittest.TestCase):
    """'인기순'으로도 찾을 수 있어야 채널 목록 밖의 영상이 들어온다."""

    @classmethod
    def setUpClass(cls):
        import collect as collect_module

        cls.collect = collect_module
        payload = {"items": [{"id": {"videoId": "v1"}, "snippet": {
            "title": "많이 본 영상", "channelTitle": "채널",
            "publishedAt": "2026-08-19T02:00:00Z", "description": ""}}]}
        cls.server = FeedServer({"s.json": json.dumps(payload, ensure_ascii=False)})
        cls.saved = collect_module.YT_SEARCH_API
        collect_module.YT_SEARCH_API = cls.server.url("s.json")

    @classmethod
    def tearDownClass(cls):
        cls.collect.YT_SEARCH_API = cls.saved
        cls.server.close()

    def test_known_orders_are_accepted(self):
        for order in self.collect.SEARCH_ORDERS:
            items = self.collect.search_videos("러닝", "러닝", "key", order=order)
            self.assertEqual(len(items), 1, order)

    def test_unknown_order_falls_back_to_date(self):
        # 오타가 있어도 그 회차를 통째로 잃지 않아야 한다
        items = self.collect.search_videos("러닝", "러닝", "key", order="popular")
        self.assertEqual(len(items), 1)


class TestChannelOnlyConfigsAlsoSearch(unittest.TestCase):
    """채널 목록만 있던 주제도 유튜브 전체에서 영상을 받아야 한다."""

    def test_every_config_has_a_video_search(self):
        import yaml as yaml_module

        repo = Path(__file__).resolve().parent.parent
        for path in sorted(repo.glob("config*.yaml")):
            config = yaml_module.safe_load(path.read_text(encoding="utf-8")) or {}
            searches = (config.get("sources") or {}).get("youtube_search") or []
            self.assertTrue(searches, f"{path.name} 에 youtube_search 가 없습니다")
            for src in searches:
                self.assertIn("query", src)
                if src.get("order"):
                    from collect import SEARCH_ORDERS

                    self.assertIn(src["order"], SEARCH_ORDERS)


class TestPageQueriesFeedBothSides(unittest.TestCase):
    """페이지 검색어는 뉴스와 유튜브 양쪽에 걸려야 썸네일이 뜬다."""

    def setUp(self):
        import settings as settings_module

        self.apply = settings_module.apply
        self.data = {"digests": [{
            "key": "it", "label": "IT",
            "slots": [{"slot": "daily", "title": "IT", "articles": 2, "videos": 3}],
            "queries": [{"name": "IT", "query": "IT 뉴스"}],
        }]}

    def test_query_becomes_news_and_video_source(self):
        cfg = self.apply({}, self.data, "it", "daily")
        self.assertEqual([s["query"] for s in cfg["sources"]["google_news"]], ["IT 뉴스"])
        self.assertEqual([s["query"] for s in cfg["sources"]["youtube_search"]], ["IT 뉴스"])

    def test_tags_carry_to_both(self):
        self.data["digests"][0]["queries"][0]["tags"] = ["tech"]
        cfg = self.apply({}, self.data, "it", "daily")
        self.assertEqual(cfg["sources"]["youtube_search"][0]["tags"], ["tech"])

    def test_youtube_can_be_turned_off_per_query(self):
        self.data["digests"][0]["queries"][0]["youtube"] = False
        cfg = self.apply({}, self.data, "it", "daily")
        self.assertEqual(cfg["sources"].get("youtube_search"), None)
        self.assertEqual(len(cfg["sources"]["google_news"]), 1)


class TestPreviewSkipsRedirects(unittest.TestCase):
    """구글 뉴스 링크는 미리보기가 비므로 썸네일 대상에서 뺀다."""

    def setUp(self):
        from collect import Item
        from telegram import _link_preview_options

        self.options = _link_preview_options
        now = datetime.now(timezone.utc)
        self.news = Item(id="n1", title="기사", source="구글뉴스", kind="article",
                         published=now, url="https://news.google.com/rss/articles/CBMi")
        self.direct = Item(id="d1", title="기사", source="블로그", kind="article",
                           published=now, url="https://runnersworld.com/a")
        self.video = Item(id="v1", title="영상", source="채널", kind="video",
                          published=now, url="https://www.youtube.com/watch?v=xyz")
        self.config = {"link_preview": {"mode": "first", "prefer": "video"}}

    def test_video_wins(self):
        opts = self.options(self.config, [self.news], [self.video])
        self.assertEqual(opts["url"], self.video.url)

    def test_redirect_article_is_skipped_for_a_real_one(self):
        opts = self.options(self.config, [self.news, self.direct], [])
        self.assertEqual(opts["url"], self.direct.url)

    def test_all_redirects_means_no_preview(self):
        opts = self.options(self.config, [self.news], [])
        self.assertEqual(opts, {"is_disabled": True})


class TestEmptySlots(unittest.TestCase):
    """시간이 하나도 없는 주제가 남아도 다른 회차를 막지 않아야 한다."""

    def setUp(self):
        import settings as settings_module

        self.settings_module = settings_module
        self.data = {"digests": [
            {"key": "빈주제", "label": "IT", "slots": None, "keywords": {"IT": 3}},
            {"config": "config.yaml", "label": "러닝", "slots": [
                {"slot": "morning", "send_at": "08:00", "enabled": True},
            ]},
        ]}

    def test_due_skips_it_and_keeps_going(self):
        now = datetime(2026, 8, 17, 8, 5, tzinfo=ZoneInfo("Asia/Seoul"))
        ready = self.settings_module.due(self.data, now=now, state={})
        self.assertEqual([(c, s) for c, s, _, _ in ready], [("config.yaml", "morning")])

    def test_lookup_returns_no_slot(self):
        digest, slot = self.settings_module.for_slot(self.data, "빈주제", "daily")
        self.assertIsNone(slot)


class TestIgnoreSeenFlag(unittest.TestCase):
    """테스트 발송은 이미 보낸 항목도 다시 골라야 한다."""

    def setUp(self):
        from collect import Item
        from filter import select

        self.select = select
        now = datetime.now(timezone.utc)
        self.items = [
            Item(id="a1", title="인터벌 훈련법", url="https://a.test/1",
                 source="테스트", published=now, kind="article"),
        ]
        self.config = {"slots": {"morning": {"articles": 3, "videos": 0}}}

    def test_seen_items_are_dropped_by_default(self):
        seen = {"a1": "2026-01-01"}
        articles, _ = self.select(self.items, seen, self.config, "morning")
        self.assertEqual(articles, [])

    def test_empty_seen_brings_them_back(self):
        # --ignore-seen 은 seen 대신 빈 딕셔너리를 넘긴다
        articles, _ = self.select(self.items, {}, self.config, "morning")
        self.assertEqual([a.url for a in articles], ["https://a.test/1"])


class TestTriggerArguments(unittest.TestCase):
    """어드민 페이지의 테스트 발송이 넘기는 인자를 파서가 받아야 한다."""

    def test_ignore_seen_is_parsed(self):
        import main

        argv = ["main.py", "--config", "config.food.yaml", "--slot", "lunch", "--ignore-seen"]
        with unittest.mock.patch.object(sys, "argv", argv):
            args = main.parse_args()
        self.assertTrue(args.ignore_seen)
        self.assertEqual((args.config, args.slot), ("config.food.yaml", "lunch"))

    def test_ignore_seen_defaults_off(self):
        import main

        with unittest.mock.patch.object(sys, "argv", ["main.py", "--due"]):
            args = main.parse_args()
        self.assertFalse(args.ignore_seen)


class TestAdminYamlRoundTrip(unittest.TestCase):
    """어드민 페이지가 저장한 YAML 을 파이썬이 그대로 읽어야 한다.

    여기가 어긋나면 저장 버튼이 설정을 조용히 망가뜨린다.
    """

    def test_round_trip_preserves_values(self):
        import shutil
        import subprocess
        import tempfile

        import yaml as yaml_module

        if not shutil.which("node"):
            self.skipTest("node 없음")

        repo = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "rt.mjs"
            script.write_text(
                'import {readFileSync} from "node:fs";\n'
                f'import {{toYaml, fromYaml}} from "{repo}/tests/js/yaml.mjs";\n'
                f'const t = readFileSync("{repo}/settings.yaml", "utf8");\n'
                "process.stdout.write(toYaml(fromYaml(toYaml(fromYaml(t)))));\n",
                encoding="utf-8",
            )
            out = subprocess.run(
                ["node", str(script)], capture_output=True, text=True, timeout=30
            )
            self.assertEqual(out.returncode, 0, out.stderr)

        original = yaml_module.safe_load((repo / "settings.yaml").read_text(encoding="utf-8"))
        self.assertEqual(yaml_module.safe_load(out.stdout), original)


class TestScheduleDue(unittest.TestCase):
    """발송 시각이 데이터가 됐으니, 언제 보낼지 판단하는 규칙."""

    def setUp(self):
        import settings as settings_module

        self.settings_module = settings_module
        self.tz = ZoneInfo("Asia/Seoul")
        self.conf = {
            "digests": [
                {"config": "a.yaml", "slots": [
                    {"slot": "morning", "send_at": "08:00"},
                    {"slot": "off", "send_at": "09:00", "enabled": False},
                ]}
            ]
        }

    def at(self, hour, minute=0):
        return datetime(2026, 8, 16, hour, minute, tzinfo=self.tz)

    def keys(self, now, state=None):
        return [k for _, _, k, _ in self.settings_module.due(self.conf, now, state)]

    def test_not_due_before_the_hour(self):
        self.assertEqual(self.keys(self.at(7, 59)), [])

    def test_due_right_after_the_hour(self):
        self.assertEqual(self.keys(self.at(8, 1)), ["a.yaml:morning"])

    def test_still_due_when_the_run_is_late(self):
        self.assertEqual(self.keys(self.at(10, 30)), ["a.yaml:morning"])

    def test_skipped_once_too_late(self):
        self.assertEqual(self.keys(self.at(23, 0)), [])

    def test_not_repeated_after_sending_today(self):
        state = {"a.yaml:morning": "2026-08-16"}
        self.assertEqual(self.keys(self.at(9, 0), state), [])

    def test_sends_again_the_next_day(self):
        state = {"a.yaml:morning": "2026-08-15"}
        self.assertEqual(self.keys(self.at(9, 0), state), ["a.yaml:morning"])

    def test_disabled_slot_never_fires(self):
        self.assertNotIn("a.yaml:off", self.keys(self.at(9, 30)))

    def test_bad_time_is_skipped_not_raised(self):
        conf = {"digests": [{"config": "a.yaml", "slots": [{"slot": "x", "send_at": "아침"}]}]}
        self.assertEqual(self.settings_module.due(conf, self.at(12)), [])


class TestSettingsOverlay(unittest.TestCase):
    """settings.yaml 값이 config 위에 덮이는지."""

    def setUp(self):
        import settings as settings_module

        self.apply = settings_module.apply
        self.settings = {
            "digests": [{
                "config": "config.yaml",
                "slots": [{"slot": "morning", "title": "새 제목", "articles": 9, "videos": 8}],
                "keywords": {"부상": 5},
            }],
            "exclude": ["광고"],
        }
        self.config = {
            "slots": {"morning": {"title": "옛 제목", "articles": 3, "videos": 2}},
            "keywords": {"훈련": 1},
            "exclude": ["부고"],
            "sources": {"youtube": [{"name": "채널"}]},
        }

    def test_slot_fields_are_overridden(self):
        merged = self.apply(self.config, self.settings, "config.yaml", "morning")
        self.assertEqual(merged["slots"]["morning"]["title"], "새 제목")
        self.assertEqual(merged["slots"]["morning"]["articles"], 9)

    def test_keywords_and_exclude_are_replaced(self):
        merged = self.apply(self.config, self.settings, "config.yaml", "morning")
        self.assertEqual(merged["keywords"], {"부상": 5})
        self.assertEqual(merged["exclude"], ["광고"])

    def test_sources_are_left_alone(self):
        merged = self.apply(self.config, self.settings, "config.yaml", "morning")
        self.assertEqual(merged["sources"], self.config["sources"])

    def test_slot_only_in_settings_is_created(self):
        """페이지에서 시간을 더 넣으면 config 에 없는 슬롯이 생긴다."""
        settings = dict(self.settings)
        settings["digests"] = [dict(
            self.settings["digests"][0],
            slots=[{"slot": "sab12x", "title": "야식 브리핑", "articles": 2, "videos": 5}],
        )]
        merged = self.apply(self.config, settings, "config.yaml", "sab12x")
        self.assertEqual(merged["slots"]["sab12x"],
                         {"title": "야식 브리핑", "articles": 2, "videos": 5})
        # 원래 있던 슬롯은 그대로 남는다
        self.assertIn("morning", merged["slots"])

    def test_unknown_slot_leaves_config_untouched(self):
        merged = self.apply(self.config, self.settings, "config.yaml", "없는슬롯")
        self.assertEqual(merged, self.config)

    def test_original_config_is_not_mutated(self):
        self.apply(self.config, self.settings, "config.yaml", "morning")
        self.assertEqual(self.config["slots"]["morning"]["title"], "옛 제목")


class TestPageCreatedTopic(unittest.TestCase):
    """페이지에서 만든 주제(config 파일 없음)가 파이썬 쪽에서 그대로 도는지."""

    def setUp(self):
        import settings as settings_module

        self.settings_module = settings_module
        self.data = {
            "digests": [{
                "key": "coffee",
                "label": "커피",
                "slots": [{"slot": "daily", "title": "커피 브리핑", "send_at": "18:00",
                           "articles": 2, "videos": 3}],
                "keywords": {"커피": 3},
                "queries": [{"name": "커피", "query": "스페셜티 커피"}],
                "channels": [{"name": "채널", "channel_id": "UC" + "x" * 22}],
            }],
            "exclude": ["광고"],
        }

    def test_found_by_key(self):
        self.assertIsNotNone(self.settings_module.find(self.data, "coffee"))

    def test_key_becomes_the_state_namespace(self):
        digest = self.settings_module.find(self.data, "coffee")
        self.assertEqual(self.settings_module.digest_key(digest), "coffee")

    def test_queries_and_channels_become_sources(self):
        cfg = self.settings_module.apply({}, self.data, "coffee", "daily")
        self.assertEqual(len(cfg["sources"]["google_news"]), 1)
        self.assertEqual(cfg["sources"]["youtube"][0]["channel_id"], "UC" + "x" * 22)
        self.assertEqual(cfg["sources"]["google_news"][0]["lang"], "ko")

    def test_defaults_fill_in_without_a_config_file(self):
        cfg = self.settings_module.apply({}, self.data, "coffee", "daily")
        self.assertEqual(cfg["timezone"], "Asia/Seoul")
        self.assertTrue(cfg["summary"]["enabled"])
        self.assertEqual(cfg["link_preview"]["prefer"], "video")

    def test_page_sources_append_to_a_config_file(self):
        base = {"sources": {"youtube": [{"name": "기존", "channel_id": "UC" + "y" * 22}]}}
        data = dict(self.data)
        data["digests"] = [dict(self.data["digests"][0], config="config.yaml")]
        cfg = self.settings_module.apply(base, data, "config.yaml", "daily")
        names = [c["name"] for c in cfg["sources"]["youtube"]]
        self.assertEqual(names, ["기존", "채널"])

    def test_config_file_sources_are_not_mutated(self):
        base = {"sources": {"youtube": [{"name": "기존"}]}}
        data = dict(self.data)
        data["digests"] = [dict(self.data["digests"][0], config="config.yaml")]
        self.settings_module.apply(base, data, "config.yaml", "daily")
        self.assertEqual(len(base["sources"]["youtube"]), 1)
