"""monimo_news / cs_sss / cs_hpp 가 지정한 chunk_size 로 청크를 만드는지 검증.

실제 호출 기반(mock 금지)이 원칙이나, LLM 서빙 호출만은 예외로 AsyncMock 으로 대체한다
(tests/unit/test_md_text_fence_unit.py 와 같은 방식). cs_hpp 는 문서 단위 extractor=llm 이라
LLM 없이는 파싱이 끝나지 않는데, LLM 결과는 문서 전역 metadata 로만 실리고 청크 경계에는
영향을 주지 않으므로 chunk_size 검증에는 손실이 없다.

세 doc_type 은 서로 다른 청킹 경로를 타고, 같은 chunk_size 설정에서 유효 상한이 달라진다:

  monimo_news / cs_sss : json_mapping(split: true) → custom_fields_row 경로
                         → _expand_splittable_rows → RecursiveCharacterTextSplitter
                         → 상한 = chunk_size 그대로 (보정 없음)
  cs_hpp               : 문서 단위 llm → docling 산출물 → GenosSmartChunker
                         → 상한 = _clamp_chunk_size(chunk_size) (1024 미만은 1024 로 상향)

이 비대칭이 의도된 동작임을 테스트가 그대로 문서화한다 — 상한을 상수로 박지 않고
경로별 계산식으로 쓴다.

chunk_size 는 kwargs 로 명시한다(kwargs > yaml). 값 1000 은 현재
resource_dev/chunking_processor_config.yaml 의 설정값과 같으며, 설정이 바뀌어도
이 테스트가 흔들리지 않도록 고정한다.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

_SAMPLES = Path(__file__).resolve().parents[2] / "sample_files" / "monimo"

CHUNK_SIZE = 1000
CHUNK_MODE = "split_only"

# cs_hpp custom_field yaml 의 output_fields 6개를 모두 채운다 — 누락되면 missing_policy 에 걸린다.
_CS_HPP_LLM_STUB = json.dumps(
    {
        "BIZ_ID": "CS-HPP-9001",
        "CS_CATEGORY": "이용안내 > 상세 이용 조건",
        "TITLE": "상세 이용 조건 안내",
        "CONTENT": "테스트용 안내본문",
        "DEEP_LINK_URL": None,
        "RELATED_KEYWORDS": ["이용조건", "신청방법"],
    },
    ensure_ascii=False,
)


def _parse_and_chunk(source: Path, doc_type: str, llm_stub: str | None = None) -> list[dict]:
    """파서→청커 왕복을 실제로 돌리고 청크 dict 목록을 돌려준다."""
    from fastapi import Request

    cp = pytest.importorskip("genon.preprocessor.facade.chunking_processor")
    pp = pytest.importorskip("genon.preprocessor.facade.parser_processor")

    async def _run():
        request = Request(scope={"type": "http"})
        parser = pp.DocumentProcessor()
        if llm_stub is not None:
            stubbed = 0
            for enricher in parser._intel.custom_fields_enrichers:
                if doc_type in enricher._doc_types:
                    enricher._call_llm = AsyncMock(return_value=llm_stub)
                    stubbed += 1
            assert stubbed, f"{doc_type} custom_fields enricher 를 찾지 못했습니다"
        payload = await parser(request, str(source), doc_type=doc_type, log_level=3)
        vectors = await cp.DocumentProcessor()(
            request, str(source), document=payload,
            chunk_size=CHUNK_SIZE, chunk_mode=CHUNK_MODE,
        )
        return [v.model_dump() for v in vectors]

    return asyncio.run(_run())


def _require(sample_name: str) -> Path:
    source = _SAMPLES / sample_name
    if not source.exists():
        pytest.skip(f"검증용 샘플 없음: {source}")
    return source


def _by_biz_id(rows: list[dict], biz_id: str) -> list[dict]:
    return [r for r in rows if r.get("BIZ_ID") == biz_id]


def _assert_row_path_record_split(rows: list[dict], long_id: str, short_id: str, doc_type: str):
    """json_mapping(행) 경로 공통 검증 — 상한 준수 + 임계 기준 분할/미분할 + metadata 보존."""
    assert rows, "청크가 생성되지 않았습니다"

    # 1) 상한 준수. 행 경로는 _clamp_chunk_size 를 타지 않으므로 chunk_size 그대로가 상한이다.
    over = [(i, len(r["text"])) for i, r in enumerate(rows) if len(r["text"]) > CHUNK_SIZE]
    assert not over, f"chunk_size={CHUNK_SIZE} 초과 청크: {over[:5]}"

    # 2) chunk_size 를 넘는 레코드는 여러 청크로 쪼개진다.
    long_rows = _by_biz_id(rows, long_id)
    assert len(long_rows) > 1, f"{long_id} 가 분할되지 않았습니다(청크 {len(long_rows)}개)"

    # 3) chunk_size 미만 레코드는 "레코드 1건 = 청크 1개" 를 유지한다.
    short_rows = _by_biz_id(rows, short_id)
    assert len(short_rows) == 1, f"{short_id} 가 불필요하게 분할됐습니다(청크 {len(short_rows)}개)"

    # 4) 분할 조각은 원 레코드의 metadata 를 그대로 물려받는다(적재 측이 조각을 묶는 근거).
    assert all(r.get("doc_type") == doc_type for r in rows)
    for key in ("GROUP_C", "TITLE"):
        values = {r.get(key) for r in long_rows}
        assert len(values) == 1, f"{long_id} 조각들의 {key} 가 갈렸습니다: {values}"

    # 제목은 metadata 에만 남아서는 안 된다. 각 청크 text 앞에 반복돼 독립 검색 결과로도
    # 무엇에 대한 본문인지 식별할 수 있어야 한다.
    title = long_rows[0]["TITLE"]
    assert all(r["text"].startswith(title) for r in long_rows), (
        f"{long_id} 분할 조각 중 TITLE 없이 시작하는 청크가 있습니다"
    )


@pytest.mark.unit
def test_monimo_news_chunks_respect_chunk_size():
    """monimo_news(json_mapping, split: true) — 상한 = chunk_size 그대로."""
    source = _require("monimo_news_chunksize_sample.json")
    rows = _parse_and_chunk(source, "monimo_news")

    _assert_row_path_record_split(rows, "CM26070001", "CM26070002", "monimo_news")

    # 본문 유실 없음 — 긴 레코드의 처음과 끝 marker 가 조각들 안에 살아있다.
    joined = "\n".join(r["text"] for r in _by_biz_id(rows, "CM26070001"))
    assert "제휴 혜택 상세 안내를 시작합니다." in joined
    assert "제휴 혜택 상세 안내를 마칩니다." in joined
    assert "단문 소식 본문입니다." in _by_biz_id(rows, "CM26070002")[0]["text"]


@pytest.mark.unit
def test_cs_sss_chunks_respect_chunk_size():
    """cs_sss(json_mapping, split: true) — 상한 = chunk_size 그대로."""
    source = _require("monimo_cs_sss_chunksize_sample.json")
    rows = _parse_and_chunk(source, "cs_sss")

    _assert_row_path_record_split(rows, "FAQ_9001", "FAQ_9002", "cs_sss")

    joined = "\n".join(r["text"] for r in _by_biz_id(rows, "FAQ_9001"))
    assert "증권 안내 본문을 시작합니다." in joined
    assert "증권 안내 본문을 마칩니다." in joined
    assert "단문 안내 본문입니다." in _by_biz_id(rows, "FAQ_9002")[0]["text"]


@pytest.mark.unit
def test_cs_hpp_chunks_respect_chunk_size():
    """cs_hpp(문서 단위 llm) — docling 경로라 상한이 _clamp_chunk_size 로 1024 까지 올라간다."""
    cp = pytest.importorskip("genon.preprocessor.facade.chunking_processor")
    source = _require("monimo_cs_hpp_chunksize_sample.html")
    rows = _parse_and_chunk(source, "cs_hpp", llm_stub=_CS_HPP_LLM_STUB)

    effective = cp._clamp_chunk_size(CHUNK_SIZE)
    assert effective == 1024, "docling 경로의 하한 보정(_MIN_CHUNK_SIZE)이 바뀌었습니다"

    assert len(rows) > 1, f"분할되지 않았습니다(청크 {len(rows)}개)"
    over = [(i, len(r["text"])) for i, r in enumerate(rows) if len(r["text"]) > effective]
    assert not over, f"유효 상한={effective} 초과 청크: {over[:5]}"

    # 상한을 넘는 '상세 이용 조건' 섹션이 실제로 쪼개졌다(처음/끝 marker 가 다른 청크에 있다).
    # 이 섹션은 하위 제목(h3) 없이 한 덩어리다 — 제목 경계가 아니라 크기가 분할 근거임을 보장한다.
    starts = [i for i, r in enumerate(rows) if "상세 이용 조건 안내를 시작합니다." in r["text"]]
    ends = [i for i, r in enumerate(rows) if "상세 이용 조건 안내를 마칩니다." in r["text"]]
    assert starts and ends, "긴 섹션의 marker 가 청크에서 사라졌습니다"
    assert starts != ends, "상한 초과 섹션이 청크 1개에 그대로 남았습니다"

    # 크기 상한이 실제 제약으로 작동했다. 섹션이 모두 작아 제목 경계로만 나뉘었다면
    # 어떤 청크도 예산의 절반을 넘기지 못하므로, 이 조건이 "상한이 binding" 을 증명한다.
    assert max(len(r["text"]) for r in rows) > effective // 2, (
        "예산 절반을 넘는 청크가 없습니다 — 크기 상한이 아니라 제목 경계로만 분할된 상태입니다"
    )

    # 문서 단위 LLM 추출 결과는 모든 청크에 metadata 로 붙는다.
    assert all(r.get("doc_type") == "cs_hpp" for r in rows)
    assert all(r.get("BIZ_ID") == "CS-HPP-9001" for r in rows)
    assert all(r.get("GROUP_C") == "HPP" for r in rows)
