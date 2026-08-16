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
from summarize import summarize  # noqa: E402
from telegram import build_message, send, _link_preview_options  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


def namespace_for(config_path):
    """config.yaml -> None(state/), config.lifestyle.yaml -> 'lifestyle'"""
    stem = Path(config_path).stem  # config / config.lifestyle
    return stem.split(".", 1)[1] if "." in stem else None

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
    parser.add_argument("--slot", required=True, help="발송 슬롯 (config 의 slots 키)")
    parser.add_argument(
        "--config", default="config.yaml", help="다이제스트 설정 파일 (기본 config.yaml)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="전송하지 않고 메시지만 출력한다. 발송 이력도 남기지 않는다.",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    config_path = REPO / args.config
    if not config_path.exists():
        print(f"설정 파일이 없습니다: {args.config}", file=sys.stderr)
        return 1
    with config_path.open(encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    slots = config.get("slots") or {}
    if args.slot not in slots:
        print(
            f"'{args.slot}' 슬롯이 {args.config} 에 없습니다. "
            f"사용 가능: {', '.join(slots)}",
            file=sys.stderr,
        )
        return 1

    ns = namespace_for(args.config)
    print(f"[main] {args.config} / {args.slot} 슬롯" + (f" (state/{ns})" if ns else ""))

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

    channel_cache = state.load_channel_cache(ns)
    items = collect(config, channel_cache)
    state.save_channel_cache(channel_cache, ns)

    seen = state.load_seen(ns)
    articles, videos = select(items, seen, config, args.slot)

    if not articles and not videos:
        print("[main] 보낼 새 항목이 없습니다. 이번 회차는 건너뜁니다.")
        return 1 if missing else 0

    # 전송할 항목이 확정된 뒤에 요약한다. 버릴 항목까지 번역할 이유가 없다.
    summarize(articles + videos, config)

    message = build_message(articles, videos, config, args.slot)

    if dry_run:
        print("--- 전송하지 않고 미리보기 ---")
        print(message)
        return 1 if missing else 0

    send(token, chat_id, message, _link_preview_options(config, articles, videos))

    state.save_seen(state.mark_sent(seen, articles + videos), ns)
    return 0


if __name__ == "__main__":
    sys.exit(main())
