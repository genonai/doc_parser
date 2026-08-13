"""json_text — JSON 안의 본문 텍스트 추출/병합 단위 테스트."""
import json

import pytest

from genon.preprocessor.converters.json_text import (
    JsonTextSpec,
    build_merged_html,
    collect_text_fields,
    detect_format,
    json_payload_to_html,
)

pytestmark = pytest.mark.unit


# ── 키 재귀 매칭 ────────────────────────────────────────────────────────────

def test_collect_finds_keys_inside_arrays():
    """경로 문법 없이 pages[*].html 같은 배열 구조를 처리한다."""
    payload = {"pages": [{"name": "A", "html": "<p>a</p>"}, {"name": "B", "html": "<p>b</p>"}]}
    assert collect_text_fields(payload, ["html"]) == [("A", "<p>a</p>"), ("B", "<p>b</p>")]


def test_collect_finds_keys_at_arbitrary_depth():
    payload = {"a": {"b": {"c": [{"deep": {"html": "<p>깊음</p>"}}]}}}
    found = collect_text_fields(payload, ["html"])
    assert [v for _, v in found] == ["<p>깊음</p>"]


def test_collect_preserves_document_order():
    payload = {"first": {"html": "1"}, "mid": [{"html": "2"}, {"html": "3"}], "last": {"html": "4"}}
    assert [v for _, v in collect_text_fields(payload, ["html"])] == ["1", "2", "3", "4"]


def test_collect_labels_from_sibling_keys():
    payload = {"pages": [
        {"name": "이름라벨", "html": "x"},
        {"title": "타이틀라벨", "html": "y"},
        {"html": "z"},
    ]}
    assert [lb for lb, _ in collect_text_fields(payload, ["html"])] == [
        "이름라벨", "타이틀라벨", "html#3",
    ]


def test_collect_skips_non_string_and_empty_values():
    payload = {"html": 123, "pages": [{"html": ""}, {"html": "   "}, {"html": "ok"}]}
    assert [v for _, v in collect_text_fields(payload, ["html"])] == ["ok"]


def test_collect_does_not_recurse_into_matched_value():
    """값이 dict 이면 본문이 아니므로 매칭하지 않되, 그 아래는 계속 탐색한다."""
    payload = {"html": {"html": "안쪽"}}
    assert [v for _, v in collect_text_fields(payload, ["html"])] == ["안쪽"]


def test_collect_multiple_keys():
    payload = {"body_html": "<p>h</p>", "notes_md": "## m"}
    found = collect_text_fields(payload, ["body_html", "notes_md"])
    assert {v for _, v in found} == {"<p>h</p>", "## m"}


def test_collect_returns_empty_when_no_match():
    assert collect_text_fields({"other": "x"}, ["html"]) == []


# ── 포맷 판별 ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    ("<main><p>a</p><div>b</div><ul><li>c</li></ul></main>", "html"),
    ("<p>단일 블록도 html</p>", "html"),
    ("## 제목\n- 목록\n", "markdown"),
    ("| a | b |\n|---|---|\n| 1 | 2 |", "markdown"),
    ("```python\nx=1\n```", "markdown"),
    ("그냥 평문", "markdown"),
    ("1. 첫째\n2. 둘째", "markdown"),
])
def test_detect_format(value, expected):
    assert detect_format(value) == expected


def test_detect_format_biases_to_html_when_tags_dominate():
    """markdown 마커가 있어도 블록 태그가 많으면 html — 오판 손실이 비대칭이기 때문."""
    value = "## 제목\n<div>a</div><p>b</p><table><tr><td>c</td></tr></table>"
    assert detect_format(value) == "html"


# ── spec 파싱 ───────────────────────────────────────────────────────────────

def test_spec_requires_text_fields():
    with pytest.raises(ValueError):
        JsonTextSpec({})
    with pytest.raises(ValueError):
        JsonTextSpec({"text_fields": []})


def test_spec_accepts_single_string():
    assert JsonTextSpec({"text_fields": "html"}).text_fields == ["html"]


