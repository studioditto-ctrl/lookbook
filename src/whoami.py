"""chat_id 확인 도우미.

봇 토큰만 있으면 getUpdates 로 채팅방 ID를 찾아 출력한다.
브라우저로 URL을 직접 열 필요 없이 GitHub Actions에서 실행하면 된다.

    python src/whoami.py
"""

import os
import sys

import requests

API = "https://api.telegram.org/bot{token}/{method}"
TIMEOUT = 20


def chats_from_updates(payload):
    """getUpdates 응답에서 중복 없는 채팅 목록을 뽑는다."""
    chats = {}
    for update in payload.get("result") or []:
        # 메시지, 수정된 메시지, 채널 게시물 등 어디에 들어 있든 찾는다
        for value in update.values():
            if not isinstance(value, dict):
                continue
            chat = value.get("chat")
            if isinstance(chat, dict) and "id" in chat:
                chats[chat["id"]] = chat
    return list(chats.values())


def describe(chat):
    name = (
        chat.get("title")
        or " ".join(filter(None, [chat.get("first_name"), chat.get("last_name")]))
        or chat.get("username")
        or "(이름 없음)"
    )
    return f"{name} · {chat.get('type', '?')}"


def call(token, method):
    resp = requests.get(API.format(token=token, method=method), timeout=TIMEOUT)
    if resp.status_code == 401:
        raise RuntimeError("토큰이 거부되었습니다. TELEGRAM_BOT_TOKEN 값을 확인하세요.")
    resp.raise_for_status()
    return resp.json()


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("TELEGRAM_BOT_TOKEN 이 설정되어 있지 않습니다.", file=sys.stderr)
        return 1

    me = call(token, "getMe").get("result", {})
    print(f"봇 확인: @{me.get('username', '?')} ({me.get('first_name', '?')})\n")

    chats = chats_from_updates(call(token, "getUpdates"))
    if not chats:
        print("최근 대화 기록이 없습니다.")
        print(f"텔레그램에서 @{me.get('username', '?')} 에게 아무 메시지나 보낸 뒤 다시 실행하세요.")
        print("(getUpdates 는 최근 24시간 기록만 보관합니다.)")
        return 1

    print("찾은 채팅방:")
    for chat in chats:
        print(f"  TELEGRAM_CHAT_ID = {chat['id']}    ← {describe(chat)}")
    print("\n이 숫자를 Settings → Secrets and variables → Actions 에")
    print("TELEGRAM_CHAT_ID 로 등록하세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
