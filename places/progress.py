"""처리 진행 표시. 텔레그램 '분석 중' 메시지 하나를 몇 초마다 고쳐 써서
단계·경과 시간·저장 진도율을 보여준다 (멈춘 게 아닌지 알 수 있게).

  ⌛ 처리 중 · 23초
  ✅ 게시물 읽기 · 슬라이드 12장
  ⏳ 장소 찾기 · Gemini 보통 10~20초
  ▫️ 지도·노트 저장

show 가 없으면 아무것도 하지 않는다 (테스트·CLI).
"""

import threading
import time

STEPS = ("게시물 읽기", "장소 찾기", "지도·노트 저장")
HINTS = ("슬라이드까지 보통 20초", "Gemini 보통 10~20초", "")
SPIN = ("⏳", "⌛")
PER_PLACE = 8  # 첫 장소를 저장하기 전 1곳당 예상 초 (구글 지도 자동 저장 기준)
BAR = 10


class Progress:
    def __init__(self, show=None, interval=3.0, clock=time.monotonic, steps=STEPS, hints=HINTS):
        self.steps, self.hints = steps, hints  # 마지막 단계에 진도율 막대가 붙는다
        self.show = show            # show(text) — 같은 메시지를 고쳐 쓴다
        self.interval = interval
        self.clock = clock
        self.t0 = clock()
        self.label = ""
        self.step = -1
        self.detail = ""
        self.total = self.done = 0
        self.place_t0 = None
        self._tick = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    # --- 단계 ---
    def start(self, label=""):
        """긴 단계(Gemini 호출 등) 동안에도 경과 시간이 흐르게 주기적으로 다시 그린다."""
        self.label = label
        if self.show and not self._thread:
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
        self.refresh()

    def stage(self, index, detail="", total=0):
        self.step, self.detail = index, detail
        if total:
            self.total, self.done, self.place_t0 = total, 0, self.clock()
        self.refresh()

    def advance(self, name=""):
        self.done += 1
        self.detail = name
        self.refresh()

    def finish(self, text=None):
        self._stop.set()
        if self.show:
            with self._lock:
                try:
                    self.show(text or f"✅ 완료 · {self.elapsed()}초")
                except Exception:  # noqa: BLE001
                    pass

    # --- 그리기 ---
    def elapsed(self):
        return int(self.clock() - self.t0)

    def eta(self):
        left = self.total - self.done
        if left <= 0:
            return 0
        per = (self.clock() - self.place_t0) / self.done if self.done else PER_PLACE
        return int(per * left + 0.5)

    def render(self):
        spin = SPIN[self._tick % len(SPIN)]
        head = f"{spin} 처리 중{' · ' + self.label if self.label else ''} · {self.elapsed()}초"
        lines = [head]
        for i, name in enumerate(self.steps):
            if i < self.step:
                lines.append(f"✅ {name}")
                continue
            if i > self.step:
                lines.append(f"▫️ {name}")
                continue
            line = f"{spin} {name}"
            if i == len(self.steps) - 1 and self.total:
                filled = BAR * self.done // self.total
                line += f"\n{'▓' * filled}{'░' * (BAR - filled)} {self.done}/{self.total}"
                if self.done < self.total:
                    line += f" · 약 {self.eta()}초 남음"
            elif self.hints[i]:
                line += f" · {self.hints[i]}"
            if self.detail:
                line += f" · {self.detail}"
            lines.append(line)
        return "\n".join(lines)

    def refresh(self):
        if not self.show or self._stop.is_set():
            return
        with self._lock:
            self._tick += 1
            try:
                self.show(self.render())
            except Exception:  # noqa: BLE001 — 표시 실패로 처리가 멈추면 안 된다
                pass

    def _loop(self):
        while not self._stop.wait(self.interval):
            self.refresh()
