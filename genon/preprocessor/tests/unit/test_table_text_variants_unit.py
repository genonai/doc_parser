"""표 표기형태별 청크 텍스트(#360 text_table_html / text_table_md) 단위 테스트.

기록부는 청크 텍스트 안의 표 문자열을 형식별 변형으로 치환한다. 찾지 못한 표는
primary 표기로 남기고 그 수만 센다 — 내용이 사라지는 경로는 없어야 한다.
"""

import pytest

from genon.preprocessor.facade.chunking import table_variants as tv


HTML = "<table><tbody><tr><td>값</td></tr></tbody></table>"
MD = "| 컬럼 |\n| --- |\n| 값 |"


def _recorder(formats=("html", "markdown")):
    return tv.TableTextVariants(formats)


@pytest.mark.unit
def test_disabled_recorder_produces_no_fields():
    variants = _recorder(())
    variants.record(HTML, {"markdown": MD})
    assert variants.enabled() is False
    assert variants.field_values("본문\n" + HTML) == {}


@pytest.mark.unit
def test_recorded_table_is_replaced_in_chunk_text():
    variants = _recorder()
    variants.record(HTML, {"markdown": MD})
    text = "HEADER: 안내\n앞 문단\n" + HTML + "\n뒤 문단"

    values = variants.field_values(text)
    assert values["text_table_md"] == "HEADER: 안내\n앞 문단\n" + MD + "\n뒤 문단"
    # 기록이 없는 형식은 원문 그대로. 표기형태가 이미 그 형식이라는 뜻이다.
    assert values["text_table_html"] == text


@pytest.mark.unit
def test_chunk_without_table_keeps_original_text():
    """필드 유무가 청크마다 갈리면 소비 측이 폴백 로직을 다시 만들어야 한다."""
    variants = _recorder()
    variants.record(HTML, {"markdown": MD})

    values = variants.field_values("표가 없는 본문")
    assert values["text_table_md"] == "표가 없는 본문"
    assert values["text_table_html"] == "표가 없는 본문"


@pytest.mark.unit
def test_only_recorded_tables_are_replaced_and_others_survive():
    other = "<table><tbody><tr><td>다른표</td></tr></tbody></table>"
    variants = _recorder()
    variants.record(HTML, {"markdown": MD})
    text = HTML + "\n사이 문단\n" + other

    rendered, misses = variants.render(text, "markdown")
    assert misses == 0            # 기록되지 않은 표는 미치환으로 세지 않는다
    assert MD in rendered
    assert other in rendered      # 내용 손실 없음


@pytest.mark.unit
def test_missing_table_text_is_counted_as_miss():
    """기록은 있는데 청크 텍스트에 없으면(중간에서 잘린 경우) primary 표기로 남긴다."""
    variants = _recorder()
    variants.record(HTML, {"markdown": MD})

    rendered, misses = variants.render("표가 잘려 나간 본문", "markdown")
    assert rendered == "표가 잘려 나간 본문"
    assert misses == 1


@pytest.mark.unit
def test_same_table_in_two_chunks_is_replaced_in_both():
    variants = _recorder()
    variants.record(HTML, {"markdown": MD})

    for text in ("첫 청크\n" + HTML, "둘째 청크\n" + HTML):
        rendered, misses = variants.render(text, "markdown")
        assert MD in rendered and misses == 0


@pytest.mark.unit
def test_longer_piece_is_replaced_before_its_substring():
    """같은 표의 분할 조각이 서로의 부분 문자열이면 짧은 쪽을 먼저 바꿔선 안 된다."""
    short = "<table><tbody><tr><td>1</td></tr></tbody></table>"
    long = short + "<table><tbody><tr><td>2</td></tr></tbody></table>"
    variants = _recorder(("markdown",))
    variants.record(short, {"markdown": "SHORT-MD"})
    variants.record(long, {"markdown": "LONG-MD"})

    rendered, misses = variants.render("본문\n" + long, "markdown")
    assert rendered == "본문\nLONG-MD"
    assert misses == 0


@pytest.mark.unit
def test_memo_serializes_each_table_and_format_once():
    """표 텍스트 생성부는 여러 번 불린다. 변형까지 그만큼 늘면 직렬화 비용이 배가 된다."""
    variants = _recorder()
    calls = []

    def build():
        calls.append(1)
        return MD

    for _ in range(3):
        assert variants.memo("#/tables/0", "markdown", build) == MD
    assert len(calls) == 1


@pytest.mark.unit
def test_memo_swallows_serialization_failure():
    variants = _recorder()

    def boom():
        raise RuntimeError("직렬화 실패")

    assert variants.memo("#/tables/0", "markdown", boom) == ""


@pytest.mark.unit
def test_field_names_maps_formats_to_chunk_fields():
    assert tv.field_names(("html",)) == ("text_table_html",)
    assert tv.field_names(("markdown",)) == ("text_table_md",)
    assert set(tv.field_names()) == {"text_table_html", "text_table_md"}


@pytest.mark.unit
def test_refs_narrow_the_scan_to_tables_the_chunk_holds():
    """표가 수백 개인 문서에서 청크마다 전체 기록을 훑으면 치환 비용이 표 수에 비례한다."""
    other = "<table><tbody><tr><td>다른표</td></tr></tbody></table>"
    variants = _recorder(("markdown",))
    variants.record(HTML, {"markdown": MD}, "#/tables/0")
    variants.record(other, {"markdown": "OTHER-MD"}, "#/tables/1")

    text = "본문\n" + HTML
    rendered, misses = variants.render(text, "markdown", ["#/texts/0", "#/tables/0"])
    assert rendered == "본문\n" + MD
    assert misses == 0
    # 다른 표는 후보에서 빠져 남의 내용이 섞이지 않는다.
    assert "OTHER-MD" not in rendered
    # 청크가 담았다고 한 표가 텍스트에 없으면 그건 관측해야 할 미치환이다.
    assert variants.render(text, "markdown", ["#/tables/1"]) == (text, 1)


@pytest.mark.unit
def test_records_without_self_ref_stay_candidates_for_every_chunk():
    """self_ref 를 못 읽은 표까지 색인 필터에 걸려 빠지면 조용히 치환이 멈춘다."""
    variants = _recorder(("markdown",))
    variants.record(HTML, {"markdown": MD})            # self_ref 없음

    rendered, misses = variants.render("본문\n" + HTML, "markdown", ["#/tables/9"])
    assert rendered == "본문\n" + MD
    assert misses == 0
