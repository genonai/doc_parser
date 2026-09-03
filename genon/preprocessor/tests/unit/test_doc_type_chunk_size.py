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
_CS_HPP_CATEGORY = "이용안내 > 상세 이용 조건"


def _chunk_header(text: str) -> str:
    """청크 선두 HEADER 라인.

    첫 청크에는 first_chunk_fields(custom_field_cs_hpp.yaml 의 CS_CATEGORY) 접두가 HEADER
    앞에 한 번 붙는다. 접두를 떼고 봐야 "HEADER 는 청크 맨 앞 한 줄" 계약을 그대로 검사할 수
    있다.
    """
    body = text
    if body.startswith(_CS_HPP_CATEGORY + "\n"):
        body = body[len(_CS_HPP_CATEGORY) + 1:]
    return body.splitlines()[0] if body else ""


_CS_HPP_LLM_STUB = json.dumps(
    {
        "BIZ_ID": "CS-HPP-9001",
        "CS_CATEGORY": _CS_HPP_CATEGORY,
        "TITLE": "상세 이용 조건 안내",
        "CONTENT": "테스트용 안내본문",
        "DEEP_LINK_URL": None,
        "RELATED_KEYWORDS": ["이용조건", "신청방법"],
    },
    ensure_ascii=False,
)


