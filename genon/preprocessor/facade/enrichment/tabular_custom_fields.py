"""행 기반 표 문서의 custom_fields 매핑.

Excel/CSV의 물리 파싱은 formats 설정이 담당하고, 이 모듈은 enrichment.custom_fields 중
extractor=tabular_mapping 설정을 적용해 행별 metadata element를 만든다.

형제 모듈 `json_records.py`(JSON 레코드 매핑)가 이 모듈의 이름/값 정규화 헬퍼
(`normalize_column_name` · `compile_value_map` · `apply_value_map`)를 그대로 가져다 쓴다.
두 경로가 같은 규칙으로 동작해야 하므로 여기가 단일 출처다.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path
from typing import Any

import yaml

_log = logging.getLogger(__name__)

from .custom_fields_enricher import (
    TABULAR_CUSTOM_FIELD_EXTRACTORS,
    build_llm_field_specs,
    custom_fields_extractor,
    matches_doc_type,
    normalize_doc_type,
    normalize_doc_types,
)
from .field_transforms import VALUE_TRANSFORMS

# build_fields → to_parse_format 사이에서만 쓰는 행 부가정보. metadata 로 내보내기 전에 pop 한다.
# 필드 dict 안에 실어야 llm_fields 의 skip_record 로 일부 행이 빠져도 정렬이 어긋나지 않는다.
_ROW_PAGE_KEY = "__cf_row_page"
_ROW_FALLBACK_TEXT_KEY = "__cf_row_fallback_text"
_ROW_INTERNAL_KEYS = (_ROW_PAGE_KEY, _ROW_FALLBACK_TEXT_KEY)


def normalize_column_name(value: Any) -> str:
    """컬럼명 비교용 정규화: Unicode/BOM/대소문자/공백·구분자 차이를 흡수한다."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.replace("\ufeff", "").strip().casefold()
    return re.sub(r"[\s_\-./]+", "", text)


