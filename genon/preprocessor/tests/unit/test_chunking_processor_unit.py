"""
모니모 Parsing(docling) + Chunk API 단위 테스트 (#283 / #284).

실제 호출 기반(mock 금지). 의존성(docling 등) 미가용 환경에서는 importorskip 으로 자동 skip(CI gate).
- #283: parser_processor 의 output.format="docling" 이 복원 가능한 DoclingDocument JSON 을 반환하는지
        (DoclingDocument.model_validate 무손실 round-trip).
- #284: chunking_processor 가 그 docling JSON 을 입력받아 GenOSVectorMeta 리스트를 반환하는지.
"""
import asyncio
import json
import logging
from pathlib import Path

import pytest

from genon.preprocessor.facade.chunking_processor import _carry_over_section_headings
from docling_core.types import DoclingDocument
from docling_core.types.doc import DocItemLabel, DocumentOrigin


def _build_doc() -> DoclingDocument:
    doc = DoclingDocument(name="monimo_sample")
    doc.add_title(text="모니모 약관")
    h1 = doc.add_heading(text="제1조 목적", level=1)
    doc.add_text(label=DocItemLabel.TEXT, text="이 약관은 모니모 서비스 이용에 관한 사항을 규정한다.", parent=h1)
    doc.add_text(label=DocItemLabel.TEXT, text="회사는 본 약관을 서비스 화면에 게시한다.", parent=h1)
    h2 = doc.add_heading(text="제2조 정의", level=1)
    doc.add_text(label=DocItemLabel.TEXT, text="'회원'이란 약관에 동의하고 가입한 자를 말한다.", parent=h2)
    return doc


def test_parser_docling_output_roundtrip():
    """#283: output.format='docling' → data.document 가 무손실 복원 가능해야 한다."""
    pp = pytest.importorskip("facade.parser_processor")

    parser = object.__new__(pp.DocumentProcessor)  # __init__(네트워크/config) 우회
    parser._output_format = "docling"
    parser._table_format = "html"

    doc = _build_doc()
    resp = parser._build_docling_response(doc)

    assert "document" in resp
    assert resp["usage"]["pages"] == doc.num_pages()

    restored = DoclingDocument.model_validate(resp["document"])
    assert [t.text for t in restored.texts] == [t.text for t in doc.texts]


def test_chunker_consumes_docling_json():
    """#284: chunking_processor 가 docling JSON 을 입력받아 GenOSVectorMeta 리스트 반환."""
    cp = pytest.importorskip("facade.chunking_processor")

    assert getattr(cp.DocumentProcessor, "IS_CHUNKER", False) is True

    doc_dict = _build_doc().model_dump(mode="json")  # parser output.format='docling' 직렬화와 동일 경로

    chunker = cp.DocumentProcessor()
    vectors = asyncio.run(
        chunker(request=None, file_path="/data/monimo_sample.pdf", document=doc_dict)
    )

    assert isinstance(vectors, list) and len(vectors) >= 1
    v0 = vectors[0]
    for field in ("text", "n_char", "i_page", "i_chunk_on_doc", "n_chunk_of_doc"):
        assert hasattr(v0, field), field
    # 마지막 청크 인덱스 == 전체 청크 수 - 1
    assert vectors[-1].i_chunk_on_doc == len(vectors) - 1


def test_chunker_missing_document_raises():
    """#284: document 입력이 없으면 GenosServiceException."""
    cp = pytest.importorskip("facade.chunking_processor")

    chunker = cp.DocumentProcessor()
    with pytest.raises(cp.GenosServiceException):
        asyncio.run(chunker(request=None, file_path=""))


# ----------------------------------------------------------------------
# parse-format(비-docling) 공통 청킹 — parser 가 docling 을 못 만드는 포맷
# (audio, csv/xlsx, ppt/pptx/doc, txt/json/md, 이미지) 연동.
# 포맷은 file_path 확장자가 아니라 payload(element) 내용으로 판별한다.
# ----------------------------------------------------------------------

def test_classify_payload_shapes():
    """payload 형태 판별: docling/parse-format/envelope/garbage."""
    cp = pytest.importorskip("facade.chunking_processor")

    assert cp._classify_payload({"document": {"x": 1}}) == ("docling", {"x": 1})
    assert cp._classify_payload({"elements": [{"content": "a"}]}) == ("parse", [{"content": "a"}])
    # docling 우선: parser docling 응답은 _normalize_response 로 빈 elements 도 함께 가질 수 있음
    assert cp._classify_payload({"document": {"x": 1}, "elements": []})[0] == "docling"
    # envelope
    assert cp._classify_payload({"code": 0, "data": {"elements": [{"content": "a"}]}})[0] == "parse"
    # raw docling dict
    assert cp._classify_payload({"schema_name": "DoclingDocument", "body": {}})[0] == "docling"
    with pytest.raises(cp.GenosServiceException):
        cp._classify_payload({"unknown": 1})