def _parse_and_chunk(source: Path, doc_type: str, llm_stub: str | None = None, *,
                     chunk_size: int = CHUNK_SIZE,
                     chunk_mode: str = CHUNK_MODE,
                     include_chunk_header: bool | None = None,
                     extra_kwargs: dict | None = None) -> list[dict]:
    """파서→청커 왕복을 실제로 돌리고 청크 dict 목록을 돌려준다.

    ``include_chunk_header`` 를 주면 kwargs 로 넘겨 yaml 설정을 덮는다. HEADER 접두어를
    단정하는 테스트는 반드시 이걸 명시한다 — resource_dev 는 개발 편의로 접두어를 꺼둔
    상태(커밋 e332b1e5)라, 설정에 기대면 테스트가 개발 설정 변경에 끌려다닌다.
    """
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
        chunk_kwargs = {"chunk_size": chunk_size, "chunk_mode": chunk_mode}
        if include_chunk_header is not None:
            chunk_kwargs["include_chunk_header"] = include_chunk_header
        chunk_kwargs.update(extra_kwargs or {})
        vectors = await cp.DocumentProcessor()(
            request, str(source), document=payload, **chunk_kwargs,
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
    # 무엇에 대한 본문인지 식별할 수 있어야 한다. 출고 설정이 TITLE 에 항목명을 주므로
    # 접두는 `제목: <TITLE>` 형태다(field_labels).
    title = long_rows[0]["TITLE"]
    assert all(
        r["text"].startswith(title) or r["text"].startswith(f"제목: {title}")
        for r in long_rows
    ), f"{long_id} 분할 조각 중 TITLE 없이 시작하는 청크가 있습니다"


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


@pytest.mark.unit
@pytest.mark.parametrize("chunk_mode", ["split_only", "resize_all"])
def test_cs_hpp_large_html_table_is_split_by_complete_rows(chunk_mode):
    """1000자 초과 단일 HTML 표는 태그/행 중간이 아니라 완전한 table 조각으로 나뉜다."""
    cp = pytest.importorskip("genon.preprocessor.facade.chunking_processor")
    source = _require("monimo_cs_hpp_large_table_sample.html")
    rows = _parse_and_chunk(
        source, "cs_hpp", llm_stub=_CS_HPP_LLM_STUB, chunk_mode=chunk_mode
    )

    effective = cp._clamp_chunk_size(CHUNK_SIZE)
    table_rows = [r for r in rows if "<table>" in r["text"]]
    assert len(table_rows) > 1, "대형 단일 표가 여러 청크로 분할되지 않았습니다"
    assert len(table_rows) == len(rows), "표와 무관한 청크가 예기치 않게 추가됐습니다"

    # 모든 조각은 독립적으로 파싱 가능한 완전한 표이며 컬럼 헤더를 반복한다.
    for row in table_rows:
        text = row["text"]
        assert text.count("<table>") == text.count("</table>") == 1
        assert text.count("<tr>") == text.count("</tr>")
        assert "<th>단계</th><th>처리 내용</th><th>확인 사항</th>" in text
        assert len(text) <= effective

    # 데이터 행은 순서대로 정확히 한 번 등장하고, 한 행의 시작/끝 marker가 같은 청크에 있어야 한다.
    marker_chunks = []
    for n in range(1, 13):
        marker = f"ROW-{n:02d}"
        assert sum(r["text"].count(f"<td>{marker}</td>") for r in table_rows) == 1
        start_chunks = [i for i, r in enumerate(table_rows) if f"{marker}-START" in r["text"]]
        end_chunks = [i for i, r in enumerate(table_rows) if f"{marker}-END" in r["text"]]
        assert start_chunks == end_chunks and len(start_chunks) == 1, f"{marker} 행이 청크 사이에서 잘렸습니다"
        marker_chunks.append(start_chunks[0])
    assert marker_chunks == sorted(marker_chunks), "표 데이터 행 순서가 바뀌었습니다"

    # 문서 단위 cs_hpp metadata는 분할된 모든 표 조각에 유지된다.
    assert all(r.get("doc_type") == "cs_hpp" for r in table_rows)
    assert all(r.get("BIZ_ID") == "CS-HPP-9001" for r in table_rows)
    assert all(r.get("GROUP_C") == "HPP" for r in table_rows)


@pytest.mark.unit
def test_cs_hpp_marker_sections_split_chunk_headers():
    """cs_hpp 마커 승격 — 도형 마커(◈/▣)가 청커 breadcrumb 의 섹션 경계로 되살아난다.

    개선 전(대조군은 test_marker_promotion_is_gated_by_doc_type, doc_type=faq)은
    distinct HEADER 가 2개뿐이라 chunk_size 로만 절단됐다. distinct >= 3 단정이
    회귀 지표다(실측 6).
    """
    cp = pytest.importorskip("genon.preprocessor.facade.chunking_processor")
    source = _require("monimo_cs_hpp_marker_sections_sample.html")
    rows = _parse_and_chunk(source, "cs_hpp", llm_stub=_CS_HPP_LLM_STUB,
                            include_chunk_header=True)

    assert len(rows) > 1
    headers = [_chunk_header(r["text"]) for r in rows]
    assert all(h.startswith("HEADER: ") for h in headers)
    assert len(set(headers)) >= 3, f"distinct HEADER 부족: {headers}"

    # first_chunk_fields 계약 — 문의유형은 첫 청크에만 1회 실린다(반복 접두가 아니다).
    leading = [i for i, r in enumerate(rows) if r["text"].startswith(_CS_HPP_CATEGORY + "\n")]
    assert leading == [0], f"첫 청크에만 붙어야 합니다: {leading}"
    # 값 자체는 모든 청크의 metadata 에 그대로 남아 필터 검색이 된다.
    assert all(r.get("CS_CATEGORY") == _CS_HPP_CATEGORY for r in rows)

    assert any("◈ 기본내용 > ▣ 네이버페이 간편결제 이용방법" in h for h in headers)
    assert any("◈ 예상Q&A" in h for h in headers)

    # 섹션 경계가 실제로 서로 다른 청크를 만든다(◈ 시행일자 청크와 ◈ 예상Q&A 청크가 다르다).
    start_idx = {i for i, h in enumerate(headers) if "◈ 시행일자" in h}
    qna_idx = {i for i, h in enumerate(headers) if "◈ 예상Q&A" in h}
    assert start_idx and qna_idx
    assert start_idx.isdisjoint(qna_idx)

    # 상한 준수. cs_hpp 는 docling 경로라 _clamp_chunk_size 로 보정된 값이 유효 상한이다.
    effective = cp._clamp_chunk_size(CHUNK_SIZE)
    over = [(i, len(r["text"])) for i, r in enumerate(rows) if len(r["text"]) > effective]
    assert not over, f"유효 상한={effective} 초과 청크: {over[:5]}"

    # 표를 포함한 청크는 태그가 완결돼 있다(섹션 분할이 표 중간을 자르지 않는다).
    for r in rows:
        text = r["text"]
        if "<table>" in text:
            assert text.count("<table>") == text.count("</table>")

    assert all(r.get("doc_type") == "cs_hpp" for r in rows)


@pytest.mark.unit
def test_cs_hpp_nospace_marker_sections_split_chunk_headers():
    """공백 없는 마커(`◆개요`)도 청커 breadcrumb 의 섹션 경계가 된다.

    캡쳐 05(INC_19570012) 전사본. 승격 규칙이 마커 뒤 공백을 요구하던 시절엔 이 문서의
    후보가 0건이라 전 청크 HEADER 가 문서 제목 하나로 같았다. 규칙 자체의 단정은
    test_html_flatten_unit.py 에 있고, 여기서는 그것이 실제 청크 경계까지 이어지는지만 본다.
    """
    source = _require("monimo_cs_hpp_marker_nospace_sample.html")
    rows = _parse_and_chunk(source, "cs_hpp", llm_stub=_CS_HPP_LLM_STUB,
                            include_chunk_header=True)

    headers = [_chunk_header(r["text"]) for r in rows]
    assert all(h.startswith("HEADER: ") for h in headers)
    assert any("◆처리방법 > ▣[홈페이지]서비스 신청 및 해지 방법" in h for h in headers)
    assert len(set(headers)) >= 3, f"distinct HEADER 부족: {headers}"


@pytest.mark.unit
def test_chunk_prefix_fields_repeat_on_every_chunk_within_chunk_size():
    """chunk_prefix_fields — 지정 필드가 모든 청크 선두에 반복되고 상한은 그대로 지켜진다.

    first_chunk_fields(1회) 와 짝을 이루는 반복 접두 경로다. 청커가 접두 몫을 크기 산정에서
    예약하지 않으면 본문이 chunk_size 를 꽉 채운 뒤 접두가 그 위에 얹혀 상한을 넘는데,
    아래 상한 단정이 그 회귀를 잡는다.

    설정 자리는 custom_field yaml 이지만 여기서는 kwargs 로 덮어 doc_type 하나에 묶이지
    않게 한다(두 경로가 같은 resolver 를 탄다).
    """
    cp = pytest.importorskip("genon.preprocessor.facade.chunking_processor")
    source = _require("monimo_cs_hpp_marker_sections_sample.html")
    rows = _parse_and_chunk(
        source, "cs_hpp", llm_stub=_CS_HPP_LLM_STUB, include_chunk_header=True,
        extra_kwargs={"chunk_prefix_fields": "TITLE"},
    )

    title = "상세 이용 조건 안내"
    assert len(rows) > 1
    assert all(r["text"].startswith(title + "\n") for r in rows), "모든 청크에 반복돼야 합니다"

    # 선두 조립 순서 계약: 반복 접두 → 첫 청크 전용 접두 → HEADER → 본문.
    # (yaml 의 first_chunk_fields=CS_CATEGORY 가 그대로 살아 있어 첫 청크만 한 줄 더 길다)
    assert rows[0]["text"].splitlines()[:2] == [title, _CS_HPP_CATEGORY]
    assert rows[0]["text"].splitlines()[2].startswith("HEADER: ")
    assert all(r["text"].splitlines()[1].startswith("HEADER: ") for r in rows[1:])

    effective = cp._clamp_chunk_size(CHUNK_SIZE)
    over = [(i, len(r["text"])) for i, r in enumerate(rows) if len(r["text"]) > effective]
    assert not over, f"접두 몫 예약 누락 — 유효 상한={effective} 초과 청크: {over[:5]}"


@pytest.mark.unit
def test_marker_promotion_is_gated_by_doc_type():
    """같은 픽스처를 doc_type=faq 로 돌리면(마커 승격 미적용) 여전히 크기로만 절단된다.

    doc_type 게이트가 실제로 마커 승격을 봉인함을 고정하는 대조군 테스트이며, 이것이
    개선 전 상태다(실측 distinct HEADER 2). faq 는 tabular_mapping/json_mapping
    계열이라 llm_stub 를 넘기면 custom_fields enricher 를 못 찾아 _parse_and_chunk 의
    `assert stubbed` 가 터지므로 llm_stub=None(기본값)으로 호출한다.
    """
    source = _require("monimo_cs_hpp_marker_sections_sample.html")
    rows = _parse_and_chunk(source, "faq", include_chunk_header=True)

    headers = [r["text"].splitlines()[0] for r in rows]
    assert len(set(headers)) < 3, f"distinct HEADER 가 예상보다 많습니다: {headers}"
    assert not any("◈" in h for h in headers)
