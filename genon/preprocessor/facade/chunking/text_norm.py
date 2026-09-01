"""청크 텍스트 정규화 — RAG 검색 품질용 결정적 후처리.

LLM 재작성이나 문장 병합은 하지 않는다. 원문 의미, 표 구조, 코드 블록, 법령 조항
구조를 보존한 채 검색 매칭을 방해하는 문자 노이즈만 제거한다.

두 단계로 나뉘며 적용 지점이 다르다.

  sanitize : 문자 위생(NFC, 제로폭/BOM/제어문자 제거, 특수 공백 → 일반 공백,
             CRLF → LF, 전각 영숫자 → 반각, 따옴표/대시 통일).
             청킹 입력에 적용한다. 그래야 청크 경계 산정(chunk_size/토크나이저)과
             표 설명 enrichment 프롬프트까지 같은 클린 텍스트를 본다.
             출력에서만 정규화하면 청크는 노이즈 문자를 세서 잘린 상태로 남는다.

  tidy     : 표현 정리(줄 끝 공백 제거, 연속 빈 줄 최대 1개, 앞뒤 공백 제거).
             가드레일 마스킹 뒤, 벡터 생성 직전에 적용한다. 그래야 임베딩 텍스트와
             n_char/n_word/n_line 통계가 일치한다.

가드레일 quote 매칭과의 순서: matcher 는 정확 매칭 실패 시 공백 무시 fuzzy 로
폴백하므로 sanitize 가 앞에 와도 견딘다. 제로폭 문자 제거는 오히려 정확 매칭
성공률을 올린다. 반면 tidy 는 줄 구조를 바꾸므로 마스킹 뒤에 둔다.

코드 블록(``` 펜스, ~~~ 펜스, 인라인 백틱) 안에서는 따옴표/대시/전각 치환과
줄 정리를 하지 않는다. 문자 위생(제로폭 제거, NFC 등)은 코드 안에서도 안전하므로
전 구간에 적용한다.

설정은 chunking.text_cleanup 하나뿐이다("off" 기본 | "safe").
"""
from __future__ import annotations

import re
import unicodedata
from typing import Callable, Optional

__all__ = [
    "MODE_OFF",
    "MODE_SAFE",
    "resolve_mode",
    "is_enabled",
    "sanitize",
    "tidy",
    "is_blank",
    "sanitize_document",
    "sanitize_elements",
    "sanitize_metadata",
    "mode_from_cfg",
    "mode_of",
    "mode_for",
    "enabled_for",
    "prepare_document",
    "drop_blank_chunks",
    "sanitize_langchain_docs",
]

MODE_OFF = "off"
MODE_SAFE = "safe"

_VALID_MODES = {MODE_OFF, MODE_SAFE}


def resolve_mode(value, fallback: str = MODE_OFF) -> str:
    """yaml/kwargs 의 text_cleanup 값을 "off" 또는 "safe" 로 정규화한다.

    알 수 없는 값은 fallback 으로 떨어진다(설정 오타로 파이프라인이 죽지 않게).
    bool 도 허용한다(True → safe).
    """
    if value is None:
        return fallback
    if isinstance(value, bool):
        return MODE_SAFE if value else MODE_OFF
    text = str(value).strip().lower()
    if text in _VALID_MODES:
        return text
    if text in {"on", "true", "1", "yes"}:
        return MODE_SAFE
    if text in {"false", "0", "no", "none", ""}:
        return MODE_OFF
    return fallback


def is_enabled(mode) -> bool:
    return resolve_mode(mode) == MODE_SAFE


# ---------------------------------------------------------------------------
# 문자 매핑 테이블
# ---------------------------------------------------------------------------

def _build_base_map() -> dict:
    """전 구간(코드 포함)에 적용해도 안전한 치환 테이블."""
    table: dict = {}

    # 제거: BOM, 제로폭, word joiner, soft hyphen, 몽골 모음 구분자.
    for cp in (0xFEFF, 0x200B, 0x200C, 0x200D, 0x2060, 0x00AD, 0x180E):
        table[cp] = None

    # 제거: C0 제어문자(탭/개행 제외) 와 C1 제어문자.
    for cp in range(0x00, 0x20):
        if cp not in (0x09, 0x0A):
            table[cp] = None
    table[0x7F] = None
    for cp in range(0x80, 0xA0):
        table[cp] = None

    # 특수 공백 → 일반 공백. NBSP 를 남기면 토크나이저·BM25 가 다른 토큰으로 본다.
    for cp in (0x00A0, 0x1680, 0x202F, 0x205F, 0x3000):
        table[cp] = " "
    for cp in range(0x2000, 0x200B):
        table[cp] = " "

    # 줄 구분자 → LF.
    table[0x2028] = "\n"
    table[0x2029] = "\n"

    return table


