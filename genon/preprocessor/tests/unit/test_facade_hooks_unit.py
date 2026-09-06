"""고객 확장 훅 단위 테스트 (#363 08-3).

훅은 "고객이 코어를 안 고치고도 새 원천을 처리한다" 는 이번 리팩터링의 목적 그 자체라,
배선이 조용히 끊겨도 골든은 차이 0 으로 통과한다(아무 것도 안 하는 훅이므로).
그래서 배선을 직접 단정한다.

네트워크·LLM 을 부르지 않는다. 훅 게이트와 호출 지점만 본다.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

core_parser = pytest.importorskip("facade.core.parser")
core_chunker = pytest.importorskip("facade.core.chunker")
parser_facade = pytest.importorskip("facade.parser_processor")
chunker_facade = pytest.importorskip("facade.chunking_processor")


def _bare(cls):
    """__init__ 을 우회한 최소 인스턴스. 훅 배선만 보므로 설정이 필요 없다."""
    return object.__new__(cls)


# ---------------------------------------------------------------------------
# pre_source 게이트 — 안 건드리면 파생 입력을 만들지 않는다
# ---------------------------------------------------------------------------

def test_untouched_hook_reports_no_change():
    """core 기본 구현 그대로면 '비활성' 이고 값도 그대로다."""
    proc = _bare(core_parser.ParserCore)
    data = {"a": 1}
    assert proc._pre_source_active() is False
    assert proc._hook_pre_source(".json", {}, data) == (data, False)


def test_passthrough_override_is_active_but_reports_no_change():
    """출고 템플릿처럼 그대로 돌려주는 훅은 활성이지만 '안 바뀜' 이다.

    이 구분이 산출 동일성을 지킨다 — 활성이어도 같은 객체를 돌려주면 core 는
    파생 입력을 만들지 않는다.
    """
    proc = _bare(parser_facade.DocumentProcessor)
    data = {"a": 1}
    assert proc._pre_source_active() is True
    assert proc._hook_pre_source(".json", {}, data) == (data, False)


def test_reshaping_hook_reports_change():
    class _P(parser_facade.DocumentProcessor):
        def pre_source(self, ext, doc_type, data, work_dir=None):
            if doc_type == "nested":
                return {"items": [i for g in data["groups"] for i in g["items"]]}
            return data

    proc = _bare(_P)
    src = {"groups": [{"items": [1, 2]}, {"items": [3]}]}
    out, changed = proc._hook_pre_source(".json", {"doc_type": "nested"}, src)
    assert changed is True and out == {"items": [1, 2, 3]}
    # 대상 doc_type 이 아니면 손대지 않는다 — 게이팅이 없으면 모든 JSON 이 바뀐다.
    assert proc._hook_pre_source(".json", {"doc_type": "other"}, src) == (src, False)


# ---------------------------------------------------------------------------
# .json 입구 — 훅이 실제로 그 자리에서 불린다
# ---------------------------------------------------------------------------

def test_json_payload_hook_is_called_at_the_single_entry(tmp_path: Path):
    src = tmp_path / "a.json"
    src.write_text(json.dumps({"groups": [{"items": [1]}, {"items": [2]}]}), encoding="utf-8")

    class _P(parser_facade.DocumentProcessor):
        def pre_source(self, ext, doc_type, data, work_dir=None):
            return {"items": [i for g in data["groups"] for i in g["items"]]}

    assert _bare(_P)._load_json_payload(str(src), "any") == {"items": [1, 2]}


def test_broken_json_reaches_the_hook_as_raw_text(tmp_path: Path):
    """JSONL 처럼 json.loads 가 실패하는 원천은 원문 str 로 훅에 온다."""
    src = tmp_path / "a.json"
    src.write_text('{"v":1}\n{"v":2}\n', encoding="utf-8")

    class _P(parser_facade.DocumentProcessor):
        def pre_source(self, ext, doc_type, data, work_dir=None):
            assert isinstance(data, str)
            return {"rows": [json.loads(ln) for ln in data.splitlines() if ln.strip()]}

    assert _bare(_P)._load_json_payload(str(src), "any") == {"rows": [{"v": 1}, {"v": 2}]}


def test_broken_json_without_hook_still_fails(tmp_path: Path):
    """훅이 손대지 않으면 종전대로 입력 오류다(하위호환)."""
    src = tmp_path / "a.json"
    src.write_text("{not json", encoding="utf-8")
    with pytest.raises(core_parser.GenosServiceException):
        _bare(parser_facade.DocumentProcessor)._load_json_payload(str(src), "any")


def test_cp949_json_is_read_without_customer_code(tmp_path: Path):
    """인코딩은 core 가 흡수한다 — 훅은 구조 문제만 다룬다."""
    src = tmp_path / "a.json"
    src.write_bytes(json.dumps({"n": "한글"}, ensure_ascii=False).encode("cp949"))
    assert _bare(parser_facade.DocumentProcessor)._load_json_payload(str(src)) == {"n": "한글"}


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
async def test_pre_and_post_chunk_are_wired_into_call():
    seen = {}

    class _P(chunker_facade.DocumentProcessor):
        def pre_chunk(self, kind, data, **kwargs):
            seen["pre"] = kind
            return data + [{"content": "added"}]

        def post_chunk(self, vectors, **kwargs):
            seen["post"] = len(vectors)
            return vectors[:1]

        async def chunk(self, request, file_path, src, **kwargs):
            seen["to_chunk"] = len(src.data)
            return ["v1", "v2"]

    proc = _bare(_P)
    proc.setup_logging = lambda *_a, **_k: None
    proc._log_level = 4
    proc._gr_cfg = type("C", (), {"masking_enabled": False})()

    out = await proc(None, "", document={"elements": [{"content": "a"}]})
    assert seen == {"pre": "parse", "to_chunk": 2, "post": 2}
    assert out == ["v1"]


@pytest.mark.asyncio
async def test_post_parse_is_wired_into_call():
    class _P(parser_facade.DocumentProcessor):
        async def run(self, request, file_path, **kwargs):
            return {"elements": [], "metadata": {}}

        def post_parse(self, ext, doc_type, result):
            result["metadata"]["src"] = f"{ext}:{doc_type}"
            return result

    proc = _bare(_P)
    proc._ext_aliases = {".parsed": ".md"}
    out = await proc(None, "/x/a.parsed", doc_type="T")
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


def test_grid_hook_is_skipped_when_the_workbook_cannot_be_read():
    """원본을 못 읽으면 훅을 건너뛰고 (None, False) 다.

    여기서 먼저 죽으면 오류 메시지와 시점이 종전과 달라진다 — 실제 오류는
    아래 파싱 경로가 낸다.
    """
    proc = _bare(parser_facade.DocumentProcessor)
    assert proc._hook_tabular_sheets("없는파일.xlsx", "/tmp") == (None, False)


def test_unchanged_grid_is_reused_to_avoid_a_second_read(tmp_path: Path):
    """훅이 손대지 않아도 이미 읽은 격자를 넘긴다 — 같은 함수 산출이라 동일하다."""
    src = xp.sheets_to_xlsx({"S": [["a", "b"], ["1", "2"]]}, str(tmp_path))
    proc = _bare(parser_facade.DocumentProcessor)
    sheets, changed = proc._hook_tabular_sheets(src, str(tmp_path))
    assert changed is False
    assert sheets == xp._load_sheets_with_merges(src)
