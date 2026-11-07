"""로그인된 인스타그램 브라우저로 게시물 캡션과 슬라이드 이미지를 읽는다.

로그인 안 된 브라우저에는 카드뉴스 슬라이드가 안 보여서(표지 한 장만) 가게 이름이
이미지에만 있는 게시물은 장소를 못 찾는다. 봇 전용 크롬 프로필에 사용자가 직접
인스타 로그인을 해 두면(`python -m places iglogin`) 그 세션으로 읽기만 한다.
비밀번호는 저장하지 않는다. 자동화 탐지로 계정이 제한될 수 있어 부계정을 권장한다.
"""

from pathlib import Path

LOGIN_WAIT_SECONDS = 300
NAV_TIMEOUT = 30_000
MAX_SLIDES = 12
# 게시물 본문(article/main) 안의 이미지. 프로필 사진·댓글 아바타는 작아서 크기로 거른다
SLIDE_IMG = "main article img, main div[role=presentation] img"
JS_SOURCES = """els => els
  .filter(e => e.naturalWidth >= 400 && !/프로필 사진|profile picture/i.test(e.alt || ''))
  .map(e => e.currentSrc || e.src)"""
NEXT_BUTTON = 'main button[aria-label="다음"], main button[aria-label="Next"]'


class InstagramSession:
    def __init__(self, config, data_dir):
        settings = config.get("instagram") or {}
        self.headless = bool(settings.get("headless", True))
        self.channel = (config.get("gmaps") or {}).get("browser_channel") or None
        self.profile = Path(data_dir) / "ig-profile"
        self._ctx = None

    @property
    def configured(self):
        """로그인을 한 번이라도 했는지 (프로필 폴더가 있는지)."""
        return self.profile.exists()

    def _open(self, headless):
        from .browser import playwright
        return playwright().chromium.launch_persistent_context(
            user_data_dir=str(self.profile),
            channel=self.channel,
            headless=headless,
            locale="ko-KR",
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 1000},
        )

    @staticmethod
    def _logged_in(ctx):
        return any(c["name"] == "sessionid" and c["value"]
                   for c in ctx.cookies("https://www.instagram.com"))

    def login(self):
        """창을 띄워 사용자가 직접 로그인하게 하고, 로그인되면 스스로 닫는다."""
        ctx = self._open(headless=False)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto("https://www.instagram.com/accounts/login/", timeout=60_000)
            print(f"열린 크롬 창에서 인스타그램에 로그인하세요 (최대 {LOGIN_WAIT_SECONDS // 60}분 기다립니다).")
            for _ in range(LOGIN_WAIT_SECONDS):
                if self._logged_in(ctx):
                    page.wait_for_timeout(3000)  # 로그인 후 '정보 저장' 화면 등이 쿠키를 마저 쓰게
                    return True
                page.wait_for_timeout(1000)
            return False
        finally:
            ctx.close()

    # --- 읽기 ---
    def fetch(self, shortcode, url):
        """로그인 세션으로 게시물을 열어 Post(caption, images) 를 돌려준다. 실패하면 None."""
        from .instagram import Post, parse_og
        ctx = None
        try:
            ctx = self._open(headless=self.headless)
            if not self._logged_in(ctx):
                print("[instagram] 인스타 로그인이 풀렸습니다. python -m places iglogin 으로 다시 로그인하세요.")
                return None
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(f"https://www.instagram.com/p/{shortcode}/", wait_until="domcontentloaded",
                      timeout=NAV_TIMEOUT)
            page.wait_for_timeout(3000)
            caption, username = parse_og(page.content())
            srcs = self._slide_sources(page)
            images = []
            for src in srcs[:MAX_SLIDES]:
                r = ctx.request.get(src, timeout=NAV_TIMEOUT)
                if r.ok:
                    images.append(r.body())
            post = Post(shortcode=shortcode, url=url, caption=caption, username=username,
                        source="session", images=images)
            print(f"[instagram] {shortcode} 로그인 세션: 캡션 {len(caption)}자, 슬라이드 {len(images)}장")
            return post if (caption or images) else None
        except Exception as e:  # noqa: BLE001 — 실패하면 로그인 없는 경로로 넘어간다
            print(f"[instagram] {shortcode} 로그인 세션 수집 실패: {e}")
            return None
        finally:
            if ctx:
                ctx.close()

    @staticmethod
    def _slide_sources(page):
        """'다음' 버튼을 눌러 가며 게시물 본문의 큰 이미지 주소를 순서대로 모은다."""
        seen = []
        for _ in range(MAX_SLIDES + 1):
            for src in page.eval_on_selector_all(SLIDE_IMG, JS_SOURCES):
                if src not in seen:
                    seen.append(src)
            nxt = page.locator(NEXT_BUTTON).first
            if not nxt.count() or not nxt.is_visible():
                break
            nxt.click()
            page.wait_for_timeout(700)
        return seen
