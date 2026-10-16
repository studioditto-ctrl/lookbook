"""텔레그램 롱폴링 루프. 집 PC 에서 상시 실행한다 (webhook 서버 불필요)."""

import os
import time

import requests

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


def run(app):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    allowed = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    print("[bot] 시작. Ctrl+C 로 종료.")
    while True:
        try:
            updates = call(token, "getUpdates", offset=app.store.offset,
                           timeout=POLL_TIMEOUT, allowed_updates=["message"])
        except (requests.RequestException, RuntimeError) as e:
            print(f"[bot] getUpdates 실패, 10초 후 재시도: {e}")
            time.sleep(10)
            continue

        for update in updates:
            app.store.offset = update["update_id"] + 1
            app.store.save()  # 처리 도중 꺼져도 같은 메시지를 두 번 처리하지 않게
            msg = update.get("message") or {}
            chat_id = str((msg.get("chat") or {}).get("id", ""))
            text = msg.get("text") or msg.get("caption") or ""
            if not chat_id or not text:
                continue
            if not allowed:
                send(token, chat_id, f"이 채팅방 ID 는 <code>{chat_id}</code> 입니다.\n"
                     "places.env 의 TELEGRAM_CHAT_ID 에 넣고 봇을 다시 실행하세요.")
                continue
            if chat_id != allowed:
                print(f"[bot] 허용되지 않은 채팅 {chat_id} 무시")
                continue
            try:
                replies = app.handle(text)
            except Exception as e:  # noqa: BLE001 — 한 메시지 실패로 봇이 죽지 않게
                print(f"[bot] 처리 실패: {e!r}")
                replies = [f"⚠️ 처리 중 오류: {type(e).__name__}. 로그를 확인해 주세요."]
            for reply in replies:
                try:
                    send(token, chat_id, reply)
                except (requests.RequestException, RuntimeError) as e:
                    print(f"[bot] 답장 실패: {e}")
