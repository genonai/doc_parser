"""toolbox 청크 편집 4종 단위 테스트 (#363 09 단계 6).

edit_output 에서 고객이 쓰는 이름이다. 통계 재계산은 이 함수들이 하지 않는다 —
refresh_stats 를 부르라는 안내가 문서·주석 계약이므로 그 경계도 함께 고정한다.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

tb = pytest.importorskip("processing.core.toolbox")
chunker_facade = pytest.importorskip("facade.chunking_processor")

META = chunker_facade.GenOSVectorMeta


def _vm(text: str, **extra):
    return META.model_validate({"text": text, "n_char": len(text), **extra})


def test_split_chunk_splits_only_matching_chunks():
    rows = [_vm("a" * 10), _vm("\n".join(["long"] * 40), DOC_ID="D1")]
    out = tb.split_chunk(rows, when=lambda vm: len(vm.text) > 50)
    assert len(out) > 2
    assert out[0].text == "a" * 10                      # 조건에 안 맞으면 그대로
    assert all(len(vm.text) <= 50 for vm in out[1:])    # 조건이 거짓이 될 때까지 나눈다
    assert all(vm.DOC_ID == "D1" for vm in out[1:])     # 원본 필드를 물려받는다
    # 통계는 그대로다 — 호출부가 refresh_stats 를 부른다.
    assert out[1].n_char != len(out[1].text)


def test_split_chunk_keeps_chunk_without_separator_boundary():
    rows = [_vm("x" * 100)]
    out = tb.split_chunk(rows, when=lambda vm: len(vm.text) > 60)
    assert len(out) == 2 and "".join(vm.text for vm in out) == "x" * 100


def test_merge_small_chunks_merges_into_previous():
    rows = [_vm("y" * 100), _vm("짧다"), _vm("z" * 100)]
    out = tb.merge_small_chunks(rows, min_chars=10)
    assert [len(vm.text) for vm in out] == [103, 100]
    assert out[0].text.endswith("짧다")


def test_merge_small_chunks_merges_leading_small_chunk_forward():
    rows = [_vm("짧다"), _vm("y" * 100)]
    out = tb.merge_small_chunks(rows, min_chars=10)
    assert len(out) == 1 and out[0].text.startswith("짧다")


def test_drop_fields_removes_declared_and_extra_fields():
    row = _vm("본문", INTERNAL_URL="http://x", title="t")
    tb.drop_fields(row, "INTERNAL_URL", "title")
    assert row.title is None                     # 선언된 필드는 None
    assert not hasattr(row, "INTERNAL_URL")      # 추가 필드는 통째로 빠진다


def test_html_to_text_returns_a_renderer_for_json_to_markdown():
    """tb.html_to_text() 는 json_to_markdown(html_renderer=) 에 넘길 콜백을 만든다."""
    render = tb.html_to_text()
    assert callable(render)
    out = tb.json_to_markdown({"본문": "<p>안녕</p>"}, html_renderer=render)
    assert "안녕" in out and "<p>" not in out
