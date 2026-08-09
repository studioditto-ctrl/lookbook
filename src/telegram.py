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


def _shorten(title):
    if len(title) <= TITLE_MAX:
        return title
    return title[: TITLE_MAX - 1].rstrip() + "…"


def _section(heading, items, start_index):
    lines = [f"{heading}"]
    for offset, item in enumerate(items):
        number = start_index + offset
        lines.append(
            f'{number}. <a href="{_esc(item.url)}">{_esc(_shorten(item.title))}</a>'
        )
        lines.append(f"   <i>{_esc(item.source)}</i>")
    return lines


def build_message(articles, videos, config, slot):
    slot_config = (config.get("slots") or {}).get(slot) or {}
    tz = ZoneInfo(config.get("timezone", "Asia/Seoul"))
    now = datetime.now(tz)

    title = slot_config.get("title", "러닝 브리핑")
    header = [
        f"<b>{_esc(title)}</b>",
        f"<i>{now.month}월 {now.day}일 ({WEEKDAYS[now.weekday()]})</i>",
    ]

    body = []
    if articles:
        body.append("")
        body += _section("📰 <b>읽을거리</b>", articles, 1)
    if videos:
        body.append("")
        body += _section("🎬 <b>영상</b>", videos, len(articles) + 1)

    lines = header + body
    message = "\n".join(lines)

    # 상한을 넘으면 뒤에서부터 항목을 덜어낸다 (한 항목은 2줄)
    while len(message) > MAX_LEN and len(lines) > len(header):
        lines = lines[:-2]
        message = "\n".join(lines)

    return message


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
