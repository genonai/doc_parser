"""extractor: html_select — 원문 HTML 선택자 추출.

파싱된 평문이 아니라 **원문 HTML** 에서 값을 뽑는 경로다. 그래서 이 테스트가 고정하는 것은
두 가지다 — 선택자 문법이 기동 시 검증되는가, 그리고 파싱이 지워 버리는 신호(class·속성)를
실제로 잡아내는가.

본문 전문을 찍지 않고 단정문으로만 고정한다(길이·값 일치).
"""

from pathlib import Path

import pytest
import yaml

from genon.preprocessor.processing.enrichment import config_schema as cs
from genon.preprocessor.processing.enrichment import config_v2 as cv2
from genon.preprocessor.processing.enrichment import html_select

pytestmark = pytest.mark.unit


# 관심소식 원천의 모양을 줄인 것. 지켜야 할 성질만 남겼다 —
#   - 카테고리는 텍스트가 아니라 wrap div 의 **속성**에 있다
#   - 요약의 `newsletter-article-txt` class 는 본문 문단과 겹쳐 상자로 범위를 좁혀야 한다
#   - 상세 class 이름에는 원천 마크업의 오타(`newslertter-`)가 들어 있다
#   - 출처는 **주석 안**이라 잡히지 않는다
SAMPLE = """
<div class="newsletter-article-wrap" newsletter-title="이슈산책">
  <div class="new-banner-box">
    <span class="new-banner-sector">이슈산책</span>
    <p class="new-banner-headline">계산대 없는 무인가게</p>
    <img class="new-banner-logo" alt="삼성카드" src="//x/card.png" />
  </div>
  <div class="article-summary-box">
    <p class="newsletter-article-txt">요약 문단이다.</p>
  </div>
  <ul class="newslertter-article-content">
    <li><p class="h2-tit">소제목</p>
        <p class="newsletter-article-txt">본문 <em class="high-light">19% 증가</em>했다.</p></li>
  </ul>
  <!-- <p class="newsletter-source">by. sericeo</p>-->
</div>
"""

SELECTORS = {
    "TITLE": {"css": ".new-banner-headline", "attr": None},
    "CATEGORY": {"css": ".newsletter-article-wrap", "attr": "newsletter-title"},
    "COMPANY": {"css": ".new-banner-logo", "attr": "alt"},
    "SUMMARY": {"css": ".article-summary-box", "attr": None},
    "DETAIL": {"css": ".newslertter-article-content", "attr": None},
    "SOURCE": {"css": ".newsletter-source", "attr": None},
}


# ── 추출 ────────────────────────────────────────────────────────────────────

def test_extracts_text_and_attribute_values():
    """docling 파싱이 지우는 신호(class 로 지목한 요소, 사용자 속성)를 그대로 잡는다."""
    values = html_select.extract_fields(SAMPLE, SELECTORS)

    assert values["TITLE"] == "계산대 없는 무인가게"
    assert values["CATEGORY"] == "이슈산책"      # 속성값
    assert values["COMPANY"] == "삼성카드"       # img 의 alt
    assert values["SUMMARY"] == "요약 문단이다."


def test_summary_scope_is_the_box_not_the_shared_class():
    """`newsletter-article-txt` 는 본문과 겹친다 — 상자로 좁힌 값만 들어와야 한다."""
    values = html_select.extract_fields(SAMPLE, SELECTORS)

    assert "본문" not in values["SUMMARY"]
    assert "본문" in values["DETAIL"]


def test_detail_keeps_inline_emphasis_text():
    """`em` 같은 인라인 강조는 문장 일부다 — 평문화해도 남아야 한다."""
    detail = html_select.extract_fields(SAMPLE, SELECTORS)["DETAIL"]

    assert "소제목" in detail and "19% 증가" in detail


def test_commented_out_element_is_not_extracted():
    """주석은 원천이 꺼 둔 것이다. 되살리지 않고 None 으로 두되 키는 남긴다."""
    values = html_select.extract_fields(SAMPLE, SELECTORS)

    assert "SOURCE" in values
    assert values["SOURCE"] is None


def test_live_element_of_the_same_selector_is_extracted():
    """같은 선택자라도 주석이 아니면 잡힌다 — 못 잡는 이유가 선택자가 아님을 고정한다."""
    live = SAMPLE.replace(
        '<!-- <p class="newsletter-source">by. sericeo</p>-->',
        '<p class="newsletter-source">by. sericeo</p>',
    )

    assert html_select.extract_fields(live, SELECTORS)["SOURCE"] == "by. sericeo"


def test_missing_element_keeps_the_key():
    """선언한 필드는 못 찾아도 키를 남긴다(사라지면 청크에서 조용히 빠진다)."""
    values = html_select.extract_fields("<div></div>", SELECTORS)

    assert set(values) == set(SELECTORS)
    assert all(v is None for v in values.values())


# ── 선택자 컴파일(기동 시 검증) ──────────────────────────────────────────────