def test_chunker_parse_format_audio_single_vector():
    """audio parse-format([AUDIO] 접두사) → 단일 벡터(분할 없음)."""
    cp = pytest.importorskip("facade.chunking_processor")

    transcript = "[AUDIO] 안녕하세요 모니모 음성 안내입니다. " * 50
    elements = [{"category": "paragraph", "content": transcript, "coordinates": [], "id": 0, "page": 1}]

    chunker = cp.DocumentProcessor()
    vectors = asyncio.run(
        chunker(request=None, file_path="/data/voice.json", document={"elements": elements})
    )

    assert len(vectors) == 1
    assert vectors[0].text.startswith("[AUDIO]")


def test_chunker_parse_format_tabular_da_vector():
    """csv/xlsx parse-format(category=='table' 전부) → 단일 [DA] 벡터."""
    cp = pytest.importorskip("facade.chunking_processor")

    elements = [
        {"category": "table", "content": "<table><tr><td>a</td></tr></table>", "page": 1, "id": 0},
        {"category": "table", "content": "<table><tr><td>b</td></tr></table>", "page": 2, "id": 1},
    ]

    chunker = cp.DocumentProcessor()
    vectors = asyncio.run(
        chunker(request=None, file_path="/data/sheet.json", document={"elements": elements})
    )

    assert len(vectors) == 1
    assert vectors[0].text.startswith("[DA] ")


def test_chunker_parse_format_tabular_rows_are_one_chunk_per_row():
    """신규 tabular_row parse-format은 doc_type 없이도 행마다 청크 하나를 만든다."""
    cp = pytest.importorskip("facade.chunking_processor")

    elements = [
        {
            "category": "tabular_row",
            "content": "| name |\n| Alice |",
            "page": 1,
            "id": 0,
            "metadata": {"name": "Alice", "column_map": '{"name": "name"}'},
        },
        {
            "category": "tabular_row",
            "content": "| name |\n| Bob |",
            "page": 1,
            "id": 1,
            "metadata": {"name": "Bob", "column_map": '{"name": "name"}'},
        },
    ]

    chunker = cp.DocumentProcessor()
    vectors = asyncio.run(
        chunker(request=None, file_path="/data/sheet.json", document={"elements": elements})
    )

    assert len(vectors) == 2
    assert [v.name for v in vectors] == ["Alice", "Bob"]
    assert [v.i_chunk_on_doc for v in vectors] == [0, 1]
    assert all(v.n_chunk_of_doc == 2 for v in vectors)


def test_chunker_parse_format_rows_mixed_with_text_warns_on_drop(caplog):
    """행 element 가 섞여 오면 비-행 element 는 버려지되, 축소 사실이 로그로 드러난다."""
    cp = pytest.importorskip("facade.chunking_processor")

    elements = [
        {"category": "paragraph", "content": "머리말 문단", "page": 1, "id": 0},
        {
            "category": "tabular_row",
            "content": "| name |\n| Alice |",
            "page": 1,
            "id": 1,
            "metadata": {"name": "Alice"},
        },
    ]

    chunker = cp.DocumentProcessor()
    with caplog.at_level(logging.WARNING):
        vectors = asyncio.run(
            chunker(request=None, file_path="/data/sheet.json", document={"elements": elements})
        )

    assert len(vectors) == 1
    assert "비-행 element 1개" in caplog.text


def test_chunker_parse_format_text_multi_chunk():
    """텍스트 parse-format → RecursiveCharacterTextSplitter 다중 청킹, 인덱스 연속."""
    cp = pytest.importorskip("facade.chunking_processor")

    elements = [
        {"category": "paragraph", "content": "가나다라마바사아자차카타파하 " * 20, "page": 1, "id": 0},
        {"category": "paragraph", "content": "ABCDEFG HIJKLMN OPQRSTU " * 20, "page": 2, "id": 1},
    ]

    chunker = cp.DocumentProcessor()
    vectors = asyncio.run(
        chunker(
            request=None, file_path="/data/note.json",
            document={"elements": elements}, chunk_size=50, chunk_overlap=0,
        )
    )

    assert len(vectors) >= 2
    # i_chunk_on_doc 가 0..N-1 연속
    assert [v.i_chunk_on_doc for v in vectors] == list(range(len(vectors)))
    # page 메타가 1-based 로 보존(parser element page 그대로, +1 하지 않음)
    assert set(v.i_page for v in vectors) <= {1, 2}


