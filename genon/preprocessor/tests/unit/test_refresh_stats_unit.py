"""post_chunk 후처리용 통계 재계산(#363).

훅이 본문을 고치거나 청크를 버린 뒤 파생 필드가 옛 값으로 남는 것을 막는다.
계산식은 청킹 본체(core/chunker.py 의 세 경로)와 같아야 한다.
"""
from __future__ import annotations

import pytest

from genon.preprocessor.facade.common.vector_meta import refresh_stats

pytestmark = pytest.mark.unit


class FakeVector:
    """GenOSVectorMeta 대역. extra=allow 모델처럼 속성 대입만 받으면 된다."""

    def __init__(self, text: str, i_page: int = 1, **fields):
        self.text = text
        self.i_page = i_page
        self.n_char = self.n_word = self.n_line = None
        self.i_chunk_on_doc = self.n_chunk_of_doc = None
        self.i_chunk_on_page = self.n_chunk_of_page = self.n_page = None
        for name, value in fields.items():
            setattr(self, name, value)


def test_stats_match_core_formula():
    v = FakeVector("첫 줄 입니다\n둘째 줄")
    refresh_stats([v])
    assert (v.n_char, v.n_word, v.n_line) == (len(v.text), 5, 2)


def test_empty_and_none_text_do_not_raise():
    a, b = FakeVector(""), FakeVector(None)
    refresh_stats([a, b])
    assert (a.n_char, a.n_word, a.n_line) == (0, 0, 0)
    assert (b.n_char, b.n_word, b.n_line) == (0, 0, 0)


def test_reindex_numbers_from_zero_per_page():
    vectors = [FakeVector("가", 1), FakeVector("나", 1), FakeVector("다", 2)]
    refresh_stats(vectors)
    assert [v.i_chunk_on_doc for v in vectors] == [0, 1, 2]
    assert [v.i_chunk_on_page for v in vectors] == [0, 1, 0]
    assert [v.n_chunk_of_page for v in vectors] == [2, 2, 1]
    assert {v.n_chunk_of_doc for v in vectors} == {3}
    assert {v.n_page for v in vectors} == {2}


def test_reindex_false_leaves_order_fields_untouched():
    v = FakeVector("가", 1, i_chunk_on_doc=7, n_chunk_of_doc=9)
    refresh_stats([v], reindex=False)
    assert v.n_char == 1
    assert (v.i_chunk_on_doc, v.n_chunk_of_doc) == (7, 9)


def test_dropping_chunks_renumbers_the_survivors():
    vectors = [FakeVector("가", 1), FakeVector("나", 1), FakeVector("다", 1)]
    refresh_stats(vectors)
    kept = [vectors[0], vectors[2]]
    refresh_stats(kept)
    assert [v.i_chunk_on_doc for v in kept] == [0, 1]
    assert {v.n_chunk_of_doc for v in kept} == {2}
    assert {v.n_chunk_of_page for v in kept} == {2}


def test_missing_i_page_falls_back_to_one():
    class NoPage(FakeVector):
        def __init__(self, text):
            super().__init__(text)
            del self.i_page

    vectors = [NoPage("가"), NoPage("나")]
    refresh_stats(vectors)
    assert [v.i_chunk_on_page for v in vectors] == [0, 1]
    assert {v.n_page for v in vectors} == {1}


def test_returns_the_same_list_object_for_chaining():
    vectors = [FakeVector("가")]
    assert refresh_stats(vectors) is vectors