def test_spec_falls_back_on_invalid_values():
    spec = JsonTextSpec({"text_fields": ["html"], "format": "yaml", "missing_policy": "boom"})
    assert spec.format == "auto"
    assert spec.missing_policy == "skip"


# ── 병합 ────────────────────────────────────────────────────────────────────

def test_build_merged_html_creates_section_per_item():
    out = build_merged_html([("첫째", "<p>A</p>"), ("둘째", "<p>B</p>")], "문서")
    assert out.count("<section>") == 2
    assert "<h2>첫째</h2>" in out and "<h2>둘째</h2>" in out
    assert "A" in out and "B" in out


def test_build_merged_html_converts_markdown_tables():
    out = build_merged_html([("요약", "| a | b |\n|---|---|\n| 1 | 2 |")], "문서")
    assert "<table>" in out
    assert "<td>1</td>" in out


def test_build_merged_html_flattens_srcdoc_inside_value():
    value = (
        '<html><body><section class="page-section"><h2 class="page-title">1. 안쪽</h2>'
        '<iframe srcdoc="&lt;main&gt;&lt;p&gt;펼쳐진 본문&lt;/p&gt;&lt;/main&gt;"></iframe>'
        "</section></body></html>"
    )
    out = build_merged_html([("겉", value)], "문서")
    assert "펼쳐진 본문" in out


def test_build_merged_html_flattens_escaped_html_inside_value():
    """iframe 없이 값 전체가 escape 된 필드도 표 구조가 복원된다.

    `_section_node` 주석이 말하는 '이중인코딩' 경로. srcdoc 만 unescape 하던 시절엔
    escape 텍스트가 그대로 남아 표가 0개인 문서가 만들어졌다. 회귀 방지용.
    """
    value = (
        '<main><div class="wrap"><section>'
        "&lt;table&gt;&lt;tr&gt;&lt;td&gt;셀값&lt;/td&gt;&lt;/tr&gt;&lt;/table&gt;"
        + "&lt;p&gt;문단&lt;/p&gt;" * 12  # precheck 임계값(10) 초과
        + "</section></div></main>"
    )
    assert detect_format(value) == "html"

    out = build_merged_html([("겉", value)], "문서")
    assert "<table>" in out
    assert "셀값" in out
    assert "&lt;table&gt;" not in out


def test_build_merged_html_honors_forced_format():
    """format: markdown 강제 시 html 처럼 보이는 값도 markdown 으로 처리된다."""
    out = build_merged_html([("x", "## 제목")], "문서", forced_format="markdown")
    assert "<h2>제목</h2>" in out


# ── missing_policy ──────────────────────────────────────────────────────────

def test_missing_policy_skip_returns_empty_document():
    spec = JsonTextSpec({"text_fields": ["nope"]})
    out = json_payload_to_html({"a": 1}, spec, "제목")
    assert "<h1>제목</h1>" in out
    assert "<section>" not in out


def test_missing_policy_error_raises():
    spec = JsonTextSpec({"text_fields": ["nope"], "missing_policy": "error"})
    with pytest.raises(ValueError):
        json_payload_to_html({"a": 1}, spec, "제목")


# ── 실제 픽스처 ─────────────────────────────────────────────────────────────

def test_monimo_sample_preserves_benefit_text(sample_dir):
    """픽스처의 aria-hidden/display:none 안 본문이 병합 결과에 남아야 한다.

    이게 깨지면 custom_field_card.yaml 의 benefit_text 추출이 조용히 열화된다.
    """
    path = sample_dir / "json" / "monimo_card_sample.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    spec = JsonTextSpec({"text_fields": ["html", "summary_md"]})
    out = json_payload_to_html(payload, spec, path.stem)

    for needle in ("대중교통", "전기차 충전요금", "주유 10,000원", "동물병원"):
        assert needle in out, f"{needle} 가 병합 결과에서 사라졌습니다"
    # 항목 3개(pages) + summary_md
    assert out.count("<section>") == 4
    # footer 노이즈는 콘텐츠영역 선택으로 빠진다
    assert "202-81-45602" not in out
