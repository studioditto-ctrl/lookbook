"""구독 채널 목록을 주제별로 자동 분류한다.

Google Takeout 의 유튜브 구독정보 CSV 에는 채널 이름·ID 밖에 없다 — 분류
정보가 없다. Gemini 에게 채널 이름만 보여주고 카테고리를 붙이게 한다.

채널이 많으면(수백 개) 한 번에 다 보내면 응답이 max_tokens 에서 잘리므로
나눠 보낸다. 뒤 배치에는 앞에서 이미 쓴 카테고리 이름을 알려줘, 같은
주제의 채널을 배치마다 비슷한 이름의 다른 카테고리로 새로 만들지 않게
한다.

어드민 페이지는 정적 GitHub Pages 라 이 스크립트를 직접 부를 수 없다(키가
브라우저에 노출된다). 그래서 recommend.py 와 같은 구조를 쓴다 — 페이지가
입력 파일(state/subs_classify/<slug>-input.json)을 먼저 커밋하고
repository_dispatch 로 이 스크립트를 돌리는 워크플로를 깨우면, 결과가
state/subs_classify/<slug>.json 에 커밋되는 것을 기다렸다가 읽는다.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import gemini_client

REPO = Path(__file__).resolve().parent.parent
CLASSIFY_DIR = REPO / "state" / "subs_classify"

# 한 번에 보낼 채널 수. 너무 크면 이름 목록만으로도 응답이 max_tokens 에서
# 잘린다 — 채널당 카테고리 한 줄씩 다 돌려줘야 하니 배치를 넉넉히 나눈다.
BATCH_SIZE = 80
MAX_TOKENS = 8000

SYSTEM = """당신은 유튜브 구독 채널 목록을 주제별로 정리하는 어시스턴트입니다.

채널 이름 목록이 주어지면, 각 채널이 다루는 내용을 보고 카테고리를
하나씩 붙입니다.

규칙:
- 카테고리는 한국어 낱말이나 짧은 구로, 2~8글자 정도가 적당합니다
  (예: "요리", "운동/피트니스", "IT/개발", "패션", "경제/재테크", "게임",
  "여행", "육아", "뷰티", "음악", "시사/뉴스", "교육").
- 이미 쓴 카테고리가 함께 주어지면, 뜻이 겹치는 채널은 새 이름을 만들지
  말고 그 카테고리를 그대로 재사용하십시오 — 같은 주제인데 배치마다
  "IT", "테크", "기술" 처럼 비슷한 이름이 여럿 생기면 안 됩니다.
- 채널 이름만으로 무엇을 다루는지 확신이 안 서면 "기타"로 두십시오.
  지어내지 마십시오.
- 한 채널은 카테고리 하나만 가집니다.
- 입력에 준 채널 id 를 그대로 돌려주십시오. 순서나 개수를 바꾸지 말고,
  입력에 있던 채널 전부에 카테고리를 붙이십시오."""

SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "category": {"type": "string"},
                },
                "required": ["id", "category"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["items"],
    "additionalProperties": False,
}


def _classify_batch(channels, known_categories):
    lines = [f"{c['id']}: {c['title']}" for c in channels]
    content = "채널 목록:\n" + "\n".join(lines)
    if known_categories:
        content += "\n\n이미 쓴 카테고리: " + ", ".join(sorted(known_categories))

    result = gemini_client.ask_gemini(
        SYSTEM, content, SCHEMA, max_tokens=MAX_TOKENS, log_prefix="classify_subs",
    )
    by_id = {}
    for item in result.get("items") or []:
        cid = str(item.get("id") or "").strip()
        category = str(item.get("category") or "").strip()
        if cid and category:
            by_id[cid] = category
    return by_id


def classify(channels):
    """[{id,title}, ...] -> {id: category}. 배치로 나눠 부른다.

    한 배치가 실패해도(모델 거부·연결 실패 등) 나머지 배치는 계속
    시도한다 — 몇백 개 중 일부만 분류 못한 것으로 남기는 편이, 처음부터
    전부 실패로 던지는 것보다 낫다."""
    known_categories = set()
    result = {}
    for i in range(0, len(channels), BATCH_SIZE):
        batch = channels[i:i + BATCH_SIZE]
        try:
            by_id = _classify_batch(batch, known_categories)
        except Exception as e:
            print(f"[classify_subs] {i}~{i + len(batch)}번째 배치 실패: {e}", file=sys.stderr)
            continue
        result.update(by_id)
        known_categories.update(by_id.values())
    return result


def _write_result(slug, result):
    CLASSIFY_DIR.mkdir(parents=True, exist_ok=True)
    path = CLASSIFY_DIR / f"{slug}.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description="구독 채널 목록을 주제별로 분류한다")
    parser.add_argument("--slug", required=True)
    args = parser.parse_args(argv)

    input_path = CLASSIFY_DIR / f"{args.slug}-input.json"

    # 페이지는 결과 파일이 생기기를 기다린다. 어떤 예외가 나도 반드시
    # 뭔가는 써야 폴링이 영원히 끝나지 않는 사태를 막는다.
    try:
        channels = json.loads(input_path.read_text(encoding="utf-8"))
        if not isinstance(channels, list):
            raise ValueError("입력 파일 형식이 배열이 아닙니다")
        channels = [c for c in channels if c.get("id") and c.get("title")]
        if not channels:
            raise ValueError("분류할 채널이 없습니다")
        categories = classify(channels)
        result = {
            "categories": categories,
            "total": len(channels),
            "classified": len(categories),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        print(f"[classify_subs] 실패: {e}", file=sys.stderr)
        result = {"error": str(e), "generated_at": datetime.now(timezone.utc).isoformat()}

    path = _write_result(args.slug, result)
    print(f"[classify_subs] {path} 에 저장했습니다.")


if __name__ == "__main__":
    main()
