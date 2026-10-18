"""메시지 처리: URL → 캡션 → 추출 → 검증 → 저장(구글 지도·옵시디언) → 답장 문구."""

import dataclasses
import html
import time

from . import extract as extract_mod
from . import coords as coords_mod
from . import geocode, instagram, nearby, obsidian, region
from .gmaps import MISMATCH, NO_SAVE
from .progress import Progress
from .store import Store

HELP = (
    "인스타그램 게시물 URL 을 보내면 장소를 찾아 구글 지도 목록과 옵시디언에 저장합니다.\n"
    "/recent 최근 저장 · /stats 지역별 개수 · /undo 마지막 저장 취소 · /retry 지도 미저장 다시 저장 · /status 작동 확인\n"
    "/sync 구글 지도 앱에서 직접 저장한 장소를 봇에 가져오기 (내 주변 추천에 반영)\n"
    "📍 근처 장소: 아래 ☕ 근처카페 · 🍽 근처맛집 · 🏛 근처장소 (또는 '/near 맛집 3') 를 누른 뒤 📍 위치 버튼을 누르세요"
)
MIN_CAPTION = 20  # URL 과 함께 온 텍스트가 이보다 길면 캡션으로 본다
NEAR_MEMORY_SECONDS = 600  # '근처 카페' 라고 한 뒤 이 안에 위치를 보내면 카페만 보여 준다


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
        self.near_state = {}  # chat_id → (찾는 말, 개수, 시각)
        self.started = time.time()
        self.last_done = ""  # /status 용: 마지막으로 처리한 게시물

    # --- 진입점 ---
    def handle(self, text, progress=None):
        """텍스트 메시지 하나 → 답장 문자열 목록. progress 로 단계·진도를 알린다."""
        progress = progress or Progress()
        text = (text or "").strip()
        if text.startswith("/"):
            cmd = text.split()[0].split("@")[0]
            if cmd == "/retry":  # 오래 걸려서 진행 표시를 넘긴다
                return [self.retry_gmaps(progress)]
            if cmd == "/sync":  # 목록을 모두 읽느라 오래 걸린다
                return [self.sync_gmaps(" ".join(text.split()[1:]), progress)]
            return [self.command(cmd)]
        urls = instagram.find_urls(text)
        if not urls:
            if self.pending and len(text) >= MIN_CAPTION:
                code, url = self.pending.popitem()
                progress.start()
                progress.stage(1)
                return [self.process(instagram.Post(code, url, caption=text, source="user"),
                                     progress=progress)]
            return [HELP]

        extra = instagram.strip_urls(text)
        replies = []
        for k, (code, url) in enumerate(urls, 1):
            if self.store.seen(code):
                replies.append(self._already(code))
                continue
            progress.start(f"링크 {k}/{len(urls)}" if len(urls) > 1 else "")
            progress.stage(0)
            if len(urls) == 1 and len(extra) >= MIN_CAPTION:
                post = instagram.Post(code, url, caption=extra, source="user")
            else:
                post = self.fetch(code, url)
            progress.stage(1, f"슬라이드 {len(post.images)}장" if post.images else "")
            if not post.caption and not post.images:
                self.pending[code] = url
                replies.append(
                    f"⚠️ 캡션을 가져오지 못했어요: {esc(url)}\n"
                    "인스타 앱에서 캡션을 복사해 그대로 보내주시면 이어서 처리할게요."
                )
                continue
            replies.append(self.process(post, progress=progress))
        return replies

    # --- 처리 ---
    def process(self, post, save=True, progress=None):
        progress = progress or Progress()
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
        progress.stage(2, total=len(result.places))
        for i, cand in enumerate(result.places, 1):
            place = self.resolver(cand)
            list_name = region.list_name(place.sido, place.area, self.config, place.address)
            if save:
                is_new = self.store.upsert(place, post.url)
                entry = self.store.get(place.key)
                keys.append(place.key)
                if is_new:
                    new_keys.append(place.key)
                gm, list_name = self._save_gmaps(place, entry, list_name)
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
            progress.advance(place.name)

        if save:
            self.store.record_post(post.shortcode, post.url, result.summary, keys, new_keys)
            obsidian.write_index(self.store.all_places(), self.config)
            self.store.save()
        self.last_done = f"{time.strftime('%m-%d %H:%M')} · {len(result.places)}곳 · {post.url}"
        head = f"📍 {len(result.places)}곳 {'저장' if save else '찾음'}"
        tail = ("\n\n<i>카카오 키가 없어 위치는 캡션 기준이에요 (지점·동네가 다를 수 있음)</i>"
                if unchecked else "")
        return f"{head}\n<i>{esc(result.summary)}</i>\n\n" + "\n".join(lines) + tail

    def _save_gmaps(self, place, entry, list_name):
        """(답장 표시, 실제 목록 이름). 목록은 구글 지도가 보여주는 주소로 정한다
        (카카오 키가 없으면 우리 쪽엔 시·군을 알 주소가 없다). 주소는 노트에도 남긴다."""
        if entry.get("gmaps_saved"):
            return "✅지도", entry.get("gmaps_list") or list_name
        link = f'<a href="{esc(place.gmaps_url)}">지도에서 저장</a>'
        if not self.gmaps:
            return link, list_name

        def pick(info):
            return region.list_name(place.sido, place.area, self.config,
                                    info.get("address") or place.address)

        ok, reason = self.gmaps.save(place.gmaps_query, pick, expect=place.name, expect_addr=place.address)
        info = getattr(self.gmaps, "last", None) or {}
        if reason == MISMATCH:
            # 열린 곳이 다른 가게다. 이 정보(주소·좌표)를 장소 기록에 넣으면 안 된다
            entry["gmaps_mismatch"] = True
            return (f"⚠️지도에서 같은 가게를 못 찾았어요 (열린 곳: {esc(info.get('name', ''))}) · {link}",
                    list_name)
        # 캡션의 '황새울로342번길 19' 같은 부분 주소는 구글의 전체 주소로 바꾼다
        old = entry.get("address") or ""
        if info.get("address") and (not old or old.replace(" ", "") in info["address"].replace(" ", "")):
            entry["address"] = info["address"]
        list_name = info.get("list") or list_name
        if info.get("lat") is not None and entry.get("lat") is None:
            entry["lat"], entry["lng"] = info["lat"], info["lng"]  # '내 주변' 추천용 좌표
        if ok:
            entry["gmaps_saved"] = True
            entry["gmaps_list"] = list_name
            return "✅지도", list_name
        if reason == NO_SAVE:
            entry["gmaps_no_save"] = True
            return f'ℹ️구글 지도에 저장 기능이 없는 장소 (<a href="{esc(place.gmaps_url)}">지도 보기</a>)', list_name
        return f"⚠️자동저장 실패 · {link}", list_name

    # --- 명령 ---
    def command(self, cmd):
        if cmd == "/recent":
            return self._recent()
        if cmd == "/stats":
            return self._stats()
        if cmd == "/undo":
            return self._undo()
        if cmd == "/retry":
            return self.retry_gmaps()
        if cmd == "/status":
            return self._status()
        return HELP

    # --- 내 주변 추천 ---
    def near_request(self, chat_id, text):
        """'근처 카페' → 무엇을 찾는지 기억해 두고 위치를 보내 달라고 한다."""
        terms, n = nearby.parse_request(text)
        self.near_state[str(chat_id)] = (terms, n, time.time())
        what = " ".join(terms) or "전체"
        return (f"📍 지금 계신 곳 기준으로 가까운 저장 장소를 찾을게요 ({esc(what)} · {n}곳).\n"
                "아래 📍 버튼을 한 번 누르거나, 📎 → 위치로 현재 위치를 보내 주세요.")

    def nearby_reply(self, chat_id, lat, lng):
        """받은 GPS 위치 → 가까운 저장 장소 답장. 위치는 저장하지 않는다."""
        terms, n = [], nearby.DEFAULT_N
        state = self.near_state.pop(str(chat_id), None)
        if state and time.time() - state[2] < NEAR_MEMORY_SECONDS:  # 바로 앞에 '근처 카페' 라고 했으면 그걸로
            terms, n = state[0], state[1]
        places, missing = nearby.candidates(self.store.data["places"], coords_mod.load(self.config["data_dir"]))
        return nearby.render(nearby.nearest(places, lat, lng, terms, n, recent=True), terms, missing, recent=True)

    def _status(self):
        """밖에서도 봇이 도는지 확인용."""
        up = int(time.time() - self.started)
        places = self.store.all_places()
        unsaved = sum(1 for e in places if not (e.get("gmaps_saved") or e.get("gmaps_no_save") or e.get("gmaps_mismatch")))
        mismatch = sum(1 for e in places if e.get("gmaps_mismatch") and not e.get("gmaps_saved"))
        return "\n".join([
            f"🟢 작동 중 · 켜진 지 {up // 3600}시간 {up % 3600 // 60}분 ({time.strftime('%m-%d %H:%M', time.localtime(self.started))})",
            f"저장 장소 {len(places)}곳 · 지도 미저장 {unsaved}곳" + (" → /retry" if unsaved else "")
            + (f" · 같은 가게를 못 찾은 {mismatch}곳(직접 확인)" if mismatch else ""),
            f"마지막 처리: {esc(self.last_done) or '이번 실행에서는 아직 없음'}",
        ])

    def retry_gmaps(self, progress=None):
        """지도에 저장 못 한 장소를 다시 저장한다 (저장 버튼이 없는 장소는 건너뛴다)."""
        if not self.gmaps:
            return "구글 지도 자동 저장이 꺼져 있어요."
        todo = [e for e in self.store.all_places()
                if not (e.get("gmaps_saved") or e.get("gmaps_no_save") or e.get("gmaps_mismatch"))]
        if not todo:
            return "✅ 지도에 저장 안 된 장소가 없어요."
        progress = progress or Progress()
        progress.start("지도 미저장 다시 저장")
        progress.stage(2, total=len(todo))
        fails, nosave = [], []
        names = {f.name for f in dataclasses.fields(geocode.Place)}
        for entry in todo:
            place = geocode.Place(**{k: entry[k] for k in names if k in entry})
            list_name = entry.get("gmaps_list") or region.list_name(
                place.sido, place.area, self.config, place.address)
            gm, list_name = self._save_gmaps(place, entry, list_name)
            if entry.get("gmaps_no_save"):
                nosave.append(entry["name"])
            elif not entry.get("gmaps_saved"):
                fails.append(entry["name"])
            obsidian.write_place(entry, list_name, "", self.config)
            progress.advance(entry["name"])
        obsidian.write_index(self.store.all_places(), self.config)
        self.store.save()
        lines = [f"🔁 지도 다시 저장: {len(todo) - len(fails) - len(nosave)}/{len(todo)}곳 완료"]
        if nosave:
            lines.append("ℹ️ 구글 지도에 저장 기능이 없는 장소: " + esc(", ".join(nosave)))
        if fails:
            lines.append("⚠️ 또 실패: " + esc(", ".join(fails)) + " — /retry 로 다시 시도")
        return "\n".join(lines)

    def sync_gmaps(self, arg="", progress=None):
        """/sync [예|아니오] — 구글 지도 앱에서 직접 저장한 장소를 봇 기록에 가져온다 (sync.py).
        봇의 크롬(self.gmaps)을 쓰고 봇의 메모리 기록(self.store)에 바로 쓴다."""
        from . import sync
        if not self.gmaps:
            return "구글 지도 자동 저장이 꺼져 있어요."
        answer = sync.answer_of(arg)
        if arg.strip() and answer is None:
            return "사용법: /sync · /sync 예 · /sync 아니오"
        progress = progress or Progress()
        progress.steps, progress.hints = sync.STEPS, sync.HINTS  # 진행 메시지 단계 이름을 동기화용으로
        engine = sync.Sync(self.config, self.store, sync.GmapsSource(self.config, self.gmaps),
                           log=print, progress=progress)
        try:
            if answer is None:
                result = engine.run()
            elif answer:
                engine.set_city(True)
                result = engine.apply_pending_city()
                if not (result["city_saved"] or result["city_failed"]):
                    return "👌 앞으로 새 장소는 시 단위 목록에도 넣을게요."
            else:
                engine.set_city(False)
                self.store.save()
                return "👌 시 단위 목록에는 넣지 않을게요. 직접 저장한 목록 이름 그대로 둡니다."
        except Exception as e:  # noqa: BLE001 — 크롬 창 문제 등은 알리고 봇은 계속
            print(f"[app] 동기화 실패: {e}")
            self.store.save()
            return f"⚠️ 동기화 중 오류가 났어요 ({esc(type(e).__name__)}). places.log 를 확인해 주세요."
        return sync.report(result, esc=esc)

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
            list_name = entry.get("gmaps_list") or region.list_name(
                entry["sido"], entry["area"], self.config, entry.get("address", ""))
            obsidian.write_place(entry, list_name, "", self.config)
        obsidian.write_index(self.store.all_places(), self.config)
        self.store.save()
        names = ", ".join(e["name"] for e in removed) or "없음"
        saved = [e["name"] for e in removed if e.get("gmaps_saved")]
        tail = f"\n구글 지도 목록에서는 직접 지워주세요: {esc(', '.join(saved))}" if saved else ""
        return f"↩️ 게시물 {esc(code)} 취소 · 삭제한 장소: {esc(names)}{tail}"
