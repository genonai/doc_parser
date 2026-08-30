"""yaml 설정 값 해석 헬퍼.

facade 5종이 각자 복제해 두었던 설정 파싱 로직의 단일 사본이다. 표준 라이브러리와
yaml 만 쓰고 docling 타입에는 의존하지 않는다.

facade 쪽에는 기존 이름(`_as_dict` 등)의 얇은 별칭만 남긴다 — 호출부를 건드리지
않기 위함이며, 단위 테스트가 그 이름을 모듈 속성으로 참조하는 곳도 있다.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Optional

import yaml

_log = logging.getLogger(__name__)

# 청킹 토크나이저 기본값. 로컬 경로가 있으면 그쪽을, 없으면 HF id 로 폴백한다.
DEFAULT_TOKENIZER_LOCAL_PATH = "/models/doc_parser_models/sentence-transformers-all-MiniLM-L6-v2"
DEFAULT_TOKENIZER_ID = "sentence-transformers/all-MiniLM-L6-v2"

_TRUE_WORDS = {"1", "true", "yes", "y", "on"}
_FALSE_WORDS = {"0", "false", "no", "n", "off"}


def as_dict(value: Any) -> dict:
    """설정 노드를 dict 로 강제. 매핑이 아니면 빈 dict."""
    return value if isinstance(value, dict) else {}


def parse_optional_bool(value: Any, key: str = "") -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE_WORDS:
            return True
        if text in _FALSE_WORDS:
            return False
    if key:
        _log.warning(f"[DocumentProcessor] Invalid bool value for '{key}': {value!r}. Fallback to default.")
    return None


def parse_optional_int(value: Any, key: str = "") -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        if key:
            _log.warning(f"[DocumentProcessor] Invalid int value for '{key}': {value!r}. Fallback to default.")
        return None


def parse_optional_float(value: Any, key: str = "") -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        if key:
            _log.warning(f"[DocumentProcessor] Invalid float value for '{key}': {value!r}. Fallback to default.")
        return None


def as_int_flag(value: Any, default: int = 0) -> int:
    """런타임 기능 플래그를 0 또는 1 로 정규화."""
    if value is None:
        return default
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return 1 if int(value) == 1 else 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _TRUE_WORDS:
            return 1
        if normalized in _FALSE_WORDS:
            return 0
    return default


def warn_unresolved_placeholders(cfg: dict, config_path: str) -> None:
    """config 에 남아있는 미치환 플레이스홀더(<UPPER_SNAKE>)를 탐지해 경고한다.

    Site 배포 시 OCR/Layout/Enrichment endpoint·serving ID 등의 치환 누락을 조기에
    드러내기 위함. fail-fast 하지 않고(기동 보존) WARNING 로그만 남긴다.
    """
    pattern = re.compile(r"<[A-Z0-9_]+>")
    found = []

    def _scan(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                _scan(v, f"{path}.{k}" if path else str(k))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                _scan(v, f"{path}[{i}]")
        elif isinstance(node, str):
            for ph in pattern.findall(node):
                found.append((path, ph))

    _scan(cfg, "")
    if found:
        lines = "\n".join(f"  - {path}: {ph}" for path, ph in found)
        _log.warning(
            "[DocumentProcessor] 미치환 설정 플레이스홀더가 발견되었습니다 "
            f"(config='{config_path}'). Site 배포 시 실제 값으로 변경하세요:\n{lines}"
        )


def load_config(config_path: str, *, strict: bool = True) -> dict:
    """yaml 설정을 읽어 dict 로 돌려준다.

    strict=True(기본): 읽기 실패나 형식 오류를 그대로 올린다(기동 시점에 드러남).
    strict=False: 경고만 남기고 빈 dict 로 진행한다(첨부 프로세서의 기존 동작).
    """
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except Exception as exc:
        if strict:
            raise
        _log.warning(f"[DocumentProcessor] Failed to load config '{config_path}': {exc}. Using defaults.")
        return {}

    if not isinstance(cfg, dict):
        if strict:
            raise ValueError(f"Invalid config format: expected mapping, got {type(cfg).__name__}")
        _log.warning(
            f"[DocumentProcessor] Invalid config format in '{config_path}' "
            f"(expected mapping, got {type(cfg).__name__}). Using defaults."
        )
        return {}
    warn_unresolved_placeholders(cfg, config_path)
    return cfg


def resolve_compact_tables(source: dict, default: bool = True) -> bool:
    """compact_tables 스위치를 bool 로 해석. 값이 없거나 해석 불가면 default.

    런타임 kwargs 와 yaml 양쪽에서 온다. 둘 다 검증 없이 전달되므로 문자열
    "false" 가 올 수 있는데 bool("false") 는 True 라서, 그대로 bool() 을 씌우면
    문서화된 off 스위치가 조용히 무시된다.
    """
    parsed = parse_optional_bool(source.get("compact_tables"), "compact_tables")
    return default if parsed is None else parsed


def resolve_tokenizer(
    chunking_cfg: dict,
    *,
    local_path: str = DEFAULT_TOKENIZER_LOCAL_PATH,
    hf_id: str = DEFAULT_TOKENIZER_ID,
):
    """chunking config 로부터 토크나이저를 결정한다.

    tokenizer_path 가 실제 존재하면 그 로컬 경로를, 없으면 tokenizer_id(HF) 로 폴백한다
    (외부 네트워크 차단 환경 대비). config 미지정 시 기본값은 현행 하드코딩 값과 동일.
    """
    local = chunking_cfg.get("tokenizer_path") or local_path
    resolved_id = chunking_cfg.get("tokenizer_id") or hf_id
    return Path(local) if Path(local).exists() else resolved_id


def clamp_chunk_size(size: Optional[int], minimum: int) -> Optional[int]:
    """chunk_size 가 0 초과이면서 minimum 미만이면 minimum 으로 보정.
    0(=분할 안 함) 과 None 은 그대로 둔다."""
    if size is not None and 0 < size < minimum:
        _log.info(f"[chunk_size] {size} < {minimum} → {minimum} 로 보정")
        return minimum
    return size


def resolve_chunk_mode(kwargs: dict, yaml_default: str) -> str:
    """청킹 병합 모드 결정. 우선순위: 요청 chunk_mode > yaml > 'split_only'.

    chunk_mode 는 문자열('split_only'|'resize_all') 또는 0/1 플래그를 받는다.
    0(false/no/off) → 'split_only'(구조 경계 보존, 초과 그룹만 분할),
    1(true/yes/on)  → 'resize_all'(인접 섹션을 chunk_size 한도까지 greedy 병합).
    (chunk_mode=0 은 falsy 라 `x or default` 로는 무시되므로 `is not None` 으로 판별한다.)
    """
    raw = kwargs.get("chunk_mode")
    if raw is not None:
        # 숫자 0/0.0/1/1.0 은 문자열 파싱 전에 정규화(JSON number 로 오는 경우 대응).
        # bool 은 제외 — 아래 문자열 분기의 "true"/"false" 로 처리한다(True==1 오분류 방지).
        if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw in (0, 1):
            return "resize_all" if raw == 1 else "split_only"
        s = str(raw).strip().lower()
        if s in {"split_only", "resize_all"}:
            return s
        if s in _TRUE_WORDS:
            return "resize_all"
        if s in _FALSE_WORDS:
            return "split_only"
    mode = str(yaml_default or "").strip().lower()
    return mode if mode in {"split_only", "resize_all"} else "split_only"


def resolve_include_chunk_header(kwargs: dict, yaml_default: bool) -> bool:
    """청크 선두 `HEADER: <섹션 경로>` 라인 부착 여부. 우선순위: 요청 kwargs > yaml > True.

    0/1 숫자와 "on"/"off" 등 문자열을 모두 허용한다.
    off 로 주면 순수 본문만 산출된다.
    """
    parsed = parse_optional_bool(kwargs.get("include_chunk_header"), "include_chunk_header")
    return bool(yaml_default) if parsed is None else parsed


def copy_enrichment_options(options, **updates):
    """DataEnrichmentOptions 를 얕게 복제하며 지정 필드를 override(원본 불변)."""
    try:
        return options.model_copy(update=updates)
    except AttributeError:
        import copy as _copy

        cloned = _copy.copy(options)
        for key, value in updates.items():
            setattr(cloned, key, value)
        return cloned
