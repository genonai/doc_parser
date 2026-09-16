"""마크다운 LaTeX 수식 보호·복원 단위 테스트.

수식은 파싱 과정에서 세 가지로 깨진다 — 세로줄이 표 모드를 켜서 본문이 통째로 사라지고,
백슬래시 이스케이프가 해독되며 문단이 조각나고, `$$` 블록이 세 아이템으로 갈린다.
`converters.md_math` 는 파싱 전에 수식을 감추고 파싱 후 되돌려 이 셋을 모두 피한다.

여기서는 네 가지를 고정한다.

1. 무엇을 수식으로 보는가 — 특히 통화 표기 오탐. 오탐은 본문을 왜곡하므로 미검출보다 나쁘다
2. 감춘 상태로 실제 백엔드를 통과시켰을 때 손상이 없는가(왕복)
3. `text_fence` 의 레이아웃 파이프 제거가 수식을 건드리지 않는가(순서 의존)
4. 청커가 블록 수식에 구분자를 붙이는가
"""
import tempfile
from pathlib import Path

import pytest

from genon.preprocessor.converters.md_math import (
    protect,
    restore_document,
)

pytestmark = pytest.mark.unit


def _round_trip(md: str):
    """감추기 → 실제 백엔드 파싱 → 되돌리기. (label, text) 목록을 준다."""
    from docling.backend.md_backend import MarkdownDocumentBackend
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.document import InputDocument

    protected, vault = protect(md)
    path = Path(tempfile.mkdtemp()) / "s.md"
    path.write_text(protected, encoding="utf-8")
    in_doc = InputDocument(
        path_or_stream=path,
        format=InputFormat.MD,
        backend=MarkdownDocumentBackend,
        filename=path.name,
    )
    doc = MarkdownDocumentBackend(in_doc=in_doc, path_or_stream=path).convert()
    restore_document(doc, vault)

    out = []
    for item, _ in doc.iterate_items():
        label = getattr(item, "label", None)
        out.append((str(getattr(label, "value", label)), getattr(item, "text", None)))
    return out


def _round_trip_table_cells(md: str) -> set:
    """왕복 뒤 표 셀 텍스트들. 표는 iterate_items 로는 본문을 볼 수 없다."""
    from docling.backend.md_backend import MarkdownDocumentBackend
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.document import InputDocument

    protected, vault = protect(md)
    path = Path(tempfile.mkdtemp()) / "s.md"
    path.write_text(protected, encoding="utf-8")
    in_doc = InputDocument(
        path_or_stream=path,
        format=InputFormat.MD,
        backend=MarkdownDocumentBackend,
        filename=path.name,
    )
    doc = MarkdownDocumentBackend(in_doc=in_doc, path_or_stream=path).convert()
    restore_document(doc, vault)
    return {
        cell.text
        for table in doc.tables
        for cell in table.data.table_cells
    }


# ── 무엇을 수식으로 보는가 ────────────────────────────────────────────────────


def test_inline_and_block_are_detected():
    _, vault = protect("본문 $a_i$ 와\n\n$$\nE = mc^2\n$$\n")
    bodies = {body: is_block for body, is_block in vault.entries.values()}
    assert bodies == {"a_i": False, "E = mc^2": True}


def test_currency_is_not_a_formula():
    """통화 표기를 수식으로 잡으면 본문이 왜곡된다. 미검출보다 나쁜 실수다."""
    out, vault = protect("가격은 $100 에서 $200 사이입니다.\n")
    assert vault.empty
    assert out == "가격은 $100 에서 $200 사이입니다.\n"


def test_escaped_dollar_is_not_a_formula():
    _, vault = protect("금액 \\$50 와 \\$100 입니다.\n")
    assert vault.empty


def test_unclosed_block_is_not_a_formula():
    """`$$` 오타 하나가 문서 나머지를 삼키면 지금 고치려는 것과 같은 소실이 된다."""
    src = "$$\n앞 문장입니다.\n\n뒤 문장입니다.\n"
    out, vault = protect(src)
    assert vault.empty
    assert out == src


def test_placeholder_has_no_characters_that_break_parsing():
    """세로줄·백슬래시·중괄호가 남아 있으면 감춘 의미가 없다."""
    out, vault = protect("본문 $\\left| S \\right|$ 입니다.\n")
    token = next(iter(vault.entries))
    assert token.isalnum() and token.isascii()
    assert not set("|\\{}$") & set(out)


# ── 실제 백엔드 왕복 ──────────────────────────────────────────────────────────


def test_pipe_in_formula_does_not_become_a_table():
    """절댓값의 세로줄로 표 모드가 켜지면 뒤따르는 본문이 전부 표로 삼켜진다."""
    items = _round_trip("적립률 $\\left| S_t - K \\right|$ 을 적용합니다.\n")
    assert not any(label == "table" for label, _ in items)
    assert items[0][1] == "적립률 $\\left| S_t - K \\right|$ 을 적용합니다."


def test_escaped_braces_keep_formula_in_one_item():
    """`\\{` 를 백엔드가 해독하면 수식이 그 자리에서 조각나고 백슬래시도 사라진다."""
    src = "확인: $P_{\\text{net}} = \\sum_{k} \\{ q_k \\} \\cdot v^{k}$\n"
    texts = [t for lb, t in _round_trip(src) if lb == "text"]
    assert texts == ["확인: $P_{\\text{net}} = \\sum_{k} \\{ q_k \\} \\cdot v^{k}$"]


