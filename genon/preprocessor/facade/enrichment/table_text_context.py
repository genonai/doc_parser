"""텍스트 기반 표를 custom_fields LLM 호출에 함께 싣기 위한 컨텍스트 유틸리티."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from docling_core.types import DoclingDocument
from docling_core.types.doc import DocItem, PictureItem, TableItem
from docling_core.types.doc.document import ContentLayer

from genon.preprocessor.facade.common.markdown_export import export_markdown


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _as_int(value: Any, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(value))
    except (TypeError, ValueError):
        return default


def merge_table_text_description(common: dict | None, local: dict | None) -> dict:
    """프로세서 공통 설정 위에 문서유형 설정을 key 단위로 얹는다(문서유형 우선).

    `rag` 하위도 통째로 교체하지 않고 key 단위로 병합해, 문서유형에서 한 항목만 바꿔도
    나머지 공통값이 살아 있게 한다.
    """
    base = dict(common) if isinstance(common, dict) else {}
    override = dict(local) if isinstance(local, dict) else {}
    merged = {**base, **override}
    base_rag = base.get("rag") if isinstance(base.get("rag"), dict) else {}
    override_rag = override.get("rag") if isinstance(override.get("rag"), dict) else {}
    if base_rag or override_rag:
        merged["rag"] = {**base_rag, **override_rag}
    return merged


@dataclass(frozen=True)
class TableTextDescriptionOptions:
    enabled: bool = False
    input_format: str = "auto"
    before_items: int = 3
    after_items: int = 2
    max_context_chars: int = 1500
    max_context_tokens: int = 128000
    completion_reserved_tokens: int = 8000
    overflow_policy: str = "batch"
    conflict_policy: str = "prefer_text"
    retrieval_context_max_chars: int = 350
    key_fact_limit: int = 3
    key_fact_max_chars: int = 160
    search_terms_limit: int = 8
    include_search_terms: bool = False
    repeat_context_on_split: bool = True
    prompt_template: str = ""

    @classmethod
    def from_config(cls, cfg: dict | None) -> "TableTextDescriptionOptions":
        cfg = cfg if isinstance(cfg, dict) else {}
        rag = cfg.get("rag") if isinstance(cfg.get("rag"), dict) else {}
        input_format = str(cfg.get("input_format") or "auto").strip().lower()
        if input_format not in {"markdown", "html", "auto"}:
            input_format = "auto"
        overflow = str(cfg.get("overflow_policy") or "batch").strip().lower()
        if overflow not in {"batch", "skip", "error"}:
            overflow = "batch"
        conflict = str(cfg.get("conflict_policy") or "prefer_text").strip().lower()
        if conflict not in {"prefer_text", "prefer_image", "error"}:
            conflict = "prefer_text"
        return cls(
            enabled=_as_bool(cfg.get("enable", cfg.get("enabled")), False),
            input_format=input_format,
            before_items=_as_int(cfg.get("before_items"), 3),
            after_items=_as_int(cfg.get("after_items"), 2),
            max_context_chars=_as_int(cfg.get("max_context_chars"), 1500, 1),
            max_context_tokens=_as_int(cfg.get("max_context_tokens"), 128000, 1),
            completion_reserved_tokens=_as_int(cfg.get("completion_reserved_tokens"), 8000),
            overflow_policy=overflow,
            conflict_policy=conflict,
            retrieval_context_max_chars=_as_int(rag.get("retrieval_context_max_chars"), 350, 1),
            key_fact_limit=_as_int(rag.get("key_fact_limit"), 3),
            key_fact_max_chars=_as_int(rag.get("key_fact_max_chars"), 160, 1),
            search_terms_limit=_as_int(rag.get("search_terms_limit"), 8),
            include_search_terms=_as_bool(rag.get("include_search_terms"), False),
            repeat_context_on_split=_as_bool(rag.get("repeat_context_on_split"), True),
            prompt_template=str(cfg.get("prompt_template") or cfg.get("prompt") or "").strip(),
        )


@dataclass(frozen=True)
class TableTextTarget:
    table_id: str
    table_item: TableItem
    page_no: int
    section_header: str
    caption: str
    before_context: str
    table_text: str
    after_context: str
    input_format: str


def _single_line(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _page_no(item: DocItem, default: int = 1) -> int:
    prov = getattr(item, "prov", None) or []
    value = getattr(prov[0], "page_no", None) if prov else None
    return value if isinstance(value, int) and value > 0 else default


def _context_candidate(item: DocItem) -> bool:
    if isinstance(item, (TableItem, PictureItem)):
        return False
    label = getattr(item, "label", None)
    label = label.value if hasattr(label, "value") else str(label or "")
    return label not in {"page_header", "page_footer"} and bool(_single_line(getattr(item, "text", "")))


def _neighbors(items: list[DocItem], index: int, page: int, count: int, direction: int) -> list[str]:
    found: list[tuple[bool, str]] = []
    cursor = index + direction
    while 0 <= cursor < len(items) and len(found) < count:
        item = items[cursor]
        if _context_candidate(item):
            found.append((_page_no(item, page) == page, _single_line(getattr(item, "text", ""))))
        cursor += direction
    same = [text for is_same, text in found if is_same]
    cross = [text for is_same, text in found if not is_same]
    selected = (same + cross)[:count]
    if direction < 0:
        selected.reverse()
    return selected


def _section_header(items: list[DocItem], index: int) -> str:
    for item in reversed(items[:index]):
        label = getattr(item, "label", None)
        label = label.value if hasattr(label, "value") else str(label or "")
        if label in {"section_header", "title"}:
            text = _single_line(getattr(item, "text", ""))
            if text:
                return text
    return ""


def _table_text(item: TableItem, document: DoclingDocument, fmt: str) -> str:
    if fmt == "html":
        return str(item.export_to_html(document) or "").strip()
    return str(export_markdown(document, item=item) or "").strip()


def _resolve_input_format(document: DoclingDocument, configured: str) -> str:
    if configured != "auto":
        return configured
    origin = getattr(document, "origin", None)
    mimetype = str(getattr(origin, "mimetype", "") or "").lower()
    filename = str(getattr(origin, "filename", "") or "").lower()
    if mimetype in {"text/html", "application/xhtml+xml"} or filename.endswith(
        (".html", ".htm", ".xhtml")
    ):
        return "html"
    return "markdown"


def collect_table_text_targets(
    document: DoclingDocument,
    options: TableTextDescriptionOptions,
    *,
    input_format: str | None = None,
    max_context_chars: int | None = None,
) -> list[TableTextTarget]:
    """문서의 모든 표와 주변 본문을 LLM 입력용 target 으로 모은다.

    `input_format`/`max_context_chars` 를 주면 설정값 대신 그 값으로 수집한다 —
    프롬프트가 예산을 넘을 때 문맥을 줄이거나 markdown 으로 낮춰 다시 부르기 위한 것이다.
    """
    context_chars = options.max_context_chars if max_context_chars is None else max(1, max_context_chars)
    items = [
        item for item, _ in document.iterate_items(
            included_content_layers={ContentLayer.BODY, ContentLayer.FURNITURE}
        )
    ]
    targets: list[TableTextTarget] = []
    fmt = _resolve_input_format(document, input_format or options.input_format)
    for index, item in enumerate(items):
        if not isinstance(item, TableItem):
            continue
        page = _page_no(item)
        before = "\n".join(_neighbors(items, index, page, options.before_items, -1))
        after = "\n".join(_neighbors(items, index, page, options.after_items, 1))
        try:
            caption = _single_line(item.caption_text(document))
        except Exception:
            caption = ""
        targets.append(TableTextTarget(
            table_id=f"table_{len(targets) + 1:04d}", table_item=item, page_no=page,
            section_header=_section_header(items, index), caption=caption,
            before_context=before[:context_chars],
            table_text=_table_text(item, document, fmt),
            after_context=after[:context_chars],
            input_format=fmt,
        ))
    return targets


def render_table_targets(targets: list[TableTextTarget]) -> str:
    blocks = []
    for target in targets:
        fmt = target.input_format
        blocks.append(
            f'<table_target id="{target.table_id}">\n'
            f"[섹션 헤더]\n{target.section_header or '-'}\n"
            f"[캡션]\n{target.caption or '-'}\n"
            f"[앞 문맥]\n{target.before_context or '-'}\n"
            f"[표 본문 format={fmt}]\n{target.table_text}\n"
            f"[뒤 문맥]\n{target.after_context or '-'}\n"
            "</table_target>"
        )
    return "<table_description_targets>\n" + "\n\n".join(blocks) + "\n</table_description_targets>"
