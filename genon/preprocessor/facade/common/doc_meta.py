"""docling_core `meta` 정리 공용 모듈 — 직렬화 본문에 섞이는 enricher 메타를 걷어낸다.

## 무엇이 문제인가

docling_core 2.85 는 deprecated 필드 `annotations` 를 모델 검증 시점에 `meta` 로 자동
이관한다(`TableItem._migrate_annotations_to_meta` / `PictureItem` 동형). 그리고 HTML·
Markdown 직렬화기는 그 `meta` 를 본문에 함께 내보낸다.

    <details class="docling-meta"><summary>Meta</summary>
      <div class="docling-meta-field" data-meta-name="description">...</div>
      <div class="docling-meta-field" data-meta-name="docling_legacy__misc">
        {'table_retrieval': {'retrieval_context': ..., 'key_facts': [...]}}</div>
    </details>

markdown 경로는 더 험해서 dict 를 그대로 문자열로 찍는다.

전처리기는 표 설명을 청크 본문 선두에 `[표 검색 설명]` 블록으로 직접 배치하므로 이 블록은
같은 문장의 중복이자, RAG 인덱스에 들어가면 안 되는 내부 구조체 노출이다.

## 왜 기존 `clean_copy` 로는 못 막았나

`TableDescriptionExtractor.clean_copy` 는 `annotations` 리스트만 비운다. `/parser` 가 낸
docling JSON 을 `/chunker` 가 다시 검증하며 읽는 순간 이관이 이미 끝나 있어서, annotations 를
비워도 `meta` 가 그대로 남아 다시 직렬화된다. 게다가 직렬화기는 `meta` 가 있으면 레거시
annotation 경로를 아예 쓰지 않으므로, 이관 이후에는 `meta` 쪽이 유일한 출력 경로다.

## 범위

전처리기 enricher 가 붙인 항목만 지운다. 문서 원본이나 docling 자체 enrichment 가 넣은
meta 는 건드리지 않는다 — 판정은 provenance(= 이관된 뒤의 `created_by`) 로만 한다.

docling 타입을 import 하지 않고 duck typing 으로 처리한다(배포본이 docling 버전에 묶이지
않게 한다).
"""
from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger(__name__)


# 전처리기 enricher 가 붙인 annotation 의 provenance. 이 값이 붙은 meta 만 걷어낸다.
# table_description.py / image_description.py 의 기본값과 같은 값이지만, 공용 모듈이
# enrichment 패키지를 역참조하지 않도록 여기에 문자열로 둔다(순환 import 방지).
ENRICHER_PROVENANCES = frozenset({
    "facade_table_description",
    "facade_table_text_description",
    "facade_image_description",
})

# provenance 는 yaml 로 바꿀 수 있으므로 기본값 나열만으로는 새 값을 놓친다.
# enricher 가 쓰는 이름 규약(`facade_` 접두)까지 인정해 설정을 바꿔도 따라오게 한다.
_ENRICHER_PROVENANCE_PREFIX = "facade_"


def _is_enricher_provenance(value: Any) -> bool:
    text = str(value or "")
    return text in ENRICHER_PROVENANCES or text.startswith(_ENRICHER_PROVENANCE_PREFIX)


# docling_core 가 MiscAnnotation 을 이관할 때 쓰는 meta 필드 이름 접두.
# (`MetaUtils._META_FIELD_LEGACY_NAMESPACE` + 구분자)
_LEGACY_FIELD_PREFIX = "docling_legacy__"


def _has_enricher_provenance(value: Any) -> bool:
    """이관된 meta 값이 전처리기 enricher 산이면 True."""
    if isinstance(value, dict):
        return _is_enricher_provenance(value.get("provenance"))
    return False


def strip_item_meta(item: Any) -> bool:
    """DocItem 하나에서 enricher 가 남긴 meta 를 제거한다. 지웠으면 True."""
    meta = getattr(item, "meta", None)
    if meta is None:
        return False

    changed = False

    description = getattr(meta, "description", None)
    if description is not None:
        if _is_enricher_provenance(getattr(description, "created_by", "")):
            try:
                meta.description = None
                changed = True
            except Exception:  # 모델이 대입을 막는 버전 대비
                _log.debug("meta.description 제거 실패", exc_info=True)

    # MiscAnnotation 이관분은 pydantic extra 로 들어간다(`docling_legacy__misc` 등).
    extra = getattr(meta, "__pydantic_extra__", None)
    if isinstance(extra, dict):
        for key in [k for k in extra if k.startswith(_LEGACY_FIELD_PREFIX)]:
            if _has_enricher_provenance(extra.get(key)):
                extra.pop(key, None)
                changed = True

    return changed


def strip_enricher_meta(document: Any) -> int:
    """문서 전체를 훑어 enricher meta 를 제거하고 제거한 아이템 수를 돌려준다.

    표·그림만 대상으로 한다(annotation 을 붙이는 아이템 유형이 이 둘뿐이다).
    """
    if document is None:
        return 0

    removed = 0
    for attr in ("tables", "pictures"):
        for item in getattr(document, attr, None) or []:
            if strip_item_meta(item):
                removed += 1
    if removed:
        _log.debug("enricher meta 제거: %d개 아이템", removed)
    return removed
