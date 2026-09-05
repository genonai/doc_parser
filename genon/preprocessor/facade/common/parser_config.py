"""custom_fields 설정에서 파싱 라우팅이 쓰는 spec/mapper 를 만든다.

facade 는 "어떤 확장자를 어느 경로로 보내는가" 를 갖고, 이 모듈은 그 경로가 참조하는
설정 객체를 만드는 일만 한다. 설정 오류를 어떤 예외로 감쌀지는 여기서 정하지 않는다 —
GenosServiceException 이 facade 마다 복제돼 있어(활성 7곳, legacy 포함 22곳) 클래스를
고를 수 없다. 호출부가 guard_config 에 자기 예외 팩토리를 넘긴다.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Iterable

from genon.preprocessor.facade.common.config_parse import as_dict
from genon.preprocessor.facade.enrichment.custom_fields_enricher import (
    normalize_doc_type,
    normalize_doc_types,
)

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
    """custom_fields 설정 중 `json:` 블록을 가진 것만 JsonTextSpec 으로 만든다.

    .json 입력에서 본문 텍스트를 꺼낼 key 목록이다. 잘못된 설정은 ValueError 로 나가고
    호출부의 guard_config 가 어느 설정인지 붙여 준다.
    """
    from genon.preprocessor.converters.json_text import JsonTextSpec

    specs = []
    for config in custom_fields_cfgs or []:
        json_cfg = as_dict(config.get("json"))
        if not json_cfg:
            continue
        specs.append(JsonTextSpec(json_cfg, normalize_doc_types(config.get("doc_type"))))
    return specs


def single_match(matching: list, runtime_doc_type: Any, label: str,
                 exc_factory: Callable[[str], Exception]):
    """doc_type 매칭 결과가 1개 이하인지 확인하고 반환한다(중복 설정은 즉시 실패)."""
    if len(matching) > 1:
        raise exc_factory(
            f"동일 doc_type에 {label} custom_fields 설정이 여러 개입니다: {runtime_doc_type}"
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
        if len(set(keys)) != len(keys) or None in keys:
            raise exc_factory(
                f"동일 doc_type 에 json_mapping 설정이 여러 개인데 records 키가 겹칩니다"
                f"({runtime_doc_type}, records={keys}). 배열마다 다른 records 키를 주거나"
                f" 설정 하나를 지우세요."
            )
    return matching