def test_block_formula_becomes_one_formula_item():
    """`$$` 블록은 아이템 하나이고 라벨이 formula 다. 셋으로 갈리면 청크가 수식을 자른다."""
    items = _round_trip("본문\n\n$$\nV_t = P \\times a_{x+t}\n$$\n")
    assert [t for lb, t in items if lb == "formula"] == ["V_t = P \\times a_{x+t}"]


def test_matrix_backslashes_and_ampersand_survive():
    items = _round_trip("$$\nA = \\begin{bmatrix} a & b \\\\ c & d \\end{bmatrix}\n$$\n")
    formula = next(t for lb, t in items if lb == "formula")
    assert "&" in formula and "\\\\" in formula


def test_formula_in_table_cell_survives():
    """표 셀은 별도 객체라 텍스트 순회에 안 걸린다 — 복원에서 빠지기 쉽다."""
    cells = _round_trip_table_cells(
        "| 구분 | 산식 |\n|---|---|\n| 순보험료 | $P = \\frac{A_x}{a_x}$ |\n"
    )
    assert "$P = \\frac{A_x}{a_x}$" in cells


def test_document_without_formula_is_untouched():
    items = _round_trip("# 제목\n\n평범한 문단입니다.\n")
    assert [t for lb, t in items] == ["제목", "평범한 문단입니다."]


# ── text_fence 와의 순서 ─────────────────────────────────────────────────────


def test_text_fence_cannot_strip_pipes_inside_protected_formula():
    """수식 보호가 펜스 전처리보다 앞에 와야 절댓값 기호가 살아남는다."""
    from genon.preprocessor.converters.md_text_fence import transform

    src = (
        "```text\n"
        "    해약환급금 산출 시 적립률 조건을 다음과 같이 판정합니다.\n"
        "    조건 $\\left| S_t - K \\right|$ 을 기준으로 봅니다.\n"
        "```\n"
    )
    protected, vault = protect(src)
    fenced, converted = transform(protected, langs=("", "text"), min_hangul_ratio=0.3)
    assert converted == 1
    assert vault.restore_text(fenced).count("$\\left| S_t - K \\right|$") == 1


def test_text_fence_keeps_block_formula_as_its_own_paragraph():
    """수식이 앞 문장과 한 단락으로 접히면 줄 첫머리의 `$$` 를 잃어 감추기가 놓친다."""
    from genon.preprocessor.converters.md_text_fence import transform

    src = (
        "```text\n"
        "    해약환급금 산출식은 아래와 같이 정해져 있습니다.\n\n"
        "    $$\n    V_t = P \\times a_{x+t}\n    $$\n\n"
        "    이상으로 산출 방법 안내를 마칩니다.\n"
        "```\n"
    )
    fenced, converted = transform(src, langs=("", "text"), min_hangul_ratio=0.3)
    assert converted == 1
    assert "\n\n$$ V_t = P \\times a_{x+t} $$\n\n" in fenced
    # 전처리를 거친 결과가 실제로 수식 아이템이 되는지까지 확인한다.
    items = _round_trip(fenced)
    assert [t for lb, t in items if lb == "formula"] == ["V_t = P \\times a_{x+t}"]


# ── 로딩 지점 가드 ───────────────────────────────────────────────────────────


def test_guard_passes_through_non_markdown():
    """md 가 아니면 파싱 입력이 종전과 같아야 한다."""
    from genon.preprocessor.converters.md_math import guard_markdown

    path = Path(tempfile.mkdtemp()) / "a.txt"
    path.write_text("본문 $a_i$ 입니다.\n", encoding="utf-8")
    with guard_markdown(str(path)) as guard:
        assert guard.path == str(path)


def test_guard_passes_through_markdown_without_formula():
    """수식이 없으면 파생 파일을 만들지 않는다 — artifacts 경로가 종전과 같아야 한다."""
    from genon.preprocessor.converters.md_math import guard_markdown

    path = Path(tempfile.mkdtemp()) / "a.md"
    path.write_text("평범한 문단입니다.\n", encoding="utf-8")
    with guard_markdown(str(path)) as guard:
        assert guard.path == str(path)


def test_guard_hides_formula_and_keeps_basename():
    """docling 은 확장자로 백엔드를 고르고 파일명에서 origin 을 가져온다."""
    from genon.preprocessor.converters.md_math import guard_markdown

    path = Path(tempfile.mkdtemp()) / "product.md"
    path.write_text("조건 $\\left| S \\right|$ 입니다.\n", encoding="utf-8")
    with guard_markdown(str(path)) as guard:
        assert guard.path != str(path)
        assert Path(guard.path).name == "product.md"
        hidden = Path(guard.path).read_text(encoding="utf-8")
        assert "|" not in hidden and "$" not in hidden


# ── 청커 표기 ────────────────────────────────────────────────────────────────


def test_chunk_text_wraps_block_formula_with_delimiters():
    """구분자가 없으면 청크를 받는 쪽이 수식을 본문 문장으로 읽는다."""
    from genon.preprocessor.processing.chunking.formula_text import item_text

    class _Item:
        label = "formula"
        text = "V_t = P \\times a_{x+t}"

    assert item_text(_Item(), _Item.text) == "$$V_t = P \\times a_{x+t}$$"


def test_chunk_text_does_not_double_wrap():
    from genon.preprocessor.processing.chunking.formula_text import item_text

    class _Item:
        label = "formula"
        text = "$$E = mc^2$$"

    assert item_text(_Item(), _Item.text) == "$$E = mc^2$$"


def test_chunk_text_leaves_plain_text_alone():
    from genon.preprocessor.processing.chunking.formula_text import item_text

    class _Item:
        label = "text"
        text = "일반 문장입니다."

    assert item_text(_Item(), _Item.text) == "일반 문장입니다."
