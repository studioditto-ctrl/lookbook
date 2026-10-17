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
MENU_SCROLLS = 6
# 이미 저장한 장소는 '저장됨'/'Saved' 로 바뀐다. 누르면 같은 목록 메뉴가 열린다
SAVE_BUTTON = re.compile(r"^(저장됨|저장|Saved|Save)(?!\w)")
NO_SAVE = "저장 버튼 없음"  # 공유숙박 등 구글 지도에 저장 기능이 없는 장소
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
        self.created = set()  # 이 실행에서 만든 목록 (메뉴에 늦게 떠도 다시 만들지 않게)
        self.last = {}

    # --- 수명 ---
    def start(self):
        if self.page and not self.page.is_closed():
            return
        if self.page:
            # 저장할 때 뜨는 크롬 창을 닫으면 이후 저장이 모두 실패했다 (2026-10-17). 다시 연다
            print("[gmaps] 크롬 창이 닫혀 있어 다시 엽니다")
            try:
                self._ctx.close()
            except Exception:  # noqa: BLE001
                pass
            self._ctx = self.page = None
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
        """(성공 여부, 사유). list_name 이 함수면 연 장소의 정보(place_info)로 목록 이름을 정한다.
        연 장소의 정보와 실제로 쓴 목록 이름은 self.last 에 남는다."""
        self.last = {}
        for attempt in (1, 2):
            try:
                self.start()
                self._open_place(query)
                ok, reason = self.save_current(list_name)
                if reason == NO_SAVE:
                    print(f"[gmaps] '{query}' 에는 저장 버튼이 없습니다 (공유숙박 등)")
                return ok, reason
            except Exception as e:  # noqa: BLE001 — 화면 변경 등 무엇이든 링크 안내로 대체
                if attempt == 1 and "has been closed" in str(e):
                    continue  # 다음 start() 가 닫힌 창을 보고 다시 연다
                return self._fail(query, e)

    def _fail(self, query, e):
        shot = self.data_dir / f"gmaps_fail_{int(time.time())}.png"
        try:
            self.page.screenshot(path=str(shot))
        except Exception:  # noqa: BLE001 — 창이 닫혔으면 스크린샷도 없다
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

    def place_info(self):
        """지금 열린 장소 패널의 이름·주소·분류·고유 ID·링크."""
        page = self.page

        def first_text(selector, attr=None):
            loc = page.locator(selector).first
            if not loc.count():
                return ""
            return (loc.get_attribute(attr) if attr else loc.inner_text()) or ""

        address = re.sub(r"^주소:\s*", "", first_text('button[data-item-id="address"]', "aria-label")).strip()
        m = re.search(r"!1s(0x[0-9a-f]+:0x[0-9a-f]+)", page.url)
        pid = m.group(1) if m else ""
        return {
            "name": first_text("h1.DUwDvf").strip(),
            "address": address,
            "category": first_text("button.DkEaL").strip(),
            "id": pid,
            # cid 링크는 장소 이름·검색어가 바뀌어도 같은 곳을 연다
            "url": f"https://maps.google.com/?cid={int(pid.split(':')[1], 16)}" if pid else "",
        }

    def save_current(self, list_name):
        """열려 있는 장소를 목록에 저장. (성공 여부, 사유)."""
        info = self.place_info()
        name = list_name(info) if callable(list_name) else list_name
        self.last = dict(info, list=name)
        if not self._click_save():
            return False, NO_SAVE
        return self._choose_list(name)

    def _click_save(self):
        page = self.page
        # 장소 패널 안에서만 찾는다. 왼쪽 메뉴의 '저장됨' 탭도 이름이 같다
        panel = page.locator('div[role="main"]', has=page.locator("h1.DUwDvf")).last
        button = panel.get_by_role("button", name=SAVE_BUTTON)
        if button.count() == 0:
            button = panel.locator('button[data-value="저장"], button[data-value="Save"]')
        if button.count() == 0:
            return False
        button.first.click()
        page.wait_for_timeout(1200)
        return True

    def _find_list(self, list_name):
        """저장 메뉴에서 이름이 정확히 같은 목록 ('📍제주' 가 '📍제주 제주시' 에 걸리지 않게).
        목록이 많으면 메뉴를 내려 가며 찾는다."""
        page = self.page
        # 보이는 항목만. 지도 레이어 메뉴(대중교통·지형 …)도 menuitem 이라 숨은 채로 섞여 있다
        items = page.locator('[role^="menuitem"]').locator("visible=true")
        items.first.wait_for(timeout=UI_TIMEOUT)
        target = items.filter(has=page.get_by_text(list_name, exact=True))
        for _ in range(MENU_SCROLLS):
            if target.count() > 0:
                break
            items.last.scroll_into_view_if_needed()
            page.wait_for_timeout(400)
        return target

    def _choose_list(self, list_name, retry=True):
        page = self.page
        target = self._find_list(list_name)
        if target.count() == 0 and list_name in self.created:
            # 방금 만든 목록은 메뉴에 바로 안 뜬다. 같은 이름 목록을 또 만들지 않게 새로고침 후 다시 본다
            # (2026-10-17 '📍경남' 이 세 개 생긴 원인)
            if not retry:
                raise RuntimeError(f"방금 만든 목록 '{list_name}' 이 저장 메뉴에 보이지 않습니다")
            page.keyboard.press("Escape")
            page.reload(timeout=NAV_TIMEOUT)
            page.locator("h1.DUwDvf").first.wait_for(timeout=UI_TIMEOUT)
            page.wait_for_timeout(1500)
            self._click_save()
            return self._choose_list(list_name, retry=False)
        if target.count() > 0:
            if (target.first.get_attribute("aria-checked") or "") == "true":
                page.keyboard.press("Escape")
                return True, "이미 저장됨"
            target.first.click()
            page.wait_for_timeout(1500)
            return True, "저장"

        # '새 목록' 은 메뉴 항목이 아니라 메뉴 아래쪽 버튼이다 (2026-10 화면)
        page.get_by_role("button", name=NEW_LIST).first.click()
        # 대화상자가 뜨면 이름 칸에 '제목 없는 목록' 이 선택된 채로 포커스가 가 있다
        box = page.locator('input[type="text"]:focus')
        box.wait_for(timeout=UI_TIMEOUT)
        box.fill(list_name)
        page.get_by_role("button", name=CREATE).first.click()
        self.created.add(list_name)
        page.wait_for_timeout(2000)
        return True, "새 목록에 저장"
