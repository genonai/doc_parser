"""layout(dots-mocr) 호출의 인증 헤더 — 키가 있을 때만 싣는다(#578).

빈 키로 `Authorization: Bearer ` 를 보내면 GenOS 게이트웨이는 헤더가 있다는 이유로 키 없이 호출 허용·내부 신뢰
분기를 건너뛰고 401 을 준다. 키가 있으면 있는 대로, 없으면 헤더 없이 — 표 구조 모델과 같은 규칙.
"""

import pytest

import docling.models.genos_dots_ocr_layout_model as mod

pytestmark = pytest.mark.unit


class _Response:
    status_code = 200
    text = ""

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": "본문"}, "finish_reason": "stop"}]}


@pytest.fixture
def sent(monkeypatch):
    calls = []

    def _post(url, json=None, headers=None, timeout=None):
        calls.append(dict(headers or {}))
        return _Response()

    monkeypatch.setattr(mod.requests, "post", _post)
    monkeypatch.setattr(mod, "cached_call", lambda url, payload, produce, **_: produce())
    return calls


def _call(api_key):
    return mod.call_vlm_server(prompt="p", base64_image="aGk=", url="http://layout/v1/chat/completions",
                               api_key=api_key, model="dots-mocr", timeout=30)


@pytest.mark.parametrize("api_key", ["", None])
def test_no_key_sends_no_authorization(sent, api_key):
    assert _call(api_key)[0] == "본문"
    assert "Authorization" not in sent[0]
    assert sent[0]["Content-Type"] == "application/json"


def test_key_is_sent_as_bearer(sent):
    _call("k-123")
    assert sent[0]["Authorization"] == "Bearer k-123"