def test_chunker_splittable_row_expands_and_keeps_metadata():
    """splittable 행(json_mapping 레코드)은 chunk_size 초과 시 여러 청크로 나뉜다."""
    cp = pytest.importorskip("facade.chunking_processor")

    elements = [
        {
            "category": "custom_fields_row",
            "content": "가나다라마바사아자차카타파하 " * 20,
            "page": 1,
            "id": 0,
            "splittable": True,
            "metadata": {"TITLE": "이벤트A", "EVENT_TO": 20260731},
        },
        {
            "category": "custom_fields_row",
            "content": "짧은 본문",
            "page": 2,
            "id": 1,
            "splittable": True,
            "metadata": {"TITLE": "이벤트B", "EVENT_TO": 20260831},
        },
    ]

    chunker = cp.DocumentProcessor()
    vectors = asyncio.run(
        chunker(
            request=None, file_path="/data/event.json",
            document={"elements": elements}, chunk_size=50, chunk_overlap=0,
        )
    )

    assert len(vectors) > 2  # 첫 레코드가 나뉘었다
    # 조각마다 원 레코드의 metadata 가 그대로 붙는다
    assert {v.TITLE for v in vectors} == {"이벤트A", "이벤트B"}
    assert all(v.EVENT_TO == 20260731 for v in vectors if v.TITLE == "이벤트A")
    # 인덱스는 확장 후 기준으로 연속
    assert [v.i_chunk_on_doc for v in vectors] == list(range(len(vectors)))
    assert all(v.n_chunk_of_doc == len(vectors) for v in vectors)


def test_chunker_rows_without_splittable_stay_one_chunk_per_row():
    """splittable 이 없는 기존 행은 chunk_size 와 무관하게 행 1개 = 청크 1개(회귀 가드)."""
    cp = pytest.importorskip("facade.chunking_processor")

    elements = [
        {
            "category": "faq_row",
            "content": "가나다라마바사아자차카타파하 " * 20,
            "page": 1,
            "id": 0,
            "metadata": {"question": "긴 질문"},
        },
    ]

    chunker = cp.DocumentProcessor()
    vectors = asyncio.run(
        chunker(
            request=None, file_path="/data/faq.json",
            document={"elements": elements}, chunk_size=50, chunk_overlap=0,
        )
    )

    assert len(vectors) == 1


# ----------------------------------------------------------------------
# chunk_prefix(json_semantic 섹션 및 json/tabular 레코드 제목) 유지 분할.
#
# JSON 레코드와 긴 Excel 행도 조각마다 TITLE/QUESTION 같은 식별 접두가 없으면 두 번째
# 조각부터 무엇에 대한 본문인지 사라진다. json_semantic 은 여기에 섹션명과 공통 정보도 싣는다.
# ----------------------------------------------------------------------

def test_chunker_splittable_row_with_prefix_keeps_prefix_on_every_piece():
    """chunk_prefix 를 가진 splittable 행이 분할되면 모든 조각이 접두로 시작한다."""
    cp = pytest.importorskip("facade.chunking_processor")

    prefix = "[혜택 상세] 0.5%~3% 빅포인트 적립\n상품코드: AAP1344\n상품명: 새마을금고 삼성카드 7"
    body = "국내외 가맹점에서 이용금액의 0.5%를 빅포인트로 적립합니다. " * 30
    elements = [
        {
            "category": "custom_fields_row",
            "content": f"{prefix}\n{body}",
            "page": 1,
            "id": 0,
            "splittable": True,
            "chunk_prefix": prefix,
            "metadata": {"SECTION_NM": "혜택 상세", "PRODUCT_NM": "새마을금고 삼성카드 7"},
        },
    ]

    chunker = cp.DocumentProcessor()
    vectors = asyncio.run(
        chunker(
            request=None, file_path="/data/product.json",
            document={"elements": elements}, chunk_size=80, chunk_overlap=0,
        )
    )

    assert len(vectors) > 1, "본문이 chunk_size 를 넘으니 여러 조각으로 나뉘어야 한다"
    for v in vectors:
        assert v.text.startswith(prefix), f"접두 없이 시작하는 조각: {v.text!r}"
        # metadata 는 조각마다 원 레코드 것을 그대로 유지한다(기존 splittable 규칙과 동일).
        assert v.SECTION_NM == "혜택 상세"


def test_chunker_splittable_row_prefix_overflow_falls_back_with_warning(caplog):
    """접두 자체가 chunk_size 이상인 병리 케이스 — 죽지 않고 경고 후 본문 기준으로 진행한다."""
    cp = pytest.importorskip("facade.chunking_processor")

    huge_prefix = "[혜택 상세] " + "아주 긴 섹션 제목이라 접두만으로도 chunk_size 를 넘긴다 " * 10
    body = "본문 내용 " * 50
    assert len(huge_prefix) > 80  # 이 테스트의 전제(접두 > chunk_size)

    elements = [
        {
            "category": "custom_fields_row",
            "content": f"{huge_prefix}\n{body}",
            "page": 1,
            "id": 0,
            "splittable": True,
            "chunk_prefix": huge_prefix,
            "metadata": {"SECTION_NM": "혜택 상세"},
        },
    ]

    chunker = cp.DocumentProcessor()
    with caplog.at_level(logging.WARNING):
        vectors = asyncio.run(
            chunker(
                request=None, file_path="/data/product.json",
                document={"elements": elements}, chunk_size=80, chunk_overlap=0,
            )
        )

    assert vectors, "병리 케이스에서도 청크는 산출되어야 한다"
    assert "접두 몫 예약" in caplog.text
    # 폴백 후에도 원문 내용은 어딘가에 남아 있어야 한다(정보 유실 방지).
    assert "본문 내용" in "\n".join(v.text for v in vectors)


