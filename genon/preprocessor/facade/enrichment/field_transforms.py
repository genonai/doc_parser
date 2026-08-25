"""field_transforms.py — 추출 메타데이터를 typed 벡터 필드로 변환하는 재사용 로직.

intelligent_processor 의 created_date 전용 코드를 일반화한 모듈이다.
설정(field_transforms)에 따라 "어떤 출력 키 → 어떤 벡터 필드 → 어떤 변환" 을 매핑한다.

순수 함수 모음이라 docling/fastapi 등 무거운 의존성이 없다(enrichment_config.py 와 동일 성격).
docling 타입은 타입 힌트 용도로만 참조하므로 TYPE_CHECKING 으로 import 한다.

신규 변환기/보조추출은 함수 작성 후 VALUE_TRANSFORMS / FALLBACK_STRATEGIES 에 등록만 하면 된다.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    from docling_core.types import DoclingDocument

_log = logging.getLogger(__name__)


# null 값을 KeyValueItem 으로 왕복하기 위한 sentinel. 빈 값 셀은 extract 에서 skip 되어
# key/value 페어링을 깨므로, None 을 비지 않는 이 토큰으로 저장하고 읽을 때 다시 None 으로 복원한다.
NULL_SENTINEL = "\x00__CF_NULL__\x00"
# 문자열 "9"와 정수 9를 구분하면서 parse→chunk API 경계를 왕복하기 위한 표식.
# 표식 없는 기존 KeyValueItem은 종전 normalize_metadata_value 규칙으로 계속 읽는다.
JSON_VALUE_SENTINEL = "\x00__CF_JSON__\x00"


# 추출 메타데이터 → typed 벡터 필드 변환의 기본값.
# yaml metadata.field_transforms 가 비어있을 때 적용되어 기존 created_date 동작을 보존한다.
# 각 항목: source(후보 키, str 또는 list) → target(벡터 필드) → type(값 변환기) / fallback(보조 추출).
DEFAULT_METADATA_FIELD_TRANSFORMS = [
    {
        "source": ["created_date", "작성일"],
        "target": "created_date",
        "type": "date_int",
        "fallback": "doc_text_scan",
    },
]


# ── 값 변환기 ───────────────────────────────────────────────────────────────

def parse_created_date(date_text: str) -> int:
    """작성일 텍스트를 파싱하여 YYYYMMDD 형식의 정수로 변환.

    Args:
        date_text: 작성일 텍스트 (YYYY-MM 또는 YYYY-MM-DD 형식)

    Returns:
        YYYYMMDD 형식의 정수, 파싱 실패시 0
    """
    if not date_text or not isinstance(date_text, str) or date_text == "None":
        return 0

    # 공백 제거 및 정리
    date_text = date_text.strip()

    # 1) YYYY-MM-DD / YYYY.MM.DD / YYYY/MM/DD / YYYY년 MM월 DD일 (문장 내부 포함) 우선
    for pattern in (
        r'(\d{4})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})',
        r'(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일?',
    ):
        match_full = re.search(pattern, date_text)
        if match_full:
            year, month, day = match_full.groups()
            try:
                datetime(int(year), int(month), int(day))
                return int(f"{year}{month.zfill(2)}{day.zfill(2)}")
            except ValueError:
                continue

    # 2) YYYY-MM / YYYY.MM / YYYY/MM / YYYY년 MM월 (문장 내부 포함) → 일자는 01
    for pattern in (
        r'(\d{4})\s*[-./]\s*(\d{1,2})',
        r'(\d{4})\s*년\s*(\d{1,2})\s*월',
    ):
        match_month = re.search(pattern, date_text)
        if match_month:
            year, month = match_month.groups()
            try:
                datetime(int(year), int(month), 1)
                return int(f"{year}{month.zfill(2)}01")
            except ValueError:
                continue

    # 3) YYYY 형식 → 월일은 0101
    match_year = re.search(r'(\d{4})', date_text)
    if match_year:
        year = match_year.group(1)
        try:
            datetime(int(year), 1, 1)
            return int(f"{year}0101")
        except ValueError:
            pass

    return 0


def transform_date_int(value: Any) -> int:
    """date_int 변환기: 날짜 텍스트/정수를 YYYYMMDD 정수로 변환."""
    if isinstance(value, (int, float)):
        candidate_int = int(value)
        return candidate_int if candidate_int > 0 else 0
    if value in (None, ""):
        return 0
    return parse_created_date(str(value))


# 2자리 연도 표기("26.07.01")를 4자리로 펼칠 때의 세기 분기점. 50 미만은 20xx, 이상은 19xx.
_SHORT_YEAR_PIVOT = 50
# 구분자 없는 압축 표기. 모니모 원천이 실제로 이 형태로 준다:
#   관심소식/이벤트 "260701"(YYMMDD) · 링크 "20260713"(YYYYMMDD)
# parse_created_date 는 `\d{4}` 를 연도로만 읽어 "260731"→2607년, "20260713"→2026-01-01 로
# 조용히 뭉갠다. 종료일이 그렇게 들어가면 기간 게이트가 영원히 열리므로 여기서 먼저 잡는다.
_COMPACT_DATE8_RE = re.compile(r"^\s*(\d{4})(\d{2})(\d{2})\s*$")
_COMPACT_DATE6_RE = re.compile(r"^\s*(\d{2})(\d{2})(\d{2})\s*$")
_SHORT_DATE_RE = re.compile(r"^\s*(\d{2})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})\s*$")


def transform_date_int_flex(value: Any) -> int:
    """date_int_flex 변환기: 2자리 연도("26.07.01")까지 받는 YYYYMMDD 정수 변환.

    `date_int`(parse_created_date)는 `\\d{4}` 연도만 인식해 "26.07.01" 을 2601 년으로도,
    날짜로도 읽지 못한다. 여기서 아래 세 형태를 먼저 정규화한 뒤 나머지는 `date_int` 에
    위임하므로 기존 created_date 동작에는 영향이 없다.
      - "26.07.01" / "26-07-01"  (2자리 연도 + 구분자)
      - "20260713"               (구분자 없는 8자리 YYYYMMDD)
      - "260701"                 (구분자 없는 6자리 YYMMDD)
    월/일이 실제 날짜가 아니면(예: "202699") 정규화하지 않고 기존 경로로 넘긴다.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # 엑셀/JSON 이 숫자로 준 압축 날짜(20260713)도 문자열과 같게 다룬다.
        as_int = int(value)
        if 10_000_000 <= as_int <= 99_999_999 or 100_000 <= as_int <= 999_999:
            value = str(as_int)

    if isinstance(value, str):
        match = _SHORT_DATE_RE.match(value)
        if match:
            year, month, day = match.groups()
            century = 2000 if int(year) < _SHORT_YEAR_PIVOT else 1900
            value = f"{century + int(year)}-{month}-{day}"
        else:
            for pattern, two_digit_year in ((_COMPACT_DATE8_RE, False), (_COMPACT_DATE6_RE, True)):
                compact = pattern.match(value)
                if not compact:
                    continue
                year, month, day = compact.groups()
                if two_digit_year:
                    century = 2000 if int(year) < _SHORT_YEAR_PIVOT else 1900
                    year = str(century + int(year))
                try:
                    datetime(int(year), int(month), int(day))
                except ValueError:
                    break          # 날짜가 아니면 압축 표기로 보지 않는다
                value = f"{year}-{month}-{day}"
                break
    return transform_date_int(value)