def _clean_cell(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace("\ufeff", "").strip()
    return value


# ── 값 별칭 매핑(value_map) ──────────────────────────────────────────────────
# 컬럼명이 아니라 **값**의 표기 흔들림을 표준 코드로 접는다.
# 모니모 GROUP_C 가 시트마다 "삼성생명 / 생명 / SLF" 로 제각각인 것이 도입 계기다.
#
#   value_map:
#     GROUP_C:
#       SLF: [삼성생명, 생명]      # 표준값: [별칭 …]
#       HPP: [삼성카드, 카드]
#
# 표준값 자신도 자동 별칭이 되므로 이미 코드로 오는 원천(FAQ 의 corp_code=HPP)은 그대로 통과한다.
# 비교는 `normalize_column_name` 과 같은 정규화를 거친다(대소문자·공백·구분자 무시).
# VALUE_TRANSFORMS 에 넣지 않는 이유: 등록 변환기는 인자를 받지 않아 매핑표를 실을 수 없다.

def compile_value_map(spec: Any, *, label: str = "value_map") -> dict[str, dict[str, Any]]:
    """설정의 value_map 을 `{목표필드: {정규화 별칭: 표준값}}` 으로 컴파일한다."""
    if not spec:
        return {}
    if not isinstance(spec, dict):
        raise ValueError(f"{label} 은 object 여야 합니다.")

    compiled: dict[str, dict[str, Any]] = {}
    for target, entries in spec.items():
        if not isinstance(entries, dict):
            raise ValueError(f"{label}.{target} 은 '표준값: [별칭…]' 형태의 object 여야 합니다.")
        lookup: dict[str, Any] = {}
        for canonical, aliases in entries.items():
            values = aliases if isinstance(aliases, (list, tuple)) else [aliases]
            for alias in [canonical, *values]:
                key = normalize_column_name(alias)
                if not key:
                    continue
                if key in lookup and lookup[key] != canonical:
                    raise ValueError(
                        f"{label}.{target} 별칭 '{alias}' 이 "
                        f"'{lookup[key]}' 와 '{canonical}' 양쪽에 있습니다."
                    )
                lookup[key] = canonical
        compiled[str(target)] = lookup
    return compiled


def apply_value_map(fields: dict, compiled: dict[str, dict[str, Any]], *, context: str = "") -> None:
    """컴파일된 value_map 을 목표필드에 제자리 적용한다.

    매핑표에 없는 값은 **원값을 그대로 두고 경고**한다. 조용히 null 로 바꾸면 GROUP_C 같은
    NOT NULL 컬럼이 적재 직전에야 터지고, 조용히 통과시키면 표준화되지 않은 값이 섞인다.
    필수 여부 판정은 기존 `required` 규칙이 그대로 담당한다.
    """
    for target, lookup in compiled.items():
        value = fields.get(target)
        if value in (None, ""):
            continue
        mapped = lookup.get(normalize_column_name(value))
        if mapped is None:
            _log.warning(
                f"[custom_fields] value_map 미등록 값{context}: {target}='{value}' "
                f"(등록된 표준값: {sorted(set(lookup.values()))})"
            )
            continue
        fields[target] = mapped


class TabularCustomFieldsMapper:
    """custom_fields 설정 하나를 행 단위 metadata 변환기로 컴파일한다."""

    def __init__(
        self,
        *,
        config_file: str,
        resource_path: str | None = None,
        doc_type: str | list[str] | None = None,
        extractor: str = "tabular_mapping",
        **_: Any,
    ) -> None:
        if str(extractor or "").strip().lower() not in TABULAR_CUSTOM_FIELD_EXTRACTORS:
            raise ValueError(f"지원하지 않는 tabular custom_fields extractor: {extractor}")
        self.doc_types = normalize_doc_types(doc_type)
        # llm_fields 의 프롬프트/LLM config 파일 경로 해석 기준(= 이 config 파일과 같은 디렉토리).
        self.resource_path = resource_path
        self.config = self._load_config(config_file, resource_path)

        # 값 정규화·파생 필드 설정은 json_mapping(JsonRecordsMapper)과 같은 키/의미를 쓴다.
        self.value_map = compile_value_map(self.config.get("value_map"))

        self.transforms = {str(k): str(v) for k, v in (self.config.get("transforms") or {}).items()}
        unknown = sorted({name for name in self.transforms.values() if name not in VALUE_TRANSFORMS})
        if unknown:
            raise ValueError(
                f"등록되지 않은 transforms 변환기: {unknown} (사용 가능: {sorted(VALUE_TRANSFORMS)})"
            )

        # 선언만 컴파일한다. 실제 호출은 parser 가 행 목록을 들고 수행한다(json_mapping 과 동일).
        self.llm_field_specs = build_llm_field_specs(self.config)

    @staticmethod
    def _load_config(config_file: str, resource_path: str | None) -> dict:
        if not config_file:
            raise ValueError("tabular_mapping custom_fields에는 config_file이 필요합니다.")
        path = Path(config_file)
        if not path.is_absolute() and resource_path:
            path = Path(resource_path) / path
        path = path.resolve()
        if not path.is_file():
            raise FileNotFoundError(f"tabular custom_fields config 없음: {path}")
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"tabular custom_fields config는 object여야 합니다: {path}")
        return loaded

    def matches(self, runtime_doc_type: Any) -> bool:
        return matches_doc_type(self.doc_types, runtime_doc_type)

    def canonical_doc_type(self, runtime_doc_type: Any) -> str:
        runtime = normalize_doc_type(runtime_doc_type)
        if runtime and runtime in self.doc_types:
            return runtime
        return self.doc_types[0] if self.doc_types else runtime

    @staticmethod
    def _aliases(target: str, source_spec: Any) -> list[str]:
        values = source_spec if isinstance(source_spec, list) else [source_spec]
        aliases = [target]
        for value in values:
            value = str(value or "").strip()
            if value and value not in aliases:
                aliases.append(value)
        return aliases

    @staticmethod
    def _header_index(row: dict) -> dict[str, str]:
        normalized: dict[str, str] = {}
        collisions: dict[str, list[str]] = {}
        for header in row:
            key = normalize_column_name(header)
            if not key:
                continue
            if key in normalized and normalized[key] != header:
                collisions.setdefault(key, [normalized[key]]).append(header)
            else:
                normalized[key] = header
        if collisions:
            detail = ", ".join(f"{key}={values}" for key, values in collisions.items())
            raise ValueError(f"정규화 후 중복되는 Excel 컬럼이 있습니다: {detail}")
        return normalized

    def _resolve_columns(self, row: dict) -> dict[str, str | None]:
        column_map = self.config.get("column_map") or {}
        if not isinstance(column_map, dict):
            raise ValueError("tabular custom_fields column_map은 object여야 합니다.")
        normalized = self._header_index(row)
        resolved: dict[str, str | None] = {}
        for target, source_spec in column_map.items():
            source = None
            aliases = self._aliases(str(target), source_spec)
            # 설정 순서대로 정확 일치 우선, 그 다음 정규화 일치.
            for alias in aliases:
                if alias in row:
                    source = alias
                    break
            if source is None:
                for alias in aliases:
                    source = normalized.get(normalize_column_name(alias))
                    if source is not None:
                        break
            resolved[str(target)] = source
        return resolved

    def build_fields(self, data_dict: dict, runtime_doc_type: Any) -> list[dict]:
        """parser의 tabular 중립 표현 → 행별 목표필드 목록.

        `to_parse_format` 과 분리해 둔 이유는 그 사이에 `llm_fields`(행마다 LLM 호출)를
        끼워 넣기 위해서다 — json_mapping 의 `build_fields → _apply_llm_fields →
        to_parse_format` 3단 구성과 같은 모양이다. 행 페이지/폴백 본문은 예약 키로 필드 dict
        안에 실어 보내고 `to_parse_format` 이 pop 한다.
        """
        column_map = self.config.get("column_map") or {}
        constants = dict(self.config.get("constants") or {})
        defaults = dict(self.config.get("defaults") or {})
        null_fields = list(self.config.get("nulls") or [])
        required_fields = set(self.config.get("required") or [])
        doc_type = self.canonical_doc_type(runtime_doc_type)

        fields_list: list[dict] = []
        sheets = data_dict.get("data", []) or []
        for sheet_idx, sheet in enumerate(sheets):
            page = sheet_idx + 1
            sheet_name = str(sheet.get("sheet_name") or f"sheet_{page}")
            rows = sheet.get("data_rows", []) or []
            resolved = self._resolve_columns(rows[0]) if rows else {
                str(target): None for target in column_map
            }
            missing_columns = [
                field for field in required_fields
                if field in column_map and resolved.get(field) is None and field not in defaults
            ]
            if missing_columns:
                available = list(rows[0].keys()) if rows else []
                raise ValueError(
                    f"필수 Excel 컬럼 매핑 실패(sheet={sheet_name}): {sorted(missing_columns)}; "
                    f"available={available}"
                )

            skipped = 0
            for row_idx, row in enumerate(rows, start=1):
                fields = {name: None for name in null_fields}
                for target in column_map:
                    source = resolved.get(str(target))
                    fields[str(target)] = _clean_cell(row.get(source)) if source else None
                fields.update(constants)
                for key, value in defaults.items():
                    if fields.get(key) in (None, ""):
                        fields[key] = value

                # 값 정규화 → 변환 순서. 별칭을 표준값으로 접은 뒤에 타입 변환을 건다.
                apply_value_map(
                    fields, self.value_map, context=f"(sheet={sheet_name}, row={row_idx})"
                )
                for target, transform_name in self.transforms.items():
                    fields[target] = VALUE_TRANSFORMS[transform_name](fields.get(target))

                if doc_type:
                    # 요청/프로파일에서 확정한 값이 config constants보다 우선한다.
                    fields["doc_type"] = doc_type

                # 필수값이 빈 행은 전체 문서를 중단하지 않고 해당 행만 skip(경고 로그). 필수 컬럼 자체가
                # 매핑 불가한 경우는 위 missing_columns 에서 이미 하드 에러로 처리(전 행 공통 스키마 문제).
                missing_values = [
                    field for field in required_fields if fields.get(field) in (None, "")
                ]
                if missing_values:
                    skipped += 1
                    _log.warning(
                        f"[tabular_custom_fields] 필수값 누락 행 skip(sheet={sheet_name}, "
                        f"row={row_idx}): {sorted(missing_values)}"
                    )
                    continue

                fields[_ROW_PAGE_KEY] = page
                # text_fields 미지정 시의 폴백(행 전체 값 결합)은 원본 행이 있어야 만들 수 있으므로
                # 여기서 미리 계산해 둔다.
                fields[_ROW_FALLBACK_TEXT_KEY] = "\n".join(
                    str(_clean_cell(value))
                    for value in row.values()
                    if _clean_cell(value) not in (None, "")
                )
                fields_list.append(fields)

            if skipped:
                # silent 축소 방지 — 몇 행이 빠졌는지 요약으로 드러낸다.
                _log.warning(
                    f"[tabular_custom_fields] skipped {skipped}/{len(rows)} rows "
                    f"(missing required) sheet={sheet_name}"
                )

        return fields_list

    def to_parse_format_from_fields(self, fields_list: list[dict], runtime_doc_type: Any) -> dict:
        """행별 목표필드 목록 → parse-format(청커 행 기반 경로가 소비하는 형태)."""
        text_fields = list(self.config.get("text_fields") or [])
        doc_type = self.canonical_doc_type(runtime_doc_type)

        elements: list[dict] = []
        max_page = 0
        for fields in fields_list:
            page = fields.pop(_ROW_PAGE_KEY, len(elements) + 1)
            max_page = max(max_page, page)
            fallback_text = fields.pop(_ROW_FALLBACK_TEXT_KEY, "")
            if text_fields:
                content = "\n".join(
                    str(fields.get(field))
                    for field in text_fields
                    if fields.get(field) not in (None, "")
                )
            else:
                content = fallback_text
            elements.append({
                "category": "custom_fields_row",
                "content": content,
                "coordinates": [],
                "id": len(elements),
                "page": page,
                "metadata": fields,
            })

        # 시트 수 대신 행에 실려온 최대 페이지 번호를 쓴다 — mapper 는 요청 간 공유되는
        # 장수명 객체라 시트 수를 인스턴스 상태로 들고 있으면 동시 요청끼리 값이 섞인다.
        result = {"elements": elements, "usage": {"pages": max_page or len(elements)}}
        if doc_type:
            result["metadata"] = {"doc_type": doc_type}
        return result

    def to_parse_format(self, data_dict: dict, runtime_doc_type: Any) -> dict:
        """parser의 tabular 중립 표현을 행별 custom_fields parse-format으로 변환한다.

        `llm_fields` 는 적용되지 않는다 — LLM 호출은 async 라 이 동기 경로에서 돌릴 수 없다.
        parser 는 `build_fields` → LLM → `to_parse_format_from_fields` 3단을 직접 쓴다.
        """
        return self.to_parse_format_from_fields(
            self.build_fields(data_dict, runtime_doc_type), runtime_doc_type
        )


