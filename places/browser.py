"""프로세스에 하나뿐인 Playwright.

Sync API 는 한 스레드에서 두 번 start() 할 수 없어서 구글 지도 저장과
인스타 캡션 수집이 같은 인스턴스를 나눠 쓴다. 프로세스가 끝나면 함께 정리된다.
"""

_pw = None


def playwright():
    global _pw
    if _pw is None:
        from playwright.sync_api import sync_playwright
        _pw = sync_playwright().start()
    return _pw
