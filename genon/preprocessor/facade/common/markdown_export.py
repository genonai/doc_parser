"""DoclingDocument → markdown 직렬화의 공용 관문.

docling HTML 백엔드는 `<a href>` 를 텍스트가 아니라 `TextItem.hyperlink` 로 보존하고,
`[라벨](URL)` 문자열은 docling_core 의 markdown serializer 가 찍는다
(`CommonParams.include_hyperlinks` 기본값이 True). URL 은 RAG 검색에 기여하지 않으면서
청크 예산만 먹으므로 라벨만 남기고 버린다 - 실측으로 상품카드 청크에
`[www.samsungfire.com](http://www.samsungfire.com/)` 처럼 라벨과 URL 이 통째로 중복됐다.

억제는 호출부에서만 가능하다. docling_core 는 포크 대상이 아니고,
`DoclingDocument.export_to_markdown()` 과 `TableItem.export_to_markdown()` 은
`include_hyperlinks` 인자를 받지 않는다. 그래서 이 모듈이 두 함수를 serializer 구성으로
대체하고, 링크 억제를 기본값으로 깔아 준다.

`export_markdown` 이 `export_to_markdown` 과 등가인 근거(docling_core 2.85 확인):
지정하지 않은 인자에 대해 `export_to_markdown` 이 넘기는 값과 `MarkdownParams` 기본값이
전부 일치한다(labels=DOCUMENT_TOKENS_EXPORT_LABELS, layers={BODY}, wrap_width=None,
blocked_meta_names=set()). 따라서 안 넘기면 그만이고 출력은 같다.

docling 타입은 함수 안에서 lazy import 한다. 배포본이 docling 버전에 묶이지 않게 한다.
"""

from typing import Any, Optional

# 라벨은 남기고 URL 만 버린다. 모든 markdown 직렬화의 최소 공통.
MD_NO_LINKS = {"include_hyperlinks": False}

# 청크 텍스트나 LLM 입력으로 쓸 평문 옵션. markdown 이스케이프와 이미지 자리표시자는 노이즈다.
MD_PLAIN_TEXT_OPTS = {
    **MD_NO_LINKS,
    "escape_html": False,
    "escape_underscores": False,
    "image_placeholder": "",
}

# `export_to_markdown` 인자명 → `MarkdownParams` 필드명. 이름이 다른 것만 적는다.
_ARG_TO_PARAM = {
    "included_content_layers": "layers",
    "from_element": "start_idx",
    "to_element": "stop_idx",
}

# `export_to_markdown` 이 받기는 하나 무시하는 인자. 넘겨도 조용히 버린다.
_DEPRECATED_ARGS = ("delim", "strict_text", "use_legacy_annotations")


def _to_param_kwargs(kwargs: dict) -> dict:
    """`export_to_markdown` 호출 인자를 `MarkdownParams` 인자로 옮긴다."""
    out: dict = {}
    for key, value in kwargs.items():
        if key in _DEPRECATED_ARGS:
            continue
        if key == "page_no":
            # 단일 페이지 지정은 pages 집합 하나로 표현된다.
            out["pages"] = None if value is None else {value}
        elif key == "text_width":
            # wrap_width 는 PositiveInt 라 0 이하는 "감싸지 않음"(None)이다.
            out["wrap_width"] = value if isinstance(value, int) and value > 0 else None
        elif key == "blocked_meta_names":
            out["blocked_meta_names"] = value or set()
        else:
            out[_ARG_TO_PARAM.get(key, key)] = value
    return out


def markdown_params(**overrides: Any):
    """링크 억제를 기본으로 깐 `MarkdownParams`.

    `MarkdownParams(...)` 를 직접 만들던 자리를 그대로 대체한다. 인자 이름은 동일하다.
    """
    from docling_core.transforms.serializer.markdown import MarkdownParams

    return MarkdownParams(**{**MD_NO_LINKS, **overrides})


def export_markdown(
    doc: Any,
    *,
    item: Any = None,
    table_serializer: Optional[Any] = None,
    **kwargs: Any,
) -> str:
    """`doc.export_to_markdown(**kwargs)` / `item.export_to_markdown(doc)` 의 대체.

    인자 이름은 `export_to_markdown` 을 그대로 쓴다(`page_no`, `included_content_layers`,
    `text_width` 등). `item` 을 주면 그 아이템만 직렬화한다. `table_serializer` 는
    표를 다른 표기형태로 바꿔 끼우는 경로(`enrichment/json_records`)를 위해 통과시킨다.
    """
    from docling_core.transforms.serializer.markdown import MarkdownDocSerializer

    params = markdown_params(**_to_param_kwargs(kwargs))
    serializer_kwargs: dict = {"doc": doc, "params": params}
    if table_serializer is not None:
        serializer_kwargs["table_serializer"] = table_serializer
    serializer = MarkdownDocSerializer(**serializer_kwargs)
    if item is not None:
        return serializer.serialize(item=item).text
    return serializer.serialize().text