def transform_text_norm(value: Any) -> Optional[str]:
    """text_norm 변환기: 표기 흔들림을 흡수한 대조용 정규화 문자열.

    모니모 TB_TERM.TERM_NORM("띄어쓰기와 대소문자를 고른 형태입니다. 같은 용어가 두 번
    등록되는 걸 막습니다") 용도. 유일키 `CLCM_C + TERM_NORM` 의 재료라 규칙이 곧 중복 판정
    기준이 된다.

    적용 규칙: NFKC 정규화 → 양끝 공백 제거 → 연속 공백 1칸으로 축약 → casefold.
    공백을 **제거하지 않고 축약만** 한다 — 완전 제거는 "선 지급"/"선지급" 같은 서로 다른
    용어까지 합쳐버릴 수 있어, 원천 담당과 규칙이 확정되기 전까지는 보수적으로 둔다.
    """
    if value in (None, ""):
        return None
    text = unicodedata.normalize("NFKC", str(value))
    text = text.replace("﻿", "").strip()
    text = re.sub(r"\s+", " ", text)
    return text.casefold() or None


# ── 보조 추출(fallback) ──────────────────────────────────────────────────────

def extract_created_date_from_document_text(document: "DoclingDocument") -> int:
    """metadata 추출이 비어있을 때 문서 본문에서 작성/기준일을 휴리스틱으로 추출."""
    try:
        raw_text = document.export_to_text() or ""
    except Exception:
        raw_text = ""
    if not raw_text:
        return 0

    prioritized_patterns = (
        r'(?:기준일|작성일|최초\s*작성일|보고자료)[^0-9]{0,20}(\d{4}\s*[./-]\s*\d{1,2}\s*[./-]\s*\d{1,2})',
        r'(?:기준일|작성일|최초\s*작성일|보고자료)[^0-9]{0,20}(\d{4}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일?)',
        r'(?:기준일|작성일|최초\s*작성일|보고자료)[^0-9]{0,20}(\d{4}\s*[./-]\s*\d{1,2})',
        r'(?:기준일|작성일|최초\s*작성일|보고자료)[^0-9]{0,20}(\d{4}\s*년\s*\d{1,2}\s*월)',
    )
    for pattern in prioritized_patterns:
        m = re.search(pattern, raw_text)
        if not m:
            continue
        parsed = parse_created_date(m.group(1))
        if parsed:
            return parsed

    # 키워드 기반으로 찾지 못하면 문서 최초 날짜를 fallback 으로 사용.
    fallback_match = re.search(
        r'(\d{4}\s*[./-]\s*\d{1,2}\s*[./-]\s*\d{1,2}|\d{4}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일?)',
        raw_text,
    )
    if fallback_match:
        parsed = parse_created_date(fallback_match.group(1))
        if parsed:
            return parsed
    return 0


