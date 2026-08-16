"""수집→선별→포맷 전 구간 테스트.

외부 네트워크 없이 돌도록 고정 피드를 로컬 HTTP 서버로 띄운다.
"""

import functools
import http.server
import json
import os
import socketserver
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

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

    def __init__(self, files):
        self.dir = tempfile.TemporaryDirectory()
        for name, content in files.items():
            (Path(self.dir.name) / name).write_text(content, encoding="utf-8")

        class QuietHandler(http.server.SimpleHTTPRequestHandler):
            # 요청 로그로 테스트 출력이 지저분해지지 않게 막는다
            def log_message(self, *args):
                pass

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
