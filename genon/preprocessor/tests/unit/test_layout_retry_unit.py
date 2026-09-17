"""layout(dots-mocr) 호출의 일시적 실패 재시도 단위 테스트.

`layout.genos_layout.retry_count` 는 설정과 고객 문서에 "비정상 VLM 응답 재시도 횟수" 로
노출돼 있으면서도 코드에서 읽히기만 하고 쓰이지 않는 죽은 설정이었다. 되살리되 의미를
좁혔다 — **일시적 전송 실패만** 다시 부른다.

폭주(`finish_reason == "length"`)와 read timeout 을 재시도하지 않는 것이 핵심이다. 같은
프롬프트를 다시 보내면 또 `max_completion_tokens` 까지 태우므로 #278 이 그 재시도를 없애고
layout_only 폴백으로 바꿨다. 이 테스트는 그 결정이 유지되는지를 함께 고정한다.
"""

import httpx
import pytest
import requests

from docling.models.genos_dots_ocr_layout_model import (
    GenosDotsOCRLayoutModel,
    VLMReadTimeout,
)

from processing.common.pipeline_setup import LayoutSettings, apply_layout_settings
from processing.enrichment.llm_response import retry_sync

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """재시도 대기를 없앤다(판정만 본다)."""
    import processing.enrichment.llm_response as lr

    monkeypatch.setattr(lr.time, "sleep", lambda *_a: None)


def _transient():
    response = requests.Response()
    response.status_code = 502
    return requests.exceptions.HTTPError(response=response)


# ── 공용 헬퍼의 N회 일반화 ────────────────────────────────────────────────────

def test_retry_sync_defaults_to_one_retry():
    """인자를 안 주면 종전과 같다 - 기존 호출부(chat/이미지)의 동작이 바뀌면 안 된다."""
    calls = {"n": 0}

    def send():
        calls["n"] += 1
        raise _transient()

    with pytest.raises(requests.exceptions.HTTPError):
        retry_sync(send)
    assert calls["n"] == 2          # 최초 1 + 재시도 1


def test_retry_sync_honours_the_configured_count():
    calls = {"n": 0}

    def send():
        calls["n"] += 1
        raise _transient()

    with pytest.raises(requests.exceptions.HTTPError):
        retry_sync(send, retries=3)
    assert calls["n"] == 4          # 최초 1 + 재시도 3


def test_retry_sync_with_zero_does_not_retry():
    calls = {"n": 0}

    def send():
        calls["n"] += 1
        raise _transient()

    with pytest.raises(requests.exceptions.HTTPError):
        retry_sync(send, retries=0)
    assert calls["n"] == 1


def test_retry_sync_stops_as_soon_as_it_succeeds():
    calls = {"n": 0}

    def send():
        calls["n"] += 1
        if calls["n"] == 1:
            raise _transient()
        return "ok"

    assert retry_sync(send, retries=3) == "ok"
    assert calls["n"] == 2


# ── layout 호출 ───────────────────────────────────────────────────────────────

def _model(retry_count=2, retry_runner=retry_sync):
    """__init__ 은 파이프라인 전체를 요구하므로 우회하고 쓰는 속성만 채운다."""
    model = object.__new__(GenosDotsOCRLayoutModel)
    model.dotocr_endpoint = "http://layout/v1/chat/completions"
    model.api_key = ""
    model.model = "dots-mocr"
    model.max_completion_tokens = 16384
    model.timeout = 1200
    model.temperature = 0.1
    model.top_p = 0.9
    model.repetition_penalty = 1.15
    model.retry_count = retry_count
    model.retry_runner = retry_runner
    return model


def _patch_call(monkeypatch, side_effect):
    import docling.models.genos_dots_ocr_layout_model as mod

    calls = {"n": 0}

    def _fake(**kwargs):
        calls["n"] += 1
        result = side_effect(calls["n"])
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(mod, "call_vlm_server", _fake)
    return calls


def test_transient_failure_is_retried_up_to_retry_count(monkeypatch):
    calls = _patch_call(
        monkeypatch,
        lambda n: ("본문", None, "stop") if n == 3 else _transient(),
    )
    text, _usage, finish = _model(retry_count=2)._call_vlm("prompt", "img")
    assert text == "본문"
    assert calls["n"] == 3          # 최초 1 + 재시도 2


def test_read_timeout_is_not_retried(monkeypatch):
    """폭주·행 상태를 다시 부르면 또 같은 시간을 태운다(#278). 폴백이 받아야 한다."""
    calls = _patch_call(monkeypatch, lambda n: VLMReadTimeout("느림"))
    with pytest.raises(VLMReadTimeout):
        _model(retry_count=2)._call_vlm("prompt", "img")
    assert calls["n"] == 1


def test_permanent_failure_is_not_retried(monkeypatch):
    response = requests.Response()
    response.status_code = 400
    calls = _patch_call(
        monkeypatch, lambda n: requests.exceptions.HTTPError(response=response)
    )
    with pytest.raises(requests.exceptions.HTTPError):
        _model(retry_count=2)._call_vlm("prompt", "img")
    assert calls["n"] == 1


def test_retry_count_zero_keeps_the_single_call(monkeypatch):
    calls = _patch_call(monkeypatch, lambda n: _transient())
    with pytest.raises(requests.exceptions.HTTPError):
        _model(retry_count=0)._call_vlm("prompt", "img")
    assert calls["n"] == 1


def test_without_a_runner_the_call_happens_once(monkeypatch):
    """docling 단독 사용(호스트가 정책을 안 넣음)에서는 종전대로 1회다."""
    calls = _patch_call(monkeypatch, lambda n: _transient())
    with pytest.raises(requests.exceptions.HTTPError):
        _model(retry_count=2, retry_runner=None)._call_vlm("prompt", "img")
    assert calls["n"] == 1


# ── 배선 ──────────────────────────────────────────────────────────────────────

def test_pipeline_setup_installs_the_shared_retry_policy():
    """설정 해석부가 재시도 실행기를 실제로 꽂는지. 안 꽂으면 설정이 다시 죽는다."""
    from docling.datamodel.pipeline_options import PdfPipelineOptions

    options = PdfPipelineOptions()
    settings = LayoutSettings(
        model_type=options.layout_options.layout_model_type,
        endpoint="http://layout/v1", api_key="", page_batch_size=32,
        max_completion_tokens=16384, model="dots-mocr", timeout=1200,
        retry_count=3, temperature=0.1, top_p=0.9, repetition_penalty=1.15,
        length_fallback_enabled=True, fallback_dpi=200, table_fallback_enabled=True,
    )
    apply_layout_settings(options, settings)

    genos = options.layout_options.genos_layout_options
    assert genos.retry_count == 3
    # 동일성(`is`)으로 보지 않는다 - 이 저장소는 같은 모듈을 두 경로로 import 하는 자리가
    # 있어 함수 객체가 갈린다. 꽂힌 것이 실제로 재시도하는지를 본다.
    calls = {"n": 0}

    def _send():
        calls["n"] += 1
        if calls["n"] == 1:
            raise _transient()
        return "ok"

    assert genos.retry_runner(_send, retries=2) == "ok"
    assert calls["n"] == 2


def test_httpx_failures_are_still_retryable_for_the_text_paths():
    """layout 을 위해 넓힌 판정이 기존 chat 경로의 판정을 바꾸지 않았는지."""
    from processing.enrichment.llm_response import is_retryable

    response = httpx.Response(503, request=httpx.Request("POST", "http://x"))
    assert is_retryable(httpx.HTTPStatusError("x", request=response.request, response=response))
    assert not is_retryable(httpx.ReadTimeout("slow"))
