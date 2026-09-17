"""docling 이 만든 요청을 전처리기가 대신 보내는 경로 단위 테스트.

TOC·메타데이터만 호출이 docling 포크 안에 있어 `params`·`headers`·`timeout` 을 설정할 수
없었고 재시도도 없었다. 이제 docling 은 무엇을 물어볼지만 정하고 전송은
`processing/enrichment/docling_sender.py` 가 맡는다. 여기서 고정하는 것은 그 경계다.

  · 설정의 headers/timeout 이 실제 요청에 실리는가
  · 실패가 docling 이 읽는 형태(LLMApiError + status_code)로 돌아오는가
  · 임의 파라미터가 payload 에 합쳐지는가 (캐시 키가 보도록 docling 쪽에서 합친다)
  · sender 를 안 주면 종전 requests 경로가 그대로 도는가

외부 모델을 부르지 않는다 — 전송 함수를 스텁으로 갈아끼운다.
"""

import httpx
import pytest

from docling.prompts.prompt_manager import LLMApiError, PromptManager

from processing.enrichment import docling_sender as ds
from processing.enrichment.llm_response import LLMResponseError

pytestmark = pytest.mark.unit


class _Conn:
    """_TocConfig / _MetadataConfig 에서 sender 가 읽는 속성만 흉내낸다."""

    def __init__(self, **kw):
        self.api_key = kw.get("api_key", "")
        self.headers = kw.get("headers", {})
        self.timeout = kw.get("timeout")


def _capture(monkeypatch, result=None, raises=None):
    """post_chat_completion_sync 를 가로채 호출 인자를 돌려준다."""
    seen = {}

    def _fake(client, url, *, payload, headers, timeout):
        seen.update(url=url, payload=payload, headers=headers, timeout=timeout)
        if raises is not None:
            raise raises
        return result if result is not None else {"content": "요약본문"}

    monkeypatch.setattr(ds, "post_chat_completion_sync", _fake)
    return seen


# ── 설정이 요청에 실리는가 ────────────────────────────────────────────────────

def test_headers_and_timeout_from_config_reach_the_request(monkeypatch):
    seen = _capture(monkeypatch)
    send = ds.sender_from_config(
        _Conn(api_key="secret", headers={"X-Tenant": "kb"}, timeout=120)
    )

    out = send(url="http://llm/v1/chat/completions", payload={"model": "m"}, headers={})

    assert out == "요약본문"
    assert seen["headers"]["X-Tenant"] == "kb"
    assert seen["headers"]["Authorization"] == "Bearer secret"
    assert seen["timeout"] == 120


def test_config_authorization_is_not_overwritten(monkeypatch):
    seen = _capture(monkeypatch)
    send = ds.sender_from_config(
        _Conn(api_key="secret", headers={"Authorization": "Basic zzz"})
    )
    send(url="u", payload={}, headers={})
    assert seen["headers"]["Authorization"] == "Basic zzz"


def test_timeout_falls_back_to_docling_default(monkeypatch):
    """설정에 timeout 이 없으면 docling 이 쓰던 3600 을 그대로 쓴다(동작 보존)."""
    seen = _capture(monkeypatch)
    ds.sender_from_config(_Conn())(url="u", payload={}, headers={})
    assert seen["timeout"] == ds.DEFAULT_TIMEOUT


def test_reasoning_text_is_stripped(monkeypatch):
    _capture(monkeypatch, result={"content": "<think>고민</think>본문"})
    assert ds.sender_from_config(_Conn())(url="u", payload={}, headers={}) == "본문"


# ── 실패를 docling 이 읽는 형태로 돌려주는가 ──────────────────────────────────
# docling 의 TOC 분할 폴백(_is_token_overflow)과 파서의 에러 봉투가 이 타입과 상태 번호를
# 읽는다. 전처리기 쪽 예외를 그대로 올리면 두 판정이 조용히 꺼진다.

def test_http_status_error_becomes_llm_api_error(monkeypatch):
    response = httpx.Response(413, text="context length exceeded")
    exc = httpx.HTTPStatusError("boom", request=httpx.Request("POST", "http://x"), response=response)
    _capture(monkeypatch, raises=exc)

    with pytest.raises(LLMApiError) as caught:
        ds.sender_from_config(_Conn())(url="u", payload={}, headers={})
    assert caught.value.status_code == 413
    assert "context length exceeded" in caught.value.raw_error_message


def test_envelope_error_keeps_status_code(monkeypatch):
    """게이트웨이가 200 으로 준 에러 봉투도 상태 번호를 잃지 않는다."""
    _capture(monkeypatch, raises=LLMResponseError("형식 아님", status_code=429))
    with pytest.raises(LLMApiError) as caught:
        ds.sender_from_config(_Conn())(url="u", payload={}, headers={})
    assert caught.value.status_code == 429


