"""옵시디언 노트 쓰기.

vault 는 구글 드라이브 데스크톱 앱이 동기화하는 로컬 폴더라 파일만 쓰면 된다.
  {folder}/{시도}/{동네}/{장소명}.md   장소 노트 (frontmatter 는 매번 갱신, 본문의 메모는 보존)
  {folder}/{index_note}.md             지역별 전체 목록 (매번 다시 생성)
"""

import re
from datetime import date
from pathlib import Path

BAD_CHARS = re.compile(r'[\\/:*?"<>|#^\[\]]')
MEMO_MARK = "## 메모"


def _safe(name):
    return BAD_CHARS.sub(" ", name).strip().rstrip(".") or "이름없음"


def _yaml_str(value):
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _root(config):
    settings = config.get("obsidian") or {}
    return Path(settings["vault_path"]) / settings.get("folder", "Places")


def note_path(entry, config):
    return (_root(config) / _safe(entry["sido"] or "기타") / _safe(entry["area"] or "기타")
            / f"{_safe(entry['name'])}.md")


def render_frontmatter(entry, list_name):
    lines = ["---", f"name: {_yaml_str(entry['name'])}",
             f"category: {_yaml_str(entry['category'])}",
             f"region: [{_yaml_str(entry['sido'])}, {_yaml_str(entry['area'])}]",
             f"address: {_yaml_str(entry.get('address', ''))}"]
    if entry.get("lat") is not None:
        lines.append(f"location: [{entry['lat']}, {entry['lng']}]")
    lines += [f"google_maps: {_yaml_str(_gmaps_url(entry))}",
              f"gmaps_list: {_yaml_str(list_name)}",
              f"gmaps_saved: {'true' if entry.get('gmaps_saved') else 'false'}"]
    if entry.get("kakao_url"):
        lines.append(f"kakao_map: {_yaml_str(entry['kakao_url'])}")
    lines.append("sources:")
    lines += [f"  - {_yaml_str(s)}" for s in entry["sources"]]
    tags = ["place", entry["category"].replace("/", "-"), entry["area"] or entry["sido"]]
    lines.append("tags: [" + ", ".join(_yaml_str(t.replace(" ", "_")) for t in tags if t) + "]")
    lines.append("---")
    return "\n".join(lines)


def _gmaps_url(entry):
    from urllib.parse import quote
    q = " ".join(p for p in (entry["name"], entry.get("address") or entry["area"]) if p)
    return f"https://www.google.com/maps/search/?api=1&query={quote(q)}"


def write_place(entry, list_name, summary, config):
    path = note_path(entry, config)
    path.parent.mkdir(parents=True, exist_ok=True)
    front = render_frontmatter(entry, list_name)
    if path.exists():
        body = _body(path.read_text(encoding="utf-8"))
        new_src = entry["sources"][-1]
        if new_src not in body:
            body = body.rstrip() + f"\n\n- {date.today().isoformat()} [게시물]({new_src})"
            if summary:
                body += f" — {summary}"
            body += "\n"
    else:
        body = (f"\n# {entry['name']}\n\n"
                f"{entry.get('note') or ''}\n\n"
                f"## 출처\n\n- {date.today().isoformat()} [게시물]({entry['sources'][0]})"
                f"{' — ' + summary if summary else ''}\n\n{MEMO_MARK}\n\n")
    path.write_text(front + "\n" + body, encoding="utf-8")
    return path


def _body(text):
    """frontmatter 를 뗀 본문. 사용자가 고친 메모를 보존하려고 쓴다."""
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[end + 5:]
    return text


def remove_place(entry, config):
    path = note_path(entry, config)
    if path.exists():
        path.unlink()


def write_index(entries, config):
    settings = config.get("obsidian") or {}
    root = _root(config)
    root.mkdir(parents=True, exist_ok=True)
    groups = {}
    for e in entries:
        groups.setdefault(e["sido"] or "기타", {}).setdefault(e["area"] or "기타", []).append(e)

    lines = [f"# {settings.get('index_note', '장소 목록')}", "",
             f"총 {len(entries)}곳 · {date.today().isoformat()} 갱신", ""]
    for sido in sorted(groups):
        lines.append(f"## {sido}")
        for area in sorted(groups[sido]):
            items = sorted(groups[sido][area], key=lambda e: (e["category"], e["name"]))
            lines.append(f"### {area} ({len(items)})")
            for e in items:
                rel = note_path(e, config).relative_to(Path(settings["vault_path"]))
                link = rel.with_suffix("").as_posix()
                mark = ("" if e.get("gmaps_saved")
                        else " ℹ️지도 저장 불가" if e.get("gmaps_no_save") else " ⚠️지도 미저장")
                lines.append(f"- [[{link}|{e['name']}]] · {e['category']}{mark}")
            lines.append("")
    path = root / f"{_safe(settings.get('index_note', '장소 목록'))}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
