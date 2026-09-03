"""역할이 섞인 중첩 JSON을 의미 단위 섹션으로 정규화해 custom_fields 매핑한다.

## 왜 필요한가

형제 모듈 `json_records.py`(extractor: json_mapping)는 **평평한 레코드 배열**을 위해
설계됐다(`eventList[*]` = "레코드 1건 = DB 1행"). 경로 문법이 없고 키 이름 BFS로만 찾으므로,
같은 `code`/`title`/`serviceUrl`이 깊이마다 다른 뜻을 갖는 카드 WCMS JSON 같은 입력에서는
소속 관계가 소실된다 — 추천 상품(`mpo`)의 혜택 문구가 본 상품 혜택으로 섞이거나, 표가
`<table>` 원문 그대로 청크에 박히거나, 스칼라 배열이 `str(list)` 로 청크에 흘러
`['새마을금고 현금카드 기능', ...]` 같은 파이썬 repr 이 그대로 노출되는 식이다.

이 모듈은 레코드 배열이 아니라 **대상 하나를 깊게 설명하는 JSON**(카드 1장, 상품 1건)을
위한 것이다. 정규화는 경로가 아니라 **값의 성격과 키의 역할**을 본다 — HTML 문자열은
표/목록을 살린 Markdown 으로, 문자열 배열은 목록 섹션으로, 객체 배열은 원소별 섹션으로,
객체가 스스로 들고 있는 제목은 섹션 제목으로 승격된다. 그 결과 "성격별로 나뉜 섹션 하나 =
검색 청크 하나"가 되고, 모든 청크에 공통 정보(상품명·상품코드 등)가 자연어로 붙는다 — 배열이
한 단계 이동해도 `serviceUrl`/`title`/`code` 의 역할이 유지되는 한 계속 동작한다.

같은 출력 계약(`category="custom_fields_row"` + `content` + `metadata`)을 쓰므로 청커의 행
기반 경로(`_chunk_custom_fields_rows`)가 그대로 소비한다 — 새 element category 를 만들지
않는다. `matches`/`build_fields`/`to_parse_format` 세 메서드도 `JsonRecordsMapper` 와 같은
시그니처라 parser 호출부가 두 매퍼를 구분하지 않는다(덕 타이핑). 차이는 "레코드 1건 =
element 1개"가 아니라 "섹션 1개 = element 1개"라는 점뿐이다.

## 키 지정 방식

`json_records.py` 와 마찬가지로 **키 이름만** 나열한다(JSONPath 등 경로 문법 없음). 설정에서
정하는 것은 세 가지뿐이다.

  - `shared_fields` : 모든 섹션에 공통으로 실을 값(상품명·상품코드 …). 별칭은
    **문서 루트 객체에서만** 찾아 문서당 한 번 확정한다. json_semantic 의 전제는
    "1 파일 = 1 대상"이므로, 루트에 상품 식별자가 없을 때 `mpo[].code` 같은 관련 상품의
    동명 키로 빈 값을 보충하면 안 된다. 하위 객체의 값은 섹션 본문에만 사용한다.
  - `sections`      : JSON key 별 표시 이름과 포함 여부(`include: false` 로 통째로 제외).
    설정하지 않은 key 도 자동으로 검색에 들어간다 — 빠뜨려서 누락되는 일이 없다.
  - `ignore_keys`   : 검색에 쓸모없는 key(이미지 경로 등)를 glob 으로 제외한다.
  - `required_shared_fields` : 이 목표필드가 하나라도 비어 있으면(문서 루트에서 못 찾으면)
    `missing_policy` 에 따라 처리한다(`error`=예외, `skip`=경고 후 섹션 0건). 기본은
    `error` 다(`json_records.py` 의 `missing_policy` 와 같은 이름·기본값).
  - `defaults` : 루트 원천값이 없거나 빈 문자열일 때만 채우는 기본값.
  - `constants` : 원천값/defaults 유무와 관계없이 항상 덮어쓰는 고정값.

나머지(HTML→Markdown 변환, 섹션 제목 승격, 부모-자식 중복 제거, 식별자뿐인 섹션 접기,
깊이/규모 상한)는 전부 값의 성격을 보고 자동으로 처리된다.

## LLM 생성 필드

json_records 는 레코드마다 LLM 을 부르지만, 이 모듈은 **문서(파일) 1건당 LLM 1회**만
호출한다(`llm_fields_scope = "document"`). 섹션이 N개라고 N번 부르면 카드 1장에 10회 넘게
호출되므로, `document_input_fields` 로 대표 입력 하나를 만들고 파서가 결과를 전 섹션에
그대로 복사한다(`parser_processor._apply_llm_fields` 의 document 스코프 분기). 이 모듈은
순수 변환만 담당하고 실제 LLM 호출은 하지 않는다.

`llm_fields.input_fields` 에 적을 수 있는 이름은 세 가지다.

  - `shared_fields` 의 목표필드명 (`PRODUCT_NM` …) — 짧은 사실값
  - `PRODUCT_INFO` — 전 섹션 본문을 이어붙인 문서 전체 평문
  - **JSON key 이름** (`htmlList`, `feeUrl` …) — 그 key 를 경로에 가진 섹션들의 청크 본문만.
    문서 전체를 넣으면 토큰이 크고 노이즈가 많아 추출이 흔들리므로 필요한 부분만 좁힌다.

어느 쪽으로도 못 찾은 이름은 경고를 남긴다 — 조용히 빠지면 빈 `raw_text` 로 LLM 이 호출돼
"모델이 못 뽑았다"로 보이기 때문이다.
"""
from __future__ import annotations

