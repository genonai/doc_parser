"""custom_fields yaml 의 **지원 키를 extractor 별로 선언**하고 기동 시 대조한다.

왜 필요한가 — 지금까지 설정 오기입은 두 가지 방식으로 조용히 사라졌다.

1. **모르는 키는 아무도 안 본다.** 검증기는 알려진 키를 이름으로 꺼내 타입·참조 정합만
   봤을 뿐, `set(cfg) - 지원키` 를 훑는 곳이 없었다. `column_maps`(오타 s) 한 글자로
   매핑 전체가 사라져도 에러도 경고도 없다.
2. **extractor 가 안 읽는 키도 검증은 통과한다.** `json_semantic` 설정에
   `chunk_prefix_fields` 를 올바른 필드명으로 적으면 검증을 통과하고 그대로 무시된다.
   반대로 이름을 틀리면 그 **읽지도 않는 키 때문에 기동이 실패**하기도 했다.

그래서 "이 extractor 가 읽는 키" 를 한 곳에 적고, 그 목록으로 두 가지를 판정한다.
  · 어느 extractor 도 모르는 키 → **기동 실패**(오타로 본다. 가장 가까운 이름을 제안한다)
  · 다른 extractor 의 키를 잘못 쓴 경우 → **기동 실패**(읽히지 않으므로 설정이 무효다)

여기 선언한 목록이 곧 "이 extractor 로 무엇을 설정할 수 있는가" 의 단일 출처다.
새 키를 코드에 추가하면 반드시 여기에도 넣어야 하며, 넣지 않으면 그 키를 쓴 설정이
기동에 실패해 누락이 바로 드러난다.
"""

from __future__ import annotations

import difflib
import logging
import os

_log = logging.getLogger(__name__)

# 검증 강도. 기본은 error(기동 실패) — 무증상 실패를 없애는 것이 이 검증의 목적이다.
#
# 운영 반영 첫 릴리스처럼 "현장 설정에 예상 못 한 키가 있는지 아직 모르는" 상황에서는
# warn 으로 낮춰 기동은 시키고 로그로만 알릴 수 있다. 배포 단위 스위치라 yaml 이 아니라
# 환경변수에 둔다 — 통합 실행(main.py)은 한 프로세스에 facade 를 여럿 올리므로
# 프로세서 yaml 에 두면 나중에 로드된 값이 앞의 것을 덮어 예측이 어려워진다.
#
# 배포 전에는 tools/config_precheck 로 현장 설정을 먼저 검사하는 것이 정석이고,
# warn 은 그 검사를 못 돌린 경우의 안전판이다.
VALIDATION_POLICY_ENV = "GENOS_CUSTOM_FIELDS_VALIDATION"
_VALID_POLICIES = ("error", "warn")


def validation_policy() -> str:
    """`error`(기본) 또는 `warn`. 모르는 값은 error 로 보고 경고를 남긴다."""
    raw = str(os.environ.get(VALIDATION_POLICY_ENV) or "error").strip().lower()
    if raw not in _VALID_POLICIES:
        _log.warning(
            f"[custom_fields] {VALIDATION_POLICY_ENV}='{raw}' 는 알 수 없는 값입니다 "
            f"(사용 가능: {list(_VALID_POLICIES)}). error 로 진행합니다."
        )
        return "error"
    return raw

# 등록 블록(parse_config.yaml 의 `- custom_fields:`)에서 넘어오는 배선 키.
# custom_field yaml 안에 적을 값이 아니라 매퍼/enricher 생성자의 인자다.
WIRING_KEYS = frozenset({
    "enable", "doc_type", "extractor", "config_file", "resource_path",
    # 아래는 enricher 가 아니라 parser 의 포맷 전처리가 소비한다.
    "json", "markdown", "html", "delimited",
    # 표 설명은 extractor 가 읽는 키가 아니라 프로세서 공통 기능의 문서유형별 오버라이드다.
    # 표는 extractor 종류와 무관하게 생기므로(레코드 본문 안의 <table> 포함) 전 extractor 가
    # 받는다. 예전에는 llm 에만 허용해, 레코드 매핑 문서유형은 표 설명을 끌 수단이 없었다.
    "table_text_description",
})

# 레코드형 3종이 공유하는 키(값 조립 → 본문 조립 파이프라인).
_RECORD_COMMON = frozenset({
    "required", "defaults", "constants",
    "value_map", "transforms", "derive", "pack", "filter",
    "llm_fields", "meta_include",
    "text_fields", "split", "chunk_prefix_fields", "field_labels",
})

