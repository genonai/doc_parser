"""비표준 확장자를 표준 포맷 확장자로 읽어주는 별칭(alias) 처리.

원천 시스템이 내려주는 파일이 항상 표준 확장자를 쓰지는 않는다. 예를 들어 마크다운
본문에 HTML 표가 섞인 산출물이 `*.html.parsed.md` 가 아니라 `*.parsed` 로 오는 식이다.
이런 파일은 facade 의 확장자 라우팅에서 어느 분기에도 걸리지 않아 구조 없는 캐치올
(TextLoader/Unstructured) 로 떨어지고, docling 에 그대로 넘겨도 `_guess_format` 이
확장자→mime 을 못 찾아 text/plain 으로 판정해 변환이 실패한다.

확장자마다 분기를 늘리는 대신 "이 확장자는 어떤 포맷으로 볼 것인가" 를 설정 한 줄로
받는다. facade 는 진입부에서 유효 확장자를 한 번 구하고, 필요하면 파싱용 사본을 표준
확장자 이름으로 만들어 넘긴다. 이후 분기는 손대지 않는다.

docling 타입에 의존하지 않는다(경로 문자열 처리만 한다).
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, Mapping

_log = logging.getLogger(__name__)

# 별칭 대상으로 허용하지 않는 확장자는 두지 않는다(blocklist 조차 필요 없다).
# 잘못된 값은 아래 정규화에서 걸러지고, 존재하지 않는 포맷을 가리키면 기존 캐치올로
# 떨어질 뿐이라 별도 allowlist 를 두지 않는다.

_CONFIG_KEY = "extension_aliases"


def _normalize_ext(value: Any) -> str:
    """'.MD', 'md', ' .md ' 를 모두 '.md' 로. 확장자로 볼 수 없으면 빈 문자열."""
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if not text.startswith("."):
        text = "." + text
    # 경로 구분자나 중간 점이 섞인 값은 확장자가 아니다('.tar.gz' 같은 다중 확장자도 제외).
    if "/" in text or "\\" in text or text.count(".") != 1 or len(text) < 2:
        return ""
    return text


def parse_extension_aliases(formats_cfg: Any) -> dict[str, str]:
    """`formats.extension_aliases` 를 {'.parsed': '.md'} 형태로 정규화한다.

    - 키/값 모두 소문자화하고 앞의 점을 보정한다.
    - 확장자로 볼 수 없는 항목, 자기 자신을 가리키는 항목은 경고 없이/경고와 함께 버린다.
    - 연쇄(a→b, b→c)는 따르지 않는다. 한 번만 치환하고 경고를 남긴다.
    """
    if not isinstance(formats_cfg, Mapping):
        return {}
    raw = formats_cfg.get(_CONFIG_KEY)
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        _log.warning(
            f"[format_alias] formats.{_CONFIG_KEY} 는 매핑이어야 합니다(무시): {type(raw).__name__}"
        )
        return {}

    aliases: dict[str, str] = {}
    for key, value in raw.items():
        src = _normalize_ext(key)
        dst = _normalize_ext(value)
        if not src or not dst:
            _log.warning(f"[format_alias] 확장자 별칭 무시: {key!r} -> {value!r}")
            continue
        if src == dst:
            continue
        aliases[src] = dst

    for src, dst in aliases.items():
        if dst in aliases:
            _log.warning(
                f"[format_alias] 별칭 연쇄는 따르지 않습니다: {src} -> {dst} (그 뒤 {aliases[dst]} 는 무시)"
            )
    return aliases


def resolve_ext(ext: str, aliases: Mapping[str, str]) -> str:
    """실제 확장자를 유효 확장자로 바꾼다. 별칭이 없으면 입력 그대로."""
    if not aliases:
        return ext
    return aliases.get(str(ext or "").strip().lower(), ext)


def materialize_alias_copy(file_path: str, target_ext: str, work_dir: str) -> str:
    """파싱용 사본을 `<원본stem><target_ext>` 이름으로 work_dir 에 만들고 그 경로를 반환.

    docling 은 파일명 확장자로 포맷을 판정하므로(`_guess_format`), 별칭이 적용된 입력은
    이름을 바꿔 넘겨야 한다. 내용은 그대로 복사하고 원본은 건드리지 않는다.
    artifacts(이미지) 경로는 호출부가 원본 경로를 따로 넘겨 유지한다.
    """
    src = Path(file_path)
    dst = Path(work_dir) / (src.stem + target_ext)
    shutil.copyfile(src, dst)
    return str(dst)
