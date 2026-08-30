from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import requests
from fastapi import APIRouter, HTTPException

from ghostroot.server.models import TranslateRequest, TranslateResponse

router = APIRouter(tags=["translation"])


@dataclass(frozen=True)
class TranslationConfig:
    api: str
    base_url: str
    api_key: str
    model: str
    timeout: float


SYSTEM_PROMPT = """Translate the user's text into the requested Chinese locale.

Rules:
- Translate every natural-language phrase and sentence, including evidence clauses.
- Preserve technical identifiers exactly: IP addresses, ports, URLs, paths, shell commands, CVEs, exploit module names, table names, usernames, credentials, filenames, flags, code snippets, quoted strings, and numeric versions.
- Do not summarize, omit, reorder, or add facts.
- Return only the translated text.
"""


@router.post("/translate", response_model=TranslateResponse)
def translate_text(body: TranslateRequest) -> TranslateResponse:
    config = _translation_config()
    user_text = _build_user_text(body)
    if config.api == "chat":
        translated = _translate_with_chat(config, user_text)
    else:
        translated = _translate_with_responses(config, user_text)
    return TranslateResponse(translated_text=translated, provider=config.api)


def _translation_config() -> TranslationConfig:
    base_url = os.getenv("GHOSTROOT_TRANSLATE_BASE_URL") or os.getenv("CODEX_BASE_URL")
    api_key = os.getenv("GHOSTROOT_TRANSLATE_API_KEY") or os.getenv("OPENAI_API_KEY")
    model = os.getenv("GHOSTROOT_TRANSLATE_MODEL") or os.getenv("CODEX_MODEL")
    api = (os.getenv("GHOSTROOT_TRANSLATE_API") or "responses").strip().lower()
    timeout_raw = os.getenv("GHOSTROOT_TRANSLATE_TIMEOUT", "45")

    if not base_url or not api_key or not model:
        raise HTTPException(
            status_code=503,
            detail=(
                "Translation backend is not configured. Set GHOSTROOT_TRANSLATE_BASE_URL, "
                "GHOSTROOT_TRANSLATE_API_KEY, and GHOSTROOT_TRANSLATE_MODEL; or configure "
                "CODEX_BASE_URL, OPENAI_API_KEY, and CODEX_MODEL."
            ),
        )
    if api not in {"responses", "chat"}:
        raise HTTPException(status_code=500, detail="GHOSTROOT_TRANSLATE_API must be 'responses' or 'chat'")

    try:
        timeout = float(timeout_raw)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="GHOSTROOT_TRANSLATE_TIMEOUT must be a number") from exc
    if timeout <= 0:
        raise HTTPException(status_code=500, detail="GHOSTROOT_TRANSLATE_TIMEOUT must be greater than 0")

    return TranslationConfig(
        api=api,
        base_url=base_url.rstrip("/"),
        api_key=api_key,
        model=model,
        timeout=timeout,
    )


def _build_user_text(body: TranslateRequest) -> str:
    target = "Simplified Chinese" if body.target_lang == "zh-CN" else "Traditional Chinese"
    return f"Target locale: {body.target_lang} ({target})\n\nText:\n{body.text}"


def _translate_with_responses(config: TranslationConfig, user_text: str) -> str:
    payload = {
        "model": config.model,
        "input": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_text},
        ],
        "temperature": 0,
    }
    data = _post_json(_responses_url(config.base_url), config, payload)
    return _extract_responses_text(data)


def _translate_with_chat(config: TranslationConfig, user_text: str) -> str:
    payload = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_text},
        ],
        "temperature": 0,
    }
    data = _post_json(_chat_url(config.base_url), config, payload)
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise HTTPException(status_code=502, detail="Translation backend returned no choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise HTTPException(status_code=502, detail="Translation backend returned empty text")
    return content.strip()


def _post_json(url: str, config: TranslationConfig, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=config.timeout,
        )
        response.raise_for_status()
    except requests.Timeout as exc:
        raise HTTPException(status_code=504, detail="Translation backend timed out") from exc
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Translation backend request failed: {exc}") from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Translation backend returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="Translation backend returned an invalid response")
    return data


def _responses_url(base_url: str) -> str:
    if base_url.endswith("/responses"):
        return base_url
    return f"{base_url}/responses"


def _chat_url(base_url: str) -> str:
    if base_url.endswith("/chat/completions"):
        return base_url
    return f"{base_url}/chat/completions"


def _extract_responses_text(data: dict[str, Any]) -> str:
    output_text = data.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    parts: list[str] = []
    output = data.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())

    if parts:
        return "\n".join(parts)
    raise HTTPException(status_code=502, detail="Translation backend returned empty text")
