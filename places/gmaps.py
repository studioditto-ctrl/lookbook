"""구글 지도 저장 목록 자동 저장 (Playwright, 실험 기능).

구글은 저장 목록 쓰기 API 를 제공하지 않아 로그인된 브라우저로 사람처럼
'저장 → 목록 선택(없으면 새 목록)' 을 누른다. 구글 화면이 바뀌면 깨질 수
있으므로 실패해도 예외를 올리지 않고 (False, 사유) 를 돌려준다. 봇은 그때
원탭 저장 링크로 대신 안내한다.

로그인은 `python -m places login` 으로 한 번만 하면 프로필 폴더에 남는다.
비밀번호는 코드·설정 어디에도 저장하지 않는다.
"""

import re
import time
from pathlib import Path
from urllib.parse import quote

NAV_TIMEOUT = 30_000
UI_TIMEOUT = 10_000
SAVE_BUTTON = re.compile(r"^(저장|Save)\b")
NEW_LIST = re.compile(r"(새 목록|New list)")
CREATE = re.compile(r"^(만들기|Create)$")


class GMaps:
    def __init__(self, config, data_dir):
        settings = config.get("gmaps") or {}
        self.headless = bool(settings.get("headless", False))
        self.channel = settings.get("browser_channel") or None
        self.data_dir = Path(data_dir)
        self.profile = self.data_dir / "gmaps-profile"
        self._pw = self._ctx = self.page = None

    # --- 수명 ---
    def start(self):
        if self.page:
            return
        from .browser import playwright
        self._pw = playwright()
        self._ctx = self._pw.chromium.launch_persistent_context(
            user_data_dir=str(self.profile),
            channel=self.channel,
            headless=self.headless,
            locale="ko-KR",
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 900},
        )
        self.page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        self.page.set_default_timeout(UI_TIMEOUT)

    def close(self):
        try:
            if self._ctx:
                self._ctx.close()
        finally:
            self._pw = self._ctx = self.page = None

    def login(self):
        """창을 띄워 사용자가 직접 로그인하게 한다."""
        self.headless = False
        self.start()
        self.page.goto("https://accounts.google.com/", timeout=NAV_TIMEOUT)
        input("브라우저 창에서 구글 로그인을 마친 뒤 여기서 Enter 를 누르세요... ")
        ok = self.logged_in()
        self.close()
        return ok

    def logged_in(self):
        self.page.goto("https://www.google.com/maps?hl=ko", timeout=NAV_TIMEOUT)
        self.page.wait_for_timeout(2000)
        return self.page.locator('a[aria-label*="Google 계정"], a[aria-label*="Google Account"]').count() > 0

    # --- 저장 ---
    def save(self, query, list_name):
        """(성공 여부, 사유)."""
        try:
            self.start()
            self._open_place(query)
            self._click_save()
            return self._choose_list(list_name)
        except Exception as e:  # noqa: BLE001 — 화면 변경 등 무엇이든 링크 안내로 대체
            shot = self.data_dir / f"gmaps_fail_{int(time.time())}.png"
            try:
                self.page.screenshot(path=str(shot))
            except Exception:  # noqa: BLE001
                shot = None
            print(f"[gmaps] '{query}' 저장 실패: {e} (스크린샷: {shot})")
            return False, f"{type(e).__name__}"

    def _open_place(self, query):
        page = self.page
        page.goto(f"https://www.google.com/maps/search/{quote(query)}?hl=ko", timeout=NAV_TIMEOUT)
        title = page.locator("h1.DUwDvf")
        results = page.locator("a.hfpxzc")
        page.wait_for_timeout(2500)
        if title.count() == 0 and results.count() > 0:
            results.first.click()  # 검색 결과가 여러 개면 첫 번째
        title.first.wait_for(timeout=UI_TIMEOUT)

    def _click_save(self):
        page = self.page
        button = page.get_by_role("button", name=SAVE_BUTTON)
        if button.count() == 0:
            button = page.locator('button[data-value="저장"], button[data-value="Save"]')
        button.first.click()
        page.wait_for_timeout(1200)

    def _choose_list(self, list_name):
        page = self.page
        items = page.locator('[role^="menuitem"]')
        items.first.wait_for(timeout=UI_TIMEOUT)
        target = items.filter(has_text=list_name)
        if target.count() > 0:
            if (target.first.get_attribute("aria-checked") or "") == "true":
                page.keyboard.press("Escape")
                return True, "이미 저장됨"
            target.first.click()
            page.wait_for_timeout(1500)
            return True, "저장"

        items.filter(has_text=NEW_LIST).first.click()
        box = page.locator('div[role="dialog"] input, input[aria-label*="목록"], input[aria-label*="List"]')
        box.first.fill(list_name)
        page.get_by_role("button", name=CREATE).first.click()
        page.wait_for_timeout(2000)
        return True, "새 목록에 저장"
