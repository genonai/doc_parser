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


def validate_target_field_names(targets: Any, *, label: str) -> None:
    """목표필드명이 벡터 예약 필드/property 규칙을 위반하면 **기동 시** 실패시킨다.

    행 metadata 는 그대로 벡터 property 로 승격되므로(`chunking_processor._chunk_custom_fields_rows`
    가 `{**row_meta, 'text': …}` 로 model_validate), 예약 필드명과 겹치면 두 가지로 터진다.
      - `title`·`created_date`·`appendix` : 선언 타입이 있어 **값이 None 이기만 해도** ValidationError
        → 요청 전체 실패. 그 예외는 GenosServiceException 으로 감싸이지 않아 stage 도 없고,
          pydantic v2 ValidationError 가 ValueError 하위라 업로드 파일 문제(INPUT_ERROR)로 오분류된다.
      - `text`·`n_char`·`i_page` 등 : 예약 키가 뒤에 와서 **조용히 덮어써져** 값이 사라진다.
    또 한글·공백·기호가 섞인 이름은 Weaviate property 규칙(`/[_A-Za-z][_0-9A-Za-z]*/`)을 벗어나
    적재 시 grpc 에러가 된다.

    일반 컬럼 경로는 `xlsx_processor._stable_key` 로 이 문제를 이미 회피하는데, custom_fields 는
    그 경로를 타지 않아 검사 없이 통과해 왔다. 판정 기준은 그쪽과 공유해 드리프트를 막는다
    (`_RESERVED_FIELDS` 는 `tests/unit/test_xlsx_processor.py::test_reserved_fields_cover_chunker_vector_meta`
    가 청커 모델과의 정합을 지킨다).
    """
    # facade → converters 단방향 import (parser_processor._parse_tabular 와 같은 방향).
    # 함수 안에서 import 하는 이유: 이 모듈은 converters 없이도 로드돼야 한다(enrichment 단독 테스트).
    from genon.preprocessor.converters.xlsx_processor import _RESERVED_FIELDS, _VALID_KEY_RE

    reserved, invalid = [], []
    for name in targets:
        text = str(name)
        if text in _RESERVED_FIELDS:
            reserved.append(text)
        elif not _VALID_KEY_RE.match(text):
            invalid.append(text)
    if reserved:
        raise ValueError(
            f"{label}: 목표필드명이 벡터 예약 필드와 겹칩니다: {sorted(reserved)}. "
            f"이 이름을 쓰면 값이 조용히 덮어써지거나 청킹에서 요청 전체가 실패합니다 — "
            f"다른 이름으로 바꾸세요(적재 DB 컬럼명은 보통 대문자라 겹치지 않습니다)."
        )
    if invalid:
        raise ValueError(
            f"{label}: 목표필드명이 property 이름 규칙(/[_A-Za-z][_0-9A-Za-z]*/)에 맞지 않습니다: "
            f"{sorted(invalid)}. 한글·공백·기호는 적재 시 실패하므로 영문/숫자/밑줄만 쓰세요 "
            f"(원천 컬럼명은 별칭 목록에 그대로 두면 됩니다)."
        )


# YAML 에서 타입을 틀리기 쉬운 키. 코드가 set()/list()/dict() 로만 감싸기 때문에
# 틀린 타입이 조용히 엉뚱하게 해석되거나 요청마다 터진다 — 기동 시에 잡는다.
_LIST_SHAPED_KEYS = ("required", "nulls", "text_fields")
_MAP_SHAPED_KEYS = (
    "column_map", "key_map", "constants", "defaults", "value_map", "transforms", "html_text_fields",
)


def validate_config_shape(cfg: dict, *, label: str) -> None:
    """리스트/맵이어야 하는 키가 다른 타입이면 **기동 시** 실패시킨다.

    YAML 에서 `-` 를 빠뜨려 스칼라가 들어오면 코드가 문자열을 글자 단위로 쪼갠다 —
    `required: TITLE` 은 `{'T','I','L','E'}` 가 되어 **전 행이 skip → 청크 0건**이 되는데
    경고만 남고 요청은 성공으로 끝난다.
    `constants` 를 리스트로 쓰면 `dict()` 강제 변환이 `build_fields` 안에서 터져 **매 요청**
    실패하고, 메시지(`dictionary update sequence element #0 …`)에 파일도 키도 없다.
    (`dict(['ab','cd'])` → `{'a':'b','c':'d'}` 처럼 **에러 없이 잘못된 값**이 되는 경우도 있다.)
    """
    wrong_list = [k for k in _LIST_SHAPED_KEYS
                  if cfg.get(k) is not None and not isinstance(cfg.get(k), list)]
    wrong_map = [k for k in _MAP_SHAPED_KEYS
                 if cfg.get(k) is not None and not isinstance(cfg.get(k), dict)]
    if wrong_list:
        raise ValueError(
            f"{label}: {sorted(wrong_list)} 는 목록이어야 합니다. YAML 에서 각 항목 앞에 '- ' 를 "
            f"붙이세요 — 문자열로 쓰면 글자 단위로 쪼개져 전 행이 걸러집니다(청크 0건)."
        )
    if wrong_map:
        raise ValueError(
            f"{label}: {sorted(wrong_map)} 는 '키: 값' 형태의 object 여야 합니다."
        )