# ----------------------------------------------------------------------
# 청크 선두 헤더(HEADER: <섹션 경로>) 정규화 + include_chunk_header on/off.
#
# 과거에는 섹션 제목이 세 지점에서 3번 붙었다(compose_vectors 의 HEADER 라인 +
# _generate_section_text_with_heading 접두 + _generate_text_from_items_with_headers 삽입).
# 실측으로 청크 텍스트의 30~56% 가 제목 반복이었고, 제목만 있고 본문이 없는 껍데기 청크도
# 생겼다(여비세칙 76개 중 20개). 부착 지점을 compose_vectors 한 곳으로 정규화했다.
# ----------------------------------------------------------------------

HEADER_SEP = " > "  # facade 의 _CHUNK_HEADER_SEP 과 같아야 한다(콤마는 heading 내부 콤마와 충돌)


def _chunk(doc_dict, **kwargs):
    cp = pytest.importorskip("facade.chunking_processor")
    chunker = cp.DocumentProcessor()
    return asyncio.run(
        chunker(request=None, file_path="/data/monimo_sample.pdf", document=doc_dict, **kwargs)
    )


def test_chunk_header_appears_once_not_repeated_in_body():
    """섹션 제목은 HEADER 라인에만 붙고 본문에서 반복되지 않는다."""
    pytest.importorskip("facade.chunking_processor")

    vectors = _chunk(_build_doc().model_dump(mode="json"))
    tagged = [v for v in vectors if v.text.startswith("HEADER: ")]
    assert tagged, "HEADER 라인이 붙은 청크가 있어야 한다"

    for v in tagged:
        header_line, _, body = v.text.partition("\n")
        path = header_line[len("HEADER: "):]
        parts = path.split(HEADER_SEP)
        for heading in (p.strip() for p in parts if p.strip()):
            # 제목은 본문에 최대 1회(문서가 내보낸 SECTION_HEADER DocItem 자신)만 남는다.
            assert body.count(heading) <= 1, f"본문에 제목 반복: {heading!r} in {body!r}"
        # 예전 헤더 삽입 로직은 빈 문자열을 경로에 넣어 "상품 안내, " 처럼 끝이 잘린 조각을 남겼다.
        # 경로 조각 중 빈 것이 없어야 한다(구분자 무관하게 성립하는 조건).
        assert all(p.strip() for p in parts), f"빈 경로 조각: {path!r}"


def test_chunk_header_off_emits_body_only():
    """include_chunk_header=0 이면 HEADER 라인이 사라지고 청크 경계는 유지된다."""
    pytest.importorskip("facade.chunking_processor")

    doc_dict = _build_doc().model_dump(mode="json")
    on = _chunk(doc_dict)
    off = _chunk(doc_dict, include_chunk_header=0)

    assert any(v.text.startswith("HEADER: ") for v in on)
    assert not any("HEADER: " in v.text for v in off)
    # 헤더만 빠지고 청크 분할 자체는 동일해야 한다.
    assert len(off) == len(on)
    for v_on, v_off in zip(on, off):
        assert v_on.text.split("\n", 1)[-1].endswith(v_off.text) or v_off.text in v_on.text


@pytest.mark.unit
def test_semantic_document_title_stays_in_chunk_header():
    """문서 이름과 다른 실제 TITLE 은 HEADER 경로에 유지한다."""
    pytest.importorskip("facade.chunking_processor")

    vectors = _chunk(_build_doc().model_dump(mode="json"))

    assert any(v.text.startswith("HEADER: ") for v in vectors)
    assert any(
        "모니모 약관" in v.text.partition("\n")[0]
        for v in vectors
        if v.text.startswith("HEADER: ")
    )
    assert "모니모 약관" in "\n".join(v.text for v in vectors)
    assert all(v.title == "모니모 약관" for v in vectors)


@pytest.mark.unit
@pytest.mark.parametrize(
    "module_name",
    ["chunking_processor", "intelligent_processor", "convert_processor"],
)
def test_all_chunkers_exclude_only_filename_title_from_header_paths(module_name):
    """세 facade 모두 파일명 TITLE 만 제외하고 실제 TITLE 은 유지한다."""
    mod = pytest.importorskip(f"facade.{module_name}")
    chunker = mod.GenosSmartChunker(tokenizer_type="char")

    doc = DoclingDocument(
        name="sample",
        origin=DocumentOrigin(
            filename="sample.pdf",
            mimetype="application/pdf",
            binary_hash=0,
        ),
    )
    doc.add_title(text="sample.pdf")
    doc.add_title(text="sample")
    doc.add_title(text="실제 문서 제목")
    section = doc.add_heading(text="제1장 총칙", level=1)
    doc.add_text(label=DocItemLabel.TEXT, text="본문", parent=section)

    base_chunk = next(chunker.preprocess(doc))
    paths = chunker._extract_header_paths(base_chunk._header_short_info_list)

    assert paths == [f"실제 문서 제목{HEADER_SEP}제1장 총칙"]


