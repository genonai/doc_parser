# 파싱용 전처리기 v.2.2.0 (2026-06-02 Release)
#
# ── 이 파일에서 고칠 자리 ────────────────────────────────────────────────────
# 파싱 본체(로더·docling 배관·custom_fields·직렬화)는 facade/core/parser.py 에 있고
# 열어 볼 일이 없다. 새 문서 유형을 만났을 때 손대는 지점은 아래뿐이다.
#
#   ROUTES      새 **확장자**를 받을 때. 표에 한 줄 + route_* 메서드 하나.
#
# 새 **doc_type** 을 추가하는 것뿐이라면 코드가 아니라 custom_field_*.yaml 이 먼저다.
# 단독 실행:  python preprocessor.py <원천파일> --doc-type <타입> -o parsed.json
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
    # 새 확장자는 **이 표에 한 줄**이면 된다. route_* 를 새로 만들 필요는 없다 —
    # pre_source 가 원천을 이미 있는 포맷으로 바꿔 그 핸들러에 태우면 된다
    # (실측: .xml -> route_json, .tsv -> route_tabular. 레시피는 gitbook_doc/facade_hooks.md).
    # **먼저 여기 등록한 다음 시험한다.** 등록 전에 넣으면 캐치올이 받아 결과가 달라진다.
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
        """① 원천 로드 → ② pre_source → ③ 파싱 → ④ post_parse

        ①~③ 은 확장자마다 로드 시점이 달라 core 가 ROUTES 안에서 처리한다.
        (.json 은 custom_fields 가 매칭될 때만 읽히므로 여기서 미리 읽으면 동작이 바뀐다)
        """
        ext = self.resolve_ext(file_path)
        doc_type = self.resolve_doc_type(**kwargs)
        result = await self.run(request, file_path, **kwargs)
        return self.post_parse(ext, doc_type, result)

    def pre_source(self, ext, doc_type, data, work_dir=None):
        """[전처리] 파싱 직전. 원천을 파싱 입력으로 바꾼다.

        data 의 형은 ext 가 정하고, 같은 형으로 돌려준다.
          .json          dict / list (깨진 JSON 이면 str)   매핑 전
          .md .html      str                              내장 전처리(flatten 등) 전
          .xlsx .csv     dict[시트명, 2차원 행]             병합셀은 이미 펴진 상태
          그 밖           str(파일 경로)                    파생 파일은 work_dir 에
        건드릴 것이 없으면 data 를 **그대로** 돌려준다.
        엑셀은 pandas·polars DataFrame 이나 list[dict] 로 돌려줘도 된다.
        """
        return data

    def post_parse(self, ext, doc_type, result):
        """[후처리] 응답 확정 직전. 청킹으로 넘어가기 전 마지막 자리.

          result["elements"]   레코드/표 경로 산출 (list[dict])
          result["document"]   docling 경로 산출   (dict)
          result["metadata"]   문서 단위 메타      (dict)
        """
        return result


if __name__ == "__main__":
    DocumentProcessor.cli()
