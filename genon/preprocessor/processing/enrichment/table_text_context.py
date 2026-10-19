"""텍스트 기반 표를 custom_fields LLM 호출에 함께 싣기 위한 컨텍스트 유틸리티."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from docling_core.types import DoclingDocument
from docling_core.types.doc import DocItem, PictureItem, TableItem
from docling_core.types.doc.document import ContentLayer

from genon.preprocessor.processing.common.config_parse import (
    MODEL_CONTEXT_TOKENS_KEYS,
    resolve_model_context_tokens,
)
from genon.preprocessor.processing.common.markdown_export import export_markdown
from genon.preprocessor.processing.enrichment.config_schema import TABLE_TEXT_SKIP_KEYS
from genon.preprocessor.processing.chunking.rich_cells import table_embedded_refs
from genon.preprocessor.processing.chunking.table_shape import (
    analyze_grid,
    cell_text,
    degenerate_reason,
    leading_header_row_count,
)

_log = logging.getLogger(__name__)


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


# 응답 크기 추정에 쓰는 고정값. search_terms 는 상한만 있고 길이 규정이 없어 표현 1개를
# 넉넉히 잡고, 스캐폴드는 table_id 와 JSON 구두점이 차지하는 몫이다.
_SEARCH_TERM_CHARS = 20
_TABLE_ENTRY_SCAFFOLD_CHARS = 120


def canonical_enable_key(cfg: dict | None) -> dict:
    """`enable`/`enabled` 두 철자를 내부 표기 `enabled` 하나로 모은다.

    한 dict 안에 둘 다 있으면 `enable` 이 이긴다(`from_config` 와 같은 순서). 내부 표기를
    `enabled` 로 두는 것은 `enrichment_config` 가 공통 블록에 이미 그렇게 하기 때문이다 —
    두 곳이 다른 철자로 모으면 융합 사본과 공통 설정이 어긋난다.

    이 정규화가 없으면 키 단위 병합에서 두 철자가 **각각 살아남아**, 공통이 `enable: true`
    일 때 문서유형의 `enabled: false` 가 조용히 무시된다(사용자 yaml 은 `enable` 을 쓴다).
    """
    if not isinstance(cfg, dict):
        return {}
    if "enable" not in cfg:
        return dict(cfg)
    merged = dict(cfg)
    merged["enabled"] = merged.pop("enable")
    return merged


def _canonical_context_key(cfg: dict) -> dict:
    """context 한도의 옛 이름들을 현재 이름 `model_context_tokens` 하나로 모은다.

    `canonical_enable_key` 와 같은 이유다. 공통이 새 이름, 문서유형이 옛 이름이면 병합 뒤
    둘 다 남아 새 이름(공통값)이 우선하므로 문서유형 값이 조용히 무시된다.
    """
    value = resolve_model_context_tokens(cfg)
    if value is None:
        return cfg
    merged = {k: v for k, v in cfg.items() if k not in MODEL_CONTEXT_TOKENS_KEYS}
    merged[MODEL_CONTEXT_TOKENS_KEYS[0]] = value
    return merged


def _canonical_rag_skip(cfg: dict) -> dict:
    """한 계층 안의 편의 표기 둘을 내부 표기로 펼친다.

    - `rag.search_terms: N` → `search_terms_limit: N`, `include_search_terms: N > 0`.
      같은 계층에 옛 두 키가 함께 있으면 새 키가 우선한다. 값이 0 이상의 정수가 아니면 버린다.
    - `skip: false` → 모든 조건을 false 로 둔 dict. dict 로 펼쳐 두어야 다른 계층의 skip 과
      키 단위로 병합된다(위 계층의 `skip: false` 위에 문서유형이 한 조건만 켜면 나머지는 꺼진 채
      남는다). 조건이 새로 생기면 `TABLE_TEXT_SKIP_KEYS` 에 넣는 것만으로 이 표기가 함께 끈다.

    계층을 병합하기 **전에** 펼쳐야 한다. 병합 뒤에 펼치면 공통의 새 키가 문서유형의 옛 키를
    이긴다(`_canonical_context_key` 와 같은 이유).
    """
    rag = cfg.get("rag")
    expand_terms = isinstance(rag, dict) and "search_terms" in rag
    if not expand_terms and cfg.get("skip") is not False:
        return cfg
    merged = dict(cfg)
    if expand_terms:
        value = rag["search_terms"]
        sub = {k: v for k, v in rag.items() if k != "search_terms"}
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            sub["search_terms_limit"] = value
            sub["include_search_terms"] = value > 0
        merged["rag"] = sub
    if cfg.get("skip") is False:
        merged["skip"] = dict.fromkeys(TABLE_TEXT_SKIP_KEYS, False)
    return merged


def merge_table_text_description(common: dict | None, local: dict | None) -> dict:
    """프로세서 공통 설정 위에 문서유형 설정을 key 단위로 얹는다(문서유형 우선).

    `rag`·`skip` 하위도 통째로 교체하지 않고 key 단위로 병합해, 문서유형에서 한 항목만
    바꿔도 나머지 공통값이 살아 있게 한다.
    """
    base = _canonical_rag_skip(_canonical_context_key(canonical_enable_key(common)))
    override = _canonical_rag_skip(_canonical_context_key(canonical_enable_key(local)))
    merged = {**base, **override}
    for key in ("rag", "skip"):
        base_sub = base.get(key) if isinstance(base.get(key), dict) else {}
        override_sub = override.get(key) if isinstance(override.get(key), dict) else {}
        if base_sub or override_sub:
            merged[key] = {**base_sub, **override_sub}
    return merged


@dataclass(frozen=True)
class TableTextDescriptionOptions:
    enabled: bool = False
    input_format: str = "auto"
    before_items: int = 3
    after_items: int = 2
    max_context_chars: int = 1500
    max_context_tokens: int = 128000
    completion_reserved_tokens: int | None = None  # None 이면 호출의 max_tokens 만큼 예약한다
    overflow_policy: str = "batch"
    conflict_policy: str = "prefer_text"
    retrieval_context_max_chars: int = 350
    key_fact_limit: int = 3
    key_fact_max_chars: int = 160
    search_terms_limit: int = 8
    include_search_terms: bool = False
    repeat_context_on_split: bool = True
    prompt_template: str = ""
    concurrency: int = 8
    # LLM 전송에서 뺄 표(`table_skip_reason`). 레이아웃 표(값이 하나뿐이거나 데이터 행이 없는 표)는
    # 설명할 내용이 없으므로 기본으로
    # 빼고, 자기서술형 표는 검색 품질 확인이 문서유형마다 필요해 기본으로 끈다.
    # 앞선 표에 이미 실린 중복 표도 같은 이유로 문서유형에서 켠다.
    skip_layout: bool = True
    skip_self_describing: bool = False
    skip_contained_duplicate: bool = False

    @classmethod
    def from_config(cls, cfg: dict | None) -> "TableTextDescriptionOptions":
        cfg = _canonical_rag_skip(cfg) if isinstance(cfg, dict) else {}
        rag = cfg.get("rag") if isinstance(cfg.get("rag"), dict) else {}
        skip = cfg.get("skip") if isinstance(cfg.get("skip"), dict) else {}
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
            max_context_tokens=_as_int(resolve_model_context_tokens(cfg), 128000, 1),
            completion_reserved_tokens=(
                None if cfg.get("completion_reserved_tokens") is None
                else _as_int(cfg.get("completion_reserved_tokens"), 8000)
            ),
            overflow_policy=overflow,
            conflict_policy=conflict,
            retrieval_context_max_chars=_as_int(rag.get("retrieval_context_max_chars"), 350, 1),
            key_fact_limit=_as_int(rag.get("key_fact_limit"), 3),
            key_fact_max_chars=_as_int(rag.get("key_fact_max_chars"), 160, 1),
            search_terms_limit=_as_int(rag.get("search_terms_limit"), 8),
            include_search_terms=_as_bool(rag.get("include_search_terms"), False),
            repeat_context_on_split=_as_bool(rag.get("repeat_context_on_split"), True),
            prompt_template=str(cfg.get("prompt_template") or cfg.get("prompt") or "").strip(),
            concurrency=_as_int(cfg.get("concurrency"), 8, 1),
            skip_layout=_as_bool(skip.get("layout"), True),
            skip_self_describing=_as_bool(skip.get("self_describing"), False),
            skip_contained_duplicate=_as_bool(skip.get("contained_duplicate"), False),
        )

    @property
    def estimated_output_chars_per_table(self) -> int:
        """표 1개의 응답이 차지할 문자 수 추정(넘치는 쪽으로 보수 판정).

        배치에 표를 몇 개까지 담을 수 있는지는 입력 예산이 아니라 이 값과 `max_tokens` 가
        정한다. 프롬프트가 모델에게 약속받은 상한들을 그대로 더해 쓰므로, 모델이 지시를
        지키는 한 실제 응답은 이보다 짧다.
        """
        return (
            self.retrieval_context_max_chars
            + self.key_fact_limit * self.key_fact_max_chars
            + self.search_terms_limit * _SEARCH_TERM_CHARS
            + _TABLE_ENTRY_SCAFFOLD_CHARS
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


# ── LLM 전송 제외 판정 ──────────────────────────────────────────────────────
# 표 설명은 표 청크만으로는 표의 주제·조건이 드러나지 않을 때 검색을 돕는다. 표 청크가
# 이미 그 역할을 하는 표는 설명을 받아도 새로 잡히는 질의가 없으므로 LLM 에 보내지 않는다.
SKIP_LAYOUT = "layout"
SKIP_SELF_DESCRIBING = "self_describing"
SKIP_CONTAINED_DUPLICATE = "contained_duplicate"
# 청커가 바깥 표 셀(rich cell)로만 싣는 중첩 표. 이 표의 설명은 청크에 실리지 않는다.
SKIP_NESTED = "nested"

# 자기서술형 판정 임계. 값 셀이 이미 문장이면 설명이 덧붙일 정보가 없다.
_SELF_DESC_MAX_DATA_ROWS = 5
_SELF_DESC_COLS = (2, 3)
_SELF_DESC_MIN_AVG_CHARS = 25
_SELF_DESC_SENTENCE_CHARS = 20
_SELF_DESC_SENTENCE_RATIO = 0.8
# 금액·비율·기간이 든 표는 질의 어휘("몇 살까지", "얼마")와 표 어휘가 달라 설명을 유지한다.
_CONDITION_VALUE_RE = re.compile(r"\d[\d,.]*\s*(?:원|만원|억|%|세|일|년|개월)")
# 앵커로 치지 않는 제목: 괄호 태그만 있는 줄([AI 에이전트용])과 원문 쪽수 접두.
_BRACKET_ONLY_RE = re.compile(r"^\[[^\]]*\]$")
_PAGE_PREFIX_RE = re.compile(r"^원문\s*\d+\s*쪽\s*-?\s*")
_ANCHOR_NOISE_RE = re.compile(r"[\s*#▣◆■□○●•\-]+")
_MIN_ANCHOR_CHARS = 4


def _is_specific_anchor(text: str) -> bool:
    # 굵은 글씨·헤딩 표기를 먼저 벗겨야 `**[AI 에이전트용]**` 도 태그로 본다.
    text = _PAGE_PREFIX_RE.sub("", _single_line(text).strip("*#_ "))
    if not text or _BRACKET_ONLY_RE.match(text):
        return False
    return len(_ANCHOR_NOISE_RE.sub("", text)) >= _MIN_ANCHOR_CHARS


def is_self_describing(
    grid: Any, num_cols: Any, anchors: list[str], *, header_rows: int | None = None
) -> bool:
    """표 청크만으로 내용과 주제가 드러나는 작은 문장형 표인가.

    모두 만족해야 한다: 구체적인 제목(섹션 제목·캡션·표 바로 위 한 줄)이 있고, 병합 셀과
    다단 헤더가 없고, 데이터 행이 5개 이하·열이 2~3개이며, 행 라벨(첫 열)을 뺀 값 셀이
    문장형이고, 값에 금액·비율·기간이 없다. 하나라도 판정할 수 없으면 False(설명 유지)다.

    ``header_rows`` 는 `degenerate_reason` 과 같은 이유로 grid 를 만든 쪽이 알면 넘긴다.
    """
    if not grid or not any(_is_specific_anchor(anchor) for anchor in anchors):
        return False
    shape = analyze_grid(grid, num_cols)
    if shape is None or shape.has_row_span or shape.has_col_span:
        return False
    header_n = leading_header_row_count(grid) if header_rows is None else int(header_rows)
    data_rows = list(grid)[max(header_n, 0):]
    if header_n > 1 or not 1 <= len(data_rows) <= _SELF_DESC_MAX_DATA_ROWS:
        return False
    if not _SELF_DESC_COLS[0] <= shape.num_cols <= _SELF_DESC_COLS[1]:
        return False
    values = [text for row in data_rows for text in map(cell_text, list(row)[1:]) if text]
    if not values:
        return False
    if sum(map(len, values)) < _SELF_DESC_MIN_AVG_CHARS * len(values):
        return False
    sentences = sum(len(value) >= _SELF_DESC_SENTENCE_CHARS for value in values)
    if sentences < _SELF_DESC_SENTENCE_RATIO * len(values):
        return False
    return not any(_CONDITION_VALUE_RE.search(value) for value in values)


# 중복 판정에서 본문이 이보다 짧은 표는 보지 않는다. 숫자 몇 개뿐인 표는 다른 표의 셀에
# 우연히 들어 있을 수 있다.
_DUPLICATE_MIN_CHARS = 20


def table_row_texts(grid: Any) -> list[str]:
    """행마다 셀 본문을 공백 없이 이어 붙인 문자열. 병합으로 반복된 이웃 셀은 한 번만 넣고 빈 행은 뺀다."""
    rows: list[str] = []
    for row in grid or []:
        cells: list[str] = []
        for cell in row:
            text = "".join(cell_text(cell).split())
            if text and (not cells or cells[-1] != text):
                cells.append(text)
        if cells:
            rows.append("".join(cells))
    return rows


def is_contained_duplicate(rows: list[str], previous: list[list[str]]) -> bool:
    """같은 문서의 앞선 표 하나가 이 표의 모든 행(헤더 포함)을 행 단위로 담고 있는가.

    `rows`·`previous` 는 `table_row_texts` 결과다. 이 표의 행마다 셀을 순서대로 이은 문자열이
    앞선 표의 한 행 안에 있어야 한다. 앞 표의 병합 셀 하나에 하위 표가 통째로 전사된 경우와
    다른 쪽에 같은 표가 다시 실린 경우가 걸리고, 셀 값만 같고 행의 짝이 다른 표(금액이 뒤바뀐
    표)는 걸리지 않는다. 같은 내용이 앞 표 청크에 이미 있으므로 이 표의 설명은 검색에 더하는
    것이 없다.
    """
    if sum(map(len, rows)) < _DUPLICATE_MIN_CHARS:
        return False
    return any(
        all(any(row in prev_row for prev_row in prev) for row in rows)
        for prev in previous
    )


def log_skipped_tables(total: int, reasons: list[str]) -> None:
    """뺀 표가 있을 때만 대상 표 수와 뺀 표 수(사유별)를 남긴다. 표 수는 호출 로그에도 있다."""
    if not reasons:
        return
    counts = {reason: reasons.count(reason) for reason in dict.fromkeys(reasons)}
    detail = ", ".join(f"{reason} {count}" for reason, count in counts.items())
    _log.info(
        f"[table_text_description] 표 {total}개 중 {len(reasons)}개를 LLM 전송에서 제외합니다({detail})."
    )


def _table_title_line(items: list[DocItem], index: int) -> str:
    """청커가 표와 같은 청크에 남기는 표 바로 위 한 줄(`smart_chunker._is_table_title`)."""
    if index <= 0 or not _context_candidate(items[index - 1]):
        return ""
    item = items[index - 1]
    label = getattr(item, "label", None)
    label = label.value if hasattr(label, "value") else str(label or "")
    text = str(getattr(item, "text", "") or "").strip()
    if label in {"section_header", "title"} or "\n" in text or len(text) > 80:
        return ""
    return text


def _item_skip_reason(
    item: TableItem, anchors: list[str], options: "TableTextDescriptionOptions",
    rows: list[str], previous: list[list[str]],
) -> str | None:
    data = getattr(item, "data", None)
    grid = getattr(data, "grid", None)
    num_cols = getattr(data, "num_cols", None)
    # 레이아웃 판정은 청커(`smart_chunker`)와 같은 호출이다 — 청커가 평문으로 내는 표만 뺀다.
    if options.skip_layout and degenerate_reason(grid, num_cols):
        return SKIP_LAYOUT
    if options.skip_contained_duplicate and is_contained_duplicate(rows, previous):
        return SKIP_CONTAINED_DUPLICATE
    if options.skip_self_describing and is_self_describing(grid, num_cols, anchors):
        return SKIP_SELF_DESCRIBING
    return None


def collect_table_text_targets(
    document: DoclingDocument,
    options: TableTextDescriptionOptions,
    *,
    input_format: str | None = None,
    max_context_chars: int | None = None,
) -> list[TableTextTarget]:
    """문서의 표와 주변 본문을 LLM 입력용 target 으로 모은다.

    `input_format`/`max_context_chars` 를 주면 설정값 대신 그 값으로 수집한다 —
    프롬프트가 예산을 넘을 때 문맥을 줄이거나 markdown 으로 낮춰 다시 부르기 위한 것이다.

    LLM 에 보낼 필요가 없는 표(`_item_skip_reason`)는 target 을 만들지 않는다. 재수집도
    이 함수를 거치므로 제외한 표가 되살아나지 않는다. 로그는 첫 수집에서만 남긴다.
    """
    context_chars = options.max_context_chars if max_context_chars is None else max(1, max_context_chars)
    items = [
        item for item, _ in document.iterate_items(
            included_content_layers={ContentLayer.BODY, ContentLayer.FURNITURE}
        )
    ]
    targets: list[TableTextTarget] = []
    fmt = _resolve_input_format(document, input_format or options.input_format)
    skipped: list[str] = []
    # 중복 판정의 비교 대상. 뺀 표도 본문은 청크에 남으므로 모든 표를 넣는다.
    previous: list[list[str]] = []
    # 청커(`smart_chunker`)가 문서 순회에서 건너뛰는 아이템과 같은 집합이다.
    embedded = table_embedded_refs(document) if options.skip_layout else set()
    total = 0
    for index, item in enumerate(items):
        if not isinstance(item, TableItem):
            continue
        total += 1
        try:
            caption = _single_line(item.caption_text(document))
        except Exception:
            caption = ""
        section_header = _section_header(items, index)
        rows = (
            table_row_texts(getattr(getattr(item, "data", None), "grid", None))
            if options.skip_contained_duplicate else []
        )
        # 바깥 표 셀로만 실리는 중첩 표는 레이아웃 표와 같이 청커가 자체 블록으로 내지 않는 표라
        # skip_layout 으로 함께 뺀다.
        if getattr(item, "self_ref", None) in embedded:
            reason = SKIP_NESTED
        else:
            reason = _item_skip_reason(
                item, [section_header, caption, _table_title_line(items, index)], options,
                rows, previous,
            )
        previous.append(rows)
        if reason:
            skipped.append(reason)
            continue
        page = _page_no(item)
        before = "\n".join(_neighbors(items, index, page, options.before_items, -1))
        after = "\n".join(_neighbors(items, index, page, options.after_items, 1))
        targets.append(TableTextTarget(
            table_id=f"table_{len(targets) + 1:04d}", table_item=item, page_no=page,
            section_header=section_header, caption=caption,
            before_context=before[:context_chars],
            table_text=_table_text(item, document, fmt),
            after_context=after[:context_chars],
            input_format=fmt,
        ))
    if input_format is None and max_context_chars is None:
        log_skipped_tables(total, skipped)
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