# extractor 별 지원 키. 값은 "그 extractor 의 매퍼/enricher 가 실제로 읽는 키" 다.
EXTRACTOR_KEYS: dict[str, frozenset[str]] = {
    # `sequence`(v2 `fields.<이름>.seq`)는 레코드 루프가 있는 두 kind 전용이다. 섹션형·문서형은
    # "항목 N번째"가 정의되지 않아(문서형은 항상 1) 여기 넣지 않는다.
    "tabular_mapping": _RECORD_COMMON | {"column_map", "row_merge", "sequence", "sheets"},
    # `raw_fields` 는 JSON 원천 2종에만 있다. 엑셀 칸(rows)과 LLM 응답(document)에는
    # "객체를 구조로 볼지 값으로 볼지" 라는 판정 자체가 없다.
    "json_mapping": _RECORD_COMMON | {
        "records", "key_map", "collect_key_map", "missing_policy", "row_merge", "sequence",
        "raw_fields", "file_fields",
    },
    # json_semantic 은 섹션 본문을 섹션 워커가 만들지만, 공통 필드(적재 DB 컬럼이 되는 값)의
    # 파이프라인은 레코드형과 같다 — `value_map`/`transforms`/`derive` 를 같은 순서로 읽는다.
    # 본문 조립 키 중에서는 `text_fields`(v2 `body.fields`)만 읽는다 — 공통 필드를 청크
    # 접두에 실을지 정하는 스위치다(섹션 본문 구성과는 무관하다).
    "json_semantic": frozenset({
        "shared_fields", "sections", "ignore_keys", "raw_fields",
        "required_shared_fields", "missing_policy",
        "defaults", "constants", "llm_fields",
        "value_map", "transforms", "derive", "pack", "meta_include",
        "text_fields", "field_labels", "first_chunk_fields",
    }),
    # 문서 단위 LLM 추출. 프롬프트·연결·출력필드가 중심이지만 원천이 하나는 아니다 —
    # markdown front matter 가 두 번째 원천이라 `front_matter_map`(v2 의 `fields.<이름>.alias`)
    # 으로 어느 front matter 키에서 가져올지 정하고, 그 값에 다른 kind 와 같은 값
    # 파이프라인(`value_map`/`transforms`/`derive`)을 같은 순서로 건다.
    "llm": frozenset({
        "url", "api_key", "model", "max_tokens", "temperature", "timeout",
        "top_p", "seed", "repetition_penalty", "params", "headers",
        "system_prompt", "user_prompt", "system_prompt_file", "user_prompt_file", "prompt",
        "output_fields", "constants", "defaults", "parser", "pages", "variables", "template",
        "thinking", "thinking_dialect",
        "front_matter_map", "file_fields", "value_map", "transforms", "derive", "pack",
        "meta_include", "body_fields", "chunk_prefix_fields", "first_chunk_fields", "field_labels",
        "short_chunk_size",
    }),
    # 문서 단위인데 값을 만드는 것이 원문 HTML 의 선택자인 경우. 연결·프롬프트·파서 키가
    # 통째로 없고 원천 지목은 `select_map` 하나다(v2 의 `fields.<이름>.select`/`attr`).
    # 그 뒤의 값 파이프라인은 llm·python 과 같다 — 값을 만드는 방법만 다르다.
    "html_select": frozenset({
        "select_map", "file_fields", "constants", "defaults",
        "value_map", "transforms", "derive", "pack", "meta_include",
        "body_fields", "chunk_prefix_fields", "first_chunk_fields", "field_labels",
        "short_chunk_size",
    }),
    # 문서 단위인데 값을 만드는 것이 LLM 이 아니라 고객 파이썬 함수인 경우. 연결·프롬프트
    # 키가 없는 대신 file/callable 을 받고, 그 뒤의 값 파이프라인은 llm 과 같다.
    "python": frozenset({
        "file", "callable",
        "output_fields", "constants", "defaults",
        "value_map", "transforms", "derive", "pack", "meta_include",
        "body_fields", "chunk_prefix_fields", "first_chunk_fields", "field_labels",
        "short_chunk_size",
    }),
}

ALL_KNOWN_KEYS = frozenset().union(*EXTRACTOR_KEYS.values()) | WIRING_KEYS


def canonical_extractor(extractor: str | None) -> str:
    """extractor 이름을 정규화한다(미지정은 하위호환으로 llm).

    이름은 종류마다 하나씩이다 — 별칭을 두면 "무엇을 써야 하나"만 늘고, 지원 키 표가
    어느 이름에 붙는지도 흐려진다.
    """
    return str(extractor or "llm").strip().lower()


def _suggest(key: str, candidates: frozenset[str]) -> str:
    """오타로 보이는 키에 가장 가까운 지원 키를 한 개 제안한다."""
    close = difflib.get_close_matches(key, sorted(candidates), n=1, cutoff=0.75)
    return f" (혹시 `{close[0]}`?)" if close else ""


