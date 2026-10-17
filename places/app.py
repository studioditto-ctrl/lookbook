"""메시지 처리: URL → 캡션 → 추출 → 검증 → 저장(구글 지도·옵시디언) → 답장 문구."""

import html

from . import extract as extract_mod
from . import geocode, instagram, obsidian, region
from .store import Store

HELP = (
    "인스타그램 게시물 URL 을 보내면 장소를 찾아 구글 지도 목록과 옵시디언에 저장합니다.\n"
    "/recent 최근 저장 · /stats 지역별 개수 · /undo 마지막 저장 취소"
)
MIN_CAPTION = 20  # URL 과 함께 온 텍스트가 이보다 길면 캡션으로 본다


def esc(text):
    return html.escape(text or "", quote=False)


class App:
    def __init__(self, config, store=None, gmaps=None, fetch=None, extractor=None, resolver=None):
        self.config = config
        self.store = store or Store(config["data_dir"])
        self.gmaps = gmaps
        channel = (config.get("gmaps") or {}).get("browser_channel")
        if fetch is None:
            from .instagram_session import InstagramSession
            session = InstagramSession(config, config["data_dir"])
            fetch = lambda code, url: instagram.fetch_any(code, url, channel, session=session)  # noqa: E731
        self.fetch = fetch
        self.extractor = extractor or (
            lambda caption, images=(): extract_mod.extract(caption, config, images=images))
        self.resolver = resolver or (lambda c: geocode.resolve(c, config))
        self.pending = {}  # 캡션을 못 받은 게시물: shortcode → url

    # --- 진입점 ---
    def handle(self, text):
        """텍스트 메시지 하나 → 답장 문자열 목록."""
        text = (text or "").strip()
        if text.startswith("/"):
            return [self.command(text.split()[0].split("@")[0])]
        urls = instagram.find_urls(text)
        if not urls:
            if self.pending and len(text) >= MIN_CAPTION:
                code, url = self.pending.popitem()
                return [self.process(instagram.Post(code, url, caption=text, source="user"))]
            return [HELP]

        extra = instagram.strip_urls(text)
        replies = []
        for code, url in urls:
            if self.store.seen(code):
                replies.append(self._already(code))
                continue
            if len(urls) == 1 and len(extra) >= MIN_CAPTION:
                post = instagram.Post(code, url, caption=extra, source="user")
            else:
                post = self.fetch(code, url)
            if not post.caption:
                self.pending[code] = url
                replies.append(
                    f"⚠️ 캡션을 가져오지 못했어요: {esc(url)}\n"
                    "인스타 앱에서 캡션을 복사해 그대로 보내주시면 이어서 처리할게요."
                )
                continue
            replies.append(self.process(post))
        return replies

    # --- 처리 ---
    def process(self, post, save=True):
        try:
            result = self.extractor(post.caption, images=post.images)
        except Exception as e:  # noqa: BLE001 — API 오류는 사용자에게 알리고 다음 메시지로
            self.pending[post.shortcode] = post.url
            print(f"[app] 장소 분석 실패: {e}")
            if "credit balance" in str(e):
                return ("⚠️ Claude API 크레딧이 부족해 분석하지 못했어요. "
                        "console.anthropic.com → Plans &amp; Billing 에서 충전한 뒤 URL 을 다시 보내주세요.")
            if "요청 한도 초과" in str(e):
                return "⚠️ Gemini 무료 사용 한도를 넘었어요. 잠시 뒤(또는 내일) URL 을 다시 보내주세요."
            return f"⚠️ 장소 분석에 실패했어요 ({esc(type(e).__name__)}). 잠시 후 URL 을 다시 보내주세요."
        if not result.places:
            return f"🤔 장소를 찾지 못했어요.\n{esc(result.summary)}\n{esc(post.url)}"

        lines, keys, new_keys, unchecked = [], [], [], False
        for i, cand in enumerate(result.places, 1):
            place = self.resolver(cand)
            list_name = region.list_name(place.sido, place.area, self.config)
            if save:
                is_new = self.store.upsert(place, post.url)
                entry = self.store.get(place.key)
                keys.append(place.key)
                if is_new:
                    new_keys.append(place.key)
                gm = self._save_gmaps(place, entry, list_name)
                obsidian.write_place(entry, list_name, result.summary, self.config)
            else:
                gm = "(미리보기)"
            low = place.confidence < geocode.MIN_CONFIDENCE
            flag = " ⚠️위치 확인 필요" if place.checked and low else ""
            unchecked |= cand.domestic and not place.checked
            lines.append(
                f"{i}. <b>{esc(place.name)}</b> · {esc(place.category)} · "
                f"{esc(list_name)} {gm}{flag}\n   {esc(place.address)}"
            )

        if save:
            self.store.record_post(post.shortcode, post.url, result.summary, keys, new_keys)
            obsidian.write_index(self.store.all_places(), self.config)
            self.store.save()
        head = f"📍 {len(result.places)}곳 {'저장' if save else '찾음'}"
        tail = ("\n\n<i>카카오 키가 없어 위치는 캡션 기준이에요 (지점·동네가 다를 수 있음)</i>"
                if unchecked else "")
        return f"{head}\n<i>{esc(result.summary)}</i>\n\n" + "\n".join(lines) + tail

    def _save_gmaps(self, place, entry, list_name):
        if entry.get("gmaps_saved"):
            return "✅지도"
        link = f'<a href="{esc(place.gmaps_url)}">지도에서 저장</a>'
        if not self.gmaps:
            return link
        ok, _ = self.gmaps.save(place.gmaps_query, list_name)
        if ok:
            self.store.mark_gmaps(place.key)
            return "✅지도"
        return f"⚠️자동저장 실패 · {link}"

    # --- 명령 ---
    def command(self, cmd):
        if cmd == "/recent":
            return self._recent()
        if cmd == "/stats":
            return self._stats()
        if cmd == "/undo":
            return self._undo()
        return HELP

    def _already(self, code):
        post = self.store.data["posts"][code]
        names = [self.store.get(k)["name"] for k in post["places"] if self.store.get(k)]
        return "이미 저장한 게시물이에요: " + esc(", ".join(names) or "장소 없음")

    def _recent(self, n=10):
        places = self.store.all_places()[-n:]
        if not places:
            return "아직 저장한 장소가 없어요."
        return "최근 저장\n" + "\n".join(
            f"- {esc(p['name'])} · {esc(p['sido'])} {esc(p['area'])}" for p in reversed(places))

    def _stats(self):
        counts = {}
        for p in self.store.all_places():
            label = f"{p['sido']} {p['area']}".strip() or "기타"
            counts[label] = counts.get(label, 0) + 1
        if not counts:
            return "아직 저장한 장소가 없어요."
        rows = sorted(counts.items(), key=lambda kv: -kv[1])
        return f"총 {sum(counts.values())}곳\n" + "\n".join(f"- {esc(k)}: {v}" for k, v in rows)

    def _undo(self):
        result = self.store.undo()
        if not result:
            return "되돌릴 기록이 없어요."
        code, removed, kept = result
        for entry in removed:
            obsidian.remove_place(entry, self.config)
        for entry in kept:  # frontmatter 의 sources 를 맞춘다 (본문 메모는 그대로)
            list_name = region.list_name(entry["sido"], entry["area"], self.config)
            obsidian.write_place(entry, list_name, "", self.config)
        obsidian.write_index(self.store.all_places(), self.config)
        self.store.save()
        names = ", ".join(e["name"] for e in removed) or "없음"
        saved = [e["name"] for e in removed if e.get("gmaps_saved")]
        tail = f"\n구글 지도 목록에서는 직접 지워주세요: {esc(', '.join(saved))}" if saved else ""
        return f"↩️ 게시물 {esc(code)} 취소 · 삭제한 장소: {esc(names)}{tail}"
