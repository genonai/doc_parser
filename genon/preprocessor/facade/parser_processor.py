# 파싱용 전처리기 v.2.2.0 (2026-06-02 Release)
#
# ── 이 파일에서 고칠 자리 ────────────────────────────────────────────────────
# 파싱 본체(로더·docling 배관·custom_fields·직렬화)는 facade/core/parser.py 에 있고
# 열어 볼 일이 없다. 새 문서 유형을 만났을 때 손대는 지점은 아래뿐이다.
#
#   ROUTES      새 **확장자**를 받을 때. 표에 한 줄 + route_* 메서드 하나.
#
# 새 **doc_type** 을 추가하는 것뿐이라면 코드가 아니라 custom_field_*.yaml 이 먼저다.
# 어디까지 설정으로 되는지는 gitbook_doc/parser_processor.md 의 지원 매트릭스를 본다.
from __future__ import annotations

from fastapi import Request

# main.py 의 예외 핸들러가 이 이름으로 잡는다. core 가 던지는 것과 같은 클래스다.
from genon.preprocessor.facade.core.errors import GenosServiceException  # noqa: F401
from genon.preprocessor.facade.core.parser import ParserCore


class DocumentProcessor(ParserCore):
    """파싱 단계만 수행하고 결과를 JSON으로 반환하는 파사드.

    청킹/벡터 조합은 수행하지 않는다.
    IS_PARSER: main.py 가 이 프로세서가 /parser API 전용임을 식별하는 데 사용.
    """

    IS_PARSER: bool = True

    # 확장자 → 처리 메서드. 위에서부터 확장자가 맞는 첫 항목을 부른다.
    # 핸들러가 응답 dict 를 돌려주면 거기서 끝이고, **None 을 돌려주면 다음 후보로
    # 넘어간다**(폴스루). 순서에 의미가 있으므로 위아래를 바꾸지 않는다.
    #
    #   확장자              핸들러           폴스루 조건
    #   ------------------  ---------------  -----------------------------------------
    #   wav mp3 m4a         route_audio      없음
    #   csv xlsx xlsm       route_tabular    없음
    #   hwp hwpx hml        route_hwp        없음
    #   docx                route_docx       없음
    #   pdf html htm md     route_docling    .md 가 formats.md.processing_mode=text 일 때
    #   json                route_json       custom_fields 에 매칭 설정이 없을 때
    #   ppt pptx            route_ppt        없음 (PDF 변환 실패는 langchain 폴백으로 끝난다)
    #   (그 밖)             route_other      없음 — 캐치올
    #
    # 새 확장자는 이 표에 한 줄과 route_* 메서드 하나를 더한다.
    ROUTES = (
        ((".wav", ".mp3", ".m4a"), "route_audio"),
        ((".csv", ".xlsx", ".xlsm"), "route_tabular"),
        ((".hwp", ".hwpx", ".hml"), "route_hwp"),
        ((".docx",), "route_docx"),
        ((".pdf", ".html", ".htm", ".md"), "route_docling"),
        ((".json",), "route_json"),
        ((".ppt", ".pptx"), "route_ppt"),
        (None, "route_other"),  # 캐치올 — 반드시 마지막
    )

    async def __call__(self, request: Request, file_path: str, **kwargs) -> dict:
        """요청 진입점. 실제 처리는 core 가 한다(확장자 판정 → ROUTES → 파싱)."""
        return await self.run(request, file_path, **kwargs)