def validate_known_keys(cfg: dict, *, label: str, extractor: str | None) -> None:
    """설정 최상위 키가 이 extractor 가 읽는 키인지 대조한다.

    두 종류를 나눠 알린다 — 오타인지, 다른 extractor 의 키를 가져다 쓴 것인지에 따라
    고칠 방법이 다르기 때문이다.
    """
    diagnosis = diagnose_keys(cfg, extractor)
    if diagnosis is None:
        return
    message = format_diagnosis(label, diagnosis)
    if validation_policy() == "warn":
        _log.warning(
            f"[custom_fields] {message} "
            f"(이 설정은 무시되고 그대로 진행합니다. {VALIDATION_POLICY_ENV}=error 면 기동을 막습니다.)"
        )
        return
    raise ValueError(message)


def diagnose_keys(cfg: dict, extractor: str | None) -> dict | None:
    """쓸 수 없는 키를 찾아 구조화해 돌려준다. 문제가 없으면 None.

    검증기와 배포 전 점검 스크립트가 같은 판정을 공유하도록 진단과 처리를 분리했다 —
    스크립트가 규칙을 다시 구현하면 둘이 갈린다.
    """
    name = canonical_extractor(extractor)
    supported = EXTRACTOR_KEYS.get(name)
    if supported is None:  # 모르는 extractor 는 각 매퍼 생성자가 이미 거부한다.
        return None

    allowed = supported | WIRING_KEYS
    unexpected = sorted(k for k in (cfg or {}) if str(k) not in allowed)
    if not unexpected:
        return None

    return {
        "extractor": name,
        # 오타(어느 extractor 도 모르는 키) → 가장 가까운 지원 키를 제안
        "typos": {k: _suggest(k, allowed).strip() for k in unexpected if k not in ALL_KNOWN_KEYS},
        # 다른 extractor 의 키 → 어느 extractor 전용인지
        "misplaced": {
            k: sorted(x for x, keys in EXTRACTOR_KEYS.items() if k in keys)
            for k in unexpected if k in ALL_KNOWN_KEYS
        },
        "supported": sorted(supported),
    }


# ── 텍스트 표 설명(table_text_description) 블록 ──────────────────────────────
# 프로세서 공통 블록과 문서유형 오버라이드(등록 블록·custom_field yaml 최상위)가 같은 키를 쓴다.
# 매뉴얼에 싣지 않는 키(옛 철자, 내부 표기, 미세 조정 값)도 코드가 읽으므로 모두 여기 둔다.
TABLE_TEXT_DESCRIPTION_KEYS = frozenset({
    "enable", "enabled", "model_preset", "resource_path",
    "url", "api_key", "model", "max_tokens", "temperature", "timeout",
    "top_p", "seed", "repetition_penalty", "params", "headers",
    "thinking", "thinking_dialect", "system_prompt", "concurrency",
    "prompt_template_file", "prompt_file", "prompt_template", "prompt",
    "model_context_tokens", "max_context_tokens", "max_context", "completion_reserved_tokens",
    "input_format", "before_items", "after_items", "max_context_chars",
    "overflow_policy", "conflict_policy", "rag", "skip",
})
# `search_terms` 는 아래 두 키(생성 상한, 청크 노출)를 한 번에 정하는 편의 표기다.
TABLE_TEXT_RAG_KEYS = frozenset({
    "retrieval_context_max_chars", "key_fact_limit", "key_fact_max_chars",
    "search_terms", "search_terms_limit", "include_search_terms", "repeat_context_on_split",
})
# LLM 전송에서 뺄 표의 종류. `skip: false` 는 여기 있는 종류를 모두 끈다. 앞의 넷은 true/false 이고,
# `long_text_table` 만 값 칸 평균 문자 수의 하한(0 이상의 정수, 0 또는 false 면 끔)이다.
TABLE_TEXT_SKIP_FLAG_KEYS = ("single_value_table", "nested_table", "duplicate_table", "self_describing_table")
TABLE_TEXT_LONG_TEXT_KEY = "long_text_table"
TABLE_TEXT_SKIP_KEYS = (*TABLE_TEXT_SKIP_FLAG_KEYS, TABLE_TEXT_LONG_TEXT_KEY)
# 옛 키 → 새 키. 옛 키는 계층 병합 전에 새 키로 펼친다(`table_text_context._canonical_rag_skip`).
# 옛 `layout` 은 값 한 칸 표와 중첩 표를 함께 켜고 껐으므로 새 키 둘로 펼친다.
TABLE_TEXT_SKIP_OLD_KEYS = {
    "layout": ("single_value_table", "nested_table"),
    "contained_duplicate": ("duplicate_table",),
    "self_describing": ("self_describing_table",),
    "long_cells": (TABLE_TEXT_LONG_TEXT_KEY,),
}


