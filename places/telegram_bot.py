"""텔레그램 롱폴링 루프. 집 PC 에서 상시 실행한다 (webhook 서버 불필요)."""

import os
import time
import traceback

import requests

from . import instagram
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


def send(token, chat_id, text):
    for start in range(0, len(text), MAX_LEN):
        call(token, "sendMessage", chat_id=chat_id, text=text[start:start + MAX_LEN],
             parse_mode="HTML", link_preview_options={"is_disabled": True})


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
    if not chat_id or not text:
        return
    log(f"[bot] 메시지 수신 (채팅 {chat_id}, {len(text)}자)")
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
    progress = None
    if instagram.find_urls(text):
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

    def reply(chat_id, text):
        try:
            send(token, chat_id, text)
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
