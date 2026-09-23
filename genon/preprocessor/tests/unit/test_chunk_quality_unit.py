"""이상 청크 판정(processing/chunking/chunk_quality.py) 단위 테스트.

판정 샘플과 설정 샘플은 tests/fixtures/chunk_quality/ 의 yaml 두 개가 정본이다. 실측
스크립트(examples/chunk_validation/measure.py)와 고객 설명 자료가 같은 파일을 쓰므로,
기준을 바꿀 때는 이 테스트가 아니라 yaml 을 고친다. 코어 연결은
test_chunking_processor_unit.py 가 다룬다.
"""
from pathlib import Path

import pytest
import yaml

from genon.preprocessor.processing.chunking import chunk_quality as cq

pytestmark = pytest.mark.unit

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "chunk_quality"


def _load(name: str) -> list:
    return yaml.safe_load((_FIXTURES / name).read_text(encoding="utf-8"))["cases"]


def build_text(spec) -> str:
    """cases.yaml 의 text 생성 규칙을 문자열로 푼다(형식은 그 파일 머리말)."""
    out = []
    for piece in spec if isinstance(spec, list) else [spec]:
        if isinstance(piece, str):
            out.append(piece)
        elif "repeat" in piece:
            out.append(piece["repeat"] * piece["times"])
        else:
            out.append("".join(chr(0xAC00 + i) for i in range(piece["distinct"])))
    return "".join(out)


@pytest.mark.parametrize("case", _load("cases.yaml"), ids=lambda c: c["id"])
def test_judge_matches_sample(case):
    prefix = case.get("prefix", "")
    verdict = cq.judge(
        prefix + build_text(case["text"]), kind=case.get("kind", "docling"),
        code_like=case.get("code_like", False), prefix_len=len(prefix), cfg=cq.Config())
    assert (verdict.reason if verdict else "keep") == case["expect"]


@pytest.mark.parametrize("case", _load("config_cases.yaml"), ids=lambda c: c["id"])
def test_config_from_cfg_matches_sample(case):
    if case["expect"] == "error":
        with pytest.raises(ValueError, match=case["error"]):
            cq.config_from_cfg(case["chunking"])
        return
    assert (cq.config_from_cfg(case["chunking"]) is not None) == case["enabled"]


def test_config_for_applies_doc_type_overrides():
    """by_doc_type 은 요청의 doc_type 을 정규화해 찾고, 적지 않은 키는 기본 블록 값을 쓴다."""
    class _Owner:
        _chunk_validation = cq.config_from_cfg({"validation": {
            "enable": True, "action": "drop", "min_chars": 6,
            "by_doc_type": {"FAQ": {"min_chars": 0}}}})

    assert (cq.config_for(_Owner(), " faq ").min_chars, cq.config_for(_Owner(), "faq").action) == (0, "drop")
    assert cq.config_for(_Owner(), "manual").min_chars == 6
    assert cq.config_for(object(), "faq") is None
