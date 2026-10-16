"""인스타그램 게시물 캡션 수집.

로그인 없이 받을 수 있는 두 경로를 차례로 시도한다.
  1. 임베드 페이지 /p/<code>/embed/captioned/ 의 Caption 블록 (전문)
  2. 게시물 페이지의 og:description 메타태그 (잘릴 수 있음)
둘 다 실패하면 None 을 돌려주고, 봇이 사용자에게 캡션을 붙여넣어 달라고 한다.
유료 수집 API·로그인 세션은 쓰지 않는다 (계정 정지 위험, 비용).
"""

import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser

import requests

TIMEOUT = 20
URL_RE = re.compile(
    r"https?://(?:www\.)?instagram\.com/(?:[\w.]+/)?(p|reel|reels|tv)/([\w-]+)", re.I
)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}


@dataclass
class Post:
    shortcode: str
    url: str
    caption: str = ""
    username: str = ""
    source: str = ""  # embed | og | user


def find_urls(text):
    """메시지에서 인스타 게시물 URL 을 (shortcode, 정규화 URL) 로 뽑는다. 중복 제거."""
    seen, out = set(), []
    for kind, code in URL_RE.findall(text or ""):
        if code in seen:
            continue
        seen.add(code)
        path = "reel" if kind.lower() in ("reel", "reels") else "p"
        out.append((code, f"https://www.instagram.com/{path}/{code}/"))
    return out


def strip_urls(text):
    return re.sub(r"https?://\S+", "", text or "").strip()


class _CaptionParser(HTMLParser):
    """임베드 HTML 의 <div class="Caption"> 텍스트를 줄바꿈 보존해 모은다."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0  # Caption div 안의 div 중첩 깊이
        self.skip = 0   # CaptionUsername·CaptionComments 안
        self.parts = []
        self.username = ""
        self._in_user = False

    def handle_starttag(self, tag, attrs):
        cls = dict(attrs).get("class") or ""
        if self.depth == 0:
            if tag == "div" and "Caption" in cls.split():
                self.depth = 1
            return
        if tag == "div":
            self.depth += 1
            if "CaptionComments" in cls:
                self.skip = self.depth
        if tag == "a" and "CaptionUsername" in cls:
            self._in_user = True
        if tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if self.depth == 0:
            return
        if tag == "a" and self._in_user:
            self._in_user = False
        if tag == "div":
            if self.skip == self.depth:
                self.skip = 0
            self.depth -= 1

    def handle_data(self, data):
        if self.depth == 0 or self.skip:
            return
        if self._in_user:
            self.username += data.strip()
            return
        self.parts.append(data)


def parse_embed(page):
    parser = _CaptionParser()
    parser.feed(page)
    text = "".join(parser.parts)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text, parser.username


OG_RE = re.compile(
    r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']*)["\']', re.I
)


def parse_og(page):
    """og:description 은 '좋아요 n개, 댓글 n개 - user님, 날짜: "캡션"' 꼴이다."""
    m = OG_RE.search(page)
    if not m:
        return "", ""
    content = html.unescape(m.group(1))
    q = re.search(r'[:：]\s*["“](.*)["”]\s*\.?\s*$', content, re.S)
    caption = q.group(1).strip() if q else content.strip()
    u = re.search(r"-\s*([\w.]+)\s*(?:님|on)\b", content)
    return caption, (u.group(1) if u else "")


def _get(session, url):
    r = session.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
    if r.status_code != 200 or "/accounts/login" in r.url:
        return None
    return r.text


def fetch(shortcode, url, session=None):
    """캡션을 받아 Post 로. 아무 경로도 안 되면 caption 이 빈 Post."""
    session = session or requests.Session()
    post = Post(shortcode=shortcode, url=url)
    try:
        page = _get(session, f"https://www.instagram.com/p/{shortcode}/embed/captioned/")
        if page:
            post.caption, post.username = parse_embed(page)
            if post.caption:
                post.source = "embed"
                return post
        page = _get(session, url)
        if page:
            post.caption, post.username = parse_og(page)
            if post.caption:
                post.source = "og"
    except requests.RequestException as e:
        print(f"[instagram] {shortcode} 수집 실패: {e}")
    return post