def _build_ascii_map() -> dict:
    """코드 블록 밖에서만 적용하는 치환 테이블.

    NFKC 일괄 변환은 하지 않는다. ㈜·㎡·½ 같은 기호까지 분해해 의미를 훼손한다.
    전각 영숫자만 코드포인트 화이트리스트로 반각화한다.
    """
    table: dict = {}

    # 전각 숫자/영문 → 반각. 한국 문서에서 빈도가 높고 위험이 거의 없다.
    for cp in range(0xFF10, 0xFF1A):          # ０-９
        table[cp] = chr(cp - 0xFF10 + ord("0"))
    for cp in range(0xFF21, 0xFF3B):          # Ａ-Ｚ
        table[cp] = chr(cp - 0xFF21 + ord("A"))
    for cp in range(0xFF41, 0xFF5B):          # ａ-ｚ
        table[cp] = chr(cp - 0xFF41 + ord("a"))

    # 따옴표 통일.
    for cp in (0x2018, 0x2019, 0x201A, 0x201B, 0x2032):
        table[cp] = "'"
    for cp in (0x201C, 0x201D, 0x201E, 0x201F, 0x2033):
        table[cp] = '"'

    # 대시 통일. 한자 낫표(「」) 같은 구조 기호는 건드리지 않는다.
    for cp in (0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212):
        table[cp] = "-"

    return table


_BASE_MAP = _build_base_map()
_ASCII_MAP = _build_ascii_map()

# 펜스 코드 블록. 닫히지 않으면 문서 끝까지를 코드로 본다(원문 보존 쪽으로 실패).
_FENCE_RE = re.compile(r"(?ms)^[ \t]*(`{3,}|~{3,}).*?(?:^[ \t]*\1[ \t]*$|\Z)")
# 인라인 백틱. 줄바꿈을 넘지 않는 것만 코드로 본다.
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")

_TRAILING_WS_RE = re.compile(r"[ \t]+(?=\n)|[ \t]+\Z")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


def _map_outside_code(text: str, fn: Callable[[str], str]) -> str:
    """코드 구간(펜스/인라인 백틱)을 건너뛰고 나머지에만 fn 을 적용한다."""
    out: list = []
    pos = 0
    for m in _FENCE_RE.finditer(text):
        out.append(_map_outside_inline_code(text[pos:m.start()], fn))
        out.append(m.group(0))
        pos = m.end()
    out.append(_map_outside_inline_code(text[pos:], fn))
    return "".join(out)


def _map_outside_inline_code(text: str, fn: Callable[[str], str]) -> str:
    if not text:
        return text
    out: list = []
    pos = 0
    for m in _INLINE_CODE_RE.finditer(text):
        out.append(fn(text[pos:m.start()]))
        out.append(m.group(0))
        pos = m.end()
    out.append(fn(text[pos:]))
    return "".join(out)


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------

def sanitize(text: Optional[str]) -> Optional[str]:
    """문자 위생. 줄 구조는 바꾸지 않는다(길이가 줄 수는 있다)."""
    if not text or not isinstance(text, str):
        return text
    result = text.replace("\r\n", "\n").replace("\r", "\n")
    result = result.translate(_BASE_MAP)
    # NFC. macOS 유래 파일이나 HWP 경유 텍스트의 한글 자모 분리(NFD)를 되돌린다.
    # 이 항목 하나가 검색 매칭에 미치는 영향이 가장 크다.
    result = unicodedata.normalize("NFC", result)
    return _map_outside_code(result, lambda seg: seg.translate(_ASCII_MAP))


def _tidy_segment(segment: str) -> str:
    if not segment:
        return segment
    segment = _TRAILING_WS_RE.sub("", segment)
    return _BLANK_LINES_RE.sub("\n\n", segment)


def tidy(text: Optional[str]) -> Optional[str]:
    """표현 정리. 벡터 생성 직전(가드레일 마스킹 뒤)에 적용한다."""
    if not text or not isinstance(text, str):
        return text
    return _map_outside_code(text, _tidy_segment).strip()


def is_blank(text: Optional[str]) -> bool:
    """정규화 후 내용이 남지 않는 텍스트인지."""
    return not text or not str(text).strip()


def sanitize_document(document) -> None:
    """DoclingDocument 의 텍스트 아이템과 표 셀을 제자리에서 sanitize 한다.

    docling 타입을 import 하지 않고 duck typing 으로 처리한다(공용 모듈이
    배포본에서 docling 버전에 묶이지 않게).
    """
    if document is None:
        return
    try:
        items = list(document.iterate_items())
    except Exception:
        return
    for entry in items:
        item = entry[0] if isinstance(entry, tuple) else entry
        for attr in ("text", "orig"):
            value = getattr(item, attr, None)
            if isinstance(value, str) and value:
                try:
                    setattr(item, attr, sanitize(value))
                except Exception:
                    pass
        data = getattr(item, "data", None)
        for cell in (getattr(data, "table_cells", None) or []):
            value = getattr(cell, "text", None)
            if isinstance(value, str) and value:
                try:
                    cell.text = sanitize(value)
                except Exception:
                    pass


