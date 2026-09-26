"""Gemini 호출 공통 로직 — 모델 이름 자가 치유(self-healing) 재시도.

recommend.py 를 만들며 실제로 겪은 문제들이다: gemini-2.5-pro 는 신규
사용자에게 막혔고, 그다음 쓴 gemini-3.1-pro-preview 는 무료 등급 쿼터가
0, gemini-3.1-flash 는 이름 자체가 없어졌고, 그다음 목록에서 고른
gemini-2.5-flash 도 또 막혀 있었다 — 모델 이름을 코드에 고정하면 계정
등급이나 모델이 바뀔 때마다 깨진다. 그래서 404 를 만나면 오류 메시지가
알려주는 대체 모델을 우선 따라가고(가장 믿을 만하다), 없으면 실제
generateContent 가능 목록에서 골라 재시도한다.

classify_subs.py 도 이 로직이 그대로 필요해 recommend.py 에서 떼어냈다.
recommend.py 자체는 이 모듈을 쓰도록 바꾸지 않았다 — 이미 이 로직을
직접 테스트하는 기존 테스트가 많아, 건드리면 그 테스트들을 전부 다시
써야 한다. 새 코드만 여기를 쓴다.
"""

import json
import os
import re
import time

MODEL = os.environ.get("GEMINI_MODEL") or "gemini-3.1-flash"

RATE_LIMIT_RETRIES = 2
RATE_LIMIT_BACKOFF_SECONDS = 20
MAX_MODEL_FALLBACKS = 3

_REFUSAL_FINISH_REASONS = {
    "SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "RECITATION", "SPII",
}
_SUGGESTED_MODEL_RE = re.compile(r"\buse\s+models/([\w.\-]+)", re.I)


def _suggested_replacement_model(message):
    match = _SUGGESTED_MODEL_RE.search(message or "")
    return match.group(1) if match else None


def _generate_content_models(client):
    """실제로 generateContent 를 지원한다고 API 가 알려주는 모델 이름 목록."""
    try:
        names = []
        for m in client.models.list():
            if "generateContent" not in (m.supported_actions or []):
                continue
            name = (m.name or "").removeprefix("models/")
            if name:
                names.append(name)
        return names
    except Exception as e:
        print(f"[gemini] 사용 가능한 모델 목록을 가져오지 못했습니다: {e}")
        return []


def _request(client, system, content, schema, model, max_tokens):
    from google.genai import types

    response = client.models.generate_content(
        model=model,
        contents=content,
        config=types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_json_schema=schema,
            max_output_tokens=max_tokens,
        ),
    )

    candidates = response.candidates or []
    finish = candidates[0].finish_reason if candidates else None
    if finish in _REFUSAL_FINISH_REASONS:
        raise RuntimeError("모델이 요청을 거부했습니다")
    if finish == "MAX_TOKENS":
        raise RuntimeError("응답이 max_tokens 에서 잘렸습니다")

    text = response.text
    if not text:
        raise RuntimeError("응답에 텍스트 블록이 없습니다")
    return json.loads(text)


def ask_gemini(system, content, schema, max_tokens=12000, log_prefix="gemini"):
    """시스템 프롬프트 + 사용자 메시지 + JSON 스키마로 Gemini 를 부르고 파싱된
    JSON 을 돌려준다. 모델이 막히면(404) 자동으로 대체 모델을 찾아 재시도한다."""
    if not os.environ.get("GEMINI_API_KEY"):
        raise RuntimeError("GEMINI_API_KEY 가 없습니다")

    import httpx
    from google import genai
    from google.genai import errors

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    model = MODEL
    model_fallbacks = 0
    attempt = 0
    while True:
        try:
            return _request(client, system, content, schema, model, max_tokens)
        except errors.APIError as e:
            if e.code == 404 and model_fallbacks < MAX_MODEL_FALLBACKS:
                model_fallbacks += 1
                suggested = _suggested_replacement_model(e.message)
                available = [] if suggested else _generate_content_models(client)
                fallback = suggested \
                    or next((n for n in available if "flash" in n.lower()), None) \
                    or (available[0] if available else None)
                if fallback and fallback != model:
                    source = "오류가 알려준 대체 모델" if suggested else "실제 사용 가능한 목록"
                    print(f"[{log_prefix}] '{model}' 모델을 쓸 수 없어(404) {source}인"
                          f" '{fallback}' 로 다시 시도합니다"
                          f"{' (전체 목록: ' + ', '.join(available) + ')' if available else ''}.")
                    model = fallback
                    continue
                available = available or _generate_content_models(client)
                hint = f" 사용 가능한 모델: {', '.join(available)}" if available else ""
                raise RuntimeError(f"API 오류 404: {e.message}.{hint}") from e
            if e.code == 404:
                raise RuntimeError(f"API 오류 404: {e.message} "
                                    f"(대체 모델을 {MAX_MODEL_FALLBACKS}번 시도했지만 계속 막힘)") from e
            if e.code == 429 and attempt < RATE_LIMIT_RETRIES:
                attempt += 1
                print(f"[{log_prefix}] 요청 한도 초과, {RATE_LIMIT_BACKOFF_SECONDS}초 후 재시도"
                      f" ({attempt}/{RATE_LIMIT_RETRIES}): {e.message}")
                time.sleep(RATE_LIMIT_BACKOFF_SECONDS)
                continue
            if e.code == 429:
                raise RuntimeError(f"요청 한도 초과: {e.message}") from e
            raise RuntimeError(f"API 오류 {e.code}: {e.message}") from e
        except httpx.HTTPError as e:
            raise RuntimeError(f"API 연결 실패: {e}") from e
