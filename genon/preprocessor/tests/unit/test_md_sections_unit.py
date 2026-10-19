"""markdown.sections — 헤딩 기준 청크 본문 섹션 선택 단위 테스트(#526)."""
from __future__ import annotations

from pathlib import Path

import pytest

from genon.preprocessor.processing.converters.md_sections import MarkdownSectionsSpec

PRODUCT_CFG = {
    "after_heading": "## 원문 전사",
    "exclude": ["## 문서 개요", "## 핵심 키워드", "## 내용 색인", "## 원문*쪽-목차"],
}

# 캡처한 실물 구조 — 안내 섹션 → 빈 `## 원문 전사` → 목차 페이지 → 원문 페이지.
CAPTURE = """# 無비추미암보험

## 문서 개요
- 생성 개요

## 핵심 키워드
- 생성 키워드

## 내용 색인
- 1쪽: 목 차

## 원문 전사

<!-- source_page: 1 -->
## 원문 1쪽 - 목 차

```text
목차 본문
```

<!-- source_page: 2 -->
## 원문 2쪽 - 상품요약서

```text
## 펜스 안 줄
원문 본문
```
"""


@pytest.mark.unit
@pytest.mark.parametrize("cfg, text, kept, dropped", [
    # 표식이 있으면 앞을 지우고 상품명 H1 만 남긴다. 표식 줄과 목차 페이지도 빠지고,
    # 펜스 안의 '#' 줄은 헤딩이 아니므로 남는다.
    (PRODUCT_CFG, CAPTURE,
     ["# 無비추미암보험", "## 원문 2쪽 - 상품요약서", "## 펜스 안 줄", "원문 본문", "source_page: 2"],
     ["생성 개요", "생성 키워드", "1쪽: 목 차", "원문 전사", "목차 본문"]),
    # 표식이 없어도 exclude 는 적용된다(정책 A).
    (PRODUCT_CFG, "# 상품\n\n## 핵심 키워드\n키\n\n## 문서 개요\n개요\n\n## 원문 1쪽 - 상품요약서\n본문\n",
     ["# 상품", "## 원문 1쪽 - 상품요약서", "본문"], ["키", "개요"]),
    # exclude 는 다음 헤딩 직전까지만 지운다 — 안내 아래 하위 레벨로 붙은 원문은 남는다.
    (PRODUCT_CFG, "# 상품\n\n## 내용 색인\n- 1쪽\n\n### 원문 1쪽\n원문 본문\n",
     ["### 원문 1쪽", "원문 본문"], ["- 1쪽"]),
    # 레벨을 지정한 패턴은 다른 레벨의 같은 이름을 건드리지 않는다.
    (PRODUCT_CFG, "# 상품\n\n## 원문 1쪽\n\n### 핵심 키워드\n원문 속 같은 이름\n",
     ["### 핵심 키워드", "원문 속 같은 이름"], []),
    # 공백·대소문자·제목 전체 강조를 무시하고, 레벨 없는 패턴은 모든 레벨에 일치한다.
    ({"exclude": ["목차"]}, "# 상품\n\n### **목 차**\n목차 본문\n\n## 원문\n본문\n",
     ["## 원문", "본문"], ["목차 본문"]),
    # 여러 줄 HTML 주석 안의 '#' 줄은 시작점이 되지 않는다.
    ({"after_heading": "## 원문 전사"}, "# 상품\n\n<!--\n## 원문 전사\n-->\n\n## 원문 1쪽\n본문\n",
     ["<!--", "-->", "## 원문 1쪽"], []),
    # 일부 키만 본문에서 뺀 front matter 는 시작점 앞에 있어도 보존한다.
    ({"after_heading": "## 원문 전사"}, "---\nimportant: 남길 값\n---\n# 상품\n\n## 개요\n안내\n\n## 원문 전사\n본문\n",
     ["important: 남길 값", "# 상품", "본문"], ["안내", "원문 전사"]),
])
def test_section_selection(cfg, text, kept, dropped):
    out, _ = MarkdownSectionsSpec.from_config(cfg).apply(text)

    assert all(k in out for k in kept), out
    assert not any(d in out for d in dropped), out


@pytest.mark.unit
def test_unmatched_document_is_returned_as_is():
    text = "# 곰두리보장보험\n\n<!-- source_page: 1 -->\n## 원문 1쪽\n\n본문"

    assert MarkdownSectionsSpec.from_config(PRODUCT_CFG).apply(text) == (text, 0)


@pytest.mark.unit
@pytest.mark.parametrize("cfg", [
    "## 원문 전사",
    {"after_headng": "## 원문 전사"},
    {"after_heading": ""},
    {"after_heading": "##원문 전사"},
    {"exclude": "## 문서 개요"},
    {"exclude": ["*"]},
    {"exclude": [42]},
])
def test_invalid_config_fails_at_startup(cfg):
    with pytest.raises(ValueError):
        MarkdownSectionsSpec.from_config(cfg)


@pytest.mark.unit
@pytest.mark.parametrize("rest", ["<!--\n주석\n-->\n", "```text\n```\n"])
def test_only_markup_left_counts_as_no_body(rest):
    """여러 줄 주석이나 빈 펜스만 남아도 본문이 없는 것으로 본다."""
    with pytest.raises(ValueError, match="남은 본문이 없습니다"):
        MarkdownSectionsSpec.from_config(PRODUCT_CFG).apply(f"# 상품\n\n## 원문 전사\n\n{rest}")


@pytest.mark.unit
def test_nothing_left_stops_parsing_with_service_error(tmp_path: Path):
    """지운 뒤 본문이 비면 원문으로 되돌리지 않고 파싱 오류로 멈춘다."""
    from genon.preprocessor.processing.core.errors import GenosServiceException
    from genon.preprocessor.processing.core.parser import ParserCore

    src = tmp_path / "doc.md"
    src.write_text("# 상품\n\n## 문서 개요\n개요\n", encoding="utf-8")
    sections = MarkdownSectionsSpec.from_config(PRODUCT_CFG)

    # ParserCore._prepare_markdown 은 사설 정적 메소드지만, 공개 경로는 facade 기동뿐이다.
    with pytest.raises(GenosServiceException, match="남은 본문이 없습니다"):
        ParserCore._prepare_markdown(str(src), str(tmp_path), None, None, sections_spec=sections)