def sanitize_elements(elements) -> list:
    """parse-format element 리스트의 content 와 metadata 문자열을 sanitize 한다.

    행 기반(tabular_row/custom_fields_row) 경로는 metadata 가 그대로 청크
    property 로 나가므로, text 만 정규화하면 같은 내용이 두 표현으로 저장된다.
    문자 위생만 적용하고 줄 구조(tidy)는 건드리지 않는다.
    """
    if not elements:
        return elements
    for element in elements:
        if not isinstance(element, dict):
            continue
        content = element.get("content")
        if isinstance(content, str) and content:
            element["content"] = sanitize(content)
        metadata = element.get("metadata")
        if isinstance(metadata, dict):
            element["metadata"] = sanitize_metadata(metadata)
        # 접두는 content 선두와 글자 단위로 같아야 한다. content 만 sanitize 하면
        # 조각 분할 경로의 `content.startswith(prefix)` 가 어긋나 접두 재부착이 조용히
        # 포기된다(두 번째 조각부터 어느 카드·섹션인지 사라진다).
        prefix = element.get("chunk_prefix")
        if isinstance(prefix, str) and prefix:
            element["chunk_prefix"] = sanitize(prefix)
    return elements


def sanitize_metadata(metadata: dict) -> dict:
    """dict 안의 문자열 값(중첩 dict/list 포함)을 sanitize 한 새 dict 를 만든다."""
    if not isinstance(metadata, dict):
        return metadata
    return {key: _sanitize_value(value) for key, value in metadata.items()}


def _sanitize_value(value):
    if isinstance(value, str):
        return sanitize(value)
    if isinstance(value, dict):
        return {k: _sanitize_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_value(v) for v in value]
    return value


# ---------------------------------------------------------------------------
# processor 공용 헬퍼
#
# 최상위 processor(chunking/intelligent/convert/attachment)는 서로 import 하지
# 않으므로, 세 곳에 같은 코드를 두는 대신 여기에 모아 공유한다.
# ---------------------------------------------------------------------------

def mode_from_cfg(chunking_cfg: dict) -> str:
    """yaml `chunking:` 섹션에서 text_cleanup 모드를 읽는다(미설정/오타는 off)."""
    return resolve_mode((chunking_cfg or {}).get("text_cleanup"), MODE_OFF)


ATTR = "_text_cleanup"


def mode_of(owner) -> str:
    """yaml 기본 모드를 읽는다. owner 는 processor 인스턴스 또는 모드 값이다.

    `object.__new__` 로 __init__ 을 우회해 만든 인스턴스(단위 테스트 스텁)에는
    속성이 없으므로 off 로 본다.
    """
    if owner is None or isinstance(owner, (str, bool)):
        return resolve_mode(owner, MODE_OFF)
    return resolve_mode(getattr(owner, ATTR, None), MODE_OFF)


def mode_for(kwargs: dict, owner) -> str:
    """요청 kwargs 의 text_cleanup 을 yaml 기본값보다 우선 적용해 모드를 정한다.

    우선순위: kwargs.text_cleanup > yaml(chunking.text_cleanup) > "off".
    알 수 없는 값은 yaml 기본값으로 떨어진다(설정 오타로 파이프라인이 죽지 않게).
    """
    default = mode_of(owner)
    value = (kwargs or {}).get("text_cleanup")
    return default if value is None else resolve_mode(value, default)


def enabled_for(kwargs: dict, owner) -> bool:
    """mode_for 결과가 safe 인지."""
    return mode_for(kwargs, owner) == MODE_SAFE


def prepare_document(document, kwargs: dict, owner) -> bool:
    """청킹 직전 문서에 문자 위생을 적용하고, 정규화 활성 여부를 돌려준다.

    호출부는 반환값으로 이후 단계(빈 청크 제거 등)를 게이팅한다.
    """
    if not enabled_for(kwargs, owner):
        return False
    sanitize_document(document)
    return True


def drop_blank_chunks(chunks, attr: str = "text") -> list:
    """내용이 남지 않는 청크를 제거한다.

    반드시 페이지 카운트(page_chunk_counts) 집계 전에 호출해야 한다. 집계 뒤에
    제거하면 n_chunk_of_doc / n_chunk_of_page 와 i_chunk_* 인덱스가 어긋난다.
    """
    return [c for c in chunks if not is_blank(getattr(c, attr, None))]


def sanitize_langchain_docs(documents) -> None:
    """langchain Document 리스트의 page_content 를 제자리에서 sanitize 한다."""
    for doc in documents or []:
        content = getattr(doc, "page_content", None)
        if isinstance(content, str) and content:
            doc.page_content = sanitize(content)
