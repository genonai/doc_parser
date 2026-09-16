"""고객 확장 훅 단위 테스트 (#363 08-3).

훅은 "고객이 코어를 안 고치고도 새 원천을 처리한다" 는 이번 리팩터링의 목적 그 자체라,
배선이 조용히 끊겨도 골든은 차이 0 으로 통과한다(아무 것도 안 하는 훅이므로).
그래서 배선을 직접 단정한다.

네트워크·LLM 을 부르지 않는다. 훅 게이트와 호출 지점만 본다.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

core_parser = pytest.importorskip("processing.core.parser")
core_chunker = pytest.importorskip("processing.core.chunker")
parser_facade = pytest.importorskip("facade.parser_processor")
chunker_facade = pytest.importorskip("facade.chunking_processor")


def _bare(cls):
    """__init__ 을 우회한 최소 인스턴스. 훅 배선만 보므로 설정이 필요 없다."""
    return object.__new__(cls)


# ---------------------------------------------------------------------------
# edit_input 게이트 — 안 건드리면 파생 입력을 만들지 않는다
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_untouched_hook_reports_no_change():
    """core 기본 구현 그대로면 '비활성' 이고 값도 그대로다."""
    proc = _bare(core_parser.ParserCore)
    data = {"a": 1}
    assert proc._edit_input_active() is False
    assert await proc._hook_edit_input(".json", {}, data) == (data, False)


@pytest.mark.asyncio
async def test_passthrough_override_is_active_but_reports_no_change():
    """출고 템플릿처럼 그대로 돌려주는 훅은 활성이지만 '안 바뀜' 이다.

    이 구분이 산출 동일성을 지킨다 — 활성이어도 같은 객체를 돌려주면 core 는
    파생 입력을 만들지 않는다.
    """
    proc = _bare(parser_facade.DocumentProcessor)
    data = {"a": 1}
    assert proc._edit_input_active() is True
    assert await proc._hook_edit_input(".json", {}, data) == (data, False)


@pytest.mark.asyncio
async def test_reshaping_hook_reports_change():
    class _P(parser_facade.DocumentProcessor):
        def edit_input(self, ext, doc_type, data, work_dir=None):
            if doc_type == "nested":
                return {"items": [i for g in data["groups"] for i in g["items"]]}
            return data

    proc = _bare(_P)
    src = {"groups": [{"items": [1, 2]}, {"items": [3]}]}
    out, changed = await proc._hook_edit_input(".json", {"doc_type": "nested"}, src)
    assert changed is True and out == {"items": [1, 2, 3]}
    # 대상 doc_type 이 아니면 손대지 않는다 — 게이팅이 없으면 모든 JSON 이 바뀐다.
    assert await proc._hook_edit_input(".json", {"doc_type": "other"}, src) == (src, False)


# ---------------------------------------------------------------------------
# .json 입구 — 훅이 실제로 그 자리에서 불린다
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_json_payload_hook_is_called_at_the_single_entry(tmp_path: Path):
    src = tmp_path / "a.json"
    src.write_text(json.dumps({"groups": [{"items": [1]}, {"items": [2]}]}), encoding="utf-8")

    class _P(parser_facade.DocumentProcessor):
        def edit_input(self, ext, doc_type, data, work_dir=None):
            return {"items": [i for g in data["groups"] for i in g["items"]]}

    assert await _bare(_P)._load_json_payload(str(src), "any") == {"items": [1, 2]}


@pytest.mark.asyncio
async def test_broken_json_reaches_the_hook_as_raw_text(tmp_path: Path):
    """JSONL 처럼 json.loads 가 실패하는 원천은 원문 str 로 훅에 온다."""
    src = tmp_path / "a.json"
    src.write_text('{"v":1}\n{"v":2}\n', encoding="utf-8")

    class _P(parser_facade.DocumentProcessor):
        def edit_input(self, ext, doc_type, data, work_dir=None):
            assert isinstance(data, str)
            return {"rows": [json.loads(ln) for ln in data.splitlines() if ln.strip()]}

    assert await _bare(_P)._load_json_payload(str(src), "any") == {"rows": [{"v": 1}, {"v": 2}]}


@pytest.mark.asyncio
async def test_broken_json_without_hook_still_fails(tmp_path: Path):
    """훅이 손대지 않으면 종전대로 입력 오류다(하위호환)."""
    src = tmp_path / "a.json"
    src.write_text("{not json", encoding="utf-8")
    with pytest.raises(core_parser.GenosServiceException):
        await _bare(parser_facade.DocumentProcessor)._load_json_payload(str(src), "any")


@pytest.mark.asyncio
async def test_raw_control_char_in_string_is_read(tmp_path: Path):
    """문자열 안의 날 제어문자는 core 가 흡수한다 — 인코딩과 같은 층의 문제다.

    CMS 원천이 HTML 본문을 escape 없이 JSON 문자열에 담아 보내면 strict 모드의
    json.loads 가 "Invalid control character" 로 거부한다. 실측: 카드 상품 원천의
    htmlList[0].feeUrl 안에 연회비 표 HTML 이 통째로 들어 있었다.
    """
    html = '<h4 class="tit">\n\t국내외겸용\r</h4>'
    body = json.dumps({"htmlList": [{"feeUrl": html}]}, ensure_ascii=False)
    # 이스케이프를 원문으로 되돌려 원천이 오는 상태를 그대로 만든다.
    body = body.replace("\\n", "\n").replace("\\t", "\t").replace("\\r", "\r")
    src = tmp_path / "a.json"
    src.write_text(body, encoding="utf-8", newline="")

    payload = await _bare(parser_facade.DocumentProcessor)._load_json_payload(str(src), "any")
    assert payload["htmlList"][0]["feeUrl"] == html


@pytest.mark.asyncio
async def test_cp949_json_is_read_without_customer_code(tmp_path: Path):
    """인코딩은 core 가 흡수한다 — 훅은 구조 문제만 다룬다."""
    src = tmp_path / "a.json"
    src.write_bytes(json.dumps({"n": "한글"}, ensure_ascii=False).encode("cp949"))
    assert await _bare(parser_facade.DocumentProcessor)._load_json_payload(str(src)) == {"n": "한글"}


# ---------------------------------------------------------------------------
# 파생 입력 파일 — 확장자를 지켜야 docling 이 포맷을 판정한다
# ---------------------------------------------------------------------------

def test_write_derived_keeps_extension(tmp_path: Path):
    out = core_parser._write_derived(str(tmp_path), "/src/doc.html.parsed", ".md", "# hi")
    assert Path(out).name == "doc.html.md"
    assert Path(out).read_text(encoding="utf-8") == "# hi"


# ---------------------------------------------------------------------------
# 청커 — 네 단계가 __call__ 에 보이고 실제로 불린다
# ---------------------------------------------------------------------------

def test_load_input_classifies_both_shapes():
    proc = _bare(chunker_facade.DocumentProcessor)
    proc.setup_logging = lambda *_a, **_k: None
    proc._log_level = 4
    proc._gr_cfg = type("C", (), {"masking_enabled": False})()

    src = proc.load_input("", document={"elements": [{"content": "a"}]})
    assert (src.kind, src.data) == ("parse", [{"content": "a"}])
    src = proc.load_input("", document={"document": {"x": 1}})
    assert (src.kind, src.data) == ("docling", {"x": 1})


@pytest.mark.asyncio
async def test_chunker_edit_input_and_edit_output_are_wired_into_call():
    seen = {}

    class _P(chunker_facade.DocumentProcessor):
        def edit_input(self, kind, data, **kwargs):
            seen["pre"] = kind
            return data + [{"content": "added"}]

        def edit_output(self, vectors, **kwargs):
            seen["post"] = len(vectors)
            return vectors[:1]

        async def chunks_to_vector_metas(self, job, chunks, converted_pdf_path=None):
            seen["to_chunk"] = len(chunks)
            return ["v1", "v2"]

    proc = _bare(_P)
    proc.setup_logging = lambda *_a, **_k: None
    proc._log_level = 4
    proc._gr_cfg = type("C", (), {"masking_enabled": False})()

    out = await proc(None, "", document={"elements": [{"content": "a"}]})
    assert seen == {"pre": "parse", "to_chunk": 2, "post": 2}
    assert out == ["v1"]


@pytest.mark.asyncio
async def test_parser_edit_output_is_wired_into_call(tmp_path: Path):
    class _P(parser_facade.DocumentProcessor):
        async def _call_route(self, job):
            return {"elements": [], "metadata": {}}

        def edit_output(self, ext, doc_type, result):
            result["metadata"]["src"] = f"{ext}:{doc_type}"
            return result

    proc = _routable(_P)
    proc._ext_aliases = {".parsed": ".md"}
    # 별칭이 적용되면 core 가 표준 확장자 사본을 만든다 — 실제 파일이 있어야 한다.
    src = tmp_path / "a.parsed"
    src.write_text("# hi\n", encoding="utf-8")
    out = await proc(None, str(src), doc_type="T")
    # 확장자는 별칭이 반영되고(.parsed -> .md), doc_type 은 정규화(소문자)되어 온다.
    # 훅에서 doc_type 을 비교할 때 대문자로 적으면 영영 안 맞는다.
    assert out["metadata"]["src"] == ".md:t"


# ---------------------------------------------------------------------------
# 엑셀 격자 훅 — 라이브러리를 고르는 건 고객이다
# ---------------------------------------------------------------------------

xp = pytest.importorskip("genon.preprocessor.converters.xlsx_processor")


def test_normalize_sheets_accepts_every_documented_shape():
    grid = [["a", "b"], ["1", "2"]]
    assert xp.normalize_sheets({"S": grid}) == {"S": grid}
    assert xp.normalize_sheets(grid) == {"table_1": grid}
    assert xp.normalize_sheets({"S": [{"a": 1, "b": 2}]}) == {"S": [["a", "b"], ["1", "2"]]}


def test_normalize_sheets_accepts_dataframe_without_importing_it():
    """core 는 pandas 를 import 하지 않는다 — 덕타이핑으로 받는다."""
    pd = pytest.importorskip("pandas")
    df = pd.DataFrame([[1, 2]], columns=["a", "b"])
    assert xp.normalize_sheets({"S": df}) == {"S": [["a", "b"], ["1", "2"]]}
    assert xp.normalize_sheets(df) == {"table_1": [["a", "b"], ["1", "2"]]}


def test_normalize_sheets_rejects_unknown_shape():
    with pytest.raises(TypeError):
        xp.normalize_sheets(object())


def test_merges_survive_value_only_edits_and_die_on_reshape():
    """결정 D6 — 행·열 개수가 그대로면 병합 좌표가 유효하므로 유지한다.

    "무조건 버린다" 로 하면 값만 고친 사용자가 멀티헤더 자동판정을 잃는다.
    """
    merges = [(0, 0, 0, 1)]
    original = {"S": ([["연락처", "연락처"], ["전화", "팩스"], ["02-1", "02-2"]], merges)}

    same_dims = {"S": [["연락처", "연락처"], ["전화", "팩스"], ["021", "022"]]}
    assert xp.merge_hook_sheets(original, same_dims)["S"][1] == merges

    fewer_rows = {"S": [["전화", "팩스"], ["021", "022"]]}
    assert xp.merge_hook_sheets(original, fewer_rows)["S"][1] == []


def test_sheets_to_xlsx_round_trips(tmp_path: Path):
    """docling 모드는 파일을 요구한다. 격자 -> 파일 -> 격자가 같아야 한다."""
    sheets = {"본문": [["a", "b"], ["1", "2"]]}
    out = xp.sheets_to_xlsx(sheets, str(tmp_path))
    assert xp.load_sheets(out) == sheets


def test_injected_grid_reaches_load_tables(tmp_path: Path):
    """훅이 돌려준 격자가 표 감지까지 실제로 흘러간다."""
    src = xp.sheets_to_xlsx({"S": [["머리", "말"], ["진짜", "헤더"], ["1", "2"]]}, str(tmp_path))
    # 앞 1행을 로고/안내문으로 보고 지운다 — 고객이 훅에서 하는 전형적인 일.
    hooked = xp.merge_hook_sheets(
        xp._load_sheets_with_merges(src), {"S": [["진짜", "헤더"], ["1", "2"]]})
    tables = xp.load_tables(src, sheets_with_merges=hooked)
    assert [t["headers"] for t in tables] == [["진짜", "헤더"]]


@pytest.mark.asyncio
async def test_grid_hook_is_skipped_when_the_workbook_cannot_be_read():
    """원본을 못 읽으면 훅을 건너뛰고 (None, False) 다.

    여기서 먼저 죽으면 오류 메시지와 시점이 종전과 달라진다 — 실제 오류는
    아래 파싱 경로가 낸다.
    """
    proc = _bare(parser_facade.DocumentProcessor)
    assert await proc._hook_tabular_sheets("없는파일.xlsx", "/tmp") == (None, False)


@pytest.mark.asyncio
async def test_unchanged_grid_is_reused_to_avoid_a_second_read(tmp_path: Path):
    """훅이 손대지 않아도 이미 읽은 격자를 넘긴다 — 같은 함수 산출이라 동일하다."""
    src = xp.sheets_to_xlsx({"S": [["a", "b"], ["1", "2"]]}, str(tmp_path))
    proc = _bare(parser_facade.DocumentProcessor)
    sheets, changed = await proc._hook_tabular_sheets(src, str(tmp_path))
    assert changed is False
    assert sheets == xp._load_sheets_with_merges(src)


# ---------------------------------------------------------------------------
# edit_output 가 청크에 닿는 통로 (08-B 가 드러낸 구멍)
# ---------------------------------------------------------------------------

tb = pytest.importorskip("processing.core.toolbox")


def test_set_chunk_metadata_writes_into_elements_for_record_paths():
    result = {"elements": [{"content": "a"}, {"content": "b", "metadata": {"x": 1}}]}
    tb.set_chunk_metadata(result, {"GROUP_C": "SSS"})
    assert [e["metadata"]["GROUP_C"] for e in result["elements"]] == ["SSS", "SSS"]
    assert result["elements"][1]["metadata"]["x"] == 1   # 기존 값은 지우지 않는다


def test_set_chunk_metadata_reaches_the_docling_document():
    """봉투의 metadata 에만 쓰면 청커가 못 읽는다 — KeyValueItem 으로 실려야 경계를 넘는다."""
    dc = pytest.importorskip("docling_core.types.doc")
    ft = pytest.importorskip("genon.preprocessor.processing.enrichment.field_transforms")

    doc = dc.DoclingDocument(name="s")
    result = {"document": doc.model_dump(mode="json")}
    tb.set_chunk_metadata(result, {"SRC": "CRM"})

    restored = dc.DoclingDocument.model_validate(result["document"])
    assert ft.extract_metadata_from_document(restored).get("SRC") == "CRM"


def test_reserved_chunk_keys_are_exposed():
    """body.once / body.fields 를 훅에서 지정하려면 이 이름이 필요하다."""
    assert tb.FIRST_CHUNK_FIELDS_KEY == "first_chunk_fields"
    assert tb.BODY_FIELDS_KEY == "body_fields"
    assert tb.CHUNK_PREFIX_FIELDS_KEY == "chunk_prefix_fields"
    assert tb.FIELD_LABELS_KEY == "field_labels"


# ---------------------------------------------------------------------------
# 훅 계약 — 요청 파라미터 전달과 async 훅 (#363 09)
#
# 두 가지를 동시에 지켜야 한다.
#   · **kwargs 를 선언한 훅은 요청 파라미터를 받는다 (부서·언어처럼 요청마다 달라지는 값을
#     self 에 두면 싱글턴 프로세서에서 요청끼리 섞인다)
#   · **kwargs 를 선언하지 않은 기존 훅은 인자가 늘지 않는다 (고객이 보관한 patch 가
#     릴리스 갱신에서 깨지면 안 된다)
# ---------------------------------------------------------------------------

hooks_mod = pytest.importorskip("genon.preprocessor.processing.common.hooks")


def test_hook_without_var_keyword_receives_nothing_extra():
    def old_style(ext, doc_type, data, work_dir=None):
        return data

    assert hooks_mod.hook_kwargs(old_style, {"doc_type": "t", "tenant": "A"}) == {}


def test_hook_with_var_keyword_receives_request_params_only():
    """자리로 이미 받는 이름(doc_type)은 빼야 중복 인자로 죽지 않는다."""
    def new_style(ext, doc_type, data, work_dir=None, **kwargs):
        return data

    got = hooks_mod.hook_kwargs(
        new_style, {"doc_type": "t", "tenant": "A", "_sensitive_infos": [1]})
    assert got == {"tenant": "A"}   # 내부 배관용 키(_로 시작)도 넘기지 않는다


@pytest.mark.asyncio
async def test_edit_input_receives_request_params(tmp_path: Path):
    src = tmp_path / "a.json"
    src.write_text(json.dumps({"v": 1}), encoding="utf-8")
    seen = {}

    class _P(parser_facade.DocumentProcessor):
        def edit_input(self, ext, doc_type, data, work_dir=None, **kwargs):
            seen.update(kwargs)
            return data

    await _bare(_P)._hook_edit_input(".json", {"doc_type": "t", "tenant": "A"}, {"v": 1})
    assert seen == {"tenant": "A"}


@pytest.mark.asyncio
async def test_json_path_also_passes_request_params(tmp_path: Path):
    """.json 은 훅 호출부가 따로라 doc_type 만 넘기던 자리다 — 여기도 같아야 한다."""
    src = tmp_path / "a.json"
    src.write_text(json.dumps({"v": 1}), encoding="utf-8")
    seen = {}

    class _P(parser_facade.DocumentProcessor):
        def edit_input(self, ext, doc_type, data, work_dir=None, **kwargs):
            seen.update(kwargs)
            return data

    await _bare(_P)._load_json_payload(str(src), "t", tenant="A")
    assert seen == {"tenant": "A"}


@pytest.mark.asyncio
async def test_legacy_edit_input_signature_still_works():
    """**kwargs 없는 기존 훅도 그대로 불린다(하위호환)."""
    class _P(parser_facade.DocumentProcessor):
        def edit_input(self, ext, doc_type, data, work_dir=None):
            return {"reshaped": True}

    out, changed = await _bare(_P)._hook_edit_input(
        ".json", {"doc_type": "t", "tenant": "A"}, {"v": 1})
    assert (out, changed) == ({"reshaped": True}, True)


@pytest.mark.asyncio
async def test_async_edit_input_is_awaited():
    """사내 API 조회처럼 외부 호출이 필요한 훅을 동기로 쓰면 이벤트 루프가 막힌다."""
    class _P(parser_facade.DocumentProcessor):
        async def edit_input(self, ext, doc_type, data, work_dir=None, **kwargs):
            return {"awaited": True}

    out, changed = await _bare(_P)._hook_edit_input(".json", {"doc_type": "t"}, {"v": 1})
    assert (out, changed) == ({"awaited": True}, True)


@pytest.mark.asyncio
async def test_async_edit_output_is_awaited():
    class _P(parser_facade.DocumentProcessor):
        async def _call_route(self, job):
            return {"elements": [], "metadata": {}}

        async def edit_output(self, ext, doc_type, result, **kwargs):
            result["metadata"]["tenant"] = kwargs.get("tenant")
            return result

    proc = _routable(_P)
    out = await proc(None, "/x/a.md", doc_type="T", tenant="A")
    assert out["metadata"]["tenant"] == "A"


@pytest.mark.asyncio
async def test_async_chunk_hooks_are_awaited():
    class _P(chunker_facade.DocumentProcessor):
        async def edit_input(self, kind, data, **kwargs):
            return data + [{"content": kwargs.get("tenant", "")}]

        async def edit_output(self, vectors, **kwargs):
            return vectors[:1]

        async def chunks_to_vector_metas(self, job, chunks, converted_pdf_path=None):
            return [c.text for c in chunks]

    proc = _bare(_P)
    proc.setup_logging = lambda *_a, **_k: None
    proc._log_level = 4
    proc._gr_cfg = type("C", (), {"masking_enabled": False})()

    out = await proc(None, "", document={"elements": [{"content": "a"}]}, tenant="A")
    assert out == ["a"]     # edit_output 가 잘라낸 결과 — edit_input 는 "A" 를 더했다


# ---------------------------------------------------------------------------
# 사이트가 바꾸는 값이 facade 에 있는가 (#363 09)
# ---------------------------------------------------------------------------

hp = pytest.importorskip("genon.preprocessor.processing.chunking.header_path")


def test_header_prefix_comes_from_the_chunker_class():
    """접두는 구분자와 같은 축이다 — core 상수가 아니라 청커 클래스가 정한다."""
    class _C(chunker_facade.GenosSmartChunker):
        CHUNK_HEADER_PREFIX = "섹션: "

    line = core_chunker._build_header_line(["A > B"], True, _C)
    assert line == "섹션: A > B\n"
    # 빈 문자열이면 경로만 붙는다.
    class _N(chunker_facade.GenosSmartChunker):
        CHUNK_HEADER_PREFIX = ""
    assert core_chunker._build_header_line(["A > B"], True, _N) == "A > B\n"


def test_header_prefix_default_is_unchanged():
    """출고 기본값은 종전 그대로다(기존 색인과 어긋나면 안 된다)."""
    assert hp.DEFAULT_HEADER_PREFIX == "HEADER: "
    assert core_chunker._build_header_line(
        ["A > B"], True, chunker_facade.GenosSmartChunker) == "HEADER: A > B\n"


def test_size_estimation_uses_the_same_prefix():
    """크기 산정과 실제 부착이 다른 문자열을 보면 청크가 chunk_size 를 넘는다."""
    class _C(chunker_facade.GenosSmartChunker):
        CHUNK_HEADER_PREFIX = "섹션: "

    chunker = _C.model_construct(chunk_prefix_text="")
    assert chunker._header_line(["A > B"], True) == core_chunker._build_header_line(
        ["A > B"], True, _C)


def test_min_chunk_size_is_configurable():
    """docling 경로 하한. 임베딩 입력이 짧은 사이트는 낮춰야 한다."""
    assert core_chunker._clamp_chunk_size(300) == 1024          # 기본 하한
    assert core_chunker._clamp_chunk_size(300, 256) == 300      # 낮춘 하한
    assert core_chunker._clamp_chunk_size(300, 0) == 300        # 보정 안 함
    assert core_chunker._clamp_chunk_size(0, 256) == 0          # 0=분할 안 함은 그대로


def test_row_categories_are_extendable_from_the_facade():
    """새 category 를 만들 때 core 두 곳을 고치던 것을 facade 한 줄로 바꾼다."""
    assert "custom_fields_row" in chunker_facade.DocumentProcessor.ROW_CATEGORIES

    class _P(chunker_facade.DocumentProcessor):
        ROW_CATEGORIES = frozenset(chunker_facade.DocumentProcessor.ROW_CATEGORIES) | {"crm_row"}

    proc = _bare(_P)
    routed = {}

    def _fake_rows(els, **kw):
        routed["rows"] = len(els)
        return []

    proc._split_rows = _fake_rows
    proc._text_variant_options = lambda **kw: {}
    proc._text_cleanup = "off"
    proc._text_cleanup_rules = ()
    job = core_chunker.jb.ChunkJob(
        kind="parse", data=[{"category": "crm_row", "content": "a"}], params={})
    proc.split_records(job)
    assert routed == {"rows": 1}


# ---------------------------------------------------------------------------
# 코드를 꽂는 자리 — 커스텀 라우트와 변환기 등록 (#363 09 A군)
#
# 셋 다 "이미 되는데 문서가 없던 것" 이라, 배선이 조용히 끊겨도 골든은 통과한다.
# 그래서 계약을 직접 단정한다.
# ---------------------------------------------------------------------------

def test_make_elements_fills_plumbing_fields():
    els = tb.make_elements(["첫 줄", {"content": "둘째", "page": 2}])
    assert [e["id"] for e in els] == [0, 1]
    assert [e["page"] for e in els] == [1, 2]
    assert all(e["category"] == "paragraph" and e["coordinates"] == [] for e in els)


def test_make_elements_passes_row_metadata_through():
    """행 1개 = 청크 1개 경로로 보내려면 category 와 metadata 가 그대로 실려야 한다."""
    els = tb.make_elements(
        [{"content": "본문", "metadata": {"ORDER_NO": "A1"}}], category="custom_fields_row")
    assert els[0]["category"] == "custom_fields_row"
    assert els[0]["metadata"] == {"ORDER_NO": "A1"}
    # 그 category 가 실제로 행 경로로 라우팅된다.
    assert els[0]["category"] in chunker_facade.DocumentProcessor.ROW_CATEGORIES


def test_make_elements_rejects_unknown_item_type():
    with pytest.raises(TypeError):
        tb.make_elements([object()])


def _routable(cls):
    """라우팅만 태우는 최소 인스턴스. 파싱 배관(enrichment)은 쓰지 않는다."""
    proc = _bare(cls)
    proc.setup_logging = lambda *_a, **_k: None
    proc._log_level = 4
    proc._ext_aliases = {}
    proc._intel = type("_I", (), {
        "_normalize_runtime_kwargs": staticmethod(lambda kw: kw),
        "_configure_runtime_image_mode": staticmethod(lambda kw: None),
    })()
    return proc


@pytest.mark.asyncio
async def test_facade_can_define_its_own_route(tmp_path: Path):
    """ROUTES 는 메서드 이름만 갖는다 — 핸들러를 facade 파일에 둘 수 있다."""
    src = tmp_path / "app.log"
    src.write_text("a\nb\n", encoding="utf-8")

    class _P(parser_facade.DocumentProcessor):
        ROUTES = (((".log",), "route_log"),) + parser_facade.DocumentProcessor.ROUTES

        async def route_log(self, file_path, ext, ctx, **kwargs):
            lines = [l for l in tb.read_text_with_fallback(file_path).splitlines() if l.strip()]
            return {"elements": tb.make_elements(lines)}

    out = await _routable(_P)(None, str(src))
    assert [e["content"] for e in out["elements"]] == ["a", "b"]
    # 나머지 응답 키는 core 가 채운다 — 라우트는 elements 만 만들면 된다.
    assert out["usage"] == {"pages": 0} and out["content"] == ""


@pytest.mark.asyncio
async def test_route_returning_none_falls_through(tmp_path: Path):
    """폴스루가 살아 있어야 '이 조건일 때만 내가 처리' 가 가능하다."""
    src = tmp_path / "app.log"
    src.write_text("x\n", encoding="utf-8")

    class _P(parser_facade.DocumentProcessor):
        ROUTES = (((".log",), "route_mine"), (None, "route_fallback"))

        async def route_mine(self, file_path, ext, ctx, **kwargs):
            return None

        async def route_fallback(self, file_path, ext, ctx, **kwargs):
            return {"elements": tb.make_elements(["fallback"])}

    out = await _routable(_P)(None, str(src))
    assert out["elements"][0]["content"] == "fallback"


@pytest.mark.asyncio
async def test_route_with_job_signature_receives_the_job(tmp_path: Path):
    """새 시그니처 route_x(self, job) 은 job 하나를 받는다. 옛 시그니처와 한 ROUTES 에 섞여도 된다."""
    src = tmp_path / "app.log"
    src.write_text("a\n", encoding="utf-8")
    seen = {}

    class _P(parser_facade.DocumentProcessor):
        ROUTES = (((".log",), "route_log"),) + parser_facade.DocumentProcessor.ROUTES

        async def route_log(self, job):
            seen.update(ext=job.ext, file_path=job.file_path, source=job.source,
                        doc_type=job.doc_type, tenant=job.params.get("tenant"),
                        has_ctx="enrichment_context" in job.ctx)
            return {"elements": tb.make_elements(["job"])}

    out = await _routable(_P)(None, str(src), doc_type="notice", tenant="A")
    assert out["elements"][0]["content"] == "job"
    assert seen == {"ext": ".log", "file_path": str(src), "source": str(src),
                    "doc_type": "notice", "tenant": "A", "has_ctx": True}


@pytest.mark.asyncio
async def test_route_hwp_passes_the_same_arguments_as_before(tmp_path: Path):
    """route_hwp 는 job 시그니처로 옮겼다. 골든은 hwp 를 흔들리는 케이스로 빼므로 인자를 여기서 고정한다."""
    calls = {}

    class _P(parser_facade.DocumentProcessor):
        def _parse_hwp_hwpx(self, file_path, **kwargs):
            calls["parse"] = (file_path, kwargs)
            return "DOC"

        async def document_to_response(self, job, doc, clear_coordinates=False):
            calls["response"] = (job, doc, clear_coordinates)
            return {"elements": tb.make_elements(["hwp"])}

    params = {"doc_type": "t", "tenant": "A"}
    job = core_parser.jb.ParseJob(
        request=None, file_path=str(tmp_path / "a.hwp"), source=str(tmp_path / "alias.hwp"),
        ext=".hwp", doc_type="t", params=params,
        ctx={"enrichment_context": {}, "artifacts_source": None})
    await _bare(_P).route_hwp(job)
    # 파싱 대상은 원본이 아니라 source(별칭 사본·파생 파일)다 — 옛 run 이 넘기던 file_path 와 같다.
    assert calls["parse"] == (str(tmp_path / "alias.hwp"), params)
    assert calls["response"] == (job, "DOC", False)


@pytest.mark.asyncio
async def test_legacy_route_ctx_carries_the_job(tmp_path: Path):
    """옛 시그니처 라우트도 ctx["job"] 으로 같은 job 에 닿는다 — 문서형 응답이 이것으로 훅 메소드를 부른다."""
    src = tmp_path / "app.log"
    src.write_text("a\n", encoding="utf-8")
    seen = {}

    class _P(parser_facade.DocumentProcessor):
        ROUTES = (((".log",), "route_log"),) + parser_facade.DocumentProcessor.ROUTES

        async def route_log(self, file_path, ext, ctx, **kwargs):
            seen["job"] = ctx.get("job")
            return {"elements": tb.make_elements(["x"])}

    await _routable(_P)(None, str(src), doc_type="notice")
    assert seen["job"].ext == ".log" and seen["job"].ctx.get("job") is seen["job"]


@pytest.mark.asyncio
async def test_job_temp_dirs_are_removed_when_the_request_ends(tmp_path: Path):
    """라우트가 job.temp_dir() 로 만든 파생 파일은 요청이 끝나면 지워진다 — 성공·실패 모두."""
    src = tmp_path / "app.log"
    src.write_text("a\n", encoding="utf-8")
    made = []

    class _P(parser_facade.DocumentProcessor):
        ROUTES = (((".log",), "route_log"),) + parser_facade.DocumentProcessor.ROUTES

        async def route_log(self, job):
            made.append(Path(job.temp_dir("t_")))
            assert made[-1].is_dir()
            if job.params.get("fail"):
                raise RuntimeError("boom")
            return {"elements": tb.make_elements(["x"])}

    await _routable(_P)(None, str(src))
    with pytest.raises(RuntimeError):
        await _routable(_P)(None, str(src), fail=True)
    assert len(made) == 2 and not any(p.exists() for p in made)


# ---------------------------------------------------------------------------
# doc_type 별 설정 오버레이 — 요청이 보낸 값이 가장 세다
# ---------------------------------------------------------------------------

def _overlay_processor(table, condition=None):
    class _P(parser_facade.DocumentProcessor):
        CONFIG_BY_DOC_TYPE = table
        ROUTES = (((".log",), "route_log"),) + parser_facade.DocumentProcessor.ROUTES

        def config_by_condition(self, job):
            return condition(job) if condition else {}

        async def route_log(self, job):
            _P.seen = dict(job.params)
            _P.seen["_config"] = job.config
            return {"elements": tb.make_elements(["x"])}

    return _P


@pytest.mark.asyncio
async def test_config_overlay_fills_only_what_the_request_did_not_send(tmp_path: Path):
    src = tmp_path / "a.log"
    src.write_text("a\n", encoding="utf-8")
    cls = _overlay_processor(
        {"notice": {"chunk_size": 500, "ocr_mode": "force"}},
        condition=lambda job: {"chunk_mode": "split_only"} if job.params.get("dept") == "IR" else {},
    )

    await _routable(cls)(None, str(src), doc_type="notice", dept="IR", ocr_mode="off")
    assert cls.seen["chunk_size"] == 500            # 표가 채운다
    assert cls.seen["chunk_mode"] == "split_only"   # 조건부 설정이 채운다
    assert cls.seen["ocr_mode"] == "off"            # 요청이 보낸 값이 이긴다
    # 적용된 값만 job.config 에 남는다 — 결과에서 되짚을 수 있어야 한다.
    assert cls.seen["_config"] == {"chunk_size": 500, "chunk_mode": "split_only"}


@pytest.mark.asyncio
async def test_config_overlay_accepts_config_file_paths(tmp_path: Path):
    """점 표기(설정 파일 경로)는 요청 파라미터 이름으로 옮겨진다.

    고객이 아는 이름은 매뉴얼에 적힌 설정 경로다. 그 이름으로 적어도 동작해야 한다.
    """
    src = tmp_path / "a.log"
    src.write_text("a\n", encoding="utf-8")
    cls = _overlay_processor({"notice": {
        "enrichment.table_description.enable": False,   # -> table_desc
        "ocr.ocr_mode": "force",                        # -> ocr_mode
    }})

    await _routable(cls)(None, str(src), doc_type="notice")
    assert cls.seen["_config"] == {"table_desc": False, "ocr_mode": "force"}
    assert cls.seen["ocr_mode"] == "force"


@pytest.mark.asyncio
async def test_config_overlay_warns_on_settings_it_cannot_change(tmp_path: Path, monkeypatch):
    """요청마다 바꿀 수 없는 설정은 조용히 무시하지 말고 경고를 남긴다."""
    warned = []
    # 고객용 facade 는 genon. 절대경로로 core 를 import 한다 — 테스트가 짧은 경로로 잡은
    # core_parser 와 다른 모듈 객체라, 실제로 실행되는 쪽의 로거를 가로챈다.
    core_mod = sys.modules[parser_facade.DocumentProcessor.__mro__[1].__module__]
    monkeypatch.setattr(core_mod._log, "warning", lambda *a, **k: warned.append(a))
    src = tmp_path / "a.log"
    src.write_text("a\n", encoding="utf-8")
    cls = _overlay_processor({"notice": {"ocr.paddle.ocr_endpoint": "http://x"}})

    await _routable(cls)(None, str(src), doc_type="notice")
    assert cls.seen["_config"] == {}
    assert warned and "ocr.paddle.ocr_endpoint" in str(warned[0])


@pytest.mark.asyncio
async def test_config_overlay_reaches_runtime_wiring(tmp_path: Path):
    """오버레이는 런타임 토글 배선보다 먼저 얹혀야 한다.

    순서가 뒤집히면 값이 job.params 에는 남지만 배선은 이미 지나간 뒤라 CONFIG_BY_DOC_TYPE 이
    조용히 무시된다. params 만 보는 단정으로는 그 결함이 잡히지 않아 배선까지 확인한다.
    """
    src = tmp_path / "a.log"
    src.write_text("a\n", encoding="utf-8")
    cls = _overlay_processor({"notice": {"table_desc": 1, "keep_pdf": 1}})
    proc = _routable(cls)

    saw: dict = {}
    proc._intel = type("_I", (), {
        "_normalize_runtime_kwargs": staticmethod(lambda kw: dict(kw)),
        "_configure_runtime_image_mode": staticmethod(saw.update),
    })()

    await proc(None, str(src), doc_type="notice")
    assert saw.get("table_desc") == 1   # enrichment 배선이 오버레이 값을 봤다
    # 변환 PDF 정책도 오버레이 뒤에 만들어져야 한다. 앞서 만들면 keep_pdf 가 무시된다.
    assert cls.seen["_pdf_policy"].keep is True


def test_ocr_mode_request_parameter_beats_yaml():
    """ocr_mode 는 기동 설정만 보던 값이었다 — 요청(그리고 오버레이)이 덮을 수 있어야 한다."""
    resolve = core_parser.cp.resolve_ocr_mode
    assert resolve({}, "auto") == "auto"                    # 안 보내면 yaml 값
    assert resolve({"ocr_mode": "force"}, "auto") == "force"
    assert resolve({"ocr_mode": "FORCE"}, "auto") == "force"  # 대소문자 무시
    assert resolve({"ocr_mode": "zzz"}, "disable") == "disable"  # 모르는 값이면 yaml 값


def test_chunker_config_overlay_lands_on_job_params():
    class _P(chunker_facade.DocumentProcessor):
        CONFIG_BY_DOC_TYPE = {"faq": {"chunk_size": 500, "chunk_mode": "resize_all"}}

    src = core_chunker.ChunkInput("parse", [], {})
    job = _bare(_P)._start_chunk_job(
        None, "", src, {"doc_type": "faq", "chunk_mode": "split_only"})
    assert job.params["chunk_size"] == 500
    assert job.params["chunk_mode"] == "split_only"   # 요청이 이긴다
    assert job.config == {"chunk_size": 500}


# ---------------------------------------------------------------------------
# edit_document — 파싱 후 enrichment 전 문서를 손보는 훅 메소드
# ---------------------------------------------------------------------------

class _ResponseRecorder(parser_facade.DocumentProcessor):
    """enrichment 와 응답 조립을 가로채 받은 인자와 호출 순서만 기록한다."""

    async def _apply_docling_post_enrichment(self, document, **kwargs):
        self.calls.append(("enrich", document, kwargs))
        return document + "+enriched"

    def _build_docling_response(self, doc, clear_coordinates=False, **kwargs):
        self.calls.append(("build", doc, clear_coordinates, kwargs))
        return {"doc": doc}


def _recorder(cls=_ResponseRecorder):
    proc = _bare(cls)
    proc.calls = []
    return proc


@pytest.mark.asyncio
async def test_docling_response_keeps_the_same_enrichment_and_build_arguments():
    """옛 라우트가 부르는 _docling_response 는 enrichment·응답 조립에 분해 전과 같은 인자를 넘긴다."""
    proc = _recorder()
    ctx = {"enrichment_context": {"metadata": {"K": 1}}, "artifacts_source": None}
    out = await proc._docling_response("DOC", ctx, clear_coordinates=True, doc_type="t", tenant="A")
    assert proc.calls == [
        ("enrich", "DOC", {"_enrichment_context": ctx["enrichment_context"],
                           "doc_type": "t", "tenant": "A"}),
        ("build", "DOC+enriched", True, {"doc_type": "t", "tenant": "A"}),
    ]
    assert out == {"doc": "DOC+enriched", "metadata": {"K": 1}}


@pytest.mark.asyncio
async def test_edit_document_runs_before_enrichment():
    class _P(_ResponseRecorder):
        def edit_document(self, job, doc):
            self.calls.append(("hook", doc, job.params.get("tenant")))
            return doc + "+hooked"

    proc = _recorder(_P)
    base = core_parser.jb.ParseJob(request=None, file_path="a.md", ext=".md", doc_type="t",
                                   params={"tenant": "B"})
    ctx = {"enrichment_context": {}, "artifacts_source": None, "job": base}
    await proc._docling_response("DOC", ctx, tenant="A")
    assert [c[0] for c in proc.calls] == ["hook", "enrich", "build"]
    assert proc.calls[0] == ("hook", "DOC", "A")   # 라우트가 넘긴 kwargs 가 job.params 가 된다
    assert proc.calls[1][1] == "DOC+hooked"


@pytest.mark.asyncio
async def test_edit_document_returning_none_keeps_the_document():
    class _P(_ResponseRecorder):
        def edit_document(self, job, doc):
            pass

    proc = _recorder(_P)
    await proc._docling_response("DOC", {"enrichment_context": {}}, doc_type="t")
    assert proc.calls[0][1] == "DOC"


@pytest.mark.asyncio
async def test_async_edit_document_is_awaited():
    class _P(_ResponseRecorder):
        async def edit_document(self, job, doc):
            return doc + "+async"

    proc = _recorder(_P)
    await proc._docling_response("DOC", {"enrichment_context": {}}, doc_type="t")
    assert proc.calls[0][1] == "DOC+async"


# ---------------------------------------------------------------------------
# 구분자 레코드 라우팅 — doc_type 이 source.pre.delimited 를 선언했으면 확장자보다
# 먼저 레코드 경로를 탄다(cs_ssf 류: 원천이 .dtms/.md/.html/.txt 어느 확장자로도 온다).
# ---------------------------------------------------------------------------

class _FakeDelimitedMapper:
    """`_route_delimited_records` 가 보는 최소 매퍼. 실제 필드 매핑은 하지 않는다."""

    records_key = "items"

    def __init__(self, doc_type: str, delimited: bool = True):
        self._doc_type = doc_type
        self.delimited = object() if delimited else None

    def matches(self, runtime_doc_type):
        return runtime_doc_type == self._doc_type


class _DelimitedRoutable(parser_facade.DocumentProcessor):
    """route_docling 을 가로채 실제 docling 파싱 없이 호출 여부만 기록한다."""

    async def route_docling(self, file_path, ext, ctx, **kwargs):
        self.docling_called = True
        return {"elements": tb.make_elements(["docling"])}

    async def _parse_json_records(self, file_path, mappers, **kwargs):
        self.records_called = True
        return {"elements": tb.make_elements(["records"])}


def _delimited_routable(mappers):
    proc = _routable(_DelimitedRoutable)
    proc._json_records_mappers = mappers
    proc.docling_called = False
    proc.records_called = False
    return proc


@pytest.mark.asyncio
async def test_delimited_route_intercepts_before_extension_routes(tmp_path: Path):
    """delimited 매퍼가 doc_type 에 매칭되면 .html 의 route_docling 보다 먼저 탄다."""
    src = tmp_path / "notice.html"
    src.write_text("a|@|b\n", encoding="utf-8")
    proc = _delimited_routable([_FakeDelimitedMapper("cs_ssf")])

    out = await proc(None, str(src), doc_type="cs_ssf")

    assert proc.records_called is True
    assert proc.docling_called is False
    assert out["elements"][0]["content"] == "records"


@pytest.mark.asyncio
async def test_delimited_route_falls_through_when_doc_type_unmatched(tmp_path: Path):
    """delimited 매퍼가 없거나 doc_type 이 안 맞으면 기존 확장자 라우팅 그대로다."""
    src = tmp_path / "notice.html"
    src.write_text("a|@|b\n", encoding="utf-8")
    proc = _delimited_routable([_FakeDelimitedMapper("other_doc_type")])

    out = await proc(None, str(src), doc_type="cs_ssf")

    assert proc.records_called is False
    assert proc.docling_called is True
    assert out["elements"][0]["content"] == "docling"


@pytest.mark.asyncio
async def test_delimited_route_skips_binary_content(tmp_path: Path):
    """매칭돼도 파일 내용이 텍스트가 아니면(바이너리 원본) 가로채지 않고 None 을 돌려준다."""
    src = tmp_path / "notice.bin"
    src.write_bytes(b"a|@|b\x00\x01\x02")
    proc = _delimited_routable([_FakeDelimitedMapper("cs_ssf")])

    result = await proc._route_delimited_records(str(src), ".bin", {}, doc_type="cs_ssf")

    assert result is None
    assert proc.records_called is False


@pytest.mark.asyncio
async def test_records_payload_rejects_zero_records(tmp_path: Path):
    """확장자 무관으로 입구가 넓어진 만큼, doc_type 을 잘못 짚은 원천은 0건 대신 에러여야 한다."""
    delimited_text = pytest.importorskip("genon.preprocessor.converters.delimited_text")
    spec = delimited_text.parse_spec({"separator": "|@|", "columns": ["a", "b"]})
    mapper = _FakeDelimitedMapper("cs_ssf")
    mapper.delimited = spec
    proc = _bare(core_parser.ParserCore)

    bad = tmp_path / "bad.md"
    bad.write_text("# 제목\n본문입니다\n", encoding="utf-8")
    with pytest.raises(core_parser.GenosServiceException):
        await proc._records_payload(str(bad), [mapper], "cs_ssf")

    good = tmp_path / "good.txt"
    good.write_text("v1|@|v2\n", encoding="utf-8")
    records = await proc._records_payload(str(good), [mapper], "cs_ssf")
    assert records == [{"a": "v1", "b": "v2"}]


def test_register_transform_reaches_the_yaml_pipeline():
    """등록한 변환기를 yaml transforms: 가 이름으로 쓴다(같은 파이프라인)."""
    tcf = pytest.importorskip("genon.preprocessor.processing.enrichment.tabular_custom_fields")
    ft = pytest.importorskip("genon.preprocessor.processing.enrichment.field_transforms")

    tb.register_transform("won_to_int_test", lambda v: int(str(v).replace(",", "")))
    try:
        fields = {"AMT": "1,200"}
        tcf.apply_transforms(fields, tcf.compile_transforms({"AMT": ["won_to_int_test"]}, label="t"))
        assert fields == {"AMT": 1200}
        # 오류 메시지의 "사용 가능" 목록도 등록분을 반영한다.
        assert "won_to_int_test" in ft.ALL_TRANSFORM_NAMES
    finally:
        ft.VALUE_TRANSFORMS.pop("won_to_int_test", None)


def test_register_transform_rejects_bad_input():
    ft = pytest.importorskip("genon.preprocessor.processing.enrichment.field_transforms")
    with pytest.raises(ValueError):
        tb.register_transform("", lambda v: v)
    with pytest.raises(TypeError):
        tb.register_transform("not_callable", "x")
    # 인자형 변환기와 이름이 겹치면 설정이 어느 쪽을 부르는지 모호해진다.
    existing = next(iter(ft.PARAM_TRANSFORMS))
    with pytest.raises(ValueError):
        tb.register_transform(existing, lambda v: v)


# ---------------------------------------------------------------------------
# edit_chunk — 청크 한 건씩 손보는 자리 (#363 09 B군 ③)
#
# edit_output 는 통계·순번이 확정된 뒤라 본문을 고치면 값이 어긋나고, 청크를 버리면
# 순번을 손으로 다시 맞춰야 했다. edit_chunk 는 그 앞이라 코어가 맞춰 준다.
# ---------------------------------------------------------------------------

def _chunker(cls, **attrs):
    proc = _bare(cls)
    proc.setup_logging = lambda *_a, **_k: None
    proc._log_level = 4
    proc._gr_cfg = type("C", (), {"masking_enabled": False})()
    proc._text_cleanup = "off"
    proc._text_cleanup_rules = ()
    proc._chunk_size = 0
    proc._recursive_chunk_overlap = 0
    proc._table_as_chunk = True
    for key, value in attrs.items():
        setattr(proc, key, value)
    return proc


def _rows(n):
    return [{"category": "custom_fields_row", "content": f"본문{i}", "page": 1,
             "metadata": {"IDX": i}} for i in range(n)]


@pytest.mark.asyncio
async def test_edit_chunk_default_changes_nothing():
    """출고 템플릿의 훅은 활성이지만 None 을 돌려주므로 산출이 그대로다."""
    proc = _chunker(chunker_facade.DocumentProcessor)
    vectors = await proc._chunk_parse_format(_rows(3))
    assert [v.text for v in vectors] == ["본문0", "본문1", "본문2"]
    # 훅을 아예 두지 않은 코어는 청크마다 호출하는 비용조차 치르지 않는다.
    assert _bare(core_chunker.ChunkerCore)._edit_chunk_active() is False


@pytest.mark.asyncio
async def test_edit_chunk_change_is_reflected_in_stats():
    """본문을 고치면 n_char 가 따라온다 — edit_output 였다면 옛 값이 남는다."""
    class _P(chunker_facade.DocumentProcessor):
        def edit_chunk(self, text, info, **kwargs):
            return text + "!!!"

    vectors = await _chunker(_P)._chunk_parse_format(_rows(2))
    assert [v.text for v in vectors] == ["본문0!!!", "본문1!!!"]
    assert all(v.n_char == len(v.text) for v in vectors)


@pytest.mark.asyncio
async def test_edit_chunk_drop_renumbers_the_rest():
    """버린 뒤 순번과 개수를 코어가 다시 맞춘다."""
    class _P(chunker_facade.DocumentProcessor):
        def edit_chunk(self, text, info, **kwargs):
            return tb.DROP if info["metadata"].get("IDX") == 1 else None

    vectors = await _chunker(_P)._chunk_parse_format(_rows(4))
    assert [v.text for v in vectors] == ["본문0", "본문2", "본문3"]
    assert [v.i_chunk_on_doc for v in vectors] == [0, 1, 2]
    assert all(v.n_chunk_of_doc == 3 for v in vectors)
    assert all(v.n_chunk_of_page == 3 for v in vectors)


@pytest.mark.asyncio
async def test_edit_chunk_returning_none_keeps_the_chunk():
    """return 을 빠뜨린 훅이 청크를 지우면 안 된다 — 버리는 것은 DROP 으로만."""
    class _P(chunker_facade.DocumentProcessor):
        def edit_chunk(self, text, info, **kwargs):
            pass

    vectors = await _chunker(_P)._chunk_parse_format(_rows(2))
    assert len(vectors) == 2


@pytest.mark.asyncio
async def test_edit_chunk_blank_is_treated_as_drop():
    class _P(chunker_facade.DocumentProcessor):
        def edit_chunk(self, text, info, **kwargs):
            return "   " if info["index"] == 0 else text

    vectors = await _chunker(_P)._chunk_parse_format(_rows(2))
    assert [v.text for v in vectors] == ["본문1"]


@pytest.mark.asyncio
async def test_edit_chunk_rejects_wrong_return_type():
    class _P(chunker_facade.DocumentProcessor):
        def edit_chunk(self, text, info, **kwargs):
            return 123

    with pytest.raises(TypeError):
        await _chunker(_P)._chunk_parse_format(_rows(1))


@pytest.mark.asyncio
async def test_edit_chunk_info_shape_is_the_same_on_every_path():
    """경로가 달라도 훅이 보는 dict 모양이 같아야 한 벌로 쓸 수 있다."""
    seen = []

    class _P(chunker_facade.DocumentProcessor):
        def edit_chunk(self, text, info, **kwargs):
            seen.append(info)
            return None

    await _chunker(_P)._chunk_parse_format(_rows(1))
    await _chunker(_P)._chunk_parse_format(
        [{"category": "text", "content": "평문 본문", "page": 2}])
    assert [i["kind"] for i in seen] == ["row", "text"]
    assert all(set(i) == {"kind", "page", "index", "headings", "metadata", "fields"} for i in seen)
    assert seen[0]["metadata"] == {"IDX": 0} and seen[1]["page"] == 2


@pytest.mark.asyncio
async def test_edit_chunk_fields_are_attached_on_row_and_text_paths():
    """청크별 값은 info["fields"] 로 싣는다 — vector_meta 조립을 오버라이드하지 않아도 된다."""
    class _P(chunker_facade.DocumentProcessor):
        def edit_chunk(self, text, info, **kwargs):
            info["fields"]["RISK"] = "high" if text.endswith("1") else "low"
            return None

    rows = await _chunker(_P)._chunk_parse_format(_rows(2))
    assert [v.RISK for v in rows] == ["low", "high"]
    texts = await _chunker(_P)._chunk_parse_format(
        [{"category": "text", "content": "평문1", "page": 1}])
    assert texts[0].RISK == "high"


@pytest.mark.asyncio
async def test_edit_chunk_fields_may_be_replaced_with_a_new_dict():
    class _P(chunker_facade.DocumentProcessor):
        def edit_chunk(self, text, info, **kwargs):
            info["fields"] = {"TAG": "x"}
            return None

    vectors = await _chunker(_P)._chunk_parse_format(_rows(1))
    assert vectors[0].TAG == "x"


@pytest.mark.asyncio
async def test_edit_chunk_fields_cannot_override_text_or_stats():
    """본문은 반환값으로 바꾼다. fields 로 덮게 두면 본문과 n_char 가 어긋난다."""
    class _P(chunker_facade.DocumentProcessor):
        def edit_chunk(self, text, info, **kwargs):
            info["fields"]["n_char"] = 0
            return None

    with pytest.raises(ValueError, match="n_char"):
        await _chunker(_P)._chunk_parse_format(_rows(1))


@pytest.mark.asyncio
async def test_edit_chunk_receives_request_params():
    seen = {}

    class _P(chunker_facade.DocumentProcessor):
        def edit_chunk(self, text, info, **kwargs):
            seen.update(kwargs)
            return None

    await _chunker(_P)._chunk_parse_format(_rows(1), tenant="A")
    assert seen.get("tenant") == "A"


@pytest.mark.asyncio
async def test_async_edit_chunk_is_awaited():
    class _P(chunker_facade.DocumentProcessor):
        async def edit_chunk(self, text, info, **kwargs):
            return text.upper()

    vectors = await _chunker(_P)._chunk_parse_format(
        [{"category": "text", "content": "abc", "page": 1}])
    assert vectors[0].text == "ABC"
