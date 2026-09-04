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

from genon.preprocessor.facade.common import config_parse as cp
from genon.preprocessor.facade.enrichment import config_schema as cs
from genon.preprocessor.facade.enrichment import config_v2 as cv2

_log = logging.getLogger(__name__)

from .custom_fields_enricher import (
    TABULAR_CUSTOM_FIELD_EXTRACTORS,
    build_llm_field_specs,
    custom_fields_extractor,
    matches_doc_type,
    normalize_doc_type,
    normalize_doc_types,
)
from .field_transforms import VALUE_TRANSFORMS, render_field_text

# 표 출력 포맷 기본값은 json_records 와 한 벌을 쓴다 — 경로마다 기본값이 갈리면
# 같은 원천이 kind 에 따라 다른 표 모양으로 적재된다.
DEFAULT_TABLE_FORMAT = "html"

# build_fields → to_parse_format 사이에서만 쓰는 행 부가정보. metadata 로 내보내기 전에 pop 한다.
# 필드 dict 안에 실어야 llm_fields 의 skip_record 로 일부 행이 빠져도 정렬이 어긋나지 않는다.
_ROW_PAGE_KEY = "__cf_row_page"
_ROW_FALLBACK_TEXT_KEY = "__cf_row_fallback_text"
_ROW_INTERNAL_KEYS = (_ROW_PAGE_KEY, _ROW_FALLBACK_TEXT_KEY)


