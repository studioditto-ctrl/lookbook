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
    if not args.dry_run and not (token and chat_id):
        print(
            "TELEGRAM_BOT_TOKEN 과 TELEGRAM_CHAT_ID 가 필요합니다. "
            "레포 Settings → Secrets and variables → Actions 에 등록하세요.",
            file=sys.stderr,
        )
        return 1

    channel_cache = state.load_channel_cache()
    items = collect(config, channel_cache)
    state.save_channel_cache(channel_cache)

    seen = state.load_seen()
    articles, videos = select(items, seen, config, args.slot)

    if not articles and not videos:
        print("[main] 보낼 새 항목이 없습니다. 이번 회차는 건너뜁니다.")
        return 0

    message = build_message(articles, videos, config, args.slot)

    if args.dry_run:
        print("--- dry run ---")
        print(message)
        return 0

    send(token, chat_id, message, _link_preview_options(config, articles, videos))

    state.save_seen(state.mark_sent(seen, articles + videos))
    return 0


if __name__ == "__main__":
    sys.exit(main())
