"""텔레그램 롱폴링 루프. 집 PC 에서 상시 실행한다 (webhook 서버 불필요)."""

import os
import time
import traceback

import requests

from . import instagram, nearby
from .progress import Progress

API = "https://api.telegram.org/bot{token}/{method}"
POLL_TIMEOUT = 50
MAX_LEN = 4096


def call(token, method, **params):
    r = requests.post(API.format(token=token, method=method), json=params,
                      timeout=POLL_TIMEOUT + 10)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description", "telegram error"))
    return data["result"]


def chunks(text, limit=MAX_LEN):
    """줄 단위로 나눈다. 글자 수로 자르면 HTML 태그가 잘려 텔레그램이 400 을 준다
    (32곳 결과 답장이 안 간 원인, 2026-10-17)."""
    out, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:  # 한 줄이 한도보다 길면 어쩔 수 없이 자른다
            out.append(line[:limit])
            line = line[limit:]
        if cur and len(cur) + 1 + len(line) > limit:
            out.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        out.append(cur)
    return out


# 항상 떠 있는 메뉴 키보드 (한 번 보내면 바꾸거나 지울 때까지 남는다). 글자 버튼이라 누르면 그 글자를 메시지로 보낸다.
MAIN_MENU = {"keyboard": [
    [{"text": "☕ 근처 카페"}, {"text": "🍽 근처 맛집"}, {"text": "🏛 근처 장소"}],
    [{"text": "🔄 동기화"}, {"text": "🕘 최근 저장"}],
    [{"text": "📊 통계"}, {"text": "🟢 상태"}, {"text": "🔁 재시도"}],
    [{"text": "↩️ 저장 취소 (/undo)"}],
], "resize_keyboard": True, "is_persistent": True}
# '근처 …' 를 누르면 이 키보드가 대신 떠서 한 번 누르면 GPS 위치를 보낸다. 쓰면 접히고 위 메뉴가 돌아온다.
# (텔레그램은 앱이 사용자 대신 위치를 보낼 수 없어, 위치 버튼은 사람이 한 번 눌러야 한다)
LOCATION_KEYBOARD = {"keyboard": [[{"text": "📍 내 위치 보내기", "request_location": True}]],
                     "resize_keyboard": True, "one_time_keyboard": True}
# 버튼 글자 → 봇이 알아듣는 말
MENU_ACTIONS = {"☕ 근처 카페": "/근처카페", "🍽 근처 맛집": "/근처맛집", "🏛 근처 장소": "/근처장소",
                "🔄 동기화": "/sync", "🕘 최근 저장": "/recent", "📊 통계": "/stats", "🟢 상태": "/status",
                "🔁 재시도": "/retry", "↩️ 저장 취소 (/undo)": "/undo"}
# 입력창 옆 '메뉴' 버튼에 뜨는 명령 목록
COMMANDS = [("near", "내 주변 저장 장소 (예: /near 카페 3)"), ("sync", "구글 지도 앱에서 저장한 장소 가져오기"),
            ("recent", "최근 저장한 장소"), ("stats", "지역별 저장 개수"), ("status", "봇 작동 확인"),
            ("retry", "지도에 저장 못 한 장소 다시 저장"), ("undo", "마지막 저장 취소")]


def send(token, chat_id, text, markup=MAIN_MENU):
    parts = chunks(text)
    for i, part in enumerate(parts):
        extra = {"reply_markup": markup} if markup and i == len(parts) - 1 else {}
        call(token, "sendMessage", chat_id=chat_id, text=part,
             parse_mode="HTML", link_preview_options={"is_disabled": True}, **extra)


def send_one(token, chat_id, text):
    """메시지 하나를 보내고 message_id 를 돌려준다 (나중에 고쳐 쓰려고)."""
    return call(token, "sendMessage", chat_id=chat_id, text=text)["message_id"]


def edit(token, chat_id, message_id, text):
    try:
        call(token, "editMessageText", chat_id=chat_id, message_id=message_id, text=text)
    except RuntimeError as e:
        if "not modified" not in str(e):  # 내용이 같으면 텔레그램이 오류를 준다
            raise


def log(message):
    # requests 오류 메시지에 API 주소(토큰 포함)가 그대로 들어간다
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if token:
        message = message.replace(token, "<토큰>")
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}", flush=True)