# ── 레지스트리 ───────────────────────────────────────────────────────────────
# 신규 변환기/보조추출은 함수 작성 후 아래 dict 에 등록만 하면 설정에서 바로 사용 가능.
VALUE_TRANSFORMS: dict[str, Callable[[Any], Any]] = {
    "date_int": transform_date_int,
    "date_int_flex": transform_date_int_flex,
    "text_norm": transform_text_norm,
}
FALLBACK_STRATEGIES: dict[str, Callable[["DoclingDocument"], Any]] = {
    "doc_text_scan": extract_created_date_from_document_text,
}


# ── 적용 ─────────────────────────────────────────────────────────────────────

def apply_field_transforms(
    field_transforms: list,
    merged_metadata: dict[str, Any],
    document: "DoclingDocument",
) -> tuple[dict[str, Any], set[str]]:
    """field_transforms 설정에 따라 추출 메타데이터를 typed 벡터 필드로 변환한다.

    Args:
        field_transforms: 변환 spec 의 list (각 spec 은 source/target/type/fallback dict).
        merged_metadata: 문서/컨텍스트 병합 메타데이터.
        document: 본문 휴리스틱(fallback)에 사용할 docling 문서.

    Returns:
        (typed_values, consumed_keys)
        - typed_values: {target_field: 변환값} — global_metadata 로 주입
        - consumed_keys: passthrough 에서 제외할 source/target 키 집합
    """
    typed_values: dict[str, Any] = {}
    consumed_keys: set[str] = set()

    for spec in field_transforms or []:
        if not isinstance(spec, dict):
            continue
        sources = spec.get("source")
        if isinstance(sources, str):
            sources = [sources]
        elif not isinstance(sources, list):
            sources = []
        target = spec.get("target") or (sources[0] if sources else None)
        if not target:
            continue
        consumed_keys.update(sources)
        consumed_keys.add(target)

        # 후보 키를 순서대로 탐색해 첫 non-empty 값 선택
        raw_value = None
        for key in sources:
            candidate = merged_metadata.get(key)
            if candidate not in (None, ""):
                raw_value = candidate
                break

        transform_name = spec.get("type")
        transform_fn = VALUE_TRANSFORMS.get(transform_name) if transform_name else None
        value = transform_fn(raw_value) if transform_fn is not None else raw_value

        # 값이 비어있고 fallback 이 지정된 경우 문서 본문 휴리스틱 적용
        if (value in (None, "") or value == 0):
            fallback_name = spec.get("fallback")
            fallback_fn = FALLBACK_STRATEGIES.get(fallback_name) if fallback_name else None
            if fallback_fn is not None:
                value = fallback_fn(document)

        typed_values[target] = value

    return typed_values, consumed_keys