import fnmatch
import json
import logging
import re
from pathlib import Path
from typing import Any

import yaml

_log = logging.getLogger(__name__)

from .custom_fields_enricher import (
    JSON_SEMANTIC_EXTRACTORS,
    build_llm_field_specs,
    custom_fields_extractor,
    matches_doc_type,
    normalize_doc_type,
    normalize_doc_types,
)
from .json_records import (
    DEFAULT_TABLE_FORMAT,
    VALID_MISSING_POLICIES,
    find_field,
    html_to_text,
    normalize_table_format,
)
from genon.preprocessor.facade.common import config_parse as cp
from genon.preprocessor.facade.enrichment import config_v2 as cv2
from .tabular_custom_fields import normalize_column_name, validate_custom_field_config

# 스칼라로 볼 값 타입(json_records._SCALAR_TYPES 와 동일 기준).
_SCALAR_TYPES = (str, int, float, bool)

# 제목이 따로 없는 컨테이너 자신의 leftover 스칼라 필드를 담는 섹션의 기본 제목
# (문서 루트가 대표적 — "이 문서가 통째로 다루는 대상의 기본 정보" 섹션).
_DEFAULT_SECTION_TITLE = "개요"

# shared_fields 목표필드명 -> 사람이 읽는 라벨. 여기 있는 필드만 본문(청크 접두)에 실린다 —
# 라벨이 없는 필드(예: BIZ_ID)는 사람이 검색어로 쓰지 않는 내부 식별자이므로 metadata(적재
# 컬럼)에만 남기고 본문에서는 뺀다(규칙 10, `_chunk_prefix` 참고). yaml 없이도 동작하는
# 기본값일 뿐, 필요하면 얼마든지 늘릴 수 있다 — 새 shared_fields target 을 여기 추가하지
# 않으면 자동으로 "본문에는 안 실리고 metadata 에만 남는" 필드가 된다.
# 공통 필드의 기본 항목명. 설정의 `field_labels` 가 이 위에 덮어쓰고 새 필드도 더할 수 있다.
_SHARED_FIELD_LABELS = {
    "PRODUCT_NM": "상품명",
    "PRODUCT_C": "상품코드",
}

# 트리 폭주 방어(순환 참조·비정상 대형 payload). 실측 카드 JSON은 깊이 5 안팎이라
# 넉넉히 잡는다 — 여기 걸리면 데이터가 아니라 설정/원천이 잘못됐을 가능성이 크다.
_MAX_DEPTH = 12
_MAX_NODES = 5000

# 규칙 9(식별자뿐인 섹션 접기) 판정용 — 키가 id/code/no/seq 로 끝나고 값이 공백 없는 짧은
# 토큰이면 식별자로 본다(예: "code": "P000210"). 이런 필드만 남은 섹션은 검색에 쓸모가 없다.
_ID_KEY_RE = re.compile(r"(?:id|code|no|seq)$", re.IGNORECASE)

# 규칙 12(인라인 HTML 평문화) 판정용 — 태그가 하나라도 있으면 길이·detect_format 과 무관하게
# html_to_text 를 통과시킨다. `_is_rich`(길이>120 또는 detect_format=="html")는 "이 값이 통째로
# 제 섹션이 될 자격이 있는가"를 보는 기준(규칙 1/4 의 문턱)이라 `cardSlogan` 처럼 짧은
# 문자열(`<br>` 하나)은 그 문턱을 못 넘어 원문 태그가 그대로 새어 나갔다 — 이 정규식은 그
# 문턱과 무관하게 "태그가 있는가" 만 본다.
_HTML_TAG_RE = re.compile(r"<[a-zA-Z][^>]*>")


# `input_fields` 이름을 SOURCE_JSON_PATH 세그먼트와 맞출 때 배열 인덱스를 떼는 패턴
# (`benefit[3]` -> `benefit`). 이 모듈은 경로 문법을 쓰지 않으므로 사람이 적는 것은 언제나
# key 이름 하나뿐이다.
_PATH_INDEX_RE = re.compile(r"\[\d+\]$")


def _path_segments(json_path: Any) -> set[str]:
    """`$.htmlList.feeUrl` -> {"htmlList", "feeUrl"}. 루트 표식 `$` 는 제외한다."""
    segments: set[str] = set()
    for part in str(json_path or "").split("."):
        part = _PATH_INDEX_RE.sub("", part.strip())
        if part and part != "$":
            segments.add(part)
    return segments


def _is_scalar(value: Any) -> bool:
    return isinstance(value, _SCALAR_TYPES)


def _find_root_field(payload: dict[str, Any], aliases: list[str]) -> Any:
    """shared field 별칭을 문서 루트의 스칼라 값에서만 찾는다.

    ``find_field``의 별칭 우선순위·정규화·문자열 정리 규칙은 재사용하되, 중첩 object/list는
    검색 입력에서 제거한다. 따라서 루트 상품코드가 빠져도 ``mpo[].code``나 ``ksp[].code``가
    상품 identity로 승격되지 않는다. 스칼라 배열은 PRODUCT_ATTRS 같은 metadata 값이므로
    루트 값으로 허용한다.
    """
    root_scalars = {
        key: value
        for key, value in payload.items()
        if _is_scalar(value)
        or (isinstance(value, list) and all(_is_scalar(item) for item in value))
    }
    return find_field(root_scalars, aliases)


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _is_rich(value: Any, detect_format: Any) -> bool:
    """이 값이 "제 스스로 섹션이 될 자격이 있는" 풍부한 콘텐츠인가(HTML 이거나 장문).

    `detect_format` 은 `converters.json_text.detect_format` 을 그대로 받는다(지연 임포트로
    호출측이 넘겨준다 — 이 모듈이 converters 를 모듈 최상단에서 import 하지 않기 위해서다).
    """
    return isinstance(value, str) and (detect_format(value) == "html" or len(value) > 120)