def test_document_title_chunk_merges_into_first_section():
    """문서 TITLE(`#`) 만 담긴 청크는 남지 않고 하위 섹션 청크의 헤더 경로로 승계된다.

    실측(상품요약서 md): TITLE 을 breadcrumb 에서 빼자 제목만 든 41자 청크가 색인됐다.
    본문이 0 개라 검색 상위를 차지해도 근거를 못 주므로 하위 섹션과 합쳐야 한다.
    """
    pytest.importorskip("facade.chunking_processor")

    doc = DoclingDocument(name="product_summary")
    title = doc.add_title(text="상품요약서")
    overview = doc.add_heading(text="문서 개요", level=1, parent=title)
    doc.add_text(label=DocItemLabel.TEXT, text="가입나이 만 15세 이상 40세 이하", parent=overview)
    keywords = doc.add_heading(text="핵심 키워드", level=1, parent=title)
    doc.add_text(label=DocItemLabel.TEXT, text="무배당, 무해약환급금형", parent=keywords)

    vectors = _chunk(doc.model_dump(mode="json"))

    assert all(v.text.strip() != "상품요약서" for v in vectors), "제목만 있는 청크가 남았다"
    assert all(v.text.startswith(f"HEADER: 상품요약서{HEADER_SEP}") for v in vectors)


def test_heading_only_chunk_is_merged_forward():
    """본문 없이 제목만 있는 청크는 다음 청크로 병합되고 제목은 headings 로 승계된다."""
    cp = pytest.importorskip("facade.chunking_processor")

    # 제1장(하위 본문 없음) → 제1조(본문 있음): 예전에는 제1장만 담긴 껍데기 청크가 생겼다.
    doc = DoclingDocument(name="heading_only")
    chapter = doc.add_heading(text="제1장 총칙", level=1)
    article = doc.add_heading(text="제1조 목적", level=2, parent=chapter)
    doc.add_text(label=DocItemLabel.TEXT, text="이 규정은 여비 지급 기준을 정한다.", parent=article)

    vectors = _chunk(doc.model_dump(mode="json"))

    # 제목만 있는 청크가 남아있지 않아야 한다.
    for v in vectors:
        body = v.text
        for heading in (v.text.partition("\n")[0][len("HEADER: "):].split(HEADER_SEP)
                        if v.text.startswith("HEADER: ") else []):
            body = body.replace(heading, "")
        assert body.strip(" ,\n"), f"제목만 있는 청크가 남았다: {v.text!r}"

    # 드롭된 제목이 유실되지 않고 어딘가의 HEADER 경로로 승계되어야 한다.
    assert any("제1장 총칙" in v.text for v in vectors)
    assert any("이 규정은 여비 지급 기준을 정한다." in v.text for v in vectors)


# ----------------------------------------------------------------------
# chunk_size 계약: 청크 선두 헤더 라인도 크기 예산에 포함되어야 한다.
#
# 회귀 배경 — 크기 판정(_size)은 헤더를 포함하는데 실제 분할 예산은 본문 토큰만 써서
# 각 조각이 본문만으로 한도를 채우고 헤더가 그 위에 얹혀 초과했다(실측 1033자 > 1024).
# 병합(_merge_heading_only_chunks)도 headings 합집합으로 헤더를 늘려 같은 초과를 만들었다.
# 조립을 _build_header_line 한 곳으로 모으고, 분할 예산에서 헤더 몫과 delim 비용을 빼고,
# 병합 시 크기를 재검증해 막았다.
# ----------------------------------------------------------------------