# ── 문서 메타데이터 추출/직렬화 유틸 ─────────────────────────────────────────────
# compose_vectors 에서 docling 문서의 KeyValueItem 메타데이터를 읽고 벡터 출력용으로
# 직렬화하는 공통 로직. intelligent_processor / convert_processor 가 공유한다.

def normalize_metadata_value(value: Any) -> Any:
    """문자열 값이 JSON 배열/객체 형태면 파싱, 아니면 strip 한 문자열을 반환."""
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped:
        return ""
    if stripped[0] in "[{" and stripped[-1] in "]}":
        try:
            return json.loads(stripped)
        except Exception:
            return stripped
    return stripped


def extract_metadata_from_document(document: "DoclingDocument") -> dict[str, Any]:
    """docling 문서의 KeyValueItem 들에서 key→value 메타데이터를 일반적으로 추출."""
    metadata: dict[str, Any] = {}
    for kv_item in getattr(document, "key_value_items", []) or []:
        graph = getattr(kv_item, "graph", None)
        cells = getattr(graph, "cells", None) or []
        pending_key: Optional[str] = None
        for cell in cells:
            text = str(getattr(cell, "text", "") or "").strip()
            if not text:
                continue
            label_obj = getattr(cell, "label", None)
            label = str(getattr(label_obj, "value", label_obj) or "").strip().lower()
            if label == "key":
                pending_key = text
                continue
            if label == "value" and pending_key:
                metadata[pending_key] = (
                    None if text == NULL_SENTINEL
                    else _deserialize_stored_metadata_value(text)
                )
                pending_key = None
                continue
            # fallback: label 정보가 없거나 예외적인 순서인 경우 순차 pair 처리
            if pending_key is None:
                pending_key = text
            else:
                metadata[pending_key] = (
                    None if text == NULL_SENTINEL
                    else _deserialize_stored_metadata_value(text)
                )
                pending_key = None
    return metadata


def _deserialize_stored_metadata_value(text: str) -> Any:
    """새 typed 표식과 기존 무표식 metadata를 모두 읽는다."""
    if text.startswith(JSON_VALUE_SENTINEL):
        candidate = text[len(JSON_VALUE_SENTINEL):]
        try:
            return json.loads(candidate)
        except Exception:
            _log.warning("typed metadata JSON 복원 실패 — 문자열로 유지")
            return candidate
    return normalize_metadata_value(text)


