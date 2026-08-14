"""진입점.

    python src/main.py --slot morning
    python src/main.py --slot evening --dry-run
"""

import argparse
import os
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import state  # noqa: E402
from collect import collect  # noqa: E402
from filter import select  # noqa: E402
from telegram import build_message, send, _link_preview_options  # noqa: E402

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"

HINTS = {
    "TELEGRAM_BOT_TOKEN": (
        "BotFather 가 준 토큰을 Settings → Secrets and variables → Actions 의 "
        "Repository secrets 에 등록하세요."
    ),
    "TELEGRAM_CHAT_ID": (
        "'chat_id 확인' 워크플로를 실행해 숫자를 확인한 뒤 Repository secrets 에 "
        "등록하세요."
    ),
}


def parse_args():
    parser = argparse.ArgumentParser(description="러닝 뉴스·영상 텔레그램 다이제스트")
    parser.add_argument(
        "--slot", choices=["morning", "evening"], required=True, help="발송 슬롯"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="전송하지 않고 메시지만 출력한다. 발송 이력도 남기지 않는다.",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    with CONFIG_PATH.open(encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    missing = [
        name
        for name, value in (("TELEGRAM_BOT_TOKEN", token), ("TELEGRAM_CHAT_ID", chat_id))
        if not value
    ]

    # 시크릿이 없다고 그냥 멈추면 수집이 되는지조차 알 수 없다.
    # 전송만 건너뛰고 나머지는 그대로 돌려서 로그에 남긴다.
    if missing and not args.dry_run:
        for name in missing:
            print(f"::error title={name} 이(가) 비어 있습니다::{HINTS[name]}")
        print("전송은 건너뜁니다. 수집 결과만 아래에 출력합니다.\n")

    dry_run = args.dry_run or bool(missing)

    channel_cache = state.load_channel_cache()
    items = collect(config, channel_cache)
    state.save_channel_cache(channel_cache)

    seen = state.load_seen()
    articles, videos = select(items, seen, config, args.slot)

    if not articles and not videos:
        print("[main] 보낼 새 항목이 없습니다. 이번 회차는 건너뜁니다.")
        return 1 if missing else 0

    message = build_message(articles, videos, config, args.slot)

    if dry_run:
        print("--- 전송하지 않고 미리보기 ---")
        print(message)
        return 1 if missing else 0

    send(token, chat_id, message, _link_preview_options(config, articles, videos))

    state.save_seen(state.mark_sent(seen, articles + videos))
    return 0


if __name__ == "__main__":
    sys.exit(main())