def _sample_docling_doc() -> dict:
    """실제 규정 문서(여비세칙) 파싱 결과. 없으면 skip."""
    path = (Path(__file__).resolve().parents[2]
            / "examples" / "parse_chunk" / "result_parse_chunk" / "hwp_sample_table.docling.json")
    if not path.exists():
        pytest.skip(f"샘플 docling JSON 없음: {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@pytest.mark.unit
@pytest.mark.parametrize("chunk_size", [1024, 2048, 4096])
def test_chunk_never_exceeds_chunk_size(chunk_size):
    """헤더 포함 최종 텍스트가 chunk_size 를 넘지 않는다(헤더 on/off 모두)."""
    cp = pytest.importorskip("facade.chunking_processor")
    doc = _sample_docling_doc()
    effective = cp._clamp_chunk_size(chunk_size)  # 0 초과면 최소 1024 로 보정된다

    for kwargs in ({}, {"include_chunk_header": 0}):
        vectors = _chunk(doc, chunk_size=chunk_size, **kwargs)
        over = [(i, len(v.text)) for i, v in enumerate(vectors) if len(v.text) > effective]
        assert not over, f"chunk_size={effective} {kwargs} 초과: {over[:5]}"


@pytest.mark.unit
def test_heading_only_merge_skipped_when_it_would_overflow():
    """병합이 한도를 넘기면 제목-only 청크를 병합하지 않고 그대로 남긴다.

    헤더 합집합만 생략하면 donor 본문이 버려지는 구조라 제목이 산출물에서 사라진다 —
    그래서 '병합을 포기'하는 쪽이 맞다. 제목이 유실되지 않았는지도 확인한다.
    """
    cp = pytest.importorskip("facade.chunking_processor")
    CS = 1024
    long_chapter = "제1장 총칙에 관한 매우 긴 장 제목 문자열"

    doc = DoclingDocument(name="overflow_probe")
    doc.add_heading(text=long_chapter, level=1)          # 본문 없는 제목 → 병합 후보
    article = doc.add_heading(text="제1조 목적", level=1)
    # 헤더 포함 크기가 한도에 거의 찬 본문
    body_len = CS - (len("HEADER: ") + len("제1조 목적") + 1) - (len("제1조 목적") + 1)
    doc.add_text(label=DocItemLabel.TEXT, text="가" * body_len, parent=article)

    vectors = _chunk(doc.model_dump(mode="json"), chunk_size=CS)

    over = [len(v.text) for v in vectors if len(v.text) > CS]
    assert not over, f"병합 후 한도 초과: {over}"
    # 병합을 포기했더라도 제목 자체는 어딘가에 남아야 한다.
    assert any(long_chapter in v.text for v in vectors), "병합 생략 시 제목이 유실됨"


@pytest.mark.unit
def test_oversized_heading_falls_back_with_warning(caplog):
    """헤더 라인이 chunk_size 이상인 병리 케이스 — 예외 없이 산출되고 경고를 남긴다.

    헤더 몫을 예약하면 예산이 0 이하가 되어 분할이 끝나지 않으므로 본문 기준으로 폴백한다.
    (근본 원인은 docling 이 조문 전체를 SECTION_HEADER 로 승격하는 것 — 별도 이슈)
    """
    cp = pytest.importorskip("facade.chunking_processor")
    CS = 1024
    # 헤더 라인("HEADER: " + heading + "\n")이 chunk_size 를 넘어야 폴백 경로를 탄다.
    huge_heading = "제1조(목적) " + "이 세칙은 여비 지급에 관한 사항을 정한다. " * 60
    assert len("HEADER: ") + len(huge_heading) + 1 > CS

    doc = DoclingDocument(name="huge_heading")
    section = doc.add_heading(text=huge_heading, level=1)
    doc.add_text(label=DocItemLabel.TEXT, text="본문 " * 500, parent=section)

    with caplog.at_level(logging.WARNING):
        vectors = _chunk(doc.model_dump(mode="json"), chunk_size=CS)

    assert vectors, "병리 케이스에서도 청크는 산출되어야 한다"
    assert "헤더 몫 예약 생략" in caplog.text, "폴백 경고가 없다"


# ----------------------------------------------------------------------
# 헤더 경로 표기와 무손실 — 리뷰 4건 회귀 방지.
#
# (1) 헤더-only 판정을 문자열 replace 로 하면 본문이 헤더 문자열로만 구성된 정상 청크를
#     오판해 본문이 사라진다(실측: 헤더 `가` + 본문 `가가가가가` → 소실). 유형으로 판정한다.
# (2) 헤더를 평탄하게 dedup 하면 형제 섹션이 부모-자식처럼 보인다
#     (`상품 안내 > 우대금리 조건 > 가입 제한` — 뒤 둘은 형제). 실제 경로를 렌더한다.
# (3) 아이템 하나가 예산보다 크면 아이템 경계 분할로는 못 자른다 → 내부 분할.
# (4) PPT 페이지 병합이 headings 를 None 으로 덮어 HEADER 가 사라졌다.
# ----------------------------------------------------------------------

PATH_SEP = " | "


def _header_of(vector) -> str:
    """청크 선두 HEADER 경로 문자열(없으면 '')."""
    if not vector.text.startswith("HEADER: "):
        return ""
    return vector.text.partition("\n")[0][len("HEADER: "):]


@pytest.mark.unit
def test_no_item_text_is_lost():
    """입력 DocItem 의 모든 텍스트가 산출물(본문 또는 헤더 경로)에 남는다 — 유실 부류 전체 가드."""
    pytest.importorskip("facade.chunking_processor")

    doc = DoclingDocument(name="lossless")
    doc.add_title(text="여비세칙")
    doc.add_text(label=DocItemLabel.TEXT, text="제정 2005. 11. 23")
    chapter = doc.add_heading(text="제1장 총칙", level=1)
    article = doc.add_heading(text="제1조 목적", level=2, parent=chapter)
    doc.add_text(label=DocItemLabel.TEXT, text="이 세칙은 여비 기준을 정한다.", parent=article)
    same = doc.add_heading(text="가", level=1)
    doc.add_text(label=DocItemLabel.TEXT, text="가가가가가", parent=same)

    sources = [t.text.strip() for t in doc.texts if (t.text or "").strip()]
    for chunk_mode in ("split_only", "resize_all"):
        vectors = _chunk(doc.model_dump(mode="json"), chunk_mode=chunk_mode)
        joined = "\n".join(v.text for v in vectors)
        missing = [s for s in sources if s not in joined]
        assert not missing, f"{chunk_mode} 에서 유실: {missing}"


@pytest.mark.unit
def test_repeated_body_text_is_not_deduped():
    """같은 섹션 안에서 반복되는 본문은 반복 횟수만큼 남는다 — 개수 기반 무손실 가드.

    존재 여부(`s not in joined`)만 보는 test_no_item_text_is_lost 는 이 부류를 못 잡는다.
    실제로 본문까지 중복 제거하던 시절 상품요약서 md 에서 아이템 65개 중 36개가 사라졌고,
    그 테스트는 통과했다. 반복은 정당한 원문 구조다(같은 특이사항 문단이 여러 쪽에 실린다).

    섹션이 chunk_size 로 쪼개지면 반복이 서로 다른 그룹으로 흩어져 증상이 감춰지므로,
    한 그룹에 다 들어가도록 chunk_size 를 넉넉히 준다.
    """
    pytest.importorskip("facade.chunking_processor")

    repeated = "해당사항 없음"
    n = 4

    doc = DoclingDocument(name="repeated_body")
    section = doc.add_heading(text="제1조 목적", level=1)
    for _ in range(n):
        doc.add_text(label=DocItemLabel.TEXT, text=repeated, parent=section)
        doc.add_text(label=DocItemLabel.TEXT, text="사이에 끼는 다른 문장.", parent=section)

    for chunk_mode in ("split_only", "resize_all"):
        vectors = _chunk(doc.model_dump(mode="json"), chunk_mode=chunk_mode, chunk_size=10000)
        joined = "\n".join(v.text for v in vectors)
        assert joined.count(repeated) == n, (
            f"{chunk_mode}: 반복 본문이 {joined.count(repeated)}회만 남음(기대 {n})"
        )
        # 섹션헤더는 여전히 HEADER 라인 + 본문 1회까지만 (헤더 dedup 은 유지된다)
        bodies = [v.text.partition("\n")[2] for v in vectors]
        assert sum(b.count("제1조 목적") for b in bodies) <= 1


@pytest.mark.unit
def test_heading_equals_body_text_preserved():
    """본문이 헤더 문자열과 같거나 헤더 문자열만으로 구성돼도 본문이 사라지지 않는다."""
    pytest.importorskip("facade.chunking_processor")

    for heading, body in (("할인", "할인"), ("가", "가가가가가")):
        doc = DoclingDocument(name="same_text")
        h = doc.add_heading(text=heading, level=1)
        doc.add_text(label=DocItemLabel.TEXT, text=body, parent=h)
        nxt = doc.add_heading(text="다음", level=1)
        doc.add_text(label=DocItemLabel.TEXT, text="보존되는 본문", parent=nxt)

        vectors = _chunk(doc.model_dump(mode="json"))
        joined = "\n".join(v.text for v in vectors)
        assert body in joined, f"본문 {body!r} 유실 (heading={heading!r})"
        assert "보존되는 본문" in joined


@pytest.mark.unit
def test_title_only_chunk_not_lost():
    """문서 선두 TITLE 청크가 병합으로 사라지지 않는다(무손실 가드).

    _is_section_header 는 TITLE 도 포함하므로 유형 판정만으로는 병합 대상이 되는데,
    TITLE 은 headings 에 안 실릴 수 있어 그대로 병합하면 텍스트가 어디에도 안 남는다.
    """
    pytest.importorskip("facade.chunking_processor")

    doc = DoclingDocument(name="title_only")
    doc.add_title(text="독립 문서 제목")
    section = doc.add_heading(text="제1장", level=1)
    doc.add_text(label=DocItemLabel.TEXT, text="장 본문입니다.", parent=section)

    joined = "\n".join(v.text for v in _chunk(doc.model_dump(mode="json")))
    assert "독립 문서 제목" in joined
    assert "장 본문입니다." in joined


@pytest.mark.unit
def test_sibling_sections_are_not_rendered_as_parent_child():
    """형제 섹션을 한 청크에 담아도 부모-자식 경로로 표기하지 않는다."""
    pytest.importorskip("facade.chunking_processor")

    doc = DoclingDocument(name="siblings")
    top = doc.add_heading(text="상품 안내", level=1)
    for name in ("우대금리 조건", "가입 제한", "수수료 안내"):
        node = doc.add_heading(text=name, level=2, parent=top)
        doc.add_text(label=DocItemLabel.TEXT, text=f"{name} 본문", parent=node)

    header = _header_of(_chunk(doc.model_dump(mode="json"),
                               chunk_mode="resize_all", chunk_size=10000)[0])
    # 공통 조상은 한 번만, 형제는 PATH_SEP 로 나열된다.
    assert header.startswith("상품 안내" + HEADER_SEP), header
    assert PATH_SEP in header, header
    # 형제가 부모-자식처럼 직접 이어지면 안 된다.
    assert f"우대금리 조건{HEADER_SEP}가입 제한" not in header, header


@pytest.mark.unit
def test_prefix_paths_are_collapsed():
    """`A` 와 `A > B` 가 함께 오면 최장 경로 하나로 접는다."""
    pytest.importorskip("facade.chunking_processor")

    doc = DoclingDocument(name="prefix")
    top = doc.add_heading(text="삼성카드", level=1)
    doc.add_text(label=DocItemLabel.TEXT, text="문서 서두", parent=top)
    child = doc.add_heading(text="카드상세", level=2, parent=top)
    doc.add_text(label=DocItemLabel.TEXT, text="상세 본문", parent=child)

    header = _header_of(_chunk(doc.model_dump(mode="json"),
                               chunk_mode="resize_all", chunk_size=10000)[0])
    assert header == f"삼성카드{HEADER_SEP}카드상세", header


@pytest.mark.unit
def test_many_sibling_paths_are_capped():
    """경로가 많으면 리프를 상한까지만 나열하고 나머지는 접는다(헤더 폭증 방지).

    실측: 71경로를 전부 나열하면 헤더가 3,239자가 되어 청크가 chunk_size 를 30% 초과했다.
    """
    cp = pytest.importorskip("facade.chunking_processor")

    doc = DoclingDocument(name="many")
    top = doc.add_heading(text="제1장 총칙", level=1)
    for i in range(1, 40):
        node = doc.add_heading(text=f"제{i}조", level=2, parent=top)
        doc.add_text(label=DocItemLabel.TEXT, text=f"제{i}조 본문", parent=node)

    header = _header_of(_chunk(doc.model_dump(mode="json"),
                               chunk_mode="resize_all", chunk_size=100000)[0])
    assert "외" in header and "개" in header, header
    assert header.count(PATH_SEP) < cp._CHUNK_PATH_MAX_LEAVES, header


@pytest.mark.unit
@pytest.mark.parametrize("chunk_size", [1024, 2048])
def test_single_oversized_text_item_is_split(chunk_size):
    """아이템 하나가 예산보다 커도 내부 분할되어 chunk_size 를 지킨다."""
    cp = pytest.importorskip("facade.chunking_processor")
    effective = cp._clamp_chunk_size(chunk_size)

    doc = DoclingDocument(name="huge_item")
    section = doc.add_heading(text="제1조 목적", level=1)
    doc.add_text(label=DocItemLabel.TEXT, text="본문내용 " * 1000, parent=section)

    for chunk_mode in ("split_only", "resize_all"):
        vectors = _chunk(doc.model_dump(mode="json"),
                         chunk_size=chunk_size, chunk_mode=chunk_mode)
        over = [len(v.text) for v in vectors if len(v.text) > effective]
        assert not over, f"{chunk_mode} chunk_size={effective} 초과: {over[:5]}"
        # 잘린 조각들이 원문을 모두 담고 있어야 한다.
        assert "본문내용" in "\n".join(v.text for v in vectors)


# ── 분할 조각의 섹션 문맥 승계 (#360) ────────────────────────────────────────
# custom_fields 의 text_from 이 원천(JSON/HTML/평문)을 `## 제목` 마크다운으로 펴 놓으면,
# 청커는 그 헤딩을 우선 분리자로 써서 섹션 경계에서 자르고, 섹션 하나가 chunk_size 를 넘어
# 중간에서 잘린 경우에만 직전 제목을 다시 붙인다.

@pytest.mark.unit
def test_carry_over_section_headings_reattaches_last_title():
    """헤딩 없이 시작하는 뒷 조각에 직전 섹션 제목을 `(이어서)` 로 붙인다.

    이게 없으면 "8월 17일 2544만으로 감소" 같은 조각이 **무엇에 대한 값인지 알 수 없는**
    상태로 검색에 노출되어, 그 조각만 뽑혔을 때 근거로 쓸 수 없다.
    """
    pieces = _carry_over_section_headings([
        "## 거래량 변화\n8월 13일 3408만",
        "8월 17일 2544만으로 감소",
        "## 종합 진단\n관망",
    ])
    assert pieces[1] == "## 거래량 변화 (이어서)\n8월 17일 2544만으로 감소"
    assert pieces[0].startswith("## 거래량 변화\n")     # 첫 조각은 그대로
    assert pieces[2].startswith("## 종합 진단")          # 헤딩으로 시작하면 손대지 않는다


@pytest.mark.unit
def test_carry_over_section_headings_tracks_nested_level():
    """가장 최근 헤딩을 물려받는다 — 레벨(##/###)도 그대로 유지한다."""
    pieces = _carry_over_section_headings([
        "## 기술적 지표\n### RSI\n8월 13일 47.72",
        "8월 17일 47.44로 하락",
    ])
    assert pieces[1].startswith("### RSI (이어서)\n")


@pytest.mark.unit
def test_carry_over_section_headings_noop_without_headings():
    """헤딩이 없는 평문 입력에서는 아무것도 하지 않는다(회귀 가드)."""
    pieces = ["첫 문단입니다.", "둘째 문단입니다."]
    assert _carry_over_section_headings(list(pieces)) == pieces
