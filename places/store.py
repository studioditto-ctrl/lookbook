"""로컬 기록 (%USERPROFILE%\\.lookbook\\places.json).

places: key → 장소 정보 (출처 게시물 누적)
posts:  shortcode → 처리 결과 (같은 게시물 재처리 방지)
log:    처리 순서 — /undo 용
offset: 텔레그램 getUpdates 오프셋
"""

import json
import os
from dataclasses import asdict
from pathlib import Path


class Store:
    def __init__(self, data_dir):
        self.path = Path(data_dir) / "places.json"
        self.data = {"places": {}, "posts": {}, "log": [], "offset": 0}
        if self.path.exists():
            self.data.update(json.loads(self.path.read_text(encoding="utf-8")))

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    # --- 장소 ---
    def upsert(self, place, source_url):
        """새 장소면 True. 기존 장소면 출처만 덧붙이고 False."""
        places = self.data["places"]
        if place.key in places:
            entry = places[place.key]
            if source_url not in entry["sources"]:
                entry["sources"].append(source_url)
            if place.note and not entry.get("note"):
                entry["note"] = place.note
            return False
        entry = asdict(place)
        entry["sources"] = [source_url]
        entry["gmaps_saved"] = False
        places[place.key] = entry
        return True

    def get(self, key):
        return self.data["places"].get(key)

    def mark_gmaps(self, key, saved=True):
        self.data["places"][key]["gmaps_saved"] = saved

    def all_places(self):
        return list(self.data["places"].values())

    # --- 게시물 ---
    def seen(self, shortcode):
        return shortcode in self.data["posts"]

    def record_post(self, shortcode, url, summary, keys, new_keys):
        self.data["posts"][shortcode] = {"url": url, "summary": summary, "places": keys}
        self.data["log"].append({"shortcode": shortcode, "new": new_keys, "all": keys})

    def undo(self):
        """마지막 게시물 처리를 되돌린다.

        (shortcode, 지운 장소 목록, 출처만 뺀 기존 장소 목록) 또는 None.
        """
        if not self.data["log"]:
            return None
        last = self.data["log"].pop()
        post = self.data["posts"].pop(last["shortcode"], {})
        removed, kept = [], []
        for key in last["all"]:
            entry = self.data["places"].get(key)
            if not entry:
                continue
            if key in last["new"]:
                removed.append(self.data["places"].pop(key))
            elif post.get("url") in entry["sources"]:
                entry["sources"].remove(post["url"])
                kept.append(entry)
        return last["shortcode"], removed, kept

    @property
    def offset(self):
        return self.data.get("offset", 0)

    @offset.setter
    def offset(self, value):
        self.data["offset"] = value
