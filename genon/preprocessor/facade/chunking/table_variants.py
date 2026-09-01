"""청크 텍스트를 표 표기형태만 바꿔 다시 내보내기 위한 기록부.

청크의 `text` 는 표를 한 가지 표기형태로만 담는다(output.table_format 이 정하고,
auto 는 표 구조에 따라 표마다 갈린다). 소비 측에서 같은 청크를 다른 표기형태로도
보려면 형식별 텍스트가 함께 실려야 한다.

이 모듈은 두 가지만 한다.

1. 청커가 표를 직렬화할 때 그 결과(primary)와 형식별 변형을 짝지어 기록한다.
2. 청크 텍스트가 확정된 뒤, 그 안의 primary 문자열을 형식별 변형으로 치환한다.

청크 조립·분할·토큰 예산 경로는 건드리지 않는다. 표 문자열이 청크 텍스트 안에
그대로 남아 있다는 사실에 기대는 대신, 남아 있지 않으면 그 표만 primary 표기로
두고 미치환 수를 세어 관측 가능하게 한다(내용이 사라지는 일은 없다).

기록 키는 self_ref 가 아니라 primary 문자열 자체다. 조각 인덱스로 잡으면 청크
병합 단계가 청크와 조각의 대응을 흐트려 놓아 매칭이 깨진다. 내용 키는 같은 표가
여러 청크에 나와도 일관되게 치환되고, 완전히 같은 표가 둘이면 서로의 변형이
동일하므로 충돌이 무해하다.

docling 타입을 import 하지 않는다. self_ref 만 duck typing 으로 읽는다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

_log = logging.getLogger(__name__)

# 형식 -> 청크 필드명. 필드 이름은 여기 한 곳에만 둔다.
VARIANT_FIELDS: dict[str, str] = {
    "html": "text_table_html",
    "markdown": "text_table_md",
}


def field_names(formats: Sequence[str] = ()) -> tuple:
    """형식 목록에 대응하는 청크 필드명. 미지정이면 이 기능이 쓰는 전체 필드명."""
    names = VARIANT_FIELDS.values() if not formats else (
        VARIANT_FIELDS[fmt] for fmt in formats if fmt in VARIANT_FIELDS)
    return tuple(dict.fromkeys(names))


class TableTextVariants:
    """표별 형식 변형 텍스트 기록부.

    smart_chunker 의 `_table_split_totals` 와 같은 전달 방식이다. DocChunk 에는 실을
    자리가 없어 청커 인스턴스 속성에 두고, compose_vectors 가 getattr 기본값으로 읽는다.
    `object.__new__` 로 만든 인스턴스에는 이 속성이 없을 수 있다.
    """

    def __init__(self, formats: Sequence[str] = ()):
        self.formats: tuple = tuple(formats)
        # primary 문자열 -> {형식: 변형 문자열}
        self._by_text: dict[str, dict[str, str]] = {}
        # self_ref -> 그 표가 남긴 primary 문자열들. 청크가 담은 표만 훑기 위한 색인이다.
        self._by_ref: dict[str, list] = {}
        # self_ref -> 형식별 직렬화 메모(같은 표를 여러 번 직렬화하지 않기 위한 캐시)
        self._memo: dict[tuple, str] = {}

    def enabled(self) -> bool:
        return bool(self.formats)

    def record(self, primary: str, variants: dict, self_ref: str = "") -> None:
        """표 하나(또는 분할 조각 하나)의 primary 와 형식별 변형을 기록한다."""
        if not self.formats or not primary:
            return
        bucket = self._by_text.setdefault(primary, {})
        for fmt, text in variants.items():
            if fmt in self.formats and text:
                bucket[fmt] = text
        if self_ref:
            refs = self._by_ref.setdefault(self_ref, [])
            if primary not in refs:
                refs.append(primary)

    def memo(self, self_ref: str, fmt: str, build: Callable[[], str]) -> str:
        """(self_ref, 형식) 별 직렬화 결과 캐시.

        표 텍스트 생성 함수는 토큰 계산과 재조립에서 여러 번 불린다. 변형 생성이
        그 횟수만큼 늘어나면 표가 많은 문서에서 직렬화 비용이 배로 뛴다.
        """
        key = (self_ref, fmt)
        if key not in self._memo:
            try:
                self._memo[key] = build() or ""
            except Exception:
                _log.debug(
                    "[table_variants] 변형 직렬화 실패 - primary 표기를 유지합니다: "
                    "table=%s format=%s", self_ref, fmt, exc_info=True)
                self._memo[key] = ""
        return self._memo[key]

    def _candidates(self, refs) -> list:
        """훑을 primary 문자열 목록. 청크가 담은 표를 알면 그것만 본다.

        표가 수백 개인 문서에서 청크마다 전체를 훑으면 치환 비용이 표 수에 비례해 늘어난다.
        """
        if not refs:
            return list(self._by_text)
        indexed = {p for primaries in self._by_ref.values() for p in primaries}
        # self_ref 를 못 읽어 색인에 없는 기록은 어느 청크에나 후보로 남긴다.
        chosen = [p for p in self._by_text if p not in indexed]
        for ref in refs:
            for primary in self._by_ref.get(ref, ()):
                if primary not in chosen:
                    chosen.append(primary)
        return chosen

    def render(self, text: str, fmt: str, refs=()) -> tuple:
        """청크 텍스트의 표 부분을 해당 형식으로 바꾼 텍스트와 미치환 표 수.

        기록에 없거나 변형이 primary 와 같으면 그 표는 그대로 둔다(치환 불필요).
        기록은 있는데 청크 텍스트 안에서 primary 문자열을 찾지 못한 경우만 미치환으로 센다.
        """
        if not text or not self._by_text:
            return text, 0
        result = text
        misses = 0
        # 긴 문자열부터 치환한다. 짧은 표가 긴 표의 부분 문자열인 경우(같은 표의 분할
        # 조각들) 짧은 쪽을 먼저 바꾸면 긴 쪽이 더는 매칭되지 않는다.
        for primary in sorted(self._candidates(refs), key=len, reverse=True):
            variant = self._by_text[primary].get(fmt)
            if not variant or variant == primary:
                continue
            if primary in result:
                result = result.replace(primary, variant)
            elif primary in text:
                # 앞선 치환이 이미 이 구간을 바꿔 놓은 경우. 미치환이 아니다.
                continue
            else:
                misses += 1
        return result, misses

    def field_values(self, text: str, refs=()) -> dict:
        """청크 필드명 -> 형식별 텍스트. 표가 없는 청크도 원문 그대로 채운다.

        필드 유무가 청크마다 갈리면 소비 측이 분기하고 폴백을 다시 만들어야 한다.
        ``refs`` 는 그 청크가 담은 아이템의 self_ref 목록(표만 걸러 두지 않아도 된다).
        """
        values: dict = {}
        for fmt in self.formats:
            field = VARIANT_FIELDS.get(fmt)
            if not field:
                continue
            rendered, misses = self.render(text, fmt, refs)
            if misses:
                _log.debug(
                    "[table_variants] 청크 텍스트에서 표 문자열을 찾지 못해 primary 표기로 "
                    "남긴 표가 있습니다: format=%s count=%d", fmt, misses)
            values[field] = rendered
        return values


def field_values_for_text(text: str, formats: Sequence[str] = (),
                          *, compact_tables: bool = True,
                          mask: Callable[[str], str] | None = None,
                          tidy: Callable[[str], str] | None = None) -> dict:
    """청크 텍스트만 보고 만드는 형식별 필드값. 표 구조 정보가 없는 경로용.

    `TableTextVariants` 는 청커가 `TableItem` 을 직렬화할 때 남긴 기록으로 치환한다.
    행 기반 custom_fields 나 parse-format 텍스트 경로에는 그 기록이 없으므로, 확정된
    청크 텍스트 안의 표 블록을 찾아 표기형태만 바꾼다(`table_blocks`).

    계약은 `field_values` 와 같다 — 형식마다 필드를 하나씩 채우고, 표가 없는 청크도
    `text` 원문 그대로 채운다(소비 측이 청크마다 분기하지 않게 한다).

    `text` 는 **가드레일 마스킹·정제 이전** 텍스트여야 하고, 그 두 후처리를 `mask`/`tidy`
    로 넘겨 변형에도 같은 순서로 적용한다. 순서를 바꾸면 가드레일로 가린 값이 변형
    필드로 평문 유출된다(docling 경로의 같은 규칙: `chunking_processor` compose_vectors).
    """
    values: dict = {}
    if not formats:
        return values
    from genon.preprocessor.facade.chunking import table_blocks as tbk

    for fmt in formats:
        field = VARIANT_FIELDS.get(fmt)
        if not field:
            continue
        value = tbk.renotate(text, fmt, compact_tables=compact_tables) if text else text
        if mask is not None:
            value = mask(value)
        if tidy is not None:
            value = tidy(value)
        values[field] = value
    return values


def text_fields_hook(formats: Sequence[str] = (), *, compact_tables: bool = True):
    """청크 텍스트 → 표 파생 필드(표기형태 변형 + `has_table`) 를 만드는 훅.

    청커를 거치지 않고 벡터를 직접 만드는 경로(xlsx tabular 직접 처리 등)가 쓴다. 그
    경로의 구현체는 `converters/` 아래에 있어 facade 를 import 하지 않으므로(단방향
    규칙), 정책을 함수로 넘겨 같은 규칙을 공유한다.
    """
    from genon.preprocessor.facade.chunking import table_blocks as tbk

    def build(text: str) -> dict:
        fields = field_values_for_text(text, formats, compact_tables=compact_tables)
        fields["has_table"] = tbk.has_table(text)
        return fields

    return build
