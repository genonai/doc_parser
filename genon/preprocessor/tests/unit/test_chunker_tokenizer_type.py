"""
청커 3종의 tokenizer_type(문자 수 기반 vs HF 토크나이저) 선택 기능 단위 테스트.

대상은 intelligent_processor / convert_processor 의 GenosSmartChunker 와
attachment_processor 의 HybridChunker 다.

정규화 규칙(기본값 char, 대소문자·앞뒤 공백 정규화, 미지원 값 폴백)은 세 클래스가 같은
규칙을 각자 복제하고 있어 한 벌로 묶어 검증한다. 클래스마다 같은 테스트를 따로 두면
프로덕션 복제가 테스트 복제로 증폭된다. 프로덕션 쪽 복제 자체는 별도 과제다 —
chunking/smart_chunker.py, chunking/hybrid_chunker.py, core/chunker.py 세 곳에 같은 코드가 있다.

의존성(docling 등)이 없는 환경에서는 importorskip 으로 자동 skip 된다(CI gate).
"""

import pytest


def _smart_chunker(module_name: str):
    mod = pytest.importorskip(f"facade.{module_name}")
    return mod.GenosSmartChunker


def _hybrid_chunker():
    mod = pytest.importorskip("facade.attachment_processor")
    return mod.HybridChunker


_SMART_MODULES = ["intelligent_processor", "convert_processor"]

# 세 청커를 같은 방식으로 만들어 주는 팩토리. 정규화 규칙은 셋이 동일하다.
_CHUNKERS = [
    pytest.param(
        lambda **kw: _smart_chunker("intelligent_processor")(max_tokens=100, **kw),
        id="smart-intelligent",
    ),
    pytest.param(
        lambda **kw: _smart_chunker("convert_processor")(max_tokens=100, **kw),
        id="smart-convert",
    ),
    pytest.param(
        lambda **kw: _hybrid_chunker()(max_tokens=100, **kw),
        id="hybrid-attachment",
    ),
]


@pytest.mark.unit
@pytest.mark.parametrize("make_chunker", _CHUNKERS)
@pytest.mark.parametrize("kwargs", [
    {},                               # 미지정 시 기본값
    {"tokenizer_type": "bogus"},      # 알 수 없는 값은 char 로 폴백
    {"tokenizer_type": "  CHAR "},    # 대소문자·앞뒤 공백 정규화
], ids=["default", "unknown-falls-back", "normalized"])
def test_tokenizer_type_resolves_to_char_without_loading_hf(make_chunker, kwargs):
    chunker = make_chunker(**kwargs)
    assert chunker.tokenizer_type == "char"
    assert chunker._tokenizer is None


@pytest.mark.unit
@pytest.mark.parametrize("module_name", _SMART_MODULES)
def test_smart_chunker_char_mode_counts_characters(module_name):
    """char 모드에서 _count_tokens 는 문자 수(len)를 반환한다."""
    chunker = _smart_chunker(module_name)(max_tokens=100)
    assert chunker._count_tokens("") == 0
    assert chunker._count_tokens("가나다라") == 4
    assert chunker._count_tokens("abcde\nfghij") == len("abcde\nfghij")


@pytest.mark.unit
@pytest.mark.parametrize("module_name", _SMART_MODULES)
def test_smart_chunker_splits_table_text_by_chars(module_name):
    """char 모드 테이블 분할은 문자 수 기준으로 chunk_size 를 넘지 않는다."""
    chunker = _smart_chunker(module_name)(max_tokens=100)
    text = "a" * 250
    parts = chunker._split_table_text(text, max_tokens=100)
    assert all(len(p) <= 100 for p in parts)
    assert "".join(parts) == text


@pytest.mark.unit
def test_hybrid_chunker_char_mode_counts_characters():
    """HybridChunker 의 _count_text_tokens 는 str 과 list 를 모두 받는다."""
    chunker = _hybrid_chunker()(max_tokens=100)
    assert chunker._count_text_tokens(None) == 0
    assert chunker._count_text_tokens("가나다라") == 4
    assert chunker._count_text_tokens(["ab", "cde"]) == 5  # 2 + 3


@pytest.mark.unit
@pytest.mark.parametrize("make_chunker", _CHUNKERS)
def test_huggingface_mode_loads_tokenizer(make_chunker):
    """huggingface 모드에서는 HF 토크나이저가 로드되고 tokenize 경로로 카운트한다.

    토크나이저(모델/네트워크) 미가용 환경에서는 skip.
    """
    try:
        chunker = make_chunker(tokenizer_type="huggingface")
    except Exception as e:  # noqa: BLE001 - 모델/네트워크 미가용
        pytest.skip(f"HF tokenizer unavailable: {e}")

    assert chunker.tokenizer_type == "huggingface"
    assert chunker._tokenizer is not None
    count = getattr(chunker, "_count_tokens", None) or chunker._count_text_tokens
    assert count("hello world example text") > 0
