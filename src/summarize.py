"""Claude API로 항목 제목·요약을 한국어로 변환.

선택한 항목 전체를 한 번의 요청으로 처리한다. 항목당 호출하면 지연과
비용이 항목 수만큼 늘어나는데, 하루 두 번 보내는 다이제스트에는 그럴
이유가 없다.

실패하면 예외를 올리지 않고 원문 제목을 그대로 쓴다. 요약이 없다고
다이제스트를 통째로 거를 이유는 없다.
"""

import json
import os

MODEL = "claude-opus-5"
MAX_TOKENS = 16000
SNIPPET_MAX = 600  # 항목당 모델에 넘길 본문 발췌 길이

SYSTEM = """당신은 러닝 뉴스 다이제스트의 번역·요약을 담당합니다.

각 항목에 대해:
- title_ko: 제목을 자연스러운 한국어로. 이미 한국어면 그대로 두되 어색한
  부분만 다듬습니다. 대회명·브랜드명 같은 고유명사는 원문을 유지합니다.
- summary_ko: 한국어 1~2문장 요약. 독자가 링크를 열지 말지 판단할 수 있게
  핵심만 씁니다.

규칙:
- 주어진 제목과 발췌에 있는 내용만 씁니다. 기록, 날짜, 수치, 인용을
  지어내지 마십시오.
- 발췌가 비어 있거나 부실하면 제목에서 확인되는 사실만으로 짧게 씁니다.
  모르는 것을 추측하지 말고, 그럴 때는 한 문장으로 끝냅니다.
- "이 기사는", "본문에 따르면" 같은 군더더기 없이 내용부터 씁니다.
- 모든 입력 항목에 대해 같은 index를 붙여 하나씩 반환합니다."""

SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "title_ko": {"type": "string"},
                    "summary_ko": {"type": "string"},
                },
                "required": ["index", "title_ko", "summary_ko"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


def _payload(items):
    return [
        {
            "index": n,
            "title": item.title,
            "source": item.source,
            "kind": "영상" if item.kind == "video" else "기사",
            "snippet": (item.summary or "")[:SNIPPET_MAX],
        }
        for n, item in enumerate(items)
    ]


def _apply(items, parsed):
    """모델 응답을 항목에 반영. 빠진 항목은 원문 제목을 유지한다."""
    applied = 0
    for entry in parsed.get("items") or []:
        index = entry.get("index")
        if not isinstance(index, int) or not 0 <= index < len(items):
            continue
        title = (entry.get("title_ko") or "").strip()
        summary = (entry.get("summary_ko") or "").strip()
        if title:
            items[index].title = title
        if summary:
            items[index].summary_ko = summary
        applied += 1
    return applied


def _request(client, items, effort):
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM,
        output_config={
            "effort": effort,
            "format": {"type": "json_schema", "schema": SCHEMA},
        },
        messages=[
            {
                "role": "user",
                "content": json.dumps(_payload(items), ensure_ascii=False, indent=2),
            }
        ],
    )

    # 거부는 예외가 아니라 정상 응답으로 온다. content 를 읽기 전에 확인한다.
    if response.stop_reason == "refusal":
        raise RuntimeError("모델이 요청을 거부했습니다")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("응답이 max_tokens 에서 잘렸습니다")

    text = next((b.text for b in response.content if b.type == "text"), None)
    if not text:
        raise RuntimeError("응답에 텍스트 블록이 없습니다")
    return json.loads(text)


def summarize(items, config):
    """items 를 제자리에서 갱신한다. 성공 여부를 bool 로 반환."""
    settings = config.get("summary") or {}
    if not settings.get("enabled", True):
        return False
    if not items:
        return False

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("[summarize] ANTHROPIC_API_KEY 가 없어 요약을 건너뜁니다.")
        return False

    try:
        import anthropic
    except ImportError:
        print("[summarize] anthropic 패키지가 없어 요약을 건너뜁니다.")
        return False

    client = anthropic.Anthropic()
    effort = settings.get("effort", "low")

    try:
        parsed = _request(client, items, effort)
    except anthropic.RateLimitError as e:
        print(f"[summarize] 요청 한도 초과, 요약 없이 진행합니다: {e}")
        return False
    except anthropic.APIConnectionError as e:
        print(f"[summarize] API 연결 실패, 요약 없이 진행합니다: {e}")
        return False
    except anthropic.APIStatusError as e:
        print(f"[summarize] API 오류 {e.status_code}, 요약 없이 진행합니다: {e.message}")
        return False
    except (RuntimeError, json.JSONDecodeError, KeyError, TypeError) as e:
        print(f"[summarize] 응답 처리 실패, 요약 없이 진행합니다: {e}")
        return False

    applied = _apply(items, parsed)
    print(f"[summarize] {applied}/{len(items)}건 한국어 변환 완료")
    return applied > 0
