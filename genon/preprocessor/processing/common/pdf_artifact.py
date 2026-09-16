"""변환 PDF(뷰어용 아티팩트)를 어디에 두고 남길지 정한다.

## 배경

HWP/PPT/DOC/DOCX/이미지는 PDF 로 바꿔야 파싱된다. 그 산출 PDF 는 파싱이 끝나도
버려지지 않았다 — GenOS 문서 뷰어가 원본 옆의 `<원본이름>.pdf` 를 직접 참조하기
때문이다(convert_processor 가 "미리보기용 PDF 아티팩트" 라고 부르는 그것).

뷰잉을 쓰지 않는 현장에서는 이 파일이 원천 디렉터리에 계속 쌓이고, 같은 이름의
기존 PDF 를 덮어쓰기까지 한다. 그래서 남길지를 설정으로 고르게 한다. **기본은
남기지 않는다** — 뷰잉을 쓰는 현장이 명시적으로 켠다.

텍스트 원천(.txt/.json 등)은 애초에 PDF 를 만들지 않으므로 이 정책과 무관하다.
docling 이 직접 읽는다.

## 위치를 옮기는 방법

변환 backend 3종(libreoffice/rhwp/pdf_sdk)은 모두 산출을 `입력경로.with_suffix('.pdf')`
에 쓴다. 출력 폴더를 지정하는 인자가 없으므로, **입력 사본을 목적지에 두고 거기서
변환한다.** 그러면 산출 PDF 도 거기 생기고 원본 폴더는 손대지 않는다. 입력 사본은
아티팩트가 아니므로 변환 후 지운다.

`dir` 이 비면(기본) 복사 없이 원본 경로 그대로 변환한다 — 현행 경로와 같다.
"""
from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from typing import Callable, Optional

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PdfArtifactOptions:
    """`pdf_output` yaml 설정.

    keep: 변환 PDF 를 남길지. False(기본)면 요청이 끝날 때 지운다.
    dir:  변환 PDF 를 만들 폴더. 빈 값(기본)이면 원본 파일과 같은 폴더.
    """

    keep: bool = False
    dir: str = ""

    @classmethod
    def from_config(cls, cfg) -> "PdfArtifactOptions":
        cfg = cfg if isinstance(cfg, dict) else {}
        return cls(
            keep=bool(cfg.get("keep")),
            dir=str(cfg.get("dir") or "").strip(),
        )

    def for_request(self, keep: Optional[bool] = None, dir: Optional[str] = None) -> "PdfArtifactPolicy":
        """요청 스코프 정책을 만든다. 인자가 주어지면 yaml 값을 덮어쓴다(kwargs 우선)."""
        return PdfArtifactPolicy(
            keep=self.keep if keep is None else keep,
            dir=self.dir if dir is None else dir,
        )


@dataclass
class PdfArtifactPolicy:
    """요청 하나 동안의 변환 PDF 정책. 만든 파일을 기억했다가 요청 끝에 정리한다.

    프로세서는 싱글턴이라 이 객체가 요청별로 만들어져야 한다. parser 의 `run()` 이
    kwargs 에 실어 변환 지점들로 내려보내고, finally 에서 `cleanup()` 을 부른다.
    """

    keep: bool = False
    dir: str = ""
    # 이 요청이 만든 산출 PDF. keep=False 면 cleanup 이 지운다.
    produced: list[str] = field(default_factory=list)

    def convert(
        self, file_path: str, convert_fn: Callable[[str], Optional[str]]
    ) -> Optional[str]:
        """`convert_fn` 을 정책이 정한 자리에서 실행하고 산출물을 기록한다."""
        target_dir = self.dir or None
        expected = self.expected_pdf_path(file_path)
        # 이미 있던 PDF 는 이 요청이 만든 것이 아니다. 덮어쓰더라도 지우지는 않는다
        # (뷰어가 보던 파일을 요청 하나가 없애 버리는 사고를 막는다).
        preexisting = bool(expected) and os.path.exists(expected)

        produced = convert_into(file_path, convert_fn, target_dir)

        if produced and not preexisting:
            self.produced.append(produced)
        elif produced and preexisting:
            _log.info(
                f"[pdf_artifact] 기존 PDF 를 덮어썼습니다 — 정리 대상에서 제외합니다: {produced}"
            )
        return produced

    def expected_pdf_path(self, file_path: str) -> str:
        """변환기가 만들 PDF 의 예상 경로. 변환 실패 시 기존 산출을 찾는 데도 쓴다."""
        base = os.path.splitext(os.path.basename(file_path))[0] + ".pdf"
        parent = self.dir or os.path.dirname(file_path)
        return os.path.join(parent, base)

    def cleanup(self) -> None:
        """keep=False 면 이 요청이 만든 PDF 를 지운다."""
        if self.keep:
            self.produced.clear()
            return
        for path in self.produced:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError as exc:
                _log.warning(f"[pdf_artifact] 변환 PDF 정리 실패: {path} ({exc})")
        self.produced.clear()


def convert_into(
    file_path: str,
    convert: Callable[[str], Optional[str]],
    target_dir: Optional[str],
) -> Optional[str]:
    """`convert` 를 target_dir 안에서 실행해 산출 PDF 가 거기 생기게 한다.

    target_dir 이 None/빈 값이면 원본 경로 그대로 변환한다(복사 없음 — 현행 동작).
    convert 는 입력 경로 하나를 받아 산출 PDF 경로 또는 None 을 돌려주는 호출체다.
    """
    if not target_dir:
        return convert(file_path)

    os.makedirs(target_dir, exist_ok=True)
    staged = os.path.join(target_dir, os.path.basename(file_path))
    same_as_source = os.path.abspath(staged) == os.path.abspath(file_path)
    if not same_as_source:
        shutil.copy2(file_path, staged)

    produced: Optional[str] = None
    try:
        produced = convert(staged)
    finally:
        # 입력 사본만 지운다. 산출 PDF 는 아티팩트이므로 남긴다. 입력이 이미 .pdf 여서
        # 사본과 산출물이 같은 경로면 지우지 않는다(아티팩트를 지우게 된다).
        produced_is_staged = (
            produced is not None
            and os.path.abspath(produced) == os.path.abspath(staged)
        )
        if not same_as_source and not produced_is_staged and os.path.exists(staged):
            try:
                os.remove(staged)
            except OSError as exc:
                _log.warning(f"[pdf_artifact] 입력 사본 정리 실패: {staged} ({exc})")
    return produced