def handle_update(app, update, allowed, reply, notified, progress_for=None):
    """메시지 하나 처리. reply(chat_id, text) 로 답한다.
    progress_for(chat_id) 가 Progress 를 주면 '처리 중' 메시지를 계속 고쳐 써서 진행을 보여준다."""
    msg = update.get("message") or {}
    chat_id = str((msg.get("chat") or {}).get("id", ""))
    text = msg.get("text") or msg.get("caption") or ""
    location = msg.get("location")
    if not chat_id or not (text or location):
        return
    # 위치 좌표는 로그에 남기지 않는다
    log(f"[bot] 위치 수신 (채팅 {chat_id})" if location and not text
        else f"[bot] 메시지 수신 (채팅 {chat_id}, {len(text)}자)")
    if not allowed:
        reply(chat_id, f"이 채팅방 ID 는 <code>{chat_id}</code> 입니다.\n"
              "places.env 의 TELEGRAM_CHAT_ID 에 넣고 봇을 다시 실행하세요.")
        return
    if chat_id != allowed:
        log(f"[bot] 허용되지 않은 채팅 {chat_id} 무시 (설정값 {allowed})")
        if chat_id not in notified:  # 설정 실수를 알아챌 수 있게 채팅방마다 한 번만 알린다
            notified.add(chat_id)
            reply(chat_id, f"이 봇은 다른 채팅방 전용입니다. 이 채팅방 ID: <code>{chat_id}</code>")
        return
    text = MENU_ACTIONS.get(text.strip(), text)  # 메뉴 버튼 글자 → 명령
    if location and not text:  # 📎 → 위치, 또는 '내 위치 보내기' 버튼
        try:
            out = app.nearby_reply(chat_id, location["latitude"], location["longitude"])
        except Exception:  # noqa: BLE001
            log("[bot] 주변 추천 실패\n" + traceback.format_exc())
            out = "⚠️ 주변 추천 중 오류가 났어요. places.log 를 확인해 주세요."
        reply(chat_id, out)
        return
    if nearby.wants_nearby(text):  # '근처 카페' · /near 맛집 3 → 위치 보내기 버튼
        reply(chat_id, app.near_request(chat_id, text), markup=LOCATION_KEYBOARD)
        return
    progress = None
    if instagram.find_urls(text) or text.strip().startswith(("/retry", "/sync")):
        progress = progress_for(chat_id) if progress_for else None
        if progress is None:
            reply(chat_id, "🔎 분석 중… (1분 정도 걸릴 수 있어요)")
    try:
        replies = app.handle(text, progress=progress) if progress else app.handle(text)
        if progress:
            progress.finish()
    except Exception:  # noqa: BLE001 — 한 메시지 실패로 봇이 죽지 않게
        log("[bot] 처리 실패\n" + traceback.format_exc())
        if progress:
            progress.finish(f"⚠️ 중단 · {progress.elapsed()}초")
        replies = ["⚠️ 처리 중 오류가 났어요. places.log 를 확인해 주세요."]
    for text_out in replies:
        reply(chat_id, text_out)


def run(app):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    allowed = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    notified = set()

    def reply(chat_id, text, markup=MAIN_MENU):  # 모든 답장에 메뉴를 붙여 항상 떠 있게 한다
        try:
            send(token, chat_id, text, markup)
        except (requests.RequestException, RuntimeError) as e:
            log(f"[bot] 답장 실패: {e}")

    def progress_for(chat_id):
        try:
            message_id = send_one(token, chat_id, "🔎 분석을 시작해요…")
        except (requests.RequestException, RuntimeError, KeyError) as e:
            log(f"[bot] 진행 메시지 실패: {e}")
            return None
        return Progress(show=lambda text: edit(token, chat_id, message_id, text))

    me = call(token, "getMe")
    try:  # 입력창 옆 메뉴 버튼의 명령 목록. 실패해도 봇은 돈다
        call(token, "setMyCommands", commands=[{"command": c, "description": d} for c, d in COMMANDS])
    except (requests.RequestException, RuntimeError) as e:
        log(f"[bot] 메뉴 명령 등록 실패: {e}")
    # 하루 첫 시작(아침 7:20 부팅)에만 알린다. 밖에서도 봇이 켜졌는지 알 수 있게
    today = time.strftime("%Y-%m-%d")
    if allowed and app.store.data.get("last_hello") != today:
        app.store.data["last_hello"] = today
        app.store.save()
        reply(allowed, f"🟢 장소 봇이 켜졌어요 ({time.strftime('%H:%M')}). "
                       "링크를 보내면 바로 처리해요 · /status 작동 확인")
    log(f"[bot] 시작: @{me.get('username')} · 허용 채팅 {allowed or '(미설정)'}. Ctrl+C 로 종료.")
    while True:
        try:
            updates = call(token, "getUpdates", offset=app.store.offset,
                           timeout=POLL_TIMEOUT, allowed_updates=["message"])
        except (requests.RequestException, RuntimeError) as e:
            log(f"[bot] getUpdates 실패, 10초 후 재시도: {e}")
            time.sleep(10)
            continue

        for update in updates:
            app.store.offset = update["update_id"] + 1
            app.store.save()  # 처리 도중 꺼져도 같은 메시지를 두 번 처리하지 않게
            handle_update(app, update, allowed, reply, notified, progress_for)