def validate_required_not_llm_generated(cfg: dict, *, label: str) -> None:
    """`required` 에 `llm_fields` 생성 필드가 있으면 **기동 시** 거부한다.

    필수값 검사는 LLM 호출보다 **먼저** 돈다(build_fields → _apply_llm_fields 순서).
    그래서 LLM 이 만들 필드를 required 로 걸면 **LLM 을 한 번도 부르지 않고 전 행이 skip** 되고,
    요청은 빈 문서 + 성공으로 끝난다 — 원인을 찾기 매우 어렵다.
    """
    llm_outputs = {
        str(f)
        for spec in (cfg.get("llm_fields") or [])
        for f in ((spec or {}).get("output_fields") or [])
    }
    clash = sorted({str(f) for f in (cfg.get("required") or [])} & llm_outputs)
    if clash:
        raise ValueError(
            f"{label}: required 에 llm_fields 가 만드는 필드가 있습니다: {clash}. "
            f"필수값 검사가 LLM 호출보다 먼저 돌아 전 행이 걸러집니다 — required 에서 빼세요."
        )


def warn_unproducible_text_fields(cfg: dict, *, label: str) -> None:
    """`text_fields` 가 아무도 만들지 않는 필드를 가리키면 경고한다.

    그 필드는 본문 조립에서 조용히 빠진다 — 전부 그러면 본문이 비어 레코드가 통째로 제외되고,
    결국 청킹에서 `chunk length is 0` 으로 터진다(원인과 에러 지점이 다르다).
    `llm_fields` 를 주석 처리하면서 그 출력 필드를 `text_fields` 에 남겨 두는 실수가 가장 흔하다.

    실패가 아니라 경고인 이유: 출고 `custom_field_monimo_event.yaml` 이 현재 이 상태라
    hard error 로 두면 서비스가 기동하지 않는다.
    """
    unproducible = [
        str(f) for f in (cfg.get("text_fields") or [])
        if str(f) not in collect_target_field_names(cfg)
    ]
    if unproducible:
        _log.warning(
            f"[custom_fields] {label}: text_fields 의 {unproducible} 를 만드는 설정이 없습니다 — "
            f"청크 본문에서 조용히 빠집니다(llm_fields 를 주석 처리하고 남겨 둔 경우가 흔합니다)."
        )


def validate_custom_field_config(cfg: dict, *, label: str) -> None:
    """custom_field yaml 하나에 대한 기동 시 검증 묶음(두 extractor 공통).

    순서가 중요하다 — shape 를 먼저 봐야 이후 검사가 엉뚱한 타입을 훑지 않는다.
    """
    validate_config_shape(cfg, label=label)
    validate_target_field_names(collect_target_field_names(cfg), label=label)
    validate_required_not_llm_generated(cfg, label=label)
    warn_unproducible_text_fields(cfg, label=label)


def collect_target_field_names(cfg: dict) -> set[str]:
    """설정이 만들어 내는 목표필드명 전체(두 extractor 공통 키 + 각자 전용 키)."""
    cfg = cfg or {}
    names = set(cfg.get("column_map") or {}) | set(cfg.get("key_map") or {})
    names |= set(cfg.get("constants") or {}) | set(cfg.get("defaults") or {})
    names |= {str(f) for f in (cfg.get("nulls") or [])}
    names |= set(cfg.get("html_text_fields") or {})
    names |= {
        str(f)
        for spec in (cfg.get("llm_fields") or [])
        for f in ((spec or {}).get("output_fields") or [])
    }
    return names


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
        # 설정 오기입을 **키를 소비하기 전에** 막는다 — 런타임 크래시·조용한 전건 skip 예방.
        validate_custom_field_config(self.config, label=f"tabular custom_fields({config_file})")

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

