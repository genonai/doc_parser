"""문서 단위 메타 필드를 청크 본문 앞에 얹는 접두 문자열 조립.

설정은 `chunk_prefix_fields`(모든 청크 반복) / `first_chunk_fields`(첫 청크 1회) 두 가지이고
해석은 `common/config_parse.py` 가 한다. 이 모듈은 해석된 필드 목록과 문서 metadata 로
실제 문자열을 만든다.

조립부를 한 곳에 두는 이유는 헤더 라인과 같다. 청커는 크기 산정에, 청킹 프로세서는 실제
부착에 같은 문자열을 써야 산출 청크가 chunk_size 를 넘지 않는다. 두 곳이 각자 만들면
그 순간 어긋난다.

항목명은 설정의 `field_labels` 에 사람이 붙인 이름이 있는 필드에만 붙인다(`상품명: …`).
필드명 자체(`PRODUCT_NM`)는 적재 스키마의 DB 컬럼명이라 그대로 노출하면 사람이 쓰지 않는
토큰이 임베딩에 섞인다 — 그래서 이름이 없으면 종전대로 값만 낸다.
"""

from typing import Any

from genon.preprocessor.facade.common import config_parse as cp


def render_value(value: Any) -> str:
    """메타 값 하나를 접두 한 줄로 렌더한다. 접두에 담을 수 없는 값이면 빈 문자열."""
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, (list, tuple, set)):
        # 키워드 배열처럼 짧은 스칼라 목록만 싣는다. 중첩 구조는 접두에 넣을 모양이 아니다.
        parts = [render_value(item) for item in value]
        return ", ".join(part for part in parts if part)
    if isinstance(value, dict):
        # PRODUCT_ATTRS 같은 구조화 속성. 통째로 얹으면 접두가 본문보다 길어진다.
        return ""
    return str(value).strip()


def build_prefix_text(
    metadata: Any, field_names: list[str], labels: dict[str, str] | None = None
) -> str:
    """필드 목록을 청크 선두에 붙일 문자열(`항목명: 값\\n` 또는 `값\\n` 들)로 만든다.

    값이 비었거나 렌더할 수 없는 필드는 조용히 건너뛴다 — 문서마다 LLM 이 뽑지 못하는
    필드가 있는데, 그때 빈 줄이 청크 선두에 남으면 임베딩에 잡음만 는다.

    중복 판정은 항목명이 아니라 값으로 한다. 같은 값이 두 필드에 실려 있으면 항목명만 다른
    같은 문장이 접두에 두 줄로 남는데, 접두는 모든 청크에 반복되므로 그만큼 손해가 크다.
    """
    source = metadata if isinstance(metadata, dict) else {}
    labels = labels or {}
    lines: list[str] = []
    seen: set[str] = set()
    for name in field_names or []:
        text = render_value(source.get(name))
        if not text or text in seen:
            continue
        seen.add(text)
        label = labels.get(name)
        lines.append(f"{label}: {text}" if label else text)
    return "".join(f"{line}\n" for line in lines)


def merge_context_metadata(document_metadata: dict, kwargs: dict) -> dict:
    """문서 metadata 에 요청의 enrichment context metadata 를 덮어쓴 값.

    청커의 크기 산정(split_documents)과 실제 부착(compose_vectors)이 같은 metadata 를 봐야
    예약한 몫과 붙는 문자열이 일치한다.
    """
    context = (kwargs or {}).get("_enrichment_context")
    merged = dict(document_metadata or {})
    if isinstance(context, dict) and isinstance(context.get("metadata"), dict):
        merged.update(context["metadata"])
    return merged


def resolve_prefix_texts(kwargs: dict, metadata: Any) -> tuple[str, str]:
    """(모든 청크 접두, 첫 청크 전용 접두) 를 한 번에 해석한다.

    호출부가 두 설정을 따로 읽다가 한쪽을 빠뜨리는 일이 없도록 묶어 둔다.
    """
    labels = cp.resolve_field_labels(kwargs, metadata)
    repeated = build_prefix_text(
        metadata, cp.resolve_chunk_prefix_fields(kwargs, metadata), labels
    )
    first_only = build_prefix_text(
        metadata, cp.resolve_first_chunk_fields(kwargs, metadata), labels
    )
    return repeated, first_only


def reserved_prefix_text(kwargs: dict, metadata: Any) -> str:
    """청커가 크기 산정에서 예약해야 할 접두 문자열.

    첫 청크 전용 몫까지 더한 보수적인 상한이다. 정확히 하려면 청커가 "지금 만드는 것이 첫
    청크인지" 를 알아야 하는데, 그 정보는 병합·흡수까지 끝난 뒤에야 확정된다. 첫 청크 몫은
    분류값 한 줄 수준이라 전 청크에 예약해도 손해가 작고, 대신 어떤 청크도 chunk_size 를
    넘지 않는다.
    """
    repeated, first_only = resolve_prefix_texts(kwargs, metadata)
    return repeated + first_only
