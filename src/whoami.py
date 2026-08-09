"""chat_id 확인 도우미.

봇 토큰만 있으면 getUpdates 로 채팅방 ID를 찾아 출력한다.
브라우저로 URL을 직접 열 필요 없이 GitHub Actions에서 실행하면 된다.

채팅방이 하나만 잡히면 확인 메시지도 보내준다. 이 봇은 평소에 답장하지
않기 때문에, 설정이 제대로 됐는지 눈으로 확인할 방법이 필요하다.

    python src/whoami.py
    python src/whoami.py --no-test-message
"""

import argparse
import os
import sys

import requests

from telegram import send

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


def parse_args():
    parser = argparse.ArgumentParser(description="텔레그램 chat_id 확인")
    parser.add_argument(
        "--no-test-message",
        action="store_true",
        help="채팅방을 찾아도 확인 메시지를 보내지 않는다",
    )
    return parser.parse_args()


def main():
    args = parse_args()
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

    if args.no_test_message:
        return 0

    # 채팅방이 여럿이면 어디로 보낼지 알 수 없으므로 보내지 않는다
    if len(chats) > 1:
        print("\n채팅방이 여러 개라 확인 메시지는 보내지 않았습니다.")
        return 0

    chat_id = chats[0]["id"]
    send(
        token,
        chat_id,
        "<b>✅ 연결 확인</b>\n"
        "러닝 다이제스트 봇이 이 채팅방으로 메시지를 보낼 수 있습니다.\n\n"
        f"<code>TELEGRAM_CHAT_ID = {chat_id}</code>\n"
        "이 값을 레포 Secrets 에 등록하면 설정이 끝납니다.\n\n"
        "<i>이 봇은 답장하지 않습니다. 정해진 시간에 보내기만 합니다.</i>",
        {"is_disabled": True},
    )
    print("\n텔레그램으로 확인 메시지를 보냈습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
