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
    """첫 항목의 링크 미리보기(썸네일) 설정.

    문자열("none"/"first")과 딕셔너리 두 형태를 모두 받는다.
    """
    setting = config.get("link_preview", "none")
    if isinstance(setting, str):
        setting = {"mode": setting}

    if setting.get("mode", "none") != "first":
        return {"is_disabled": True}

    # 유튜브 링크는 썸네일이 확실히 잡히는 반면, Google 뉴스 링크는 리다이렉트라
    # 미리보기가 비는 일이 잦다. 그래서 메시지 순서와 무관하게 영상을 먼저 쓴다.
    if setting.get("prefer", "video") == "video":
        first = next(iter(videos or articles or []), None)
    else:
        first = next(iter(articles or videos or []), None)
    if first is None:
        return {"is_disabled": True}

    options = {"url": first.url}
    if setting.get("large", True):
        options["prefer_large_media"] = True
    else:
        options["prefer_small_media"] = True
    if setting.get("above_text", True):
        options["show_above_text"] = True
    return options


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
