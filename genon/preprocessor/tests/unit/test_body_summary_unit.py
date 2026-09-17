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


def _patch_client(body):
    """`body_summary.httpx.Client` 를 context manager mock 으로 패치한다."""
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value=body)

    client = MagicMock()
    client.post = MagicMock(return_value=resp)

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
