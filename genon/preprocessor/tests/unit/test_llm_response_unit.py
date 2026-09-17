"""LLM 응답 해석(에러 봉투 분류)과 일시적 실패 재시도의 계약 테스트.

실제 httpx 클라이언트에 MockTransport 를 끼워 상태 번호·예외·헤더 처리를 httpx 그대로
거치게 한다. 외부 LLM 은 부르지 않고, 재시도 대기는 기록만 하고 실제로 기다리지 않는다.
"""

import asyncio
import time

import httpx
import pytest

from docling.utils.llm_cache import build_context, classify_error, reset_context, set_context
from genon.preprocessor.processing.enrichment import llm_response as lr
from genon.preprocessor.processing.enrichment.custom_fields_enricher import CustomFieldsEnricher
from genon.preprocessor.processing.enrichment.metadata_enricher import MetadataEnricher

URL = "http://llm.invalid/v1/chat/completions"
OK_BODY = {"choices": [{"message": {"content": "정상"}}]}


@pytest.fixture(autouse=True)
def slept(monkeypatch):
    """재시도 대기를 실제로 하지 않고 대기 시간만 모은다."""
    waits: list = []

    async def _sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(lr.asyncio, "sleep", _sleep)
    return waits


@pytest.fixture
def deadline():
    """요청 deadline 을 세운다. 테스트가 끝나면 원래 컨텍스트로 되돌린다."""
    tokens = []

    def _set(seconds_left: float):
        ctx = build_context(
            llm_cache=False, workflow_id=None, run_id=None,
            deadline=time.monotonic() + seconds_left,
        )
        tokens.append(set_context(ctx))

    yield _set
    for token in reversed(tokens):
        reset_context(token)


def _ok(request):
    return httpx.Response(200, json=OK_BODY)


def _status(code, body=None, headers=None):
    return lambda request: httpx.Response(
        code, json=body if body is not None else {"error": "x"}, headers=headers
    )


def _envelope(code):
    return lambda request: httpx.Response(
        200, json={"object": "error", "message": "게이트웨이 오류", "code": code}
    )


def _raise(exc_type):
    def _step(request):
        raise exc_type("x", request=request)
    return _step


def _run(*steps):
    """steps 를 차례로 응답하는 서버에 한 번 호출한다. (결과 또는 예외, 요청 목록)."""
    requests: list = []
    queue = list(steps)

    def handler(request):
        requests.append(request)
        return queue.pop(0)(request)

    async def main():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await lr.post_chat_completion(
                client, URL, payload={}, headers={}, timeout=600.0
            )

    try:
        return asyncio.run(main()), requests
    except Exception as exc:  # noqa: BLE001 — 테스트가 예외 종류를 직접 단정한다
        return exc, requests


# ── 에러 봉투 분류 ────────────────────────────────────────────────────────────
# 봉투가 밝힌 상태 번호를 status_code 로 실어 두면 classify_error 가 그대로 읽는다.

@pytest.mark.unit
@pytest.mark.parametrize("body,status,kind", [
    ({"object": "error", "code": 503}, 503, "transient"),
    ({"object": "error", "code": 502}, 502, "transient"),
    ({"object": "error", "code": 400}, 400, "permanent"),
    # 확인된 형식은 vLLM 최상위 정수 code 뿐이다. 표본이 나오기 전에는 다른 자리를 읽지 않는다.
    ({"error": {"code": 503, "message": "overloaded"}}, None, "permanent"),
    ({"object": "error", "code": "503"}, None, "permanent"),
    ({"error": {"code": "rate_limit_exceeded"}}, None, "permanent"),
    ({"object": "error", "code": True}, None, "permanent"),
    ({"object": "error", "code": 200}, None, "permanent"),
    ({"detail": "Not Found"}, None, "permanent"),
    ([1, 2], None, "permanent"),
], ids=[
    "vllm-503", "vllm-502", "vllm-400", "nested-code-not-read", "string-code-not-read",
    "openai-string", "bool", "non-error-code", "no-code", "not-a-dict",
])
def test_envelope_status_drives_error_classification(body, status, kind):
    with pytest.raises(lr.LLMResponseError) as excinfo:
        lr.chat_completion_message(body)
    assert excinfo.value.status_code == status
    assert classify_error(excinfo.value) == kind


@pytest.mark.unit
def test_envelope_error_is_still_a_value_error():
    """종전 호출부가 ValueError 로 받던 계약을 유지한다."""
    with pytest.raises(ValueError):
        lr.chat_completion_message({"object": "error", "code": 503})


# ── 재시도 대상 ───────────────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("first", [
    _status(503), _status(502), _status(504), _status(429),
    _envelope(503),
    _raise(httpx.ConnectError), _raise(httpx.RemoteProtocolError),
], ids=[
    "http-503", "http-502", "http-504", "http-429",
    "envelope-503", "connect-error", "remote-protocol-error",
])
def test_transient_failure_is_retried_once_and_recovers(first, slept):
    result, requests = _run(first, _ok)
    assert result == {"content": "정상"}
    assert len(requests) == 2
    assert len(slept) == 1


