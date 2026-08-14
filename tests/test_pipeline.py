"""수집→선별→포맷 전 구간 테스트.

외부 네트워크 없이 돌도록 고정 피드를 로컬 HTTP 서버로 띄운다.
"""

import functools
import http.server
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
