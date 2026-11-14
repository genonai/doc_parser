"""custom_fields 설정에서 파싱 라우팅이 쓰는 spec/mapper 를 만든다.

facade 는 "어떤 확장자를 어느 경로로 보내는가" 를 갖고, 이 모듈은 그 경로가 참조하는
설정 객체를 만드는 일만 한다. 설정 오류를 어떤 예외로 감쌀지는 여기서 정하지 않는다 —
GenosServiceException 이 facade 마다 복제돼 있어(활성 7곳, legacy 포함 22곳) 클래스를
고를 수 없다. 호출부가 guard_config 에 자기 예외 팩토리를 넘긴다.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Iterable

from genon.preprocessor.processing.common.config_parse import as_dict
from genon.preprocessor.processing.enrichment.custom_fields_enricher import (
    normalize_doc_type,
    normalize_doc_types,
)
from genon.preprocessor.processing.enrichment.tabular_custom_fields import normalize_column_name

_log = logging.getLogger(__name__)

# 설정 빌더가 낼 수 있는 "설정이 잘못됐다" 류 예외.
#   ValueError        스키마 위반
#   TypeError         `constants: 5` 처럼 dict() 강제 변환이 실패하는 경우
#   FileNotFoundError 프롬프트 파일 경로 오타
CONFIG_ERRORS = (ValueError, TypeError, FileNotFoundError)


def guard_config(label: str, exc_factory: Callable[[str], Exception], fn, *args, **kwargs):
    """설정 빌더를 감싸 **어느 설정이 문제인지** 드러낸다.

    감싸지 않으면 raw 예외가 __init__ 을 뚫고 나가 서비스 import 자체가 죽고, 로그에는
    어느 yaml 이 문제인지 남지 않는다. 기동 실패인 것은 어느 쪽이든 같다.
    """
    try:
        return fn(*args, **kwargs)
    except CONFIG_ERRORS as exc:
        raise exc_factory(f"custom_fields {label} 설정 오류: {exc}") from exc


def build_json_text_specs(custom_fields_cfgs: Iterable[dict] | None) -> list:
    """`json:` 블록을 가진 custom_fields 설정만 JsonTextSpec 으로 만든다.

    .json 입력에서 본문 텍스트를 꺼낼 key 목록이다. 잘못된 설정은 ValueError 로 나가고
    호출부의 guard_config 가 어느 설정인지 붙여 준다.

    해석은 markdown·html 과 **같은 공용 해석기**에 맡긴다 — 이 블록은 그 둘과 같은 일을
    한다(원천을 docling 이 읽을 문서 한 벌로 만든다). 전용 판정을 두면 설정 파일의
    `source.pre.json` 이 안 보이고, 문서형 extractor 게이트도 이 블록만 빠진다.
    공용 해석기가 주는 것: `config_file` 의 `source.pre.json` 을 읽는 것과, 문서 단위
    extractor 가 아니면 제외하는 것. markdown·html 이 함께 갖는 "등록 블록으로 덮어쓰기"와
    `false` 명시적 비활성은 json 에는 **없다** — 등록 블록의 `json:` 은 옛 자리라 아래에서
    막기 때문이다(`json: false` 도 그 자리이므로 함께 막힌다).
    """
    from genon.preprocessor.processing.converters.json_text import JsonTextSpec
    from genon.preprocessor.processing.enrichment.markdown_front_matter import (
        resolve_format_cfg,
    )

    specs = []
    for config in custom_fields_cfgs or []:
        if config.get("json") is not None:
            # 등록 블록은 기동 시 키 검증을 받지 않는다(설정 파일만 받는다). 그대로 두면
            # 오류가 아니라 조용히 무시되고 본문이 캐치올로 빠져 표·heading 구조가
            # 소실된다 — 제일 나쁜 실패 모드라 여기서 명시적으로 막는다.
            raise ValueError(
                f"custom_fields[doc_type={config.get('doc_type')}] 설정 거부: 등록 블록 `json:` 은 지원하지 않음\n"
                f"`{config.get('config_file')}` 의 `source.pre.json` 으로 이동 필요 "
                f"(키 이름 변경: text_fields → body_from, missing_policy → on_missing)"
            )
        json_cfg = as_dict(resolve_format_cfg(config, "json"))
        if not json_cfg:
            continue
        specs.append(JsonTextSpec(json_cfg, normalize_doc_types(config.get("doc_type"))))
    return specs


def single_match(matching: list, runtime_doc_type: Any, label: str,
                 exc_factory: Callable[[str], Exception]):
    """doc_type 매칭 결과가 1개 이하인지 확인하고 반환한다(중복 설정은 즉시 실패)."""
    if len(matching) > 1:
        raise exc_factory(
            f"custom_fields {label} 설정 중복: doc_type={runtime_doc_type} 에 설정 여러 개"
        )
    return matching[0] if matching else None


def specs_for_doc_type(specs: Iterable, runtime_doc_type: Any) -> list:
    """doc_types 가 비었으면 전체 대상, 아니면 정규화한 doc_type 이 들어 있는 것만."""
    key = normalize_doc_type(runtime_doc_type)
    return [s for s in specs if not s.doc_types or key in s.doc_types]


def json_records_mappers_for(mappers: Iterable, runtime_doc_type: Any,
                             exc_factory: Callable[[str], Exception]) -> list:
    """런타임 doc_type 에 매칭되는 json_mapping 매퍼 **목록**. 없으면 빈 목록.

    한 파일에 성격이 다른 배열이 여럿 오는 원천(`faqList` + `noticeList`)을 다루려면
    매퍼가 여러 개여야 한다. 다만 실수로 같은 설정을 두 번 등록한 것과 구분해야 하므로
    **`records` 키가 서로 달라야** 의도적인 것으로 본다 — 같거나 둘 다 없으면 어느
    매퍼가 무엇을 맡는지 알 수 없어 거부한다.
    """
    matching = [m for m in mappers if m.matches(runtime_doc_type)]
    if len(matching) > 1:
        # 이 목록에는 json_semantic 매퍼도 섞여 있다(빌더 둘의 결과를 합쳐 담는다).
        # 그쪽은 records_key 가 없으므로 getattr 로 견딘다 — 키가 None 이면 무엇을 맡는지
        # 알 수 없으니 거부된다(semantic 은 1파일=1대상이라 섞이는 것 자체가 모호하다).
        keys = [getattr(m, "records_key", None) for m in matching]
        # 레코드 탐색이 키를 정규화해 찾으므로(`faq_list` == `FAQ-LIST`) 겹침도 같은 규칙으로 본다.
        normalized = {normalize_column_name(k) for k in keys if k is not None}
        if len(normalized) != len(keys) or None in keys:
            raise exc_factory(
                f"json_mapping 설정 충돌: doc_type={runtime_doc_type} 의 설정 여러 개에서 source.records_at 겹침 "
                f"(source.records_at={keys})\n"
                f"배열마다 다른 source.records_at 지정 또는 설정 하나 삭제 필요"
            )
    return matching


# `defaults.unknown_doc_type` 이 받는 값. warn 이 기본값이다.
UNKNOWN_DOC_TYPE_POLICIES = ("warn", "fail")


def resolve_unknown_doc_type_policy(value: Any) -> str:
    """`defaults.unknown_doc_type` 값을 해석한다. 없으면 warn 이다.

    그 밖의 값은 ValueError 로 기동을 막는다. 누락을 드러내려고 둔 키이므로, 오기입이
    조용히 warn 으로 떨어지면 목적과 반대가 된다.
    """
    policy = str(value or "").strip().lower() or "warn"
    if policy not in UNKNOWN_DOC_TYPE_POLICIES:
        raise ValueError(
            f"defaults.unknown_doc_type 값 오류: {value!r} (허용 값: {' | '.join(UNKNOWN_DOC_TYPE_POLICIES)})"
        )
    return policy


def check_doc_type_configured(custom_fields_cfgs: Iterable[dict] | None, runtime_doc_type: Any,
                              policy: str, exc_factory: Callable[[str], Exception],
                              config_path: str | None = None) -> None:
    """요청 doc_type 에 맞는 custom_fields 등록이 있는지 확인한다.

    등록이 없으면 파싱은 확장자 기본 경로로 진행되고, 결과에는 doc_type 만 찍혀 정상
    처리와 구별되지 않는다. policy 가 warn 이면 경고 로그를 남기고, fail 이면 요청을
    실패시킨다. doc_type 이 비었거나, doc_type 을 적지 않은 등록 블록(전체 대상)이 있으면
    판정하지 않는다 — specs_for_doc_type 과 같은 규칙이다.

    config_path 는 안내에 적을 설정 파일 — 실제로 읽은 파일이어야 한다. 사본(`activities/parse_x/`)이나
    사이트 덮어쓰기에서 원형 경로를 가리키면 엉뚱한 파일을 고치게 된다(#627). 모르면 "이 액티비티의 config.yaml".
    """
    key = normalize_doc_type(runtime_doc_type)
    if not key:
        return
    registered: set[str] = set()
    for config in custom_fields_cfgs or []:
        doc_types = normalize_doc_types(config.get("doc_type"))
        if not doc_types:
            return
        registered.update(doc_types)
    if key in registered:
        return
    where = config_path or "이 액티비티의 config.yaml"
    register = f"{where} 의 enrichment 에 custom_fields(doc_type: {key})를 등록"
    if policy == "fail":
        raise exc_factory(
            f"doc_type={key} parsing 중단: custom_fields 등록 없음 (defaults.unknown_doc_type=fail)\n"
            f"{register}하거나, 필드 추출 없이 파싱하려면 같은 파일의 "
            f"defaults.unknown_doc_type 을 warn 으로 바꿔야 함"
        )
    _log.warning(f"doc_type={key} 필드 추출 건너뜀: custom_fields 등록 없음, 일반 문서로 파싱함, "
                 f"필드 추출이 필요하면 {register}해야 함")