def test_compile_rejects_unparsable_selector():
    with pytest.raises(ValueError, match="선택자를 해석할 수 없습니다"):
        html_select.compile_selectors(
            {"select_map": {"X": {"css": "div[", "attr": None}}}, label="t"
        )


def test_compile_rejects_empty_selector():
    with pytest.raises(ValueError, match="select 값이 비어 있습니다"):
        html_select.compile_selectors(
            {"select_map": {"X": {"css": "  ", "attr": None}}}, label="t"
        )


# ── 설정 표기(v2) ───────────────────────────────────────────────────────────

def _normalize(fields: dict, kind: str = "html") -> dict:
    raw = {"schema": "v2", "source": {"kind": kind}, "fields": fields}
    return cv2.load(raw, label="t")[0]


def test_kind_html_infers_the_html_select_extractor():
    _internal, extractor = cv2.load(
        {"schema": "v2", "source": {"kind": "html"},
         "fields": {"TITLE": {"select": ".t"}}}, label="t"
    )

    assert extractor == "html_select"


def test_select_and_attr_become_select_map():
    internal = _normalize({
        "TITLE": {"select": ".new-banner-headline"},
        "CATEGORY": {"select": ".wrap", "attr": "newsletter-title"},
    })

    assert internal["select_map"] == {
        "TITLE": {"css": ".new-banner-headline", "attr": None},
        "CATEGORY": {"css": ".wrap", "attr": "newsletter-title"},
    }


def test_select_is_rejected_on_other_kinds():
    """kind 를 잘못 적고 선택자를 쓰면 값이 조용히 사라진다 — 기동에서 막는다."""
    with pytest.raises(cv2.ConfigV2Error, match="kind: html 전용"):
        _normalize({"TITLE": {"select": ".t"}}, kind="document")


def test_attr_without_select_is_rejected():
    with pytest.raises(cv2.ConfigV2Error, match="select 와 함께"):
        _normalize({"TITLE": {"attr": "data-x"}})


def test_html_select_does_not_accept_llm_keys():
    """연결·프롬프트 키는 이 extractor 가 읽지 않는다 — 적으면 기동 실패다."""
    with pytest.raises(ValueError, match="읽지 않는 키"):
        cs.validate_known_keys(
            {"select_map": {}, "url": "http://x"}, label="t", extractor="html_select"
        )


def test_select_map_is_covered_by_v2_notation():
    """내부 키를 늘리고 v2 표기를 잊는 드리프트를 막는다(config_v2 의 가드와 같은 성질)."""
    assert "select_map" in cv2.COVERED_V1_KEYS
    assert "select_map" in cs.EXTRACTOR_KEYS["html_select"]


# ── 출고 설정 ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("resource_dir", ["resource", "resource_dev"])
def test_shipped_monimo_news_selectors_compile_and_cover_the_requirement(resource_dir):
    """관심소식 출고 설정이 요건표의 다섯 값을 모두 지목하는지 고정한다."""
    base = Path(__file__).resolve().parents[2] / resource_dir
    raw = yaml.safe_load(
        (base / "custom_field_monimo_news.yaml").read_text(encoding="utf-8")
    )
    internal, extractor = cv2.load(raw, label="custom_field_monimo_news.yaml")

    assert extractor == "html_select"
    compiled = html_select.compile_selectors(internal, label="monimo_news")
    assert set(compiled) == {"TITLE", "CATEGORY", "SUMMARY", "DETAIL", "SOURCE"}
    # 카테고리만 속성에서 온다. 나머지는 요소 텍스트다.
    assert compiled["CATEGORY"]["attr"] == "newsletter-title"
    assert [t for t, s in compiled.items() if s["attr"]] == ["CATEGORY"]
    # 원천 마크업의 오타를 그대로 써야 상세가 잡힌다.
    assert compiled["DETAIL"]["css"] == ".newslertter-article-content"


@pytest.mark.parametrize("resource_dir", ["resource", "resource_dev"])
def test_shipped_monimo_news_extracts_from_the_source_shape(resource_dir):
    """출고 선택자를 원천 모양에 걸어 실제로 값이 나오는지까지 본다."""
    base = Path(__file__).resolve().parents[2] / resource_dir
    raw = yaml.safe_load(
        (base / "custom_field_monimo_news.yaml").read_text(encoding="utf-8")
    )
    internal, _extractor = cv2.load(raw, label="custom_field_monimo_news.yaml")
    values = html_select.extract_fields(
        SAMPLE, html_select.compile_selectors(internal, label="monimo_news")
    )

    assert values["TITLE"] == "계산대 없는 무인가게"
    assert values["CATEGORY"] == "이슈산책"
    assert values["SUMMARY"] == "요약 문단이다."
    assert "소제목" in values["DETAIL"]
    assert values["SOURCE"] is None  # 이 원천에서는 주석 처리되어 온다


# ── 파서 ↔ enrichment 통로 ──────────────────────────────────────────────────

def test_source_html_round_trips_through_the_enrichment_context():
    context = {}
    html_select.stash_source_html(context, "<p>x</p>")

    assert html_select.source_html_from({"_enrichment_context": context}) == "<p>x</p>"