def serialize_metadata_value_for_output(value: Any) -> Any:
    """벡터 결과 포맷 일관성을 위해 중첩 metadata는 JSON 문자열로 직렬화."""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def store_metadata_in_document(
    document: "DoclingDocument",
    metadata: dict,
    preserve_nulls: bool = False,
    typed_keys: "set[str] | None" = None,
) -> None:
    """추출한 key→value 메타데이터를 문서의 KeyValueItem 으로 저장한다.

    extract_metadata_from_document 의 쓰기측 대칭 함수. 문서에 실리면 청커의
    passthrough(chunking_processor.compose_vectors)가 각 청크에 자동 부착한다
    — created_date(MetadataEnricher 가 이미 add_key_values 로 저장)와 동일 경로.
    parse↔chunk 가 별도 API 여도 DoclingDocument 에 실려 경계를 넘는다.

    None 값 필드 처리:
    - preserve_nulls=False(기본): 저장하지 않는다. 값 셀 text 가 비면 extract 측이 셀을
      건너뛰어(extract_metadata_from_document 의 빈 텍스트 skip) key/value 페어링이 깨지므로,
      null 은 애초에 emit 하지 않는다.
    - preserve_nulls=True(custom_fields): 선언된 output_fields 를 값이 null 이어도 결과에
      모두 남기기 위해, None 을 비지 않는 NULL_SENTINEL 로 저장한다(읽을 때 다시 None 으로 복원).

    typed_keys: 값의 **타입까지** chunk API 경계 너머로 보존할 키 집합(예: md front matter 의
        `source_pages: 9`). 여기 든 키만 JSON_VALUE_SENTINEL 로 직렬화되어 읽을 때 int/bool/
        list 원형으로 복원된다.
        **의도적으로 opt-in 이다** — 전 필드에 적용하면 문서 단위 custom_fields 를 쓰는 모든
        doc_type 의 청크 property 타입이 함께 바뀐다(실측: card 의 annual_fee_amount 가
        '18000' → 18000). 이미 text 로 생성된 벡터 컬렉션 property 에 int 가 들어가면 적재가
        깨지므로, 기존 doc_type 은 종전 직렬화 규칙(dict/list → json.dumps, 그 외 → str)을
        그대로 유지한다.
    """
    typed_keys = typed_keys or set()
    try:
        from docling_core.types.doc.document import GraphData, GraphCell, GraphCellLabel
    except ImportError:
        _log.warning("GraphData/GraphCell import 실패 — 문서 메타데이터 저장 생략")
        return

    graph_cells = []
    cell_id = 0
    for key, value in (metadata or {}).items():
        if value is None:
            if not preserve_nulls:
                continue  # null 필드는 저장하지 않음(빈 value 셀 skip → 페어링 붕괴 방지)
            value_str = NULL_SENTINEL  # 페어링을 깨지 않는 sentinel 로 왕복
        elif isinstance(value, str):
            value_str = value
        elif key in typed_keys:
            # 숫자/bool/배열/객체의 타입을 별도 chunk API 까지 보존한다. JSON 직렬화가
            # 불가능한 사용자 객체는 기존 동작처럼 문자열로 안전하게 내린다.
            try:
                value_str = JSON_VALUE_SENTINEL + json.dumps(value, ensure_ascii=False)
            except (TypeError, ValueError):
                value_str = str(value)
        else:
            # 기존 규칙(하위 호환): dict/list 는 표식 없는 JSON, 그 외는 문자열.
            value_str = (
                json.dumps(value, ensure_ascii=False)
                if isinstance(value, (dict, list))
                else str(value)
            )
        graph_cells.append(GraphCell(label=GraphCellLabel.KEY, cell_id=cell_id, text=str(key), orig=str(key)))
        cell_id += 1
        graph_cells.append(GraphCell(label=GraphCellLabel.VALUE, cell_id=cell_id, text=value_str, orig=value_str))
        cell_id += 1

    if not graph_cells:
        return  # 저장할 값이 없으면 빈 KeyValueItem 을 만들지 않음

    graph_data = GraphData(cells=graph_cells, links=[])
    document.add_key_values(graph=graph_data, prov=None, parent=None)