def warn_tabular_llm_fields_unsupported(mappers: list, processor: str) -> None:
    """`llm_fields` 를 실행하지 않는 프로세서에서 그 사실을 기동 시 드러낸다.

    intelligent/convert 의 xlsx 경로는 `build_tabular_custom_fields_vectors`(동기)로 벡터를
    바로 만들기 때문에 async LLM 호출을 끼워 넣을 자리가 없다. 경고 없이 두면 설정에는 요약본문이
    선언돼 있는데 결과에는 없는 상태가 조용히 만들어진다 — parser 경로로 돌려야 채워진다.
    """
    for mapper in mappers or []:
        specs = getattr(mapper, "llm_field_specs", ()) or ()
        if not specs:
            continue
        outputs = sorted({name for spec in specs for name in spec.output_fields})
        _log.warning(
            f"[{processor}] tabular custom_fields 의 llm_fields 는 이 프로세서에서 실행되지 "
            f"않습니다(doc_type={list(mapper.doc_types)}): {outputs} 는 채워지지 않습니다. "
            f"요약본문 등 LLM 생성 필드가 필요하면 parser 경로를 사용하세요."
        )


def build_tabular_custom_fields_mappers(configs: list[dict]) -> list[TabularCustomFieldsMapper]:
    return [
        TabularCustomFieldsMapper(**dict(config))
        for config in (configs or [])
        if custom_fields_extractor(config) in TABULAR_CUSTOM_FIELD_EXTRACTORS
    ]