def _ignored(key: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(key, pattern) for pattern in patterns)


def _looks_like_identifier(key: str, value: Any) -> bool:
    """규칙 9 판정: 이 (key, value) 한 줄이 식별자뿐인가."""
    if not isinstance(value, (str, int, float, bool)):
        return False
    text = str(value)
    return bool(_ID_KEY_RE.search(str(key))) and len(text) <= 24 and " " not in text


def _render_value(value: Any) -> str:
    """`key: value` 줄에 쓸 문자열. 스칼라 리스트는 `", ".join` 으로 — `str(list)` 는 파이썬
    repr(`['a', 'b']`)을 그대로 흘려 청크 본문을 오염시킨다(json_records.build_text 의 기존
    버그와 같은 실수를 반복하지 않는다)."""
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def _flatten_scalar_text(value: Any, table_format: str, compact_tables: bool) -> Any:
    """규칙 12 — 스칼라 문자열 안의 인라인 HTML(`<br>`, `<b>`, `<span>` …)을 평문화한다.

    태그가 없으면 그대로 반환한다(문자열이 아닌 값도 그대로 통과). 결과는 한 줄짜리 "사실
    행"(`key: value` 또는 값만)으로 쓰이므로, html_to_text 가 만든 개행(`<br>` 이 markdown
    줄바꿈이 된 것)은 공백으로 합쳐 한 줄을 유지한다 — 여러 줄로 쪼개지면 다음 사실 행과
    경계가 무너진다.
    """
    if not isinstance(value, str) or not _HTML_TAG_RE.search(value):
        return value
    text = html_to_text(value, table_format=table_format, compact_tables=compact_tables)
    return " ".join(text.split())


def _local_lines_to_text(
    local_lines: list[tuple[str, Any]], *, title: str, extra_body: str
) -> list[str]:
    """규칙 11 — shared_fields 가 아닌 원문 스칼라 한 줄을 정리해 렌더링한다.

    순서대로 판정한다.
      1) 값이 섹션 제목과 같으면 제외(예: `serviceName` 이 `title` 과 중복)
      2) 이미 본문에 나온 값과 같으면 제외(같은 섹션 안에서의 중복 방지)
      3) 식별자로 보이면 제외(규칙 9 판정 헬퍼 `_looks_like_identifier` 재사용)
      4) 남으면 원문 key 이름 없이 값만 한 줄로 낸다 — 원문 key(`tabName`, `mpoNum` 등)를
         본문 라벨로 노출하는 경로를 완전히 없앤다. key 이름은 사람이 붙인 라벨
         (`_SHARED_FIELD_LABELS`, `sections[].name`)일 때만 본문에 등장한다.
    """
    seen = {(title or "").strip()}
    if extra_body and extra_body.strip():
        seen.add(extra_body.strip())
    lines: list[str] = []
    for key, value in local_lines:
        text = _render_value(value).strip()
        if not text or text in seen:
            continue
        if _looks_like_identifier(key, value):
            continue
        seen.add(text)
        lines.append(text)
    return lines


def _list_item_text(item: Any) -> str:
    """문자열 배열(규칙 2) 원소 하나를 목록 줄로 렌더링한다. dict/list 가 섞여 들어오면
    (혼합 배열) JSON 문자열로 흡수해 최소한 정보는 남긴다."""
    if _is_scalar(item):
        return str(item)
    try:
        return json.dumps(item, ensure_ascii=False)
    except TypeError:
        return str(item)


def _to_markdown(value: Any, table_format: str, compact_tables: bool) -> str:
    """규칙 1 — HTML(또는 장문) 문자열 → 구조를 보존한 Markdown. json_records.html_to_text 재사용."""
    return html_to_text(value, table_format=table_format, compact_tables=compact_tables) or ""


def _promote_title(markdown_body: str) -> tuple[str | None, str]:
    """규칙 4 — 본문이 스스로 들고 있는 첫 heading 을 제목으로 승격하고 본문에서 제거한다.

    docling markdown 은 h1~h6 를 전부 `#`...`######` 로 내보내므로 레벨 구분 없이 첫 `#` 줄을
    찾는다. 같은 텍스트의 다른 줄(우연한 중복)도 함께 제거되지만, 섹션 하나가 원래 짧은 조각
    이라 실측에서는 부작용이 없었다.
    """
    lines = markdown_body.splitlines()
    for line in lines:
        if line.startswith("#"):
            title = line.lstrip("# ").strip()
            body = "\n".join(l for l in lines if l.lstrip("# ").strip() != title)
            return title, body.strip()
    return None, markdown_body


def _merge_rich_children(
    rich_children: list[tuple[str, Any, Any, Any]],
    table_format: str,
    compact_tables: bool,
    *,
    existing_title: str,
) -> str:
    """배열 원소(레코드형 컨테이너)가 직접 들고 있는 rich 필드를 같은 섹션 본문으로 합친다.

    본문 안의 제목이 이미 정해진 섹션 제목(`existing_title`, 배열 형제 필드에서 뽑은 라벨)과
    같으면 규칙 5(부모와 같은 제목이면 중복 제거)에 따라 버리고, 다르면 정보 손실을 피하기
    위해 본문 첫 줄로 남긴다.
    """
    existing_norm = (existing_title or "").strip()
    parts: list[str] = []
    for _key, value, *_ctx in rich_children:
        title, body = _promote_title(_to_markdown(value, table_format, compact_tables))
        if title and title.strip() != existing_norm:
            body = f"{title}\n{body}" if body else title
        if body.strip():
            parts.append(body.strip())
    return "\n\n".join(parts)


