"""장소 좌표. '내 주변' 추천(nearby.py)에 쓴다.

좌표는 구글 지도가 장소를 열 때 주소창에 넣는 값(`!3d37.5!4d127.0` 또는 `@37.5,127.0`)에서 읽는다.
  - 앞으로 저장하는 장소: gmaps.place_info() 가 저장하면서 읽어 둔다
  - 이미 저장한 장소: python -m places coords  (로그인 없는 헤드리스 크롬으로 하나씩 열어 읽는다.
    봇이 쓰는 크롬 프로필을 건드리지 않아서 봇을 끄지 않아도 된다)

백필한 좌표는 places.json 이 아니라 따로 coords.json 에 둔다. 봇이 메모리에 든 places.json 을 통째로
다시 저장해서, 같은 파일에 쓰면 봇이 덮어쓰기 때문이다. 장소 기록의 lat/lng 가 있으면 그쪽이 우선.
"""

import json
import re
from pathlib import Path

from .store import Store

COORD_PIN = re.compile(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)")
COORD_AT = re.compile(r"@(-?\d+\.\d+),(-?\d+\.\d+)")
# 로그인 없이 연 장소 주소에는 좌표 대신 플러스 코드가 들어 있다: ...!20s8Q58FHF2%2B6JF
PLUS_CODE = re.compile(r"!20s([0-9A-Z]{8})(?:%2B|\+)([0-9A-Z]{2,})")
CID = re.compile(r"cid=(\d+)")
OLC_ALPHABET = "23456789CFGHJMPQRVWX"


def decode_plus_code(code):
    """Open Location Code(플러스 코드) → 칸 중심의 (위도, 경도). 11자리면 약 3m 안쪽으로 맞는다."""
    code = code.replace("+", "").upper()
    lat, lng, size = -90.0, -180.0, 20.0
    pairs = code[:10]
    for i in range(0, len(pairs), 2):
        lat += OLC_ALPHABET.index(pairs[i]) * size
        lng += OLC_ALPHABET.index(pairs[i + 1]) * size
        if i + 2 < len(pairs):
            size /= 20
    lat_size = lng_size = size
    for ch in code[10:]:  # 11번째부터는 5행 × 4열 격자로 더 쪼갠다
        idx = OLC_ALPHABET.index(ch)
        lat_size, lng_size = lat_size / 5, lng_size / 4
        lat += (idx // 4) * lat_size
        lng += (idx % 4) * lng_size
    return lat + lat_size / 2, lng + lng_size / 2


def from_url(url):
    """구글 지도 주소 → (위도, 경도) 또는 None. 장소 핀(!3d!4d) → 플러스 코드 → 화면 중심(@) 순.
    @ 는 지도 화면 중심이라 장소와 다를 수 있어 마지막에만 쓴다."""
    url = url or ""
    m = COORD_PIN.search(url)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = PLUS_CODE.search(url)
    if m:
        try:
            return decode_plus_code(m.group(1) + m.group(2))
        except ValueError:
            pass
    m = COORD_AT.search(url)
    return (float(m.group(1)), float(m.group(2))) if m else None


def path(data_dir):
    return Path(data_dir) / "coords.json"


def load(data_dir):
    p = path(data_dir)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save(data_dir, coords):
    p = path(data_dir)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(coords, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def of(key, entry, sidecar):
    """장소의 (위도, 경도). 기록에 없으면 coords.json 에서."""
    if entry.get("lat") is not None and entry.get("lng") is not None:
        return entry["lat"], entry["lng"]
    ll = sidecar.get(key)
    return (ll[0], ll[1]) if ll else None


def cid_url(entry):
    return next((s for s in entry.get("sources", []) if CID.search(s)), "")


# --- 백필 ---
def _wait(page, tries=32):
    for _ in range(tries):
        ll = from_url(page.url)
        if ll:
            return ll
        page.wait_for_timeout(250)
    return None




def _open(page, entry):
    """장소 하나를 열고 (이름, 좌표) 를 돌려준다. 못 열면 (None, None)."""
    from urllib.parse import quote

    from .namematch import address_match, name_matches
    url = cid_url(entry)
    if url:
        page.goto(url, timeout=30_000)
    else:
        where = entry.get("address") or " ".join(p for p in (entry.get("sido"), entry.get("area")) if p)
        page.goto(f"https://www.google.com/maps/search/{quote(entry['name'] + ' ' + where)}?hl=ko",
                  timeout=30_000)
        page.wait_for_timeout(2000)
        title = page.locator("h1.DUwDvf")
        results = page.locator("a.hfpxzc")
        if title.count() == 0 and results.count() > 0:
            results.first.click()
    page.locator("h1.DUwDvf").first.wait_for(timeout=10_000)
    got = page.locator("h1.DUwDvf").first.inner_text().strip()
    if not url:
        # 검색으로 연 건 이름이나 주소가 같을 때만 믿는다 (엉뚱한 가게 좌표 방지)
        addr = page.locator('button[data-item-id="address"]').first
        page_addr = (addr.get_attribute("aria-label") or "") if addr.count() else ""
        if not (name_matches(entry["name"], got) or address_match(entry.get("address"), page_addr)):
            return got, None
    return got, _wait(page)


def fill(config, log=print, limit=0):
    """좌표 없는 장소를 구글 지도에서 열어 coords.json 에 채운다. 이어서 실행해도 된다."""
    from .browser import playwright
    data_dir = config["data_dir"]
    store = Store(data_dir)
    known = load(data_dir)
    todo = [(k, e) for k, e in store.data["places"].items()
            if e.get("lat") is None and k not in known]
    if limit:
        todo = todo[:limit]
    log(f"좌표 없는 장소 {len(todo)}곳 (이미 알고 있는 {len(known)}곳은 건너뜀)")
    channel = (config.get("gmaps") or {}).get("browser_channel") or None
    browser = playwright().chromium.launch(channel=channel, headless=True)
    page = browser.new_context(locale="ko-KR", viewport={"width": 1280, "height": 900}).new_page()
    page.set_default_timeout(10_000)
    done = skipped = 0
    try:
        for i, (key, entry) in enumerate(todo, 1):
            try:
                got, ll = _open(page, entry)
            except Exception as e:  # noqa: BLE001 — 한 곳 실패로 멈추지 않게
                log(f"  ! {entry['name']}: {str(e)[:80]}")
                skipped += 1
                continue
            if ll:
                known[key] = list(ll)
                done += 1
            else:
                log(f"  ⚠️ {entry['name']} → 좌표를 못 정함 (열린 곳: {got})")
                skipped += 1
            if i % 10 == 0:
                save(data_dir, known)
                log(f"  … {i}/{len(todo)}")
    finally:
        save(data_dir, known)
        browser.close()
    log(f"\n좌표 {done}곳 추가 · 못 정한 곳 {skipped}곳 · 전체 {len(known)}곳")
    return done, skipped
