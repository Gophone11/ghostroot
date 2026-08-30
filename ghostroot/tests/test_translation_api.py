from __future__ import annotations

import pytest
from fastapi import HTTPException

from ghostroot.server.models import TranslateRequest
from ghostroot.server.routers import translation


def test_translate_text_uses_responses_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GHOSTROOT_TRANSLATE_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("GHOSTROOT_TRANSLATE_API_KEY", "secret")
    monkeypatch.setenv("GHOSTROOT_TRANSLATE_MODEL", "translator-model")

    captured: dict[str, object] = {}

    class Response:
        status_code = 200
        text = "{}"

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"output_text": "该记录描述了一项探索结果观察。"}

    def fake_post(url: str, **kwargs: object) -> Response:
        captured["url"] = url
        captured.update(kwargs)
        return Response()

    monkeypatch.setattr(translation.requests, "post", fake_post)

    result = translation.translate_text(
        TranslateRequest(text="This fact records an exploration result observation.", target_lang="zh-CN")
    )

    assert result.translated_text == "该记录描述了一项探索结果观察。"
    assert result.provider == "responses"
    assert captured["url"] == "https://llm.example/v1/responses"
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["Authorization"] == "Bearer secret"
    payload = captured["json"]
    assert isinstance(payload, dict)
    assert payload["model"] == "translator-model"
    assert payload["temperature"] == 0
    assert "Translate the user's text" in payload["input"][0]["content"]


def test_translate_text_requires_config(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "GHOSTROOT_TRANSLATE_BASE_URL",
        "GHOSTROOT_TRANSLATE_API_KEY",
        "GHOSTROOT_TRANSLATE_MODEL",
        "CODEX_BASE_URL",
        "OPENAI_API_KEY",
        "CODEX_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(HTTPException) as exc:
        translation.translate_text(TranslateRequest(text="hello", target_lang="zh-CN"))

    assert exc.value.status_code == 503