def _child_path(parent_path: str, key: str) -> str:
    return f"{parent_path}.{key}"


def _resolve_child_context(
    key: str,
    sections_cfg: dict[str, dict[str, Any]],
    parent_key: str | None,
    parent_name: str | None,
) -> tuple[str | None, str | None, bool]:
    """이 key 의 섹션 컨텍스트(section_key/section_name/include). 설정에 없으면 부모 것을 그대로
    물려받는다 — `htmlList.feeUrl` 처럼 컨테이너 자신만 설정되고 자식은 안 적힌 경우, 자식들이
    전부 같은 SECTION_NM("상품 문서")을 갖게 하기 위해서다.

    물려받을 부모 이름조차 없으면(= 조상 어디에도 sections 설정이 없는 서브트리) 규칙 13
    (SECTION_NM 은 항상 값이 있어야 한다)에 따라 이 key 이름 그대로를 SECTION_NM 으로 쓴다 —
    "None" 이 metadata 로 새어 나가지 않게 하는 최종 폴백이다. 진짜 루트(payload 최상위)의
    leftover 스칼라 섹션만 이 함수를 거치지 않으므로 `_emit` 이 그 경우를 "개요"로 별도
    기본값 처리한다.
    """
    cfg = sections_cfg.get(key)
    if cfg:
        return key, cfg["name"], cfg["include"]
    if parent_name:
        return parent_key, parent_name, True
    return key, key, True


class _WalkContext:
    """트리 순회 한 번(payload 하나) 동안 공유되는 설정 + 누적 상태."""

    __slots__ = (
        "shared_field_alias_set",
        "sections_cfg",
        "ignore_keys",
        "table_format",
        "compact_tables",
        "label_from_siblings",
        "detect_format",
        "label_keys",
        "sections",
        "node_count",
        "collapsed",
        "truncated",
    )

    def __init__(
        self,
        *,
        shared_fields: dict[str, list[str]],
        sections_cfg: dict[str, dict[str, Any]],
        ignore_keys: list[str],
        table_format: str,
        compact_tables: bool,
        label_from_siblings: Any,
        detect_format: Any,
        label_keys: tuple[str, ...],
    ) -> None:
        # shared_fields 별칭은 이미 identity 로(문서 루트에서 한 번) 확정돼 있으므로 본문에
        # 원문 key 로 다시 나오지 않게 걸러낸다(_walk 에서 매 노드마다 참조) — 단, 값이 스칼라
        # 배열이면 억제하지 않는다(규칙 보강, `_walk` 참고).
        self.shared_field_alias_set = {
            normalize_column_name(alias)
            for aliases in shared_fields.values()
            for alias in aliases
        }
        self.sections_cfg = sections_cfg
        self.ignore_keys = ignore_keys
        self.table_format = table_format
        self.compact_tables = compact_tables
        self.label_from_siblings = label_from_siblings
        self.detect_format = detect_format
        self.label_keys = label_keys
        self.sections: list[dict[str, Any]] = []
        self.node_count = 0
        self.collapsed = 0
        self.truncated = False


def _emit(
    ctx: _WalkContext,
    *,
    title: str,
    section_name: str | None,
    json_path: str,
    identity: dict[str, Any],
    local_lines: list[tuple[str, Any]],
    extra_body: str,
) -> None:
    """섹션 하나를 확정한다. 본문 조립은 규칙 11(원문 스칼라 정리)을 거친 뒤 규칙 9(본문이
    식별자/제목 중복뿐이면 검색에 쓸모없으므로) 로 접는다 — 접은 건수만 세고 silent 축소를
    피한다(build_fields 끝에서 요약 경고로 드러낸다).

    `section_name` 이 비어 있으면 규칙 13(SECTION_NM 은 항상 값이 있어야 한다)에 따라
    "개요"로 채운다 — `_resolve_child_context` 가 이미 key 이름 폴백을 보장하므로, 여기
    도달할 때 비어 있다는 것은 depth 0(진짜 루트)의 leftover 스칼라 섹션뿐이다.
    """
    section_name = section_name or _DEFAULT_SECTION_TITLE
    body_lines = _local_lines_to_text(local_lines, title=title, extra_body=extra_body)

    meaningful = bool(extra_body and extra_body.strip()) or bool(body_lines)
    if not meaningful:
        ctx.collapsed += 1
        return

    body_parts = list(body_lines)
    if extra_body and extra_body.strip():
        body_parts.append(extra_body.strip())

    ctx.sections.append({
        "title": title,
        "section_name": section_name,
        "json_path": json_path,
        "identity": dict(identity),
        "body": "\n".join(body_parts),
    })


