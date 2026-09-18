"""지정한 chunk_size 로 청크를 만드는지 검증(행 경로 / docling 경로).

실제 호출 기반(mock 금지)이 원칙이나, LLM 서빙 호출만은 예외로 AsyncMock 으로 대체한다
(tests/unit/test_md_text_fence_unit.py 와 같은 방식). 문서 단위 extractor=llm 인 doc_type 은
LLM 없이는 파싱이 끝나지 않는데, LLM 결과는 문서 전역 metadata 로만 실리고 청크 경계에는
영향을 주지 않으므로 chunk_size 검증에는 손실이 없다.

두 경로는 같은 chunk_size 설정에서 유효 상한이 달라진다:

  행 경로    : json_mapping/tabular_mapping(split: true) → custom_fields_row 경로
               → _expand_splittable_rows → RecursiveCharacterTextSplitter
               → 상한 = chunk_size 그대로 (보정 없음)          — cs_sss, cs_hpp
  docling 경로: 문서 단위 산출물 → GenosSmartChunker
               → 상한 = _clamp_chunk_size(chunk_size) (1024 미만은 1024 로 상향)

cs_hpp 는 2026-09 사이트 설정 개편으로 문서 단위 llm(html)에서 **JSON 레코드 매핑**으로
바뀌어(custom_field_cs_hpp.yaml: `kind: records`) 행 경로에 들어왔다. 그래서 docling 경로
쪽 단정은 doc_type 에 묶이지 않는 형태로 둔다 — 대형 표 분할은 custom_fields 가 붙지 않는
doc_type 으로, 반복 접두는 문서형 doc_type(card)에 kwargs 를 얹어서 본다.

monimo_news 도 행 경로였으나 원천이 레코드 배열에서 HTML 문서 한 건으로 바뀌면서
`kind: html` 설정이 됐다(custom_field_monimo_news.yaml). 행 경로 계약은 cs_sss 가 덮고,
monimo_news 자체 산출은 골든 대조가 덮는다.

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

# 청크 크기 하한 보정은 배관이라 처리 본체(core)에 있다(#363 08-2).
from genon.preprocessor.processing.core.chunker import _clamp_chunk_size

_SAMPLES = Path(__file__).resolve().parents[2] / "sample_files" / "monimo"

CHUNK_SIZE = 1000
CHUNK_MODE = "split_only"

# 새 cs_hpp 원천(hpp_rag_adcc_*.json)의 레코드 두 건. 값은 샘플 픽스처와 같아야 한다.
_CS_HPP_L1 = "이용안내"
_CS_HPP_LONG_TITLE = "해외 이용금액 분할 납부 운영 기준"
_CS_HPP_SHORT_TITLE = "카드 분실 신고 방법"
# 접두 줄에는 yaml `body.labels` 가 정한 사람이 읽는 항목명이 앞에 붙는다. 값만 단정하면
# 라벨이 붙은 순간 테스트가 깨지므로, 설정이 만드는 줄 전체를 기준으로 둔다.
_CS_HPP_L1_LINE = f"문의유형: {_CS_HPP_L1}"

# 접두 구역으로 볼 선두 줄 수. 접두는 `chunk_prefix_fields`(반복) + `first_chunk_fields`
# (첫 청크 1회) 로 이뤄지고 출고 설정 어디에도 3개를 넘는 조합이 없다. HEADER 라인까지
# 여유로 덮는 값이다.
_PREFIX_SCAN_LINES = 4


def _chunk_header(text: str) -> str:
    """청크의 HEADER 라인. 없으면 빈 문자열.

    접두(`chunk_prefix_fields` / `first_chunk_fields`)는 HEADER 앞에 붙는다 - 코드가
    의도한 순서다(chunking_processor 의 compose_vectors: "접두는 헤더 앞이다 - 문서
    식별이 섹션 경로보다 앞에 와야 한다"). 접두 줄이 몇 개인지는 doc_type 설정이
    정하므로 특정 값을 떼어내는 대신 HEADER 라인을 찾는다 - 설정이 접두 필드를
    늘리거나 줄여도 이 헬퍼가 끌려다니지 않는다.
    """
    for line in text.splitlines():
        if line.startswith("HEADER: "):
            return line
    return ""


# 반복 접두 검증용. 문서형(extractor=llm) doc_type 이 만드는 문서 전역 metadata 를
# 흉내낸다 — 적지 않은 output_fields 는 None 으로 채워지므로 접두에 쓸 값만 준다.
_CARD_PREFIX_FIELD = "product_name"
_CARD_PRODUCT_NAME = "테스트 카드 상품"
_CARD_LLM_STUB = json.dumps(
    {"product_name": _CARD_PRODUCT_NAME, "product_name_norm": _CARD_PRODUCT_NAME},
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


def _by_field(rows: list[dict], key: str, value: str) -> list[dict]:
    return [r for r in rows if r.get(key) == value]


def _by_biz_id(rows: list[dict], biz_id: str) -> list[dict]:
    return _by_field(rows, "BIZ_ID", biz_id)


def _assert_row_path_record_split(rows: list[dict], long_id: str, short_id: str, doc_type: str,
                                  *, id_key: str = "BIZ_ID"):
    """json_mapping(행) 경로 공통 검증 — 상한 준수 + 임계 기준 분할/미분할 + metadata 보존.

    ``id_key`` 는 레코드를 가르는 필드다. 원천에 레코드 식별자가 없는 doc_type(cs_hpp 는
    BIZ_ID 별칭 `ID`/`id` 가 원천에 없어 null 이다)도 같은 계약을 검증할 수 있게 열어 둔다.
    """
    assert rows, "청크가 생성되지 않았습니다"

    # 1) 상한 준수. 행 경로는 _clamp_chunk_size 를 타지 않으므로 chunk_size 그대로가 상한이다.
    over = [(i, len(r["text"])) for i, r in enumerate(rows) if len(r["text"]) > CHUNK_SIZE]
    assert not over, f"chunk_size={CHUNK_SIZE} 초과 청크: {over[:5]}"

    # 2) chunk_size 를 넘는 레코드는 여러 청크로 쪼개진다.
    long_rows = _by_field(rows, id_key, long_id)
    assert len(long_rows) > 1, f"{long_id} 가 분할되지 않았습니다(청크 {len(long_rows)}개)"

    # 3) chunk_size 미만 레코드는 "레코드 1건 = 청크 1개" 를 유지한다.
    short_rows = _by_field(rows, id_key, short_id)
    assert len(short_rows) == 1, f"{short_id} 가 불필요하게 분할됐습니다(청크 {len(short_rows)}개)"

    # 4) 분할 조각은 원 레코드의 metadata 를 그대로 물려받는다(적재 측이 조각을 묶는 근거).
    assert all(r.get("doc_type") == doc_type for r in rows)
    for key in ("GROUP_C", "CUSTOM_TITLE"):
        values = {r.get(key) for r in long_rows}
        assert len(values) == 1, f"{long_id} 조각들의 {key} 가 갈렸습니다: {values}"

    # 제목은 metadata 에만 남아서는 안 된다. 각 청크 접두에 반복돼 독립 검색 결과로도
    # 무엇에 대한 본문인지 식별할 수 있어야 한다. 출고 설정이 CUSTOM_TITLE 에 항목명을 주므로
    # 접두 줄은 `제목: <CUSTOM_TITLE>` 형태다(field_labels).
    #
    # 접두 안에서 CUSTOM_TITLE 이 몇 번째 줄인지는 설정의 `body.repeat` 순서가 정한다
    # (cs_sss 는 [CS_CATEGORY, CUSTOM_TITLE] 이라 1행이 분류다). 그래서 선두 고정이 아니라
    # 접두 구역 안에 있는지를 본다 - 설정이 접두 필드 순서를 바꿔도 끌려다니지 않는다.
    title = long_rows[0]["CUSTOM_TITLE"]
    title_lines = {title, f"제목: {title}"}
    assert all(
        title_lines & set(r["text"].splitlines()[:_PREFIX_SCAN_LINES])
        for r in long_rows
    ), f"{long_id} 분할 조각 중 CUSTOM_TITLE 접두가 없는 청크가 있습니다"


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
    """cs_hpp(json_mapping, split: true) — 행 경로라 상한이 chunk_size 그대로다.

    사이트 설정 개편으로 원천이 html 문서에서 JSON 레코드로 바뀌었다
    (custom_field_cs_hpp.yaml: `kind: records`). 그래서 cs_sss 와 같은 계약을 본다.
    """
    source = _require("monimo_cs_hpp_chunksize_sample.json")
    rows = _parse_and_chunk(source, "cs_hpp")

    # 원천에 레코드 식별자가 없어(BIZ_ID 별칭 `ID`/`id` 미제공) 제목으로 가른다.
    _assert_row_path_record_split(
        rows, _CS_HPP_LONG_TITLE, _CS_HPP_SHORT_TITLE, "cs_hpp", id_key="CUSTOM_TITLE",
    )

    long_rows = _by_field(rows, "CUSTOM_TITLE", _CS_HPP_LONG_TITLE)
    joined = "\n".join(r["text"] for r in long_rows)
    assert "카드 안내 본문을 시작합니다." in joined
    assert "카드 안내 본문을 마칩니다." in joined
    short_text = _by_field(rows, "CUSTOM_TITLE", _CS_HPP_SHORT_TITLE)[0]["text"]
    assert "단문 안내 본문입니다." in short_text

    # 분류(ORN_NM)는 매 청크 접두에 반복된다(yaml `body.repeat`).
    assert all(r["text"].startswith(_CS_HPP_L1_LINE + "\n") for r in rows)
    # 원천 HTML 은 CONTENT 로 평문화돼 실린다 — 태그가 본문에 남지 않는다.
    assert not any("<div>" in r["text"] or "<p>" in r["text"] for r in rows)
    # 연관 키워드는 to_json 으로 묶여 metadata 에만 남는다.
    assert all("KEYW_LIS" in (r.get("RELATED_KEYWORDS") or "") for r in rows)


@pytest.mark.unit
@pytest.mark.parametrize("chunk_mode", ["split_only", "resize_all"])
def test_large_html_table_is_split_by_complete_rows(chunk_mode):
    """1000자 초과 단일 HTML 표는 태그/행 중간이 아니라 완전한 table 조각으로 나뉜다.

    표 분할은 청커 계약이라 doc_type 과 무관하다. custom_fields 가 붙지 않는
    doc_type(faq 는 행 매핑 계열이라 .html 원천에 매칭되지 않는다)으로 돌려
    docling 경로만 남긴다 — 예전에는 cs_hpp(문서 단위 llm)로 돌렸으나 그 설정이
    JSON 레코드 매핑으로 바뀌어 더 이상 이 경로를 태우지 않는다.

    ``table_format`` 을 html 로 못 박는다. 출고 기본값은 auto 이고 이 표는 정형 grid 라
    auto 가 markdown 을 고른다 - 그러면 아래 태그 단정이 전부 무의미해진다. 여기서 보는
    것은 "html 로 낼 때 조각이 완전한 표인가" 이고, auto 의 형식 선택 자체는
    test_table_shape_unit.py / test_table_text_variant_chunks.py 가 본다.
    """
    cp = pytest.importorskip("genon.preprocessor.facade.chunking_processor")
    source = _require("monimo_cs_hpp_large_table_sample.html")
    rows = _parse_and_chunk(
        source, "faq", chunk_mode=chunk_mode, extra_kwargs={"table_format": "html"},
    )

    effective = _clamp_chunk_size(CHUNK_SIZE)
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


@pytest.mark.unit
def test_chunk_prefix_fields_repeat_on_every_chunk_within_chunk_size():
    """chunk_prefix_fields — 지정 필드가 모든 청크 선두에 반복되고 상한은 그대로 지켜진다.

    청커가 접두 몫을 크기 산정에서 예약하지 않으면 본문이 chunk_size 를 꽉 채운 뒤 접두가
    그 위에 얹혀 상한을 넘는데, 아래 상한 단정이 그 회귀를 잡는다.

    접두는 **docling 경로의 레버**다 — 행 경로는 매퍼가 접두까지 포함한 본문을 만들어
    내려보내므로 kwargs 가 닿지 않는다. 그래서 문서 단위 metadata 를 만드는
    doc_type(card, extractor=llm)에 kwargs 를 얹어 본다. 설정 자리는 custom_field yaml
    이지만 kwargs 로 덮어 doc_type 하나에 묶이지 않게 한다(두 경로가 같은 resolver 를 탄다).
    """
    cp = pytest.importorskip("genon.preprocessor.facade.chunking_processor")
    source = _require("monimo_cs_hpp_marker_sections_sample.html")
    rows = _parse_and_chunk(
        source, "card", llm_stub=_CARD_LLM_STUB, include_chunk_header=True,
        extra_kwargs={"chunk_prefix_fields": _CARD_PREFIX_FIELD},
    )

    assert len(rows) > 1
    assert all(r["text"].startswith(_CARD_PRODUCT_NAME + "\n") for r in rows), \
        "모든 청크에 반복돼야 합니다"

    # 선두 조립 순서 계약: 반복 접두 → HEADER → 본문.
    assert all(r["text"].splitlines()[1].startswith("HEADER: ") for r in rows)

    effective = _clamp_chunk_size(CHUNK_SIZE)
    over = [(i, len(r["text"])) for i, r in enumerate(rows) if len(r["text"]) > effective]
    assert not over, f"접두 몫 예약 누락 — 유효 상한={effective} 초과 청크: {over[:5]}"


@pytest.mark.unit
def test_marker_promotion_is_gated_by_doc_type():
    """마커 승격은 doc_type 설정이 켜야만 일어난다 — 켜지 않으면 크기로만 절단된다.

    `source.pre.html.marker_headings` 를 선언한 출고 설정이 현재 하나도 없다(사이트
    cs_hpp 가 JSON 레코드 매핑으로 바뀌면서 그 선언이 빠졌다). 그래서 이 테스트는
    "승격이 기본으로 새지 않는다" 를 지키는 자리로 남는다 — 승격 규칙 자체의 단정은
    test_html_flatten_unit.py, 마크다운 쪽은 test_md_marker_headings_unit.py 에 있다.
    html 원천에 custom_fields 가 붙지 않는 doc_type(faq)으로 돌린다.
    """
    source = _require("monimo_cs_hpp_marker_sections_sample.html")
    rows = _parse_and_chunk(source, "faq", include_chunk_header=True)

    headers = [r["text"].splitlines()[0] for r in rows]
    assert len(set(headers)) < 3, f"distinct HEADER 가 예상보다 많습니다: {headers}"
    # ◈ 로 보면 안 된다 — 승격이 일어나도 text_cleanup 규칙이 지우므로 이 대조군이
    # "봉인됐다" 와 "승격됐는데 글리프만 지워졌다" 를 구분하지 못한다. ▣ 는 그 규칙을
    # 타지 않아 승격이 일어났을 때만 breadcrumb 에 나타난다(실측: 봉인 시 0건).
    assert not any("▣" in h for h in headers)
