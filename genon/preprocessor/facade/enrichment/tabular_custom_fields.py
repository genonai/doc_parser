"""행 기반 표 문서의 custom_fields 매핑.

Excel/CSV의 물리 파싱은 formats 설정이 담당하고, 이 모듈은 enrichment.custom_fields 중
extractor=tabular_mapping 설정을 적용해 행별 metadata element를 만든다.
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
    custom_fields_extractor,
    matches_doc_type,
    normalize_doc_type,
    normalize_doc_types,
)


def normalize_column_name(value: Any) -> str:
    """컬럼명 비교용 정규화: Unicode/BOM/대소문자/공백·구분자 차이를 흡수한다."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.replace("\ufeff", "").strip().casefold()
    return re.sub(r"[\s_\-./]+", "", text)


def _clean_cell(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace("\ufeff", "").strip()
    return value


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
        self.config = self._load_config(config_file, resource_path)

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

    def to_parse_format(self, data_dict: dict, runtime_doc_type: Any) -> dict:
        """parser의 tabular 중립 표현을 행별 custom_fields parse-format으로 변환한다."""
        column_map = self.config.get("column_map") or {}
        constants = dict(self.config.get("constants") or {})
        defaults = dict(self.config.get("defaults") or {})
        null_fields = list(self.config.get("nulls") or [])
        required_fields = set(self.config.get("required") or [])
        text_fields = list(self.config.get("text_fields") or [])
        doc_type = self.canonical_doc_type(runtime_doc_type)

        elements: list[dict] = []
        element_idx = 0
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

                if text_fields:
                    parts = [
                        str(fields.get(field))
                        for field in text_fields
                        if fields.get(field) not in (None, "")
                    ]
                else:
                    parts = [
                        str(_clean_cell(value))
                        for value in row.values()
                        if _clean_cell(value) not in (None, "")
                    ]
                elements.append({
                    "category": "custom_fields_row",
                    "content": "\n".join(parts),
                    "coordinates": [],
                    "id": element_idx,
                    "page": page,
                    "metadata": fields,
                })
                element_idx += 1

            if skipped:
                # silent 축소 방지 — 몇 행이 빠졌는지 요약으로 드러낸다.
                _log.warning(
                    f"[tabular_custom_fields] skipped {skipped}/{len(rows)} rows "
                    f"(missing required) sheet={sheet_name}"
                )

        result = {"elements": elements, "usage": {"pages": len(sheets)}}
        if doc_type:
            result["metadata"] = {"doc_type": doc_type}
        return result


def build_tabular_custom_fields_mappers(configs: list[dict]) -> list[TabularCustomFieldsMapper]:
    return [
        TabularCustomFieldsMapper(**dict(config))
        for config in (configs or [])
        if custom_fields_extractor(config) in TABULAR_CUSTOM_FIELD_EXTRACTORS
    ]

