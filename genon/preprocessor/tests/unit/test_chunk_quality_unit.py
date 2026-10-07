"""이상 청크 판정(processing/chunking/chunk_quality.py) 단위 테스트.

판정 샘플과 설정 샘플은 tests/fixtures/chunk_quality/ 의 yaml 두 개가 정본이다. 실측
스크립트(tools/chunk_validation/measure.py)와 고객 설명 자료가 같은 파일을 쓰므로,
기준을 바꿀 때는 이 테스트가 아니라 yaml 을 고친다. 코어 연결은
test_chunking_processor_unit.py 가 다룬다.
"""
from pathlib import Path
from types import SimpleNamespace

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


_SHORT = {"page": 1, "reason": "min_chars", "message": "본문이 지나치게 짧음(글자·숫자 1자, 최소 4자)",
          "preview": ""}


def _chunk(text, page=1, headings=()):
    return SimpleNamespace(text=text, kind="docling", page=page, headings=list(headings), source=None)


def _summarize(action, steps):
    """코어 반복문 순서대로 돌린다: 초기 검사 → (훅·마스킹을 거친) 출력 본문 → 최종 검사.

    steps 의 원소는 (청크, 코어 접두, 출력 본문). 출력 본문이 None 이면 훅이 버린 청크다.
    """
    session = cq.Session(cq.Config(action=action))
    outputs = []
    for index, (chunk, prefix, output) in enumerate(steps):
        if session.check_chunk(chunk, index) or output is None:
            continue
        vector_meta = SimpleNamespace(text=output)
        session.remember(vector_meta, chunk, prefix, index)
        outputs.append(vector_meta)
    session.finalize(outputs)
    return session.summary()


_GOOD_STEP = (_chunk("결제일은 매월 14일이다."), "", "결제일은 매월 14일이다.")


@pytest.mark.parametrize("action,steps,expected", [
    pytest.param("report", [_GOOD_STEP], None, id="no_flagged"),
    # 요약은 출력 본문에서만 만든다. 마스킹으로 바뀐 헤딩은 출력에 그대로 없으므로 section 이
    # 빠지고, 훅이 버린 청크는 본문을 싣지 않는다.
    pytest.param("report", [
        (_chunk(" \n", page=3, headings=["1장 > 홍길동 안내"]), "HEADER: 1장 > 홍길동 안내\n",
         "HEADER: 1장 > [이름] 안내\n"),
        (_chunk("\x1c\x1d\x1e가\x01\x02\x03", page=0), "", "\x1c\x1d\x1e가\x01\x02\x03"),
        (_chunk("상담직원용 기밀 설명. " * 30, headings=["내부"]), "", None),
        _GOOD_STEP,
    ], {"action": "report", "count": 3,
        "reasons": {"blank": 1, "broken_chars": 1, "repetition": 1}, "items": [
        {"index": 0, "page": 3, "reason": "blank", "message": "본문이 비어 있음", "preview": "HEADER: 1장 > [이름] 안내"},
        {"index": 1, "reason": "broken_chars", "message": "깨진 문자가 많음(깨진 문자 6자 / 전체 6자, 100%)",
         "preview": "\u241c\u241d\u241e가\u2401\u2402\u2403"},
        {"index": 2, "page": 1, "reason": "repetition", "message": "같은 내용이 반복됨(같은 문구 29회)",
         "preview": ""},
    ]}, id="report_uses_output_text_only"),
    # 코어가 마스킹 변환(display)을 넘기지 않으면 drop 으로 뺀 청크의 본문을 싣지 않는다.
    pytest.param("drop", [(_chunk("1.", headings=["섹션"]), "", "1.")] * 21 + [_GOOD_STEP],
                 {"action": "drop", "count": 21, "reasons": {"min_chars": 21},
                  "items": [{"index": i, **_SHORT} for i in range(20)]}, id="drop_caps_items_without_text"),
])
def test_session_summary(action, steps, expected):
    """성공 응답에 싣는 요약. 걸린 청크가 없으면 None 이라 응답에 키가 붙지 않는다."""
    assert _summarize(action, steps) == expected