def test_connect_failure_becomes_llm_api_error_without_status(monkeypatch):
    _capture(monkeypatch, raises=httpx.ConnectError("refused"))
    with pytest.raises(LLMApiError) as caught:
        ds.sender_from_config(_Conn())(url="u", payload={}, headers={})
    assert caught.value.status_code is None


# ── docling 쪽 경계 ───────────────────────────────────────────────────────────

def _prompt_manager(monkeypatch, api_config):
    pm = PromptManager(custom_api_configs={"toc_extraction": api_config})
    monkeypatch.setattr(pm, "get_messages", lambda *a, **k: [{"role": "user", "content": "x"}])
    monkeypatch.setattr(
        pm, "get_model_config",
        lambda *a, **k: {"model": "m", "api_provider": "custom"},
    )
    monkeypatch.setattr(pm, "_run_prompt_precheck", lambda **k: True)
    return pm


def test_sender_replaces_the_builtin_requests_call(monkeypatch):
    """sender 가 있으면 docling 은 스스로 전송하지 않는다."""
    import docling.prompts.prompt_manager as pm_mod

    def _must_not_post(*a, **k):
        raise AssertionError("sender 가 있는데 docling 이 직접 전송했다")

    monkeypatch.setattr(pm_mod.requests, "post", _must_not_post)

    seen = {}

    def _sender(*, url, payload, headers):
        seen.update(url=url, payload=payload)
        return "목차 결과"

    pm = _prompt_manager(monkeypatch, {
        "provider": "custom",
        "api_base_url": "http://llm/v1",
        "model": "m",
        "chat_sender": _sender,
    })
    out = pm.call_ai_model("toc_extraction", "korean_document")

    assert out == "목차 결과"
    assert seen["url"] == "http://llm/v1/chat/completions"
    assert seen["payload"]["messages"] == [{"role": "user", "content": "x"}]


def test_params_are_merged_into_payload_before_the_cache_key(monkeypatch):
    """임의 파라미터는 docling 이 payload 에 합친다 - 캐시 키가 (url, payload) 이기 때문."""
    seen = {}

    def _sender(*, url, payload, headers):
        seen.update(payload=payload)
        return "ok"

    pm = _prompt_manager(monkeypatch, {
        "provider": "custom",
        "api_base_url": "http://llm/v1/chat/completions",
        "model": "m",
        "params": {"presence_penalty": 0.5, "stop": ["END"]},
        "chat_sender": _sender,
    })
    pm.call_ai_model("toc_extraction", "korean_document")

    assert seen["payload"]["presence_penalty"] == 0.5
    assert seen["payload"]["stop"] == ["END"]


def test_without_sender_the_builtin_path_is_used(monkeypatch):
    """sender 를 안 주면 종전 requests 경로가 그대로 돈다(업스트림·단독 사용 보존)."""
    import docling.prompts.prompt_manager as pm_mod

    called = {"n": 0}

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "기본 경로"}}]}

    def _post(*a, **k):
        called["n"] += 1
        return _Resp()

    monkeypatch.setattr(pm_mod.requests, "post", _post)
    pm = _prompt_manager(monkeypatch, {
        "provider": "custom", "api_base_url": "http://llm/v1", "model": "m",
    })
    assert pm.call_ai_model("toc_extraction", "korean_document") == "기본 경로"
    assert called["n"] == 1


# ── toc 설정이 나머지 섹션과 같은 옵션을 받는가 ───────────────────────────────

def test_toc_section_now_reads_params_headers_and_timeout():
    """toc 만 빠져 있던 세 옵션. 호출이 docling 안에 있어 마지막까지 못 받았다."""
    from pathlib import Path

    from processing.enrichment.enrichment_config import EnrichmentConfig

    raw = [{"toc": {
        "enable": True, "url": "http://m/v1",
        "top_p": 0.8, "seed": 7, "params": {"presence_penalty": 0.5},
        "headers": {"X-Tenant": "kb"}, "timeout": 120,
    }}]
    toc = EnrichmentConfig.from_raw(raw, Path("."), parent_cfg={}).toc

    assert toc.params["top_p"] == 0.8
    assert toc.params["seed"] == 7
    assert toc.params["presence_penalty"] == 0.5   # 코드가 모르는 키도 통한다
    assert toc.headers == {"X-Tenant": "kb"}
    assert toc.timeout == 120


def test_toc_without_extra_options_stays_empty():
    """설정을 안 적으면 아무것도 싣지 않는다(기존 현장 요청 무변경)."""
    from pathlib import Path

    from processing.enrichment.enrichment_config import EnrichmentConfig

    toc = EnrichmentConfig.from_raw(
        [{"toc": {"enable": True, "url": "http://m/v1"}}], Path("."), parent_cfg={}
    ).toc
    assert toc.params == {}
    assert toc.headers == {}
    assert toc.timeout is None
