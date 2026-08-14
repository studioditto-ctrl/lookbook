"""요약 요청을 실제 SDK로 만들어 로컬 가짜 서버에 보내 검증한다.

목(mock)은 내가 상상한 인터페이스만 확인해 준다. 여기서는 진짜
anthropic SDK가 직렬화한 HTTP 본문을 받아 확인하므로, 파라미터 이름이나
스키마 모양이 틀리면 드러난다. API 키는 필요 없다.
"""

import json
import os
import sys
import threading
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from collect import Item  # noqa: E402
import summarize as summarize_module  # noqa: E402

NOW = datetime.now(timezone.utc)

MODEL_REPLY = {
    "items": [
        {"index": 0, "title_ko": "무릎 부상 없이 거리 늘리기", "summary_ko": "회복일 배치가 핵심이다."},
        {"index": 1, "title_ko": "주간 훈련 루틴", "summary_ko": "주 2회 근력 운동을 권한다."},
    ]
}


class FakeAnthropic(BaseHTTPRequestHandler):
    requests = []
    stop_reason = "end_turn"

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        FakeAnthropic.requests.append({"path": self.path, "body": body})

        payload = {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": body.get("model", "?"),
            "content": [
                {"type": "text", "text": json.dumps(MODEL_REPLY, ensure_ascii=False)}
            ],
            "stop_reason": FakeAnthropic.stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": 100, "output_tokens": 50},
        }
        raw = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class TestSummarizeOverHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = HTTPServer(("127.0.0.1", 0), FakeAnthropic)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        port = cls.httpd.server_address[1]
        cls.saved = {k: os.environ.get(k) for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL")}
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-not-a-real-key"
        os.environ["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        for key, value in cls.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def setUp(self):
        FakeAnthropic.requests.clear()
        FakeAnthropic.stop_reason = "end_turn"

    def items(self):
        return [
            Item(
                id="a",
                title="How to add mileage without knee pain",
                url="https://ex.com/1",
                source="Runner's World",
                kind="article",
                published=NOW,
                summary="Recovery days matter more than the 10% rule.",
            ),
            Item(
                id="b",
                title="Weekly training routine",
                url="https://youtu.be/x",
                source="Ben Parkes",
                kind="video",
                published=NOW,
                summary="",
            ),
        ]

    def test_end_to_end_updates_items(self):
        items = self.items()
        self.assertTrue(summarize_module.summarize(items, {}))
        self.assertEqual(items[0].title, "무릎 부상 없이 거리 늘리기")
        self.assertEqual(items[0].summary_ko, "회복일 배치가 핵심이다.")
        self.assertEqual(items[1].title, "주간 훈련 루틴")

    def test_request_hits_messages_endpoint(self):
        summarize_module.summarize(self.items(), {})
        self.assertEqual(len(FakeAnthropic.requests), 1)
        self.assertEqual(FakeAnthropic.requests[0]["path"], "/v1/messages")

    def test_request_body_shape(self):
        summarize_module.summarize(self.items(), {"summary": {"effort": "medium"}})
        body = FakeAnthropic.requests[0]["body"]
        self.assertEqual(body["model"], "claude-opus-5")
        self.assertEqual(body["output_config"]["effort"], "medium")
        self.assertEqual(body["output_config"]["format"]["type"], "json_schema")
        self.assertIn("items", body["output_config"]["format"]["schema"]["properties"])
        self.assertIsInstance(body["max_tokens"], int)
        self.assertIn("러닝 뉴스 다이제스트", body["system"])

    def test_user_message_carries_items(self):
        summarize_module.summarize(self.items(), {})
        content = FakeAnthropic.requests[0]["body"]["messages"][0]["content"]
        payload = json.loads(content)
        self.assertEqual(len(payload), 2)
        self.assertEqual(payload[0]["title"], "How to add mileage without knee pain")
        self.assertEqual(payload[0]["snippet"], "Recovery days matter more than the 10% rule.")
        self.assertEqual(payload[1]["kind"], "영상")
        self.assertEqual(payload[1]["snippet"], "")

    def test_refusal_leaves_items_untouched(self):
        FakeAnthropic.stop_reason = "refusal"
        items = self.items()
        self.assertFalse(summarize_module.summarize(items, {}))
        self.assertEqual(items[0].title, "How to add mileage without knee pain")

    def test_truncated_response_leaves_items_untouched(self):
        FakeAnthropic.stop_reason = "max_tokens"
        items = self.items()
        self.assertFalse(summarize_module.summarize(items, {}))
        self.assertEqual(items[0].title, "How to add mileage without knee pain")


if __name__ == "__main__":
    unittest.main(verbosity=2)