def _is_nonneg_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _diagnose_skip_value(key: str, value) -> list[str]:
    """`skip` 하위 키 하나의 값 형식을 검사한다. 위반한 값은 무시되어 기본값이 쓰인다."""
    if key in (TABLE_TEXT_LONG_TEXT_KEY, "long_cells"):
        if value is False or _is_nonneg_int(value):
            return []
        return [f"`skip.{key}` 는 0 이상의 정수여야 합니다(받은 값: {value!r}). 이 값은 무시됩니다"]
    if not isinstance(value, bool):
        return [f"`skip.{key}` 는 true 또는 false 여야 합니다(받은 값: {value!r}). 이 값은 무시됩니다"]
    return []


def diagnose_table_text_description(block) -> list[str]:
    """텍스트 표 설명 블록 하나의 오기입을 찾아 사람이 읽는 문장 목록으로 돌려준다.

    계층(공통·등록 블록·custom_field yaml)을 병합하기 전에 계층마다 부른다 — 병합 뒤에는
    어느 계층에 적힌 값인지 알 수 없다. 처리 정책은 호출하는 쪽이 정한다.
    """
    if not isinstance(block, dict):
        return []
    problems = [
        f"모르는 키 `{key}` 는 무시됩니다{_suggest(str(key), TABLE_TEXT_DESCRIPTION_KEYS)}"
        for key in block if str(key) not in TABLE_TEXT_DESCRIPTION_KEYS
    ]
    rag = block.get("rag")
    if isinstance(rag, dict):
        problems += [
            f"모르는 키 `rag.{key}` 는 무시됩니다{_suggest(str(key), TABLE_TEXT_RAG_KEYS)}"
            for key in rag if str(key) not in TABLE_TEXT_RAG_KEYS
        ]
        if "search_terms" in rag:
            value = rag["search_terms"]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                problems.append(f"`rag.search_terms` 는 0 이상의 정수여야 합니다(받은 값: {value!r}). 이 값은 무시됩니다")
            old = sorted(k for k in ("search_terms_limit", "include_search_terms") if k in rag)
            if old:
                problems.append(f"`rag.search_terms` 와 옛 키 {old} 를 함께 적었습니다. `rag.search_terms` 가 우선합니다")
    elif rag is not None:
        problems.append("`rag` 는 '키: 값' 형태여야 합니다. 이 값은 무시됩니다")
    skip = block.get("skip")
    if isinstance(skip, dict):
        for key, value in skip.items():
            key = str(key)
            if key in TABLE_TEXT_SKIP_OLD_KEYS:
                news = TABLE_TEXT_SKIP_OLD_KEYS[key]
                names = ", ".join(f"`skip.{new}`" for new in news)
                problems.append(f"`skip.{key}` 는 옛 이름입니다. {names} 를 쓰십시오")
                if any(new in skip for new in news):
                    problems.append(f"`skip.{key}` 와 새 키 {names} 를 함께 적었습니다. 새 키가 우선합니다")
            elif key not in TABLE_TEXT_SKIP_KEYS:
                problems.append(
                    f"모르는 키 `skip.{key}` 는 무시됩니다{_suggest(key, frozenset(TABLE_TEXT_SKIP_KEYS))}"
                )
                continue
            problems += _diagnose_skip_value(key, value)
    elif skip is not None and skip is not False:
        problems.append(f"`skip` 은 false 또는 조건별 '키: 값' 형태여야 합니다(받은 값: {skip!r}). 이 값은 무시됩니다")
    return problems


def warn_table_text_description(block, *, label: str) -> None:
    """`diagnose_table_text_description` 결과를 경고로 남긴다. 기동은 막지 않는다.

    쓰지 않는 블록의 오타로 서비스가 뜨지 않는 일을 피하려고 첫 릴리스는 경고만 남긴다.
    """
    for problem in diagnose_table_text_description(block):
        _log.warning(f"[table_text_description] {label}: {problem}.")


def format_diagnosis(label: str, diagnosis: dict) -> str:
    """`diagnose_keys` 결과를 사람이 읽는 한 줄로."""
    lines = []
    if diagnosis["typos"]:
        detail = ", ".join(f"`{k}`{(' ' + s) if s else ''}"
                           for k, s in diagnosis["typos"].items())
        lines.append(f"모르는 키: {detail}")
    if diagnosis["misplaced"]:
        detail = ", ".join(f"`{k}`(→ {'/'.join(owners)} 전용)"
                           for k, owners in diagnosis["misplaced"].items())
        lines.append(f"이 extractor 가 읽지 않는 키: {detail}")
    return (
        f"{label}: extractor '{diagnosis['extractor']}' 설정에 쓸 수 없는 키가 있습니다. "
        + " / ".join(lines)
        + f". 지원 키: {diagnosis['supported']}"
    )