def _walk(
    node: Any,
    ctx: _WalkContext,
    *,
    json_path: str,
    section_key: str | None,
    section_name: str | None,
    inherited: dict[str, Any],
    forced_title: str | None,
    depth: int = 0,
) -> None:
    """JSON 트리를 재귀 순회하며 섹션을 `ctx.sections` 에 쌓는다.

    `forced_title` 이 있으면 이 `node` 자체가 배열 원소(레코드형 컨테이너, 규칙 3)라는 뜻이다
    — 자신의 rich 필드는 같은 섹션 본문으로 합친다(규칙 4/5). 없으면 순수 컨테이너(문서 루트나
    `htmlList` 같은 중간 dict)라는 뜻이다 — rich 자식마다 자기 섹션을 낳고(규칙 1/4), 남는
    스칼라 필드는 "개요" 섹션 하나로 묶는다.
    """
    ctx.node_count += 1
    if ctx.node_count > _MAX_NODES or depth > _MAX_DEPTH:
        ctx.truncated = True
        return
    if not isinstance(node, dict):
        return

    # shared_fields(identity) 는 문서 루트에서 딱 한 번 확정돼 불변으로 전달된다(규칙 수정 —
    # 예전에는 노드마다 자신의 값으로 덮어써서 `ksp[].code` 같은 하위 객체의 동명 키가
    # 상품코드를 가로챘다). 이 함수는 상속값을 그대로 쓸 뿐 다시 확정하지 않는다.
    identity = inherited

    # 배열 항목의 라벨로 이미 쓰인 형제 키(name/title/…)는 본문에서 중복 표시하지 않는다.
    label_keys_used: set[str] = set()
    if forced_title:
        for candidate in ctx.label_keys:
            value = node.get(candidate)
            if isinstance(value, str) and value.strip() == forced_title:
                label_keys_used.add(candidate)

    local_lines: list[tuple[str, Any]] = []
    rich_children: list[tuple[str, Any, str | None, str | None]] = []
    dict_children: list[tuple[str, str | None, str | None]] = []
    objarray_children: list[tuple[str, str | None, str | None]] = []
    scalararray_children: list[tuple[str, str | None, str | None]] = []

    for key, value in node.items():
        if key in label_keys_used:
            continue
        if _ignored(key, ctx.ignore_keys):
            continue
        if _is_empty(value):
            continue
        is_scalar_array = isinstance(value, list) and all(_is_scalar(item) for item in value)
        if normalize_column_name(key) in ctx.shared_field_alias_set and not is_scalar_array:
            # identity(상품코드·상품명·상태 등 스칼라)는 이미 청크 접두에 실리므로 본문에서
            # 중복 억제한다. 단 값이 스칼라 배열이면 억제하지 않는다 — 배열은 정체성이
            # 아니라 콘텐츠다(예: `PRODUCT_ATTRS` 의 원천인 `benefit` 목록 섹션이 사라지면
            # 안 된다).
            continue
        child_key, child_name, include = _resolve_child_context(
            key, ctx.sections_cfg, section_key, section_name
        )
        if not include:
            continue

        if isinstance(value, dict):
            dict_children.append((key, child_key, child_name))
        elif isinstance(value, list):
            if all(isinstance(item, dict) for item in value):
                objarray_children.append((key, child_key, child_name))
            else:
                scalararray_children.append((key, child_key, child_name))
        elif _is_rich(value, ctx.detect_format):
            rich_children.append((key, value, child_key, child_name))
        else:
            # 규칙 12 — _is_rich 문턱을 못 넘는 짧은 문자열도 태그가 있으면 평문화한다.
            local_lines.append((key, _flatten_scalar_text(value, ctx.table_format, ctx.compact_tables)))

    if forced_title is not None:
        # 규칙 3 — 배열 원소 하나 = 섹션 하나. 자신의 rich 필드는 같은 섹션 본문으로 합친다.
        merged_body = _merge_rich_children(
            rich_children, ctx.table_format, ctx.compact_tables, existing_title=forced_title
        )
        _emit(
            ctx, title=forced_title, section_name=section_name, json_path=json_path,
            identity=identity, local_lines=local_lines, extra_body=merged_body,
        )
    else:
        # 규칙 1/4 — 순수 컨테이너의 rich 자식은 저마다 자기 섹션이 된다(htmlList.feeUrl 등).
        for key, value, child_key, child_name in rich_children:
            child_path = _child_path(json_path, key)
            promoted_title, body = _promote_title(
                _to_markdown(value, ctx.table_format, ctx.compact_tables)
            )
            title = promoted_title or child_name or key
            _emit(
                ctx, title=title, section_name=child_name, json_path=child_path,
                identity=identity, local_lines=[], extra_body=body,
            )
        # 컨테이너 자신에 남는 스칼라 필드는 "개요" 섹션 하나로 묶는다.
        if local_lines:
            _emit(
                ctx, title=_DEFAULT_SECTION_TITLE, section_name=section_name, json_path=json_path,
                identity=identity, local_lines=local_lines, extra_body="",
            )

    for key, child_key, child_name in dict_children:
        _walk(
            node[key], ctx, json_path=_child_path(json_path, key), section_key=child_key,
            section_name=child_name, inherited=identity, forced_title=None, depth=depth + 1,
        )

    for key, child_key, child_name in objarray_children:
        base_path = _child_path(json_path, key)
        # 경로(index)는 JSONPath 규약대로 0-base — `$.bubble[0]` 이 첫 원소를 가리켜야
        # 원문 역추적이 실제 항목과 어긋나지 않는다. 라벨 폴백(`key#seq`, `_label_from_siblings`)
        # 은 사람이 읽는 번호라 1-base 가 자연스러우므로 index+1 로 따로 준다.
        for index, item in enumerate(node[key]):
            if not isinstance(item, dict):
                continue
            label = ctx.label_from_siblings(item, key, index + 1)
            _walk(
                item, ctx, json_path=f"{base_path}[{index}]", section_key=child_key,
                section_name=child_name, inherited=identity, forced_title=label, depth=depth + 1,
            )

    for key, child_key, child_name in scalararray_children:
        # 규칙 2 — 문자열(스칼라) 배열은 `- 항목` 목록 섹션이 된다.
        body = "\n".join(f"- {_list_item_text(item)}" for item in node[key])
        title = child_name or key
        _emit(
            ctx, title=title, section_name=child_name, json_path=_child_path(json_path, key),
            identity=identity, local_lines=[], extra_body=body,
        )


