"""body_summary.summarize_body 의 LLM 응답 처리 단위 테스트.

이 경로는 실패해도 ""(빈 요약)을 돌려 파이프라인을 막지 않는다. 그래서 유일한 단서가
경고 로그이고, 게이트웨이가 HTTP 200 으로 에러 봉투를 돌려준 경우 그 사유가 로그에
남아야 원인을 되짚을 수 있다.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("httpx")
_bs = pytest.importorskip("processing.enrichment.body_summary")

summarize_body = _bs.summarize_body


def _patch_client(body, captured=None):
    """`body_summary.httpx.Client` 를 context manager mock 으로 패치한다.

    captured 를 주면 post() 에 실린 json/headers 를 기록한다(payload/헤더 조립 검증용).
    """
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value=body)

    def _post(url, headers=None, json=None, **_kwargs):
        if captured is not None:
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
        return resp

    client = MagicMock()
    client.post = MagicMock(side_effect=_post)

    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=client)
    ctx.__exit__ = MagicMock(return_value=False)
    return patch("processing.enrichment.body_summary.httpx.Client", MagicMock(return_value=ctx))


class _Doc:
    """collect_body_text 가 읽는 최소 문서. docling 의존 없이 본문 한 줄만 돌려준다."""

    def iterate_items(self, **_kwargs):
        item = MagicMock()
        item.text = "본문 한 줄"
        yield item, 0


@pytest.mark.unit
def test_error_envelope_logs_server_reason_and_returns_empty(caplog):
    envelope = {"object": "error", "message": "upstream timeout", "code": 504}
    with _patch_client(envelope), caplog.at_level(logging.WARNING):
        result = summarize_body(_Doc(), api_url="http://llm.internal/v1/chat/completions")
    assert result == ""
    # 파이프라인은 막지 않되, 서버가 준 사유는 경고 로그에 남아야 한다.
    assert "upstream timeout" in caplog.text


@pytest.mark.unit
def test_normal_response_still_returns_content():
    body = {"choices": [{"message": {"content": "  요약문  "}}]}
    with _patch_client(body):
        result = summarize_body(_Doc(), api_url="http://llm.internal/v1/chat/completions")
    assert result == "요약문"


# ── model_params 경유 생성 파라미터 · 헤더 (issue/372) ────────────────────────

_OK_BODY = {"choices": [{"message": {"content": "요약"}}]}


@pytest.mark.unit
def test_params_dict_keys_land_in_payload():
    captured = {}
    with _patch_client(_OK_BODY, captured):
        summarize_body(
            _Doc(),
            api_url="http://llm.internal/v1/chat/completions",
            params={"top_p": 0.7, "seed": 42, "repetition_penalty": 1.1},
        )
    payload = captured["json"]
    assert payload["top_p"] == 0.7
    assert payload["seed"] == 42
    assert payload["repetition_penalty"] == 1.1


@pytest.mark.unit
def test_unset_params_keys_absent_from_payload():
    captured = {}
    with _patch_client(_OK_BODY, captured):
        summarize_body(_Doc(), api_url="http://llm.internal/v1/chat/completions")
    payload = captured["json"]
    assert "top_p" not in payload
    assert "seed" not in payload
    assert "repetition_penalty" not in payload
    # 기존 현장 payload 는 model/messages 두 키뿐이었다. thinking 기본값이 "off" 라
    # chat_template_kwargs 가 새로 붙는다 — metadata/custom_fields 와 같아지는 의도된
    # 변화다(issue/372 2단계). 그 외 키는 늘어나지 않는다.
    assert set(payload.keys()) == {"model", "messages", "chat_template_kwargs"}
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}


@pytest.mark.unit
def test_headers_config_carried_and_authorization_not_overwritten():
    captured = {}
    with _patch_client(_OK_BODY, captured):
        summarize_body(
            _Doc(),
            api_url="http://llm.internal/v1/chat/completions",
            api_key="ignored-key",
            headers={"Authorization": "Bearer explicit", "X-Custom": "v"},
        )
    assert captured["headers"]["Authorization"] == "Bearer explicit"
    assert captured["headers"]["X-Custom"] == "v"


# ── 공용 동기 호출 경로(post_chat_completion_sync) 경유 (issue/372 2단계) ────────
# body_summary 가 metadata/custom_fields 와 같은 호출 경로를 쓰는지, 그리고
# strip_reasoning·thinking 이 같은 규칙으로 적용되는지를 고정한다.

@pytest.mark.unit
def test_thinking_on_sends_chat_template_kwargs():
    captured = {}
    with _patch_client(_OK_BODY, captured):
        summarize_body(
            _Doc(),
            api_url="http://llm.internal/v1/chat/completions",
            thinking="on",
        )
    assert captured["json"]["chat_template_kwargs"] == {"enable_thinking": True}


@pytest.mark.unit
def test_thinking_auto_sends_no_chat_template_kwargs():
    captured = {}
    with _patch_client(_OK_BODY, captured):
        summarize_body(
            _Doc(),
            api_url="http://llm.internal/v1/chat/completions",
            thinking="auto",
        )
    assert "chat_template_kwargs" not in captured["json"]


@pytest.mark.unit
def test_reasoning_text_is_stripped_from_summary():
    """thinking 모델이 <think> 블록을 content 에 섞어 보내도 요약문에 남지 않아야 한다."""
    body = {"choices": [{"message": {"content": "<think>내부 추론</think>요약문"}}]}
    with _patch_client(body):
        result = summarize_body(_Doc(), api_url="http://llm.internal/v1/chat/completions")
    assert result == "요약문"
    assert "내부 추론" not in result


@pytest.mark.unit
def test_transient_failure_is_retried_once_and_recovers(monkeypatch):
    """공용 동기 경로로 옮긴 뒤에는 502 같은 일시적 실패를 metadata/custom_fields 처럼
    한 번 다시 불러 복구한다 — 이전에는 body_summary 만 재시도가 없었다."""
    import httpx as httpx_module

    calls = {"n": 0}
    ok_resp = MagicMock()
    ok_resp.raise_for_status = MagicMock()
    ok_resp.json = MagicMock(return_value=_OK_BODY)

    def _post(url, headers=None, json=None, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            request = httpx_module.Request("POST", url)
            response = httpx_module.Response(502, request=request, json={"error": "busy"})
            raise httpx_module.HTTPStatusError("busy", request=request, response=response)
        return ok_resp

    client = MagicMock()
    client.post = MagicMock(side_effect=_post)
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=client)
    ctx.__exit__ = MagicMock(return_value=False)
    from processing.enrichment import llm_response as _lr

    monkeypatch.setattr(_lr.time, "sleep", lambda _seconds: None)

    with patch("processing.enrichment.body_summary.httpx.Client", MagicMock(return_value=ctx)):
        result = summarize_body(_Doc(), api_url="http://llm.internal/v1/chat/completions")

    assert result == "요약"
    assert calls["n"] == 2


@pytest.mark.unit
def test_repeated_failure_still_returns_empty_string():
    """재시도가 생겼어도 fail-open 은 그대로다 — 계속 실패하면 문서 처리를 막지 않고 ""."""
    envelope = {"object": "error", "message": "still down", "code": 500}
    with _patch_client(envelope):
        result = summarize_body(_Doc(), api_url="http://llm.internal/v1/chat/completions")
    assert result == ""
