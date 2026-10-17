"""실행 진입점.

  python -m places check            설정·키·vault 경로 점검
  python -m places login            구글 로그인 (최초 1회, 창이 뜬다)
  python -m places iglogin          인스타 로그인 (카드뉴스 슬라이드 읽기용, 최초 1회)
  python -m places coords           저장 장소의 좌표를 구글 지도에서 읽어 채움 ('내 주변 추천'용, coords.py)
  python -m places addlist FILE [--apply]장소 목록(미쉐린 등)을 지도 목록·옵시디언에 추가 (addlist.py)
  python -m places reorg [--apply]구글 지도 저장 목록을 시 단위로 재정리 + 옵시디언 노트 (reorg.py)
  python -m places try URL [URL..]  저장 없이 분석만 해 보기 (--save 로 실제 저장)
  python -m places run [--log FILE] 텔레그램 봇 실행 (--log 면 출력을 UTF-8 파일에 덧붙인다)
"""

import argparse
import os
import sys
import traceback
from pathlib import Path

from .config import ENV_PATH, OPTIONAL_KEYS, load_config, load_env, missing_secrets


def _gmaps(config):
    if not (config.get("gmaps") or {}).get("enabled"):
        return None
    from .gmaps import GMaps
    return GMaps(config, config["data_dir"])


def _vault_ok(config):
    raw = (config.get("obsidian") or {}).get("vault_path") or ""
    ok = bool(raw) and Path(raw).is_dir()
    print(f"옵시디언 vault: {raw or '(미설정)'} {'(있음)' if ok else '(없음!)'}")
    return ok


def cmd_check(config):
    ok = True
    print(f"비밀값 파일: {ENV_PATH} {'(있음)' if ENV_PATH.exists() else '(없음)'}")
    for key in missing_secrets(config):
        print(f"  ✗ {key} 없음")
        ok = False
    for key in OPTIONAL_KEYS:
        if not os.environ.get(key):
            print(f"  - {key} 없음 (선택: 국내 장소 위치 검증을 건너뜀)")
    ok &= _vault_ok(config)
    try:
        import playwright  # noqa: F401
        print("Playwright: 설치됨")
    except ImportError:
        print("Playwright: 없음 (pip install playwright)")
        ok = False
    print("점검 통과" if ok else "위 항목을 채운 뒤 다시 실행하세요.")
    return 0 if ok else 1


def cmd_try(config, urls, save):
    from . import instagram
    from .app import App
    gmaps = _gmaps(config) if save else None
    app = App(config, gmaps=gmaps)
    try:
        for code, url in instagram.find_urls(" ".join(urls)):
            post = app.fetch(code, url)
            print(f"\n=== {url}\n수집 경로: {post.source or '실패'} · 계정: {post.username}")
            print(f"캡션:\n{post.caption or '(없음)'}\n")
            if post.caption:
                print(app.process(post, save=save))
    finally:
        if gmaps:
            gmaps.close()
    return 0


def cmd_run(config):
    from . import telegram_bot
    from .app import App
    missing = [k for k in missing_secrets(config) if k != "TELEGRAM_CHAT_ID"]
    if missing:
        print("필요한 값이 없습니다: " + ", ".join(missing))
        return 1
    if not _vault_ok(config):
        return 1
    gmaps = _gmaps(config)
    try:
        telegram_bot.run(App(config, gmaps=gmaps))
    except KeyboardInterrupt:
        print("\n종료합니다.")
    except Exception:  # noqa: BLE001 — 왜 꺼졌는지 로그에 남기고 재시작은 run.ps1 에 맡긴다
        traceback.print_exc()
        return 1
    finally:
        if gmaps:
            gmaps.close()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="places")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    sub.add_parser("login")
    sub.add_parser("iglogin")
    p_reorg = sub.add_parser("reorg")
    p_reorg.add_argument("--apply", action="store_true", help="실제로 새 목록에 추가하고 노트를 쓴다")
    p_reorg.add_argument("--sample", type=int, default=0, help="목록마다 앞 N곳만 (미리보기용)")
    p_reorg.add_argument("--retry", action="store_true", help="--apply 중 실패한 곳만 다시")
    p_coords = sub.add_parser("coords")
    p_coords.add_argument("--limit", type=int, default=0, help="처음 N곳만 (시험용)")
    p_add = sub.add_parser("addlist")
    p_add.add_argument("file", help="장소 목록 JSON (addlist.py 설명 참고)")
    p_add.add_argument("--apply", action="store_true", help="확인을 통과한 장소를 실제로 저장")
    p_try = sub.add_parser("try")
    p_try.add_argument("urls", nargs="+")
    p_try.add_argument("--save", action="store_true")
    p_run = sub.add_parser("run")
    p_run.add_argument("--log", help="출력을 덧붙일 로그 파일")
    args = parser.parse_args(argv)

    if getattr(args, "log", None):
        # PowerShell 5.1 의 >> 리디렉션은 UTF-16·cp949 로 섞여 로그가 깨진다. 직접 쓴다.
        sys.stdout = sys.stderr = open(args.log, "a", encoding="utf-8", buffering=1)
    else:
        # 윈도우 콘솔(cp949)은 이모지를 못 찍어 UnicodeEncodeError 로 죽는다
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
    load_env()
    config = load_config()
    os.makedirs(config["data_dir"], exist_ok=True)
    if args.cmd == "check":
        return cmd_check(config)
    if args.cmd == "login":
        from .gmaps import GMaps
        ok = GMaps(config, config["data_dir"]).login()
        print("로그인 확인됨" if ok else "로그인을 확인하지 못했습니다. 다시 시도하세요.")
        return 0 if ok else 1
    if args.cmd == "coords":
        from . import coords
        coords.fill(config, log=lambda m: print(m, flush=True), limit=args.limit)
        return 0
    if args.cmd == "addlist":
        from . import addlist
        addlist.AddList(config, addlist.load(args.file), log=lambda m: print(m, flush=True)).run(apply=args.apply)
        return 0
    if args.cmd == "reorg":
        from .reorg import Reorg
        r = Reorg(config, log=lambda m: print(m, flush=True))
        r.retry_failed() if args.retry else r.run(apply=args.apply, sample=args.sample)
        return 0
    if args.cmd == "iglogin":
        from .instagram_session import InstagramSession
        ok = InstagramSession(config, config["data_dir"]).login()
        print("인스타 로그인 확인됨" if ok else "로그인을 확인하지 못했습니다. 다시 시도하세요.")
        return 0 if ok else 1
    if args.cmd == "try":
        return cmd_try(config, args.urls, args.save)
    return cmd_run(config)


if __name__ == "__main__":
    sys.exit(main())