class SemanticJsonMapper:
    """custom_fields 설정 하나를 JSON 트리 → 섹션(=청크) 변환기로 컴파일한다.

    `json_records.JsonRecordsMapper` 와 같은 시그니처(matches/build_fields/to_parse_format)를
    제공해 parser 호출부가 두 매퍼를 구분하지 않고 그대로 쓸 수 있다(덕 타이핑). 차이는
    "레코드 1건 = element 1개"가 아니라 "섹션 1개 = element 1개"라는 점뿐이다.
    """

    def __init__(
        self,
        *,
        config_file: str = "",
        resource_path: str | None = None,
        doc_type: str | list[str] | None = None,
        extractor: str = "json_semantic",
        **_: Any,
    ) -> None:
        if str(extractor or "").strip().lower() not in JSON_SEMANTIC_EXTRACTORS:
            raise ValueError(f"지원하지 않는 json_semantic custom_fields extractor: {extractor}")
        self.doc_types = normalize_doc_types(doc_type)
        # 프롬프트/LLM config 파일 경로 해석 기준(= 이 config 파일과 같은 디렉토리).
        self.resource_path = resource_path
        cfg = self._load_config(config_file, resource_path)

        shared_fields_cfg = cfg.get("shared_fields")
        if not isinstance(shared_fields_cfg, dict) or not shared_fields_cfg:
            raise ValueError("json_semantic custom_fields 에는 shared_fields 가 필요합니다.")
        self.shared_fields: dict[str, list[str]] = {
            str(target): self._aliases(str(target), sources)
            for target, sources in shared_fields_cfg.items()
        }

        # 공통 필드를 청크 접두에 실을 때 붙일 항목명. 기본값 위에 설정을 덮어쓴다 —
        # 이름이 있는 필드만 접두에 실리므로(규칙 10), 여기에 필드를 더하면 그 필드가
        # metadata 에만 있던 상태에서 본문(임베딩 대상)으로 올라온다.
        self.field_labels = {
            **_SHARED_FIELD_LABELS,
            **cp.parse_field_labels(cfg.get(cp.FIELD_LABELS_KEY)),
        }

        # 필수 공통 필드 — 문서 루트에서 못 찾으면(identity 확정 후에도 비어 있으면)
        # missing_policy 에 따라 처리한다(build_fields 참고).
        self.required_shared_fields = [
            str(f).strip() for f in (cfg.get("required_shared_fields") or []) if str(f).strip()
        ]
        policy = str(cfg.get("missing_policy") or "error").strip().lower()
        if policy not in VALID_MISSING_POLICIES:
            _log.warning(f"[json_semantic] Invalid missing_policy '{policy}', fallback to 'error'")
            policy = "error"
        self.missing_policy = policy

        sections_cfg = cfg.get("sections")
        if not isinstance(sections_cfg, dict) or not sections_cfg:
            raise ValueError("json_semantic custom_fields 에는 sections 가 필요합니다.")
        self.sections_cfg: dict[str, dict[str, Any]] = {}
        for key, spec in sections_cfg.items():
            spec = spec or {}
            if not isinstance(spec, dict):
                raise ValueError(f"json_semantic custom_fields sections.{key} 는 object 여야 합니다.")
            self.sections_cfg[str(key)] = {
                "name": str(spec.get("name") or key),
                "include": bool(spec.get("include", True)),
            }

        self.ignore_keys = [str(p).strip() for p in (cfg.get("ignore_keys") or []) if str(p).strip()]
        self.defaults = dict(cfg.get("defaults") or {})
        self.constants = dict(cfg.get("constants") or {})
        self.llm_field_specs = build_llm_field_specs(cfg)
        # 섹션(청크)마다 LLM 을 부르면 카드 1장에 10회 넘게 호출된다 — parser 의
        # _apply_llm_fields 가 이 값을 보고 문서 1회만 호출한 뒤 전 섹션에 결과를 복사한다.
        self.llm_fields_scope = "document"

        # 설정 오기입을 여기서 막는다(json_mapping/tabular 와 동일 기준).
        validate_custom_field_config(
            cfg, label=f"json_semantic custom_fields({config_file})", extractor=extractor
        )

    # ── 설정 로딩 ────────────────────────────────────────────────────────────
    @staticmethod
    def _load_config(config_file: str, resource_path: str | None) -> dict:
        if not config_file:
            raise ValueError("json_semantic custom_fields 에는 config_file 이 필요합니다.")
        path = Path(config_file)
        if not path.is_absolute() and resource_path:
            path = Path(resource_path) / path
        path = path.resolve()
        if not path.is_file():
            raise FileNotFoundError(f"json_semantic custom_fields config 없음: {path}")
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"json_semantic custom_fields config 는 object 여야 합니다: {path}")
        # `schema: v2` 면 내부(v1) 형태로 번역해 넘긴다 — 아래 코드는 v1/v2 를 구분하지 않는다.
        normalized, _ = cv2.load(loaded, label=f"json_semantic custom_fields({config_file})")
        return normalized

    @staticmethod
    def _aliases(target: str, source_spec: Any) -> list[str]:
        values = source_spec if isinstance(source_spec, list) else [source_spec]
        aliases = [target]
        for value in values:
            value = str(value or "").strip()
            if value and value not in aliases:
                aliases.append(value)
        return aliases

    # ── 매칭 ─────────────────────────────────────────────────────────────────
    def matches(self, runtime_doc_type: Any) -> bool:
        return matches_doc_type(self.doc_types, runtime_doc_type)

    def canonical_doc_type(self, runtime_doc_type: Any) -> str:
        runtime = normalize_doc_type(runtime_doc_type)
        if runtime and runtime in self.doc_types:
            return runtime
        return self.doc_types[0] if self.doc_types else runtime

    # ── 변환 ─────────────────────────────────────────────────────────────────
    def build_fields(
        self,
        payload: Any,
        runtime_doc_type: Any,
        *,
        table_format: str = DEFAULT_TABLE_FORMAT,
        compact_tables: bool = True,
    ) -> list[dict]:
        """payload → 섹션별 목표필드 목록(섹션 하나가 나중에 element 하나가 된다)."""
        if not isinstance(payload, dict):
            _log.warning("[json_semantic] payload 최상위가 object 가 아니라 섹션을 만들 수 없습니다 — 0건.")
            return []

        from genon.preprocessor.converters.json_text import (
            _LABEL_KEYS,
            _label_from_siblings,
            detect_format,
        )

        doc_type = self.canonical_doc_type(runtime_doc_type)
        table_format = normalize_table_format(table_format)

        # shared_fields(identity)는 문서 루트에서 딱 한 번만 확정한다.
        # `_walk` 는 이 값을 그대로 상속만 하고 노드마다 다시 확정하지 않는다(예전에는
        # `ksp[].code` 같은 하위 객체의 동명 키가 상품코드를 덮어썼다). 루트에 값이 없을
        # 때도 `mpo[].code` 같은 관련 상품 값으로 보충하지 않는다. 선언된 필드는 못 찾아도
        # None 으로 채워 metadata(적재 컬럼)에 항상 나타나게 한다.
        identity: dict[str, Any] = {target: None for target in self.shared_fields}
        for target, aliases in self.shared_fields.items():
            value = _find_root_field(payload, aliases)
            if value not in (None, ""):
                identity[target] = _flatten_scalar_text(value, table_format, bool(compact_tables))

        # json_mapping/tabular와 같은 공통 필드 우선순위:
        #   루트 원천값 -> 빈 값만 defaults로 보충 -> constants로 무조건 덮어쓰기.
        # required 검사는 이 적용 뒤에 수행하므로 기본값/상수로 충족된 필드도 정상 통과한다.
        for key, value in self.defaults.items():
            if identity.get(key) in (None, ""):
                identity[key] = value
        identity.update(self.constants)

        missing = [f for f in self.required_shared_fields if identity.get(f) in (None, "")]
        if missing:
            msg = f"[json_semantic] 필수 공통 필드 누락: {sorted(missing)}"
            if self.missing_policy == "error":
                raise ValueError(msg)
            _log.warning(f"{msg} — 섹션 0건으로 진행합니다.")
            return []

        ctx = _WalkContext(
            shared_fields=self.shared_fields,
            sections_cfg=self.sections_cfg,
            ignore_keys=self.ignore_keys,
            table_format=table_format,
            compact_tables=bool(compact_tables),
            label_from_siblings=_label_from_siblings,
            detect_format=detect_format,
            label_keys=_LABEL_KEYS,
        )

        _walk(
            payload, ctx, json_path="$", section_key=None, section_name=None,
            inherited=identity, forced_title=None,
        )

        if ctx.collapsed:
            # silent 축소 방지 — 몇 건이 식별자/빈 본문뿐이라 접혔는지 드러낸다(json_records 와 같은 방침).
            _log.warning(f"[json_semantic] 식별자/빈 본문뿐인 섹션 {ctx.collapsed}건을 접었습니다.")
        if ctx.truncated:
            _log.warning(
                f"[json_semantic] 노드 수({_MAX_NODES})/깊이({_MAX_DEPTH}) 상한을 초과해 "
                f"트리 일부를 절단했습니다 — sections 설정으로 불필요한 서브트리를 제외하세요."
            )

        fields_list: list[dict] = []
        for section in ctx.sections:
            fields: dict[str, Any] = {
                "SECTION_NM": section["section_name"],
                "SOURCE_JSON_PATH": section["json_path"],
                **section["identity"],
                **self.constants,
                "_title": section["title"],
                "_body": section["body"],
            }
            if doc_type:
                fields["doc_type"] = doc_type
            fields_list.append(fields)
        return fields_list

    def _chunk_prefix(self, fields: dict) -> str:
        """청크 1행(`[섹션명] 제목`) + 상속된 공통 정보 행들. 과대 섹션 분할 시 조각마다
        이 접두가 다시 붙는다(chunking_processor._expand_splittable_rows).

        헤더 줄은 규칙 5(부모와 같은 제목이면 중복 제거)의 연장이다 — 섹션명과 제목이 같으면
        (`개요`처럼 루트 자체가 곧 섹션이거나, `benefit` → "주요 혜택"처럼 배열 자체가 곧
        섹션이라 형제 라벨이 없는 경우) `[주요 혜택] 주요 혜택`으로 겹쳐 나오지 않게 대괄호 없이
        한 번만 낸다. 다르면(`[혜택 상세] 0.5%~3% 빅포인트 적립`처럼) 대괄호로 구분해 "이 청크가
        속한 성격"과 "이 청크만의 제목"을 함께 보여준다.
        """
        section_name = fields.get("SECTION_NM")
        title = str(fields.get("_title") or "").strip()
        if section_name and title and section_name != title:
            header = f"[{section_name}] {title}"
        else:
            header = title or section_name or ""
        lines = [header] if header else []
        for target in self.shared_fields:
            # 규칙 10 — 사람이 붙인 항목명(field_labels)이 있는 필드만 본문에 싣는다.
            # BIZ_ID 처럼 이름이 없는 내부 식별자는 metadata(적재 컬럼)에만 남는다 — 사람이
            # 검색어로 쓰지 않는 값을 임베딩에 태우지 않기 위해서다.
            label = self.field_labels.get(target)
            if not label:
                continue
            value = fields.get(target)
            if value in (None, ""):
                continue
            lines.append(f"{label}: {_render_value(value)}")
        return "\n".join(lines)

    def build_text(self, fields: dict) -> str:
        """청크 본문 — 접두(제목+공통 정보) + 본문(규칙에 따라 정규화된 텍스트)."""
        prefix = self._chunk_prefix(fields)
        body = str(fields.get("_body") or "").strip()
        return f"{prefix}\n{body}" if body else prefix

    def to_parse_format(self, fields_list: list[dict], runtime_doc_type: Any) -> dict:
        """섹션별 목표필드 목록 → parse-format(청커 행 기반 경로가 소비하는 형태).

        `chunk_prefix` 를 함께 실어 둔다 — 과대 섹션이 chunk_size 로 분할될 때 청커가 조각마다
        이 접두를 다시 붙인다(json_records 의 splittable 레코드와 달리 접두 유지가 필요하다).
        """
        doc_type = self.canonical_doc_type(runtime_doc_type)
        elements: list[dict] = []
        empty = 0
        for fields in fields_list:
            content = self.build_text(fields)
            if not content.strip():
                empty += 1
                continue
            metadata = {k: v for k, v in fields.items() if not str(k).startswith("_")}
            elements.append({
                "category": "custom_fields_row",
                "content": content,
                "coordinates": [],
                "id": len(elements),
                "page": len(elements) + 1,
                "metadata": metadata,
                "splittable": True,
                "chunk_prefix": self._chunk_prefix(fields),
            })

        if empty:
            _log.warning(
                f"[json_semantic] 본문이 빈 섹션 {empty}/{len(fields_list)}건을 제외했습니다."
            )

        result: dict[str, Any] = {"elements": elements, "usage": {"pages": len(elements)}}
        if doc_type:
            result["metadata"] = {"doc_type": doc_type}
        return result

    def document_input_fields(
        self, fields_list: list[dict], input_field_names: list[str] | None = None
    ) -> dict:
        """문서(파일) 단위 LLM 입력용 대표 필드 dict.

        공통 정보(상품명/상품코드 등)는 모든 섹션이 같으므로 첫 섹션 것을 그대로 쓰고, 섹션
        본문은 전부 이어붙여 `PRODUCT_INFO` 로 제공한다.

        `input_fields` 가 그중 어느 것도 아니면 **JSON key 이름**으로 해석해, 그 key 를 경로에
        가진 섹션들의 청크 본문만 모아 같은 이름으로 실어 준다(`htmlList` = 그 컨테이너 아래
        전부, `feeUrl` = 그 한 덩어리만). 카드 1장 전체(`PRODUCT_INFO`)를 넣으면 토큰이 크고
        노이즈가 많아 추출 정확도가 떨어지므로, 필요한 부분만 좁혀 넣기 위한 것이다. 경로
        문법은 없다 — 이 모듈의 다른 설정과 마찬가지로 key 이름만 적는다.

        LLM 에 넣는 것은 원문 HTML 이 아니라 **청크에 실제로 실리는 평문**이다. 추출값이
        이상할 때 청크만 보고 원인을 짚을 수 있어야 하고(LLM 은 봤는데 청크엔 없는 값이 생기면
        환각과 구분되지 않는다), 태그를 걷어낸 쪽이 같은 정보를 훨씬 적은 토큰으로 담는다.
        """
        if not fields_list:
            return {}
        merged = {k: v for k, v in fields_list[0].items() if not str(k).startswith("_")}
        merged["PRODUCT_INFO"] = "\n\n".join(self.build_text(fields) for fields in fields_list)

        for name in input_field_names or ():
            name = str(name).strip()
            if not name or name in merged:
                continue
            text = self._section_input_text(fields_list, name)
            if text:
                merged[name] = text
                continue
            # silent 축소 방지 — 예전에는 못 찾은 이름이 그냥 빠져서 raw_text 가 빈 채로
            # LLM 에 나갔고, 모델이 근거 없이 null 을 돌려주는 것으로만 드러났다.
            _log.warning(
                f"[json_semantic] llm_fields.input_fields 의 '{name}' 을(를) 찾지 못했습니다 "
                f"— 공통 필드도 PRODUCT_INFO 도 아니고, 이 이름을 경로에 가진 섹션도 없습니다. "
                f"사용 가능한 이름: {sorted(set(merged) | self._section_input_names(fields_list))}"
            )
        return merged

    @staticmethod
    def _section_input_names(fields_list: list[dict]) -> set[str]:
        """`input_fields` 에 적을 수 있는 JSON key 이름 전체(경고 메시지용)."""
        names: set[str] = set()
        for fields in fields_list:
            names |= _path_segments(fields.get("SOURCE_JSON_PATH"))
        return names

    def _section_input_text(self, fields_list: list[dict], name: str) -> str:
        """`name` 을 경로에 가진 섹션들의 청크 본문(평문)을 선언 순서대로 이어붙인다."""
        parts = [
            self.build_text(fields)
            for fields in fields_list
            if name in _path_segments(fields.get("SOURCE_JSON_PATH"))
        ]
        return "\n\n".join(part for part in parts if part.strip())


def build_semantic_json_mappers(configs: list[dict]) -> list[SemanticJsonMapper]:
    """custom_fields 설정 중 extractor=json_semantic 만 매퍼로 컴파일한다(json_records 와 같은 패턴)."""
    return [
        SemanticJsonMapper(**dict(config))
        for config in (configs or [])
        if custom_fields_extractor(config) in JSON_SEMANTIC_EXTRACTORS
    ]
