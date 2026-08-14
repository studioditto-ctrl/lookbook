"""메시지 포맷팅과 텔레그램 전송."""

import html
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

API = "https://api.telegram.org/bot{token}/sendMessage"
TIMEOUT = 20
MAX_LEN = 4096  # 텔레그램 메시지 길이 상한
TITLE_MAX = 110
WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]


def _esc(text):
    return html.escape(text, quote=False)


def _shorten(text, limit=TITLE_MAX):
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


SUMMARY_MAX = 200


def _blocks(heading, items, start_index):
    """항목 하나를 줄 리스트 하나로 묶는다. 길이 초과 시 블록째 덜어내기 위해서."""
    blocks = [[heading]]
    for offset, item in enumerate(items):
        number = start_index + offset
        block = [
            f'{number}. <a href="{_esc(item.url)}">{_esc(_shorten(item.title))}</a>'
        ]
        if item.summary_ko:
            block.append(f"   {_esc(_shorten(item.summary_ko, SUMMARY_MAX))}")
        block.append(f"   <i>{_esc(item.source)}</i>")
        blocks.append(block)
    return blocks


def build_message(articles, videos, config, slot):
    slot_config = (config.get("slots") or {}).get(slot) or {}
    tz = ZoneInfo(config.get("timezone", "Asia/Seoul"))
    now = datetime.now(tz)

    title = slot_config.get("title", "러닝 브리핑")
    header = [
        f"<b>{_esc(title)}</b>",
        f"<i>{now.month}월 {now.day}일 ({WEEKDAYS[now.weekday()]})</i>",
    ]

    blocks = [header]
    if articles:
        blocks += [[""]] + _blocks("📰 <b>읽을거리</b>", articles, 1)
    if videos:
        blocks += [[""]] + _blocks("🎬 <b>영상</b>", videos, len(articles) + 1)

    def render(bs):
        return "\n".join(line for block in bs for line in block)

    # 상한을 넘으면 뒤에서부터 항목 블록째 덜어낸다
    while len(render(blocks)) > MAX_LEN and len(blocks) > 1:
        blocks = blocks[:-1]

    return render(blocks)


def _link_preview_options(config, articles, videos):
    mode = config.get("link_preview", "none")
    if mode == "first":
        first = (articles or videos or [None])[0]
        if first is not None:
            return {"url": first.url, "prefer_small_media": True}
    return {"is_disabled": True}


def send(token, chat_id, message, preview_options, max_attempts=4):
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "link_preview_options": preview_options,
    }

    delay = 2
    for attempt in range(1, max_attempts + 1):
        try:
            resp = requests.post(API.format(token=token), json=payload, timeout=TIMEOUT)
        except requests.RequestException as e:
            if attempt == max_attempts:
                raise
            print(f"[telegram] 전송 실패 ({e}), {delay}초 후 재시도")
            time.sleep(delay)
            delay *= 2
            continue

        if resp.ok:
            print("[telegram] 전송 완료")
            return True

        # 요청 과다면 텔레그램이 알려준 시간만큼 기다린다
        if resp.status_code == 429:
            retry_after = resp.json().get("parameters", {}).get("retry_after", delay)
            print(f"[telegram] 429, {retry_after}초 대기")
            time.sleep(retry_after)
            continue

        # 4xx는 설정 문제라 재시도해도 같은 결과다
        if 400 <= resp.status_code < 500:
            raise RuntimeError(f"텔레그램 전송 거부 {resp.status_code}: {resp.text}")

        if attempt == max_attempts:
            raise RuntimeError(f"텔레그램 전송 실패 {resp.status_code}: {resp.text}")

        print(f"[telegram] {resp.status_code}, {delay}초 후 재시도")
        time.sleep(delay)
        delay *= 2

    return False