@pytest.mark.unit
@pytest.mark.parametrize("first,expected", [
    (_status(500), httpx.HTTPStatusError),
    (_status(400), httpx.HTTPStatusError),
    (_status(401), httpx.HTTPStatusError),
    (_envelope(400), lr.LLMResponseError),
    (lambda request: httpx.Response(200, json={"detail": "x"}), lr.LLMResponseError),
    (_raise(httpx.ReadTimeout), httpx.ReadTimeout),
    (_raise(httpx.ConnectTimeout), httpx.ConnectTimeout),
    (_raise(httpx.ReadError), httpx.ReadError),
], ids=[
    "http-500", "http-400", "http-401", "envelope-400", "envelope-no-code",
    "read-timeout", "connect-timeout", "read-error",
])
def test_non_transient_failure_is_not_retried(first, expected, slept):
    result, requests = _run(first, _ok)
    assert isinstance(result, expected)
    assert len(requests) == 1, "재시도하지 않고 바로 올려야 한다"
    assert slept == []


@pytest.mark.unit
def test_retries_only_once():
    result, requests = _run(_status(503), _status(503), _ok)
    assert isinstance(result, httpx.HTTPStatusError)
    assert result.response.status_code == 503
    assert len(requests) == 2


# ── 대기 시간 ─────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_default_wait_is_short_and_jittered(slept):
    _run(_status(503), _ok)
    assert 1.0 <= slept[0] <= 2.0


@pytest.mark.unit
@pytest.mark.parametrize("header,expected", [("5", 5.0), ("60", 10.0)], ids=["honored", "capped"])
def test_retry_after_is_honored_up_to_the_cap(header, expected, slept):
    _run(_status(429, headers={"Retry-After": header}), _ok)
    assert slept == [expected]


# ── 요청 deadline ─────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_no_retry_when_the_wait_would_outlive_the_deadline(deadline, slept):
    deadline(0.5)  # 최소 대기(1초)보다 짧다
    result, requests = _run(_status(503), _ok)
    assert isinstance(result, httpx.HTTPStatusError)
    assert len(requests) == 1
    assert slept == []


@pytest.mark.unit
def test_each_attempt_is_bounded_by_the_remaining_request_time(deadline):
    """호출 제한시간(600초)보다 남은 요청 시간이 짧으면 시도마다 그 시간으로 줄인다."""
    deadline(100.0)
    result, requests = _run(_status(503), _ok)
    assert result == {"content": "정상"}
    read_timeouts = [request.extensions["timeout"]["read"] for request in requests]
    # 클라이언트 기본값(5초)이 아니라 남은 요청 시간이 실렸는지까지 본다.
    assert all(90.0 < value <= 100.0 for value in read_timeouts)
    assert read_timeouts[1] <= read_timeouts[0], "재시도는 첫 시도가 쓴 시간만큼 짧아진다"


# ── enricher 배선 ─────────────────────────────────────────────────────────────
# 모든 LLM 호출(문서·표 배치·레코드 필드)이 두 enricher 의 _call_llm 을 지난다.

def _custom_fields():
    return CustomFieldsEnricher(
        url=URL, model="m", output_fields=["x"], user_prompt="{{raw_text}}",
        parser={"type": "json"},
    )


def _metadata():
    return MetadataEnricher(
        url=URL, api_key="", model="m", system_prompt="", user_prompt="{{raw_text}}",
        output_fields=["x"], parser={"type": "json"},
    )


@pytest.mark.unit
@pytest.mark.parametrize("factory", [_custom_fields, _metadata], ids=["custom_fields", "metadata"])
def test_enricher_llm_call_recovers_from_a_transient_failure(factory, monkeypatch):
    requests: list = []
    queue = [_status(503), _ok]

    def handler(request):
        requests.append(request)
        return queue.pop(0)(request)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )

    assert asyncio.run(factory()._call_llm("X")) == "정상"
    assert len(requests) == 2


# ── 동기 경로(post_chat_completion_sync) ─────────────────────────────────────
# body_summary 가 쓰는 경로. 판정은 async 판과 동일해야 하므로 같은 실패·재시도
# 조합을 그대로 반복한다 — 차이는 client.post/time.sleep 이 sync 라는 것뿐이다.

@pytest.fixture(autouse=True)
def slept_sync(monkeypatch):
    """동기 경로의 재시도 대기를 실제로 하지 않고 대기 시간만 모은다."""
    waits: list = []

    def _sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(lr.time, "sleep", _sleep)
    return waits


def _run_sync(*steps):
    """steps 를 차례로 응답하는 서버에 동기 경로로 한 번 호출한다."""
    requests: list = []
    queue = list(steps)

    def handler(request):
        requests.append(request)
        return queue.pop(0)(request)

    try:
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            return lr.post_chat_completion_sync(
                client, URL, payload={}, headers={}, timeout=600.0
            ), requests
    except Exception as exc:  # noqa: BLE001 — 테스트가 예외 종류를 직접 단정한다
        return exc, requests


@pytest.mark.unit
def test_sync_transient_failure_is_retried_once_and_recovers(slept_sync):
    result, requests = _run_sync(_status(502), _ok)
    assert result == {"content": "정상"}
    assert len(requests) == 2
    assert len(slept_sync) == 1


@pytest.mark.unit
def test_sync_timeout_is_not_retried(slept_sync):
    result, requests = _run_sync(_raise(httpx.ReadTimeout), _ok)
    assert isinstance(result, httpx.ReadTimeout)
    assert len(requests) == 1
    assert slept_sync == []


@pytest.mark.unit
def test_sync_retries_only_once():
    result, requests = _run_sync(_status(503), _status(503), _ok)
    assert isinstance(result, httpx.HTTPStatusError)
    assert result.response.status_code == 503
    assert len(requests) == 2
