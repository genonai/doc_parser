"""file_source.py — 입력 파일명을 custom_fields 의 원천으로 쓰는 기구.

원천 본문에 식별자가 없고 파일명에만 있는 문서가 있다(상품설명서 `20071_20260611_….md`,
관심소식 `TD00008415_d_5199.html.json` 등). yaml 에서는 `alias` 에 예약어 `$file` 을
적어 지목하고, 잘라내기는 기존 변환기(`regex_extract`)가 맡는다.

    BIZ_ID:
      alias: [$file]
      transform: {name: regex_extract, pattern: '^(\\d+_\\d{8})_'}

설정 해석(config_v2)이 이 필드를 내부 키 `file_fields` 로 옮기고, 매퍼가 값 파이프라인
**앞에서** 파일명을 채운다. 그래서 `transform`·`template` 이 다른 원천값과 같은 순서로 걸린다.

파일명은 요청 진입점(ParserCore._start_job)이 원본 경로 기준으로 한 번 정해
`_source_filename` 으로 실어 보낸다. 확장자 별칭 사본이나 전처리 파생 파일의 이름이
끼어들지 않게 하기 위해서다.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Iterable

_log = logging.getLogger(__name__)

# yaml `alias` 에 적는 예약어.
FILE_ALIAS = "$file"
# 설정 해석 후 내부 형태의 키(`{목표필드: …}` 가 아니라 목표필드 목록이다).
FILE_FIELDS_KEY = "file_fields"
# 요청 kwargs 에 실리는 사설 키.
PARAM_KEY = "_source_filename"


def resolve_source_filename(params: dict, file_path: str) -> str | None:
    """요청이 준 `org_filename` 을 먼저 쓰고, 없으면 입력 경로의 파일 이름을 쓴다."""
    name = str(params.get("org_filename") or "").strip()
    if not name:
        name = os.path.basename(str(file_path or ""))
    return os.path.basename(name) or None


def fill_file_fields(fields: dict, targets: Iterable[str], filename: Any) -> None:
    """파일명을 목표필드에 원천값으로 채운다. 파일명이 없으면 손대지 않는다."""
    if not filename:
        return
    for target in targets:
        fields[target] = filename


def warn_unmatched(fields: dict, targets: Iterable[str], filename: Any, label: str) -> None:
    """파일명이 있었는데 값 파이프라인 뒤에 비었으면 경고한다.

    `regex_extract` 는 매칭이 없을 때 조용히 None 을 돌려준다. 파일명 규칙이 어긋난
    파일이 들어와도 파싱은 계속하되, 식별자가 비었다는 사실은 로그에 드러낸다.
    """
    if not filename:
        return
    empty = [t for t in targets if fields.get(t) in (None, "")]
    if empty:
        _log.warning(
            f"[custom_fields] {label}: 파일명에서 {empty} 를 만들지 못했습니다"
            f"(filename={filename}). 파일명 규칙과 transform 패턴을 확인하세요."
        )
