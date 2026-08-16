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

import settings as settings_module  # noqa: E402
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
    parser.add_argument("--slot", help="발송 슬롯 (config 의 slots 키)")
    parser.add_argument(
        "--due",
        action="store_true",
        help="settings.yaml 의 발송 시각을 보고 지금 보낼 회차를 모두 처리한다",
    )
    parser.add_argument(
        "--config", default="config.yaml", help="다이제스트 설정 파일 (기본 config.yaml)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="전송하지 않고 메시지만 출력한다. 발송 이력도 남기지 않는다.",
    )
    return parser.parse_args()


def run_one(config_name, slot_name, dry_run):
    """다이제스트 한 회차를 처리한다. 종료 코드를 돌려준다."""
    settings = settings_module.load()
    digest = settings_module.find(settings, config_name)

    # config 파일은 선택이다. 페이지에서 만든 주제는 settings.yaml 만으로 돈다.
    config = {}
    if config_name.endswith(".yaml"):
        config_path = REPO / config_name
        if not config_path.exists():
            print(f"설정 파일이 없습니다: {config_name}", file=sys.stderr)
            return 1
        with config_path.open(encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    elif digest is None:
        print(f"'{config_name}' 주제를 settings.yaml 에서 찾지 못했습니다.", file=sys.stderr)
        return 1

    config = settings_module.apply(config, settings, config_name, slot_name)

    slots = config.get("slots") or {}
    if slot_name not in slots:
        print(
            f"'{slot_name}' 슬롯이 {config_name} 에 없습니다. "
            f"사용 가능: {', '.join(slots)}",
            file=sys.stderr,
        )
        return 1

    ns = settings_module.digest_key(digest) if digest else namespace_for(config_name)
    print(f"\n[main] {config_name} / {slot_name} 슬롯" + (f" (state/{ns})" if ns else ""))

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    missing = [
        name
        for name, value in (("TELEGRAM_BOT_TOKEN", token), ("TELEGRAM_CHAT_ID", chat_id))
        if not value
    ]
    if missing and not dry_run:
        for name in missing:
            print(f"::error title={name} 이(가) 비어 있습니다::{HINTS[name]}")
        print("전송은 건너뜁니다. 수집 결과만 아래에 출력합니다.\n")
    preview_only = dry_run or bool(missing)

    channel_cache = state.load_channel_cache(ns)
    items = collect(config, channel_cache)
    state.save_channel_cache(channel_cache, ns)

    seen = state.load_seen(ns)
    articles, videos = select(items, seen, config, slot_name)

    if not articles and not videos:
        print("[main] 보낼 새 항목이 없습니다. 이번 회차는 건너뜁니다.")
        return 1 if missing else 0

    summarize(articles + videos, config)
    message = build_message(articles, videos, config, slot_name)

    if preview_only:
        print("--- 전송하지 않고 미리보기 ---")
        print(message)
        return 1 if missing else 0

    send(token, chat_id, message, _link_preview_options(config, articles, videos))
    state.save_seen(state.mark_sent(seen, articles + videos), ns)
    return 0


def main():
    args = parse_args()

    if args.due:
        settings = settings_module.load()
        schedule_state = state.load_schedule()
        ready = settings_module.due(settings, state=schedule_state)
        if not ready:
            print("[main] 지금 보낼 회차가 없습니다.")
            return 0

        worst = 0
        for config_name, slot_name, key, today in ready:
            code = run_one(config_name, slot_name, args.dry_run)
            worst = max(worst, code)
            if code == 0 and not args.dry_run:
                schedule_state[key] = today
        if not args.dry_run:
            state.save_schedule(schedule_state)
        return worst

    if not args.slot:
        print("--slot 또는 --due 중 하나가 필요합니다.", file=sys.stderr)
        return 1
    return run_one(args.config, args.slot, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