def test_source_html_is_absent_without_the_context():
    """통로가 없으면 빈 문자열이다 — 보조 입력이라 없다고 실패시키지 않는다."""
    assert html_select.source_html_from({}) == ""
    assert html_select.source_html_from({"_enrichment_context": None}) == ""


def _parser_with(enrichers):
    """__init__(네트워크/config) 우회 — 이 함수가 보는 것은 _intel 하나뿐이다."""
    from types import SimpleNamespace

    from genon.preprocessor.facade import parser_processor as pp

    parser = object.__new__(pp.DocumentProcessor)
    parser._intel = SimpleNamespace(custom_fields_enrichers=enrichers)
    return parser


class _Wanting:
    def wants_source_html(self, doc_type=None):
        return True


class _NotWanting:
    def wants_source_html(self, doc_type=None):
        return False


def test_parser_hands_over_source_html_when_an_extractor_wants_it(tmp_path):
    path = tmp_path / "doc.html"
    path.write_text(SAMPLE, encoding="utf-8")
    context = {}

    _parser_with([_Wanting()])._stash_source_html(
        str(path), doc_type="monimo_news", _enrichment_context=context
    )

    assert html_select.SOURCE_HTML_KEY in context
    assert "newsletter-article-wrap" in context[html_select.SOURCE_HTML_KEY]


def test_parser_does_not_read_the_file_when_nobody_wants_it(tmp_path):
    """필요한 문서유형이 없으면 파일을 열지도 않는다(없는 경로여도 조용히 지나간다)."""
    context = {}

    _parser_with([_NotWanting()])._stash_source_html(
        str(tmp_path / "does_not_exist.html"), _enrichment_context=context
    )

    assert context == {}


def test_parser_skips_non_html_inputs(tmp_path):
    path = tmp_path / "doc.pdf"
    path.write_text("x", encoding="utf-8")
    context = {}

    _parser_with([_Wanting()])._stash_source_html(
        str(path), _enrichment_context=context
    )

    assert context == {}


# ── 원천 샘플 파일 ───────────────────────────────────────────────────────────

# sample_files/monimo/TD00008415_d_5199.html.json 은 카드 WCMS 관심소식 원천의 모양을
# 그대로 옮긴 것이다(2026-09-11 캡쳐 `01_news_html_06~09` 복원). 실제 운영 파일 자체가
# 아니므로 값의 사실 여부가 아니라 **구조**를 고정하는 용도로만 쓴다 — json 두 겹 아래의
# `content`, class·속성 이름, 그리고 주석 처리된 출처.
NEWS_SAMPLE = "TD00008415_d_5199.html.json"


def _shipped_selectors(resource_dir: str = "resource") -> dict:
    base = Path(__file__).resolve().parents[2] / resource_dir
    raw = yaml.safe_load(
        (base / "custom_field_monimo_news.yaml").read_text(encoding="utf-8")
    )
    internal, _extractor = cv2.load(raw, label="custom_field_monimo_news.yaml")
    return html_select.compile_selectors(internal, label="monimo_news")


def _news_sample_content() -> str:
    """`source.pre.json.body_from: [content]` 이 꺼내는 것과 같은 값을 얻는다."""
    import json

    from genon.preprocessor.converters.json_text import collect_text_fields

    path = (
        Path(__file__).resolve().parents[2] / "sample_files" / "monimo" / NEWS_SAMPLE
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = collect_text_fields(payload, ["content"])
    assert len(items) == 1, "원천은 content 하나짜리다"
    return items[0][1]


def test_body_from_finds_content_at_any_depth():
    """`content` 는 `wcmsData.html.content` 로 두 겹 아래 있다 — 이름만으로 잡혀야 한다."""
    assert "newsletter-article-wrap" in _news_sample_content()


def test_shipped_selectors_extract_from_the_sample_file():
    """출고 선택자를 원천 샘플에 그대로 걸어 다섯 값을 확인한다."""
    values = html_select.extract_fields(_news_sample_content(), _shipped_selectors())

    assert values["TITLE"] == "계산대 없는 무인가게, 누가 열고 누가 찾을까요?"
    assert values["CATEGORY"] == "이슈산책"
    assert values["SUMMARY"].startswith("삼성카드 데이터랩이 2022년 6월부터")
    assert len(values["SUMMARY"]) == 182
    # 상세는 소제목 4개와 그래프 수치(.sr-only)까지 담는다.
    assert values["DETAIL"].count("무인점포") > 5
    assert "가동 가맹점수 전체 2023년 6월" in values["DETAIL"]
    assert len(values["DETAIL"]) > 2000
    # 이 문서의 출처는 주석 처리되어 있다.
    assert values["SOURCE"] is None


def test_sample_summary_does_not_swallow_the_body():
    """요약 선택자가 본문까지 먹으면 길이로 바로 드러난다."""
    values = html_select.extract_fields(_news_sample_content(), _shipped_selectors())

    assert len(values["SUMMARY"]) < len(values["DETAIL"]) / 10