def claimed_row_pages(fields_list: list) -> set:
    """레코드 목록이 실제로 맡은 표(페이지) 번호 집합.

    `_ROW_PAGE_KEY` 는 build_fields 와 to_parse_format 사이에서만 쓰는 내부 키라 호출측이
    직접 들여다보면 안 된다. "어느 표를 맡았나"는 여러 매퍼를 돌리는 파서가 알아야 하므로
    판정을 여기 한 벌만 두고 호출측은 이 함수만 쓴다.
    """
    pages = set()
    for fields in fields_list or []:
        page = (fields or {}).get(_ROW_PAGE_KEY)
        if page is not None:
            pages.add(page)
    return pages


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
# ignore_keys/shared_fields/sections 는 json_semantic(SemanticJsonMapper) 전용 키다.
_LIST_SHAPED_KEYS = (
    "required", "text_fields", "chunk_prefix_fields", "ignore_keys",
    "required_shared_fields",
)
_MAP_SHAPED_KEYS = (
    "column_map", "key_map", "collect_key_map", "constants", "defaults", "value_map", "transforms",
    "field_labels",
    "text_from", "html_text_fields", "shared_fields", "sections",
    "row_merge",
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


def validate_chunk_prefix_fields(cfg: dict, *, label: str) -> None:
    """반복 접두 필드는 매퍼가 실제로 만들 수 있는 필드만 허용한다.

    잘못된 필드명을 허용하면 ``content.startswith(chunk_prefix)`` 계약이 깨져 청커가
    접두 재부착을 조용히 포기한다. 제목 소실을 막기 위한 설정인 만큼 기동 시에 즉시 잡는다.
    """
    unknown = sorted(
        {str(f) for f in (cfg.get("chunk_prefix_fields") or [])}
        - collect_target_field_names(cfg)
    )
    if unknown:
        raise ValueError(
            f"{label}: chunk_prefix_fields 의 {unknown} 를 만드는 설정이 없습니다. "
            f"column_map/key_map/constants/defaults/text_from/llm_fields 중 하나에 "
            f"필드를 선언하세요."
        )


def warn_unknown_field_labels(cfg: dict, *, label: str) -> None:
    """`field_labels` 가 어디서도 만들어지지 않는 필드를 가리키면 경고한다.

    이름을 잘못 적으면 에러 없이 라벨만 조용히 사라져 청크 본문이 값만 남는다 — 눈으로
    비교하기 전에는 안 드러나므로 기동 시에 이름을 대조해 둔다.
    """
    labels = cp.parse_field_labels(cfg.get(cp.FIELD_LABELS_KEY))
    if not labels:
        return
    unknown = sorted(set(labels) - collect_target_field_names(cfg))
    if unknown:
        _log.warning(
            f"[custom_fields] {label}: field_labels 의 {unknown} 를 만드는 설정이 없습니다 — "
            f"이름이 틀렸다면 그 필드는 항목명 없이 값만 실립니다."
        )


def validate_custom_field_config(cfg: dict, *, label: str, extractor: str | None = None) -> None:
    """custom_field yaml 하나에 대한 기동 시 검증 묶음(extractor 3종 공통).

    순서가 중요하다 — 지원 키를 먼저 봐야 오타가 "만들 수 없는 필드" 같은 엉뚱한
    메시지로 새지 않고, shape 를 그다음에 봐야 이후 검사가 틀린 타입을 훑지 않는다.

    `extractor` 를 주면 그 extractor 가 읽지 않는 키를 거부하고, **그 키에 딸린 검사도
    건너뛴다**. 예전에는 `json_semantic` 이 읽지도 않는 `chunk_prefix_fields` 를 검사해
    아무 효과 없는 키 때문에 기동이 실패했다(반대로 이름이 맞으면 조용히 무시됐다).
    """
    supported = cs.EXTRACTOR_KEYS.get(cs.canonical_extractor(extractor)) if extractor else None

    def uses(key: str) -> bool:
        """이 extractor 가 그 키를 읽는가(extractor 미지정이면 종전대로 전부 검사)."""
        return supported is None or key in supported

    cs.validate_known_keys(cfg, label=label, extractor=extractor)
    validate_config_shape(cfg, label=label)
    validate_target_field_names(collect_target_field_names(cfg), label=label)
    if uses("required"):
        validate_required_not_llm_generated(cfg, label=label)
    if uses("chunk_prefix_fields"):
        validate_chunk_prefix_fields(cfg, label=label)
    if uses("text_fields"):
        warn_unproducible_text_fields(cfg, label=label)
    warn_unknown_field_labels(cfg, label=label)


# ── 원천 필드 → 평문 파생 필드(text_from) ────────────────────────────────────
# 같은 컬럼에 JSON·HTML·평문이 섞여 오는 원천이 있어(모니모 AI차트뷰 detail_desc) 종류를
# 미리 못 박을 수 없다. 그래서 키 하나로 접고 종류는 render_field_text 가 자동 판별한다.
#
#   text_from:
#     DETAIL_TEXT: DETAIL_DESC     # 원본 필드는 그대로 남고 평문 사본만 더해진다
#
# `html_text_fields` 는 HTML 로 **강제**하는 별칭이다. 판별을 믿을 수
# 없는 원천이나 기존 출고 설정(monimo_event·monimo_news)이 계속 쓰던 대로 동작한다.
_TEXT_FROM_BLOCKS = (("text_from", None), ("html_text_fields", "html"))


def compile_text_from(cfg: dict, *, label: str = "text_from") -> list[tuple[str, str, str | None]]:
    """`[(목표필드, 원천필드, 강제종류|None)]` 로 컴파일한다(세 블록 공통)."""
    specs: list[tuple[str, str, str | None]] = []
    seen: dict[str, str] = {}
    for key, forced in _TEXT_FROM_BLOCKS:
        block = cfg.get(key)
        if not block:
            continue
        if not isinstance(block, dict):
            raise ValueError(f"{label}: {key} 는 '파생필드: 원천필드' 형태의 object 여야 합니다.")
        for target, source in block.items():
            target, source = str(target), str(source)
            if target in seen:
                raise ValueError(
                    f"{label}: 파생 필드 '{target}' 가 {seen[target]} 와 {key} 양쪽에 선언됐습니다. "
                    f"한 곳에만 두세요."
                )
            seen[target] = key
            specs.append((target, source, forced))
    return specs


def apply_text_from(fields: dict, specs: list, html_renderer: Any = None) -> None:
    """컴파일된 text_from 을 제자리 적용한다. 원본 필드는 건드리지 않는다."""
    for target, source, forced in specs:
        fields[target] = render_field_text(
            fields.get(source), kind=forced, html_renderer=html_renderer
        )


def structural_html_renderer(**options: Any):
    """구조 HTML(표·목록)을 docling 으로 평문화하는 콜백을 만든다.

    함수 안에서 import 하는 이유: `json_records` 가 이 모듈을 import 하므로 모듈 최상단에서
    되가져오면 순환 import 가 된다. 호출 시점에는 양쪽이 모두 로드돼 있어 안전하다.
    표가 없는 인라인 조각은 render_field_text 가 경량 경로로 처리하므로 docling 을 타지 않는다.
    """
    from .json_records import html_to_text

    return lambda value: html_to_text(value, **options)


def collect_target_field_names(cfg: dict) -> set[str]:
    """설정이 만들어 내는 목표필드명 전체(세 extractor 공통 키 + 각자 전용 키)."""
    cfg = cfg or {}
    names = (
        set(cfg.get("column_map") or {})
        | set(cfg.get("key_map") or {})
        | set(cfg.get("collect_key_map") or {})
        | set(cfg.get("shared_fields") or {})  # json_semantic 전용
    )
    names |= set(cfg.get("constants") or {}) | set(cfg.get("defaults") or {})
    # derive 는 다른 필드를 합쳐 새 목표필드를 만든다. 여기 넣지 않으면 그 필드를
    # text_fields 에 쓰면 오탐 경고가 나고, chunk_prefix_fields/filter 에 쓰면 기동이 실패한다.
    names |= set(cfg.get("derive") or {})
    names |= {target for target, _, _ in compile_text_from(cfg)}
    names |= {
        str(f)
        for spec in (cfg.get("llm_fields") or [])
        for f in ((spec or {}).get("output_fields") or [])
    }
    # 문서형(llm) 전용 목표필드. 최상위 `output_fields` 는 LLM 이 만드는 필드이고,
    # `front_matter_map`(v2 `fields.<목표>.alias`)은 markdown front matter 에서 가져오는
    # 필드다. 넣지 않으면 그 이름을 `template` 이 참조할 때 "만들 수 없는 필드" 로 막힌다.
    names |= {str(f) for f in (cfg.get("output_fields") or [])}
    names |= set(cfg.get("front_matter_map") or {})
    return names


# ── 여러 행을 한 레코드로 접기(row_merge) ────────────────────────────────────
# 원천이 값 하나를 여러 행에 나눠 보내는 스키마가 있다. 모니모 AI차트뷰(stock_insight)는
# 한 종목의 세부내용 JSON 하나를 `ntc_objline` 1..N 으로 **문자 단위 절단**해 뿌린다 —
# 실제 원천에 `"price_pat` / `tern_desc":` 처럼 키 이름 중간에서 끊긴 사례가 있다.
# 그래서 기본 separator 는 빈 문자열이다. 구분자를 끼우면 JSON 이 복원되지 않는다.
#
#   row_merge:
#     group_by: [REGT_NO, JONG_CODE]   # 이 값이 같은 "연속" 행이 한 묶음
#     order_by: NTC_OBJLINE_NO         # 묶음 안 정렬 기준(숫자 우선)
#     concat:   [DETAIL_DESC]          # 순서대로 이어붙일 필드
#     separator: ""                    # 기본값
#
# 이름은 원천 컬럼명이 아니라 **목표필드명**을 쓴다(column_map 을 거친 뒤 이름). yaml 안에서
# 어휘를 하나로 유지하려는 것이고, 오타는 아래 검증이 기동 시에 잡는다.


def compile_row_merge(cfg: dict, *, label: str) -> dict | None:
    """`row_merge` 설정을 검증해 컴파일한다. 미선언이면 None(= 행 1개 = 레코드 1개)."""
    spec = cfg.get("row_merge")
    if not spec:
        return None
    if not isinstance(spec, dict):
        raise ValueError(f"{label}: row_merge 는 '키: 값' 형태의 object 여야 합니다.")

    def as_list(key: str) -> list[str]:
        value = spec.get(key)
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError(f"{label}: row_merge.{key} 는 목록이어야 합니다(각 항목 앞에 '- ').")
        return [str(f).strip() for f in value if str(f).strip()]

    group_by = as_list("group_by")
    concat = as_list("concat")
    order_by = str(spec.get("order_by") or "").strip() or None
    if not group_by:
        raise ValueError(f"{label}: row_merge 에는 group_by 가 필요합니다(묶음 경계를 정하는 필드).")
    if not concat:
        raise ValueError(f"{label}: row_merge 에는 concat 이 필요합니다(이어붙일 필드).")

    known = collect_target_field_names(cfg)
    unknown = sorted({*group_by, *concat, *([order_by] if order_by else [])} - known)
    if unknown:
        raise ValueError(
            f"{label}: row_merge 의 {unknown} 를 만드는 설정이 없습니다. "
            f"column_map/constants/defaults 중 하나에 선언된 목표필드명을 쓰세요 "
            f"(원천 컬럼명이 아니라 매핑 후 이름입니다)."
        )
    return {
        "group_by": group_by,
        "order_by": order_by,
        "concat": concat,
        "separator": str(spec.get("separator", "") or ""),
    }


def _order_key(value: Any) -> tuple[int, float, str]:
    """order_by 정렬 키. 숫자면 숫자로, 아니면 문자열로 — 섞여 있어도 터지지 않는다."""
    if value in (None, ""):
        return (2, 0.0, "")
    try:
        return (0, float(str(value).strip()), "")
    except (TypeError, ValueError):
        return (1, 0.0, str(value))


def merge_row_records(
    records: list[tuple[dict, dict]],
    spec: dict,
    resolved: dict[str, str | None],
) -> list[tuple[dict, dict]]:
    """`(목표필드 dict, 원본 row dict)` 목록을 group_by 연속 런 단위로 접는다.

    **연속 런** 기준인 이유: 멀리 떨어진 동일 키를 끌어와 붙이면 원천이 같은 등록번호를
    재사용했을 때 서로 다른 게시물이 한 덩어리로 뭉개진다. 원천은 조각을 붙여서 보내므로
    연속으로 충분하고, 그렇지 않은 데이터를 조용히 이어붙이지 않는 쪽이 안전하다.

    concat 이외 필드는 정렬 후 **첫 행** 값을 쓴다. 원본 row dict 도 첫 행 것을 쓰되
    concat 대상의 원천 컬럼만 이어붙인 값으로 덮는다(폴백 본문이 조각만 갖지 않게).
    """
    group_by = spec["group_by"]
    order_by = spec["order_by"]
    concat = spec["concat"]
    separator = spec["separator"]

    merged: list[tuple[dict, dict]] = []
    run: list[tuple[dict, dict]] = []
    run_key: Any = object()

    def flush() -> None:
        if not run:
            return
        ordered = sorted(run, key=lambda item: _order_key(item[0].get(order_by))) if order_by else run
        fields = dict(ordered[0][0])
        row = dict(ordered[0][1])
        for target in concat:
            pieces = [
                str(item[0].get(target))
                for item in ordered
                if item[0].get(target) not in (None, "")
            ]
            value = separator.join(pieces) if pieces else None
            fields[target] = value
            source = resolved.get(target)
            if source is not None:
                row[source] = value
        merged.append((fields, row))
        run.clear()

    for fields, row in records:
        key = tuple(fields.get(name) for name in group_by)
        if run and key != run_key:
            flush()
        run_key = key
        run.append((fields, row))
    flush()
    return merged


# ── 값 변환(transforms) — 이름만 쓰는 형태와 인자를 주는 형태 둘 다 ──────────
#
#   transforms:
#     OPEN_DT: date_int_flex                              # 인자 없는 변환기(종전 표기)
#     FEE_AMT:                                            # 인자를 주는 변환기, 순서대로 적용
#       - {name: regex_sub, pattern: "[^0-9]", repl: ""}
#       - {name: to_int}
#
# 체이닝을 허용하는 이유: "콤마를 지우고 정수로" 처럼 두 단계가 필요한 요건이 흔한데, 이걸
# 못 쓰면 그때마다 field_transforms.py 에 전용 함수를 추가하게 된다 — 그게 "새 요건 = 코드
# 수정"의 통로였다.


def compile_transforms(spec: Any, *, label: str) -> dict[str, list]:
    """`transforms` 를 `{목표필드: [적용할 (함수, 인자) 목록]}` 으로 컴파일한다.

    잘못된 변환기 이름·빠진 인자·컴파일 안 되는 정규식을 **기동 시**에 잡는다. 요청 때
    터지면 어느 설정이 문제인지 로그만 보고는 알 수 없다.
    """
    if not spec:
        return {}
    if not isinstance(spec, dict):
        raise ValueError(f"{label}: transforms 는 '키: 값' 형태의 object 여야 합니다.")

    compiled: dict[str, list] = {}
    for target, entry in spec.items():
        steps = entry if isinstance(entry, list) else [entry]
        chain = []
        for step in steps:
            if isinstance(step, str):
                name, kwargs = step, {}
            elif isinstance(step, dict):
                kwargs = {k: v for k, v in step.items() if k != "name"}
                name = str(step.get("name") or "")
            else:
                raise ValueError(
                    f"{label}: transforms.{target} 의 각 단계는 이름(문자열)이거나 "
                    f"`{{name: …, 인자: …}}` object 여야 합니다."
                )
            chain.append(_compile_transform_step(name, kwargs, target=str(target), label=label))
        compiled[str(target)] = chain
    return compiled


def _compile_transform_step(name: str, kwargs: dict, *, target: str, label: str):
    """변환기 한 단계를 `(함수, 인자)` 로 만든다. 이름·인자·정규식을 여기서 검증한다."""
    from .field_transforms import (
        ALL_TRANSFORM_NAMES, PARAM_TRANSFORM_REQUIRED, PARAM_TRANSFORMS,
    )

    if name in VALUE_TRANSFORMS:
        if kwargs:
            raise ValueError(
                f"{label}: transforms.{target} 의 '{name}' 은 인자를 받지 않습니다: {sorted(kwargs)}"
            )
        return (VALUE_TRANSFORMS[name], {})
    if name not in PARAM_TRANSFORMS:
        raise ValueError(
            f"{label}: 등록되지 않은 transforms 변환기: {name!r} "
            f"(사용 가능: {list(ALL_TRANSFORM_NAMES)})"
        )

    missing = [k for k in PARAM_TRANSFORM_REQUIRED[name] if k not in kwargs]
    if missing:
        raise ValueError(f"{label}: transforms.{target} 의 '{name}' 에 {missing} 인자가 필요합니다.")
    if "pattern" in kwargs:
        try:
            re.compile(str(kwargs["pattern"]))
        except re.error as exc:
            raise ValueError(
                f"{label}: transforms.{target} 의 정규식이 잘못됐습니다: {exc}"
            ) from exc
    return (PARAM_TRANSFORMS[name], dict(kwargs))


def apply_transforms(fields: dict, compiled: dict[str, list]) -> None:
    """컴파일된 변환 체인을 제자리 적용한다."""
    for target, chain in compiled.items():
        value = fields.get(target)
        for func, kwargs in chain:
            value = func(value, **kwargs) if kwargs else func(value)
        fields[target] = value


# ── 필드 결합(derive) ────────────────────────────────────────────────────────
#
#   derive:
#     DISPLAY_NM: "{{BRAND}} {{PRODUCT_NM}}"
#
# 지금까지 두 필드를 하나로 합치는 방법이 없었다. `row_merge.concat` 은 같은 필드를 여러
# 행에 걸쳐 잇는 것이고, 별칭 목록은 첫 값을 고르는 폴백이라 결합이 아니다. 유일한 결합
# 지점이 `text_fields`(청크 본문)뿐이라 **metadata 필드**로는 만들 수 없었다.
_DERIVE_VAR_RE = re.compile(r"\{\{\s*([_A-Za-z][_0-9A-Za-z]*)\s*\}\}")


def compile_derive(cfg: dict, *, label: str) -> dict[str, str]:
    """`derive` 를 검증해 컴파일한다. 참조하는 필드가 없으면 기동 시 실패한다."""
    spec = cfg.get("derive")
    if not spec:
        return {}
    if not isinstance(spec, dict):
        raise ValueError(f"{label}: derive 는 '키: 값' 형태의 object 여야 합니다.")

    known = collect_target_field_names(cfg)
    compiled: dict[str, str] = {}
    for target, template in spec.items():
        if not isinstance(template, str):
            raise ValueError(
                f"{label}: derive.{target} 는 `\"{{{{필드}}}} {{{{필드}}}}\"` 형태의 문자열이어야 합니다."
            )
        unknown = sorted({v for v in _DERIVE_VAR_RE.findall(template)} - known - set(spec))
        if unknown:
            raise ValueError(
                f"{label}: derive.{target} 가 참조하는 {unknown} 를 만드는 설정이 없습니다."
            )
        compiled[str(target)] = template
    return compiled


def apply_derive(fields: dict, compiled: dict[str, str]) -> None:
    """템플릿을 채워 파생 필드를 만든다. 값이 없는 자리는 빈 문자열로 두고 양끝을 다듬는다."""
    for target, template in compiled.items():
        def _sub(match: "re.Match") -> str:
            value = fields.get(match.group(1))
            return "" if value in (None, "") else str(value)

        text = _DERIVE_VAR_RE.sub(_sub, template).strip()
        fields[target] = text or None


# ── 값 기반 레코드 필터(filter) ──────────────────────────────────────────────
#
#   filter:
#     - {field: DEL_YN, not_in: [Y]}
#     - {field: STATUS, in: [ACTIVE, PENDING]}
#
# `required` 는 **빈 값**만 거른다. "삭제여부 Y 인 행은 빼라" 를 표현할 수 없어, 지금까지는
# value_map 에 빈 문자열 표준값을 두고 required 로 떨구는 우회를 썼다. 그 우회는 열거하지
# 않은 값이 그대로 통과하는 fail-open 이고(신규 코드값이 조용히 적재된다) 원래 값도 파괴된다.
#
# 필터는 required 보다 **먼저** 본다 — 대상이 아닌 레코드를 "필수값 누락"으로 경고하면
# 정상 동작이 데이터 품질 사고처럼 보인다.
_FILTER_OPS = ("in", "not_in")


def compile_filter(cfg: dict, *, label: str) -> list[tuple[str, str, set]]:
    """`filter` 를 `[(필드, 연산, 정규화된 값 집합)]` 으로 컴파일한다."""
    spec = cfg.get("filter")
    if not spec:
        return []
    if not isinstance(spec, list):
        raise ValueError(f"{label}: filter 는 목록이어야 합니다(각 항목 앞에 '- ').")

    known = collect_target_field_names(cfg)
    compiled: list[tuple[str, str, set]] = []
    for index, item in enumerate(spec):
        where = f"{label}: filter[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"{where} 는 `{{field: …, in: […]}}` 형태의 object 여야 합니다.")
        field = str(item.get("field") or "").strip()
        if not field:
            raise ValueError(f"{where} 에 field 가 필요합니다.")
        if field not in known:
            raise ValueError(f"{where}: {field!r} 를 만드는 설정이 없습니다.")
        ops = [op for op in _FILTER_OPS if op in item]
        if len(ops) != 1:
            raise ValueError(
                f"{where} 에는 {list(_FILTER_OPS)} 중 정확히 하나가 필요합니다: {sorted(item)}"
            )
        values = item[ops[0]]
        if not isinstance(values, list) or not values:
            raise ValueError(f"{where}.{ops[0]} 는 비어 있지 않은 목록이어야 합니다.")
        # 비교 규칙은 value_map 과 같다 — 대소문자·공백·구분자 차이를 무시한다.
        compiled.append((field, ops[0], {normalize_column_name(v) for v in values}))
    return compiled


def passes_filter(fields: dict, compiled: list[tuple[str, str, set]]) -> bool:
    """모든 조건을 만족하면 True. 조건이 없으면 항상 True."""
    for field, op, values in compiled:
        current = normalize_column_name(fields.get(field))
        if op == "in" and current not in values:
            return False
        if op == "not_in" and current in values:
            return False
    return True


# ── doc_type 하나에 매퍼 여러 개 ────────────────────────────────────────────
#
# 한 파일에 성격이 다른 묶음이 여럿 오는 원천이 있다 — JSON 에 `faqList` 와 `noticeList` 가
# 함께 오거나, 엑셀 한 장에 스키마가 다른 표가 둘 있는 경우다. 매퍼 하나에 `column_map`
# 하나뿐이라 그중 하나만 다룰 수 있었고, 나머지는 통째로 버려졌다.
#
# `tables:` 같은 하위 스키마를 새로 만들지 않고 **매퍼를 여러 개 등록하게** 푼다. 각 매퍼가
# 자기가 다룰 수 있는 묶음만 맡고 결과를 이어 붙인다 — 설정 개념이 늘지 않고, 이미 있는
# doc_type 등록 구조를 그대로 쓴다.
#
# 주의: 실수로 같은 설정을 두 번 등록한 것과 구분해야 한다. 그 판정은 호출측이 한다
# (json 은 `records` 키가 서로 달라야, tabular 은 맡는 블록이 겹치지 않아야 의도적이다).


def merge_parse_formats(results: list[dict]) -> dict:
    """여러 매퍼의 parse-format 산출을 하나로 합친다(element 는 등록 순서대로 이어 붙인다)."""
    if not results:
        return {"elements": [], "usage": {"pages": 0}}
    if len(results) == 1:
        return results[0]

    elements: list[dict] = []
    pages = 0
    metadata: dict = {}
    for result in results:
        elements.extend(result.get("elements") or [])
        pages = max(pages, int((result.get("usage") or {}).get("pages") or 0))
        metadata.update(result.get("metadata") or {})

    merged = {"elements": elements, "usage": {"pages": pages or len(elements)}}
    if metadata:
        merged["metadata"] = metadata
    return merged


def compile_chunk_prefix_fields(cfg: dict, *, split: bool) -> list[str]:
    """`chunk_prefix_fields` 를 매퍼가 쓸 목록으로 정규화한다(두 매퍼 공통).

    `split: false` 면 빈 목록으로 만든다. 접두는 본문 맨 앞으로 끌어올려지므로, 분할하지 않는
    설정에서 접두를 허용하면 `text_fields` 중간 필드를 지정했을 때 청크 본문 순서가 조용히
    바뀐다 — 분할과 무관한 본문 재정렬 수단으로 오용되지 않게 여기서 막는다.
    """
    if not split:
        return []
    return [
        str(f).strip()
        for f in (cfg.get("chunk_prefix_fields") or [])
        if str(f).strip()
    ]


# ── 청크 본문에 실을 값의 표기 ───────────────────────────────────────────────
# 목표필드 값은 스칼라만이 아니다. json_mapping 의 `collect`(반복 key 수집)와 스칼라 배열
# 원천(`related_keywords`)은 값이 **리스트**다. 이를 `str()` 로 그냥 찍으면 파이썬 repr
# (`['a', 'b']`)이 그대로 임베딩 입력에 실리고, 빈 리스트는 `'[]'` 라는 본문을 만들어
# "본문이 빈 레코드는 제외한다"는 계약을 빠져나가 내용 없는 벡터가 적재된다.


def _has_chunk_text_value(value: Any) -> bool:
    """본문에 실을 값이 있는가. 빈 컨테이너는 값이 없는 것으로 본다."""
    if value is None or value == "":
        return False
    if isinstance(value, (list, tuple, dict)) and not value:
        return False
    return True


def _chunk_text_value(value: Any) -> str:
    """본문에 실을 문자열. 배열은 항목을 `, ` 로 잇는다(파이썬 repr 을 내보내지 않는다).

    줄바꿈이 아니라 쉼표로 잇는 이유: 라벨(`키워드: …`)은 여러 줄 블록에 붙지 않으므로,
    줄바꿈으로 이으면 항목이 둘 이상일 때만 항목명이 조용히 사라진다.
    """
    if isinstance(value, (list, tuple)):
        return ", ".join(
            str(item) for item in value if item is not None and item != ""
        )
    return str(value)


def build_chunk_text(
    fields: dict,
    text_fields: list[str],
    chunk_prefix_fields: list[str],
    *,
    fallback_text: str = "",
    column_map: dict | None = None,
    field_labels: dict | None = None,
) -> tuple[str, str]:
    """본문과 모든 분할 조각에 반복할 접두를 함께 만든다.

    접두 필드는 본문에서 제외해 제목이 두 번 들어가지 않게 한다. 반환한 ``content`` 는
    접두가 있으면 반드시 그 문자열로 시작하므로 chunking processor 의 재부착 계약을 만족한다.
    """
    column_map = column_map or {}
    field_labels = field_labels or {}

    def label_for(field: str) -> str | None:
        """이 필드를 `항목명: 값` 으로 낼 때 쓸 이름. 이름이 없으면 None(값만 낸다).

        우선순위는 `field_labels`(사람이 붙인 이름) > `column_map` 별칭 첫 값(엑셀/CSV 원천
        헤더) 이다. json_mapping 은 `column_map` 이 없는데, 그 자리의 `key_map` 별칭은
        `depth4`·`htmlText` 같은 시스템 key 라 라벨로 쓰면 잡음만 된다 — 그래서 폴백하지 않고
        `field_labels` 에 이름이 있는 필드만 항목명과 함께 나간다.

        `column_map` 이 있어도 **거기 없는 필드는 이름 없이 값만 낸다**. 엑셀 헤더로 폴백하는
        근거는 그 헤더가 사람이 읽는 말이라는 것뿐인데, constants·llm_fields 출력처럼
        column_map 에 없는 필드에는 그런 헤더가 없다. 목표필드명으로 폴백하면 `SUMMARY_TEXT: `
        같은 적재 DB 컬럼명이 청크마다 임베딩에 실린다.
        """
        explicit = field_labels.get(field)
        if explicit:
            return str(explicit)
        source_spec = column_map.get(field)
        if isinstance(source_spec, list) and source_spec:
            return str(source_spec[0])
        if source_spec is not None:
            return str(source_spec)
        return None

    def labeled(name: str) -> str:
        """짧은 값은 `항목명: 값`, 여러 줄 블록은 값만.

        text_from 이 만든 파생 필드는 `## 제목` 헤딩을 가진 마크다운 블록이다. 거기에
        `detail_desc: ` 라벨을 덧붙이면 첫 줄만 라벨 뒤에 붙어 구조가 깨지고, 임베딩에는
        의미 없는 컬럼명이 하나 더 들어간다. 블록은 자기 제목을 이미 갖고 있다.
        """
        value = _chunk_text_value(fields[name])
        label = label_for(name)
        return value if ("\n" in value or not label) else f"{label}: {value}"

    prefix_names = set(chunk_prefix_fields)
    prefix = "\n".join(
        labeled(name)
        for name in chunk_prefix_fields
        if _has_chunk_text_value(fields.get(name))
    )
    if text_fields:
        body = "\n".join(
            labeled(name)
            for name in text_fields
            if name not in prefix_names and _has_chunk_text_value(fields.get(name))
        )
    else:
        body = fallback_text

    content = f"{prefix}\n{body}" if prefix and body else (prefix or body)
    return content, prefix


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
        validate_custom_field_config(
            self.config, label=f"tabular custom_fields({config_file})", extractor=extractor
        )

        # 값 정규화·파생 필드 설정은 json_mapping(JsonRecordsMapper)과 같은 키/의미를 쓴다.
        self.value_map = compile_value_map(self.config.get("value_map"))

        self.transforms = compile_transforms(
            self.config.get("transforms"), label=f"tabular custom_fields({config_file})"
        )
        self.derive = compile_derive(self.config, label=f"tabular custom_fields({config_file})")
        self.filter = compile_filter(self.config, label=f"tabular custom_fields({config_file})")
        # 선언만 컴파일한다. 실제 호출은 parser 가 행 목록을 들고 수행한다(json_mapping 과 동일).
        self.llm_field_specs = build_llm_field_specs(self.config)
        self.split = bool(self.config.get("split", False))
        self.chunk_prefix_fields = compile_chunk_prefix_fields(self.config, split=self.split)
        # 사람이 붙인 항목명. 적으면 column_map 별칭 첫 값 대신 이 이름으로 본문에 실린다.
        self.field_labels = cp.parse_field_labels(self.config.get(cp.FIELD_LABELS_KEY))

        # 여러 행에 쪼개져 오는 값을 한 레코드로 접는다(미선언이면 종전대로 행 1개 = 레코드 1개).
        self.row_merge = compile_row_merge(
            self.config, label=f"tabular custom_fields({config_file})"
        )
        # 원천 필드 → 평문 파생 필드. 원본 필드는 그대로 남는다(json_mapping 과 같은 키).
        self.text_from = compile_text_from(
            self.config, label=f"tabular custom_fields({config_file})"
        )

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
        # `schema: v2` 면 내부(v1) 형태로 번역해 넘긴다 — 아래 코드는 v1/v2 를 구분하지 않는다.
        normalized, _ = cv2.load(loaded, label=f"tabular custom_fields({config_file})")
        return normalized

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

    # 행이 아니라 **시트/표 단위**로 붙는 값. 원천이 시트명이나 표 제목에만 담아 보내는
    # 구분값(관계사·상품군 등)을 column_map 에서 그대로 참조할 수 있게 한다.
    # 실제 컬럼이 항상 이기므로, 같은 이름의 진짜 컬럼이 있으면 그쪽이 쓰인다.
    _SHEET_CONTEXT_SOURCES = ("sheet_name", "title")

    def _resolve_sheet_context(self, sheet: dict, sheet_name: str) -> dict[str, Any]:
        """이 시트에서 참조 가능한 컨텍스트 값(정규화된 이름 → 값)."""
        values = {"sheet_name": sheet_name, "title": sheet.get("title")}
        return {
            normalize_column_name(name): values.get(name)
            for name in self._SHEET_CONTEXT_SOURCES
            if str(values.get(name) or "").strip()
        }

    def _context_value(self, target: str, source_spec: Any, context: dict) -> Any:
        """target 의 별칭 중 하나가 컨텍스트 이름이면 그 값을, 아니면 None."""
        if not context:
            return None
        for alias in self._aliases(str(target), source_spec):
            value = context.get(normalize_column_name(alias))
            if value is not None:
                return value
        return None

    def build_fields(
        self, data_dict: dict, runtime_doc_type: Any, *, skip_unmapped: bool = False,
        table_format: str = DEFAULT_TABLE_FORMAT, compact_tables: bool = True,
    ) -> list[dict]:
        """parser의 tabular 중립 표현 → 레코드별 목표필드 목록.

        `to_parse_format` 과 분리해 둔 이유는 그 사이에 `llm_fields`(레코드마다 LLM 호출)를
        끼워 넣기 위해서다 — json_mapping 의 `build_fields → _apply_llm_fields →
        to_parse_format` 3단 구성과 같은 모양이다. 행 페이지/폴백 본문은 예약 키로 필드 dict
        안에 실어 보내고 `to_parse_format` 이 pop 한다.

        3단으로 나뉘어 있다.
          1. 원시 매핑 — 행마다 `column_map` 값만 채운다.
          2. 병합 — `row_merge` 가 있으면 연속 런을 한 레코드로 접는다.
          3. 레코드 마감 — defaults/constants/value_map/transforms/text_from/required.
        순서가 중요하다. transforms 와 required 는 **병합이 끝난 값**을 봐야 한다 —
        조각 하나만 보고 날짜를 변환하거나 필수값을 판정하면 결과가 달라진다.
        `row_merge` 미선언이면 런 길이가 1이라 종전과 동일하다.
        """
        column_map = self.config.get("column_map") or {}
        constants = dict(self.config.get("constants") or {})
        defaults = dict(self.config.get("defaults") or {})
        required_fields = set(self.config.get("required") or [])
        doc_type = self.canonical_doc_type(runtime_doc_type)

        # 구조 HTML 이 섞여 올 수 있으므로 렌더러를 준비한다(파생 필드가 없으면 만들지 않는다).
        # 표 모양을 docling 경로·records 경로와 같은 설정으로 맞춘다. 인자를 주지 않으면
        # 항상 <table> 이 되어, 같은 파일 안에서도 kind 마다 표가 다르게 나온다.
        html_renderer = structural_html_renderer(
            table_format=table_format, compact_tables=compact_tables
        ) if self.text_from else None

        fields_list: list[dict] = []
        sheets = data_dict.get("data", []) or []
        for sheet_idx, sheet in enumerate(sheets):
            page = sheet_idx + 1
            sheet_name = str(sheet.get("sheet_name") or f"sheet_{page}")
            rows = sheet.get("data_rows", []) or []
            resolved = self._resolve_columns(rows[0]) if rows else {
                str(target): None for target in column_map
            }
            sheet_context = self._resolve_sheet_context(sheet, sheet_name)
            missing_columns = [
                field for field in required_fields
                if field in column_map and resolved.get(field) is None and field not in defaults
                and self._context_value(field, column_map[field], sheet_context) is None
            ]
            if missing_columns:
                available = list(rows[0].keys()) if rows else []
                if skip_unmapped:
                    # doc_type 에 매퍼가 여럿일 때는 "내가 맡을 표가 아니다"라는 뜻이다.
                    # 다른 매퍼가 맡으며, 아무도 안 맡으면 호출측이 경고한다.
                    _log.info(
                        f"[tabular_custom_fields] 이 매퍼가 맡지 않는 표(sheet={sheet_name}): "
                        f"필수 컬럼 {sorted(missing_columns)} 없음; available={available}"
                    )
                    continue
                raise ValueError(
                    f"필수 Excel 컬럼 매핑 실패(sheet={sheet_name}): {sorted(missing_columns)}; "
                    f"available={available}"
                )

            # 1단계 — 원시 매핑. 원본 row 도 함께 들고 간다(폴백 본문·병합 재료).
            records: list[tuple[dict, dict]] = []
            for row in rows:
                fields = {}
                for target in column_map:
                    source = resolved.get(str(target))
                    if source:
                        fields[str(target)] = _clean_cell(row.get(source))
                        continue
                    # 컬럼으로 못 잡은 필드만 시트/표 컨텍스트에서 채운다(실제 컬럼이 우선).
                    fields[str(target)] = _clean_cell(
                        self._context_value(target, column_map[target], sheet_context)
                    )
                records.append((fields, row))

            # 2단계 — 병합. 시트 경계는 넘지 않는다(시트마다 page 가 다르다).
            if self.row_merge and records:
                merged = merge_row_records(records, self.row_merge, resolved)
                if len(merged) != len(records):
                    # silent 축소 방지 — 몇 행이 몇 레코드로 접혔는지 드러낸다.
                    _log.info(
                        f"[tabular_custom_fields] row_merge {len(records)} rows -> "
                        f"{len(merged)} records (sheet={sheet_name}, "
                        f"group_by={self.row_merge['group_by']})"
                    )
                records = merged

            # 3단계 — 레코드 마감.
            skipped = filtered = 0
            for record_idx, (fields, row) in enumerate(records, start=1):
                # defaults 를 먼저 채우고 constants 로 덮는다. 반대 순서면 `constants: {X: ""}`
                # 처럼 상수를 빈 값으로 못 박았을 때 defaults 가 그것을 되살려 "constants 가
                # 이긴다"는 계약이 깨진다(json_semantic 과 같은 순서).
                for key, value in defaults.items():
                    if fields.get(key) in (None, ""):
                        fields[key] = value
                fields.update(constants)

                # 값 정규화 → 변환 순서. 별칭을 표준값으로 접은 뒤에 타입 변환을 건다.
                apply_value_map(
                    fields, self.value_map, context=f"(sheet={sheet_name}, row={record_idx})"
                )
                apply_transforms(fields, self.transforms)
                # 결합은 변환 뒤에 — 정규화된 값으로 합쳐야 표기가 흔들리지 않는다.
                apply_derive(fields, self.derive)

                # 파생 필드는 transforms 뒤에 만든다 — 원본 필드는 그대로 두고 평문 사본만 더한다.
                apply_text_from(fields, self.text_from, html_renderer)

                # 대상이 아닌 레코드는 여기서 빠진다. required 보다 **먼저** 보는 것이 중요하다 —
                # 뒤에 두면 정상 제외가 "필수값 누락" 경고로 찍혀 데이터 사고처럼 보인다.
                if not passes_filter(fields, self.filter):
                    filtered += 1
                    continue

                if doc_type:
                    # 요청/프로파일에서 확정한 값이 config constants보다 우선한다.
                    fields["doc_type"] = doc_type

                # 필수값이 빈 레코드는 전체 문서를 중단하지 않고 그것만 skip(경고 로그). 필수 컬럼
                # 자체가 매핑 불가한 경우는 위 missing_columns 에서 이미 하드 에러로 처리했다
                # (전 행 공통 스키마 문제).
                missing_values = [
                    field for field in required_fields if fields.get(field) in (None, "")
                ]
                if missing_values:
                    skipped += 1
                    _log.warning(
                        f"[tabular_custom_fields] 필수값 누락 행 skip(sheet={sheet_name}, "
                        f"row={record_idx}): {sorted(missing_values)}"
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

            if filtered:
                # 정상 제외지만 조용히 줄면 "왜 건수가 다르지"가 된다 — info 로 남긴다.
                _log.info(
                    f"[tabular_custom_fields] filtered {filtered}/{len(records)} records "
                    f"(filter 조건 불일치) sheet={sheet_name}"
                )
            if skipped:
                # silent 축소 방지 — 몇 건이 빠졌는지 요약으로 드러낸다.
                _log.warning(
                    f"[tabular_custom_fields] skipped {skipped}/{len(records)} records "
                    f"(missing required) sheet={sheet_name}"
                )

        return fields_list

    def to_parse_format_from_fields(self, fields_list: list[dict], runtime_doc_type: Any) -> dict:
        """행별 목표필드 목록 → parse-format(청커 행 기반 경로가 소비하는 형태)."""
        text_fields = list(self.config.get("text_fields") or [])
        doc_type = self.canonical_doc_type(runtime_doc_type)

        # 파생 필드는 원본 필드의 라벨을 물려받는다. DETAIL_TEXT 는 원본 detail_desc 를 평문으로
        # 바꾼 같은 내용이므로 원천 헤더가 그대로 맞는 이름이다. 이게 없으면 파생 필드는
        # column_map 에 없어 이름 없이 값만 나간다(목표필드명으로 폴백하지는 않는다).
        column_map = dict(self.config.get("column_map") or {})
        for target, source, _ in self.text_from:
            if target not in column_map and source in column_map:
                column_map[target] = column_map[source]

        elements: list[dict] = []
        max_page = 0
        for fields in fields_list:
            page = fields.pop(_ROW_PAGE_KEY, len(elements) + 1)
            max_page = max(max_page, page)
            fallback_text = fields.pop(_ROW_FALLBACK_TEXT_KEY, "")
            content, prefix = build_chunk_text(
                fields,
                text_fields,
                self.chunk_prefix_fields,
                fallback_text=fallback_text,
                column_map=column_map,
                field_labels=self.field_labels,
            )
            element = {
                "category": "custom_fields_row",
                "content": content,
                "coordinates": [],
                "id": len(elements),
                "page": page,
                "metadata": fields,
            }
            if self.split:
                element["splittable"] = True
                if prefix:   # split 이 아니면 chunk_prefix_fields 가 비어 prefix 도 항상 ""
                    element["chunk_prefix"] = prefix
            elements.append(element)

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
