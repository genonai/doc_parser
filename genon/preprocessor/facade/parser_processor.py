# 파싱용 전처리기
#
# 파일 하나를 받아 파싱 후 JSON 으로 반환한다. 청킹은 하지 않는다.
#
# 처리 순서. 아래 __call__ 의 메소드 호출 순서와 같다.
#   파일 -> _start_job -> pre_parse -> 라우팅 -> post_parse -> JSON
#   문서형 라우트는 중간에 on_docling_document 를 거친다.
#
# 구성
#   1 처리 흐름          파이프라인 전체 호출 순서
#   2 확장자별 라우팅     새 확장자는 ROUTES 에 한 줄 추가
#   3 doc_type 별 설정   기능 on/off. 코드 수정보다 우선
#   4 훅 메소드          no-op 기본 구현을 채우는 메소드 3개
#   5 오버라이드          기본 구현 자체를 바꿀 때
#
# 결과 형식은 두 가지다.
#   문서형 {"document": {...}}   pdf hwp docx ppt md html, 설정을 갖춘 json 과 엑셀
#   행형   {"elements": [...]}   엑셀 행, JSON 레코드, 그 밖
# 한 응답에 둘 다 있으면 청커는 document 만 사용한다. 표는 행, 본문은 문단으로 내보내려면
# 한 형식으로 통일한다.
#
# CLI 실행 예시: python parser_processor.py 계약서.pdf --doc-type contract -o parsed.json
from typing import TYPE_CHECKING

from genon.preprocessor.facade.core import toolbox as tb  # noqa: F401
# main.py 의 예외 핸들러가 이 이름으로 잡는다. core 가 던지는 것과 같은 클래스다.
from genon.preprocessor.facade.core.errors import GenosServiceException  # noqa: F401
from genon.preprocessor.facade.core.parser import ParserCore

if TYPE_CHECKING:  # 타입 힌트 전용. 실행 시에는 docling 을 불러오지 않는다
    from docling_core.types.doc.document import DoclingDocument


class DocumentProcessor(ParserCore):
    """파싱 전용 전처리기. main.py 가 /parser 요청을 이 클래스로 라우팅한다."""

    IS_PARSER = True   # True 여야 /parser 엔드포인트가 활성화된다

    # --- 1. 처리 흐름 ---
    #
    # 훅 메소드는 _call_pre_parse, _call_post_parse 로 호출한다. _call_* 는 async def / def 를
    # 모두 허용하고, **kwargs 를 선언한 경우에만 요청 파라미터를 넘긴다. 그래서 훅 메소드는
    # 필요한 만큼만 선언해 쓰면 된다.
    # _ 로 시작하는 메소드는 호출 규약과 설정 적용 순서를 담당하므로 오버라이드하지 않는다.

    async def __call__(self, request, file_path, **kwargs):
        """파서 진입점

        job 은 요청 컨텍스트 객체다.
            job.ext        확장자(소문자)          job.doc_type  문서 유형(소문자)
            job.file_path  원본 파일 경로          job.source    현재 처리할 입력
            job.params     요청 파라미터           job.config    적용된 설정(3 참조)
            job.work_dir   임시 디렉터리(자동 삭제) job.notes     단계 간 공유 dict

        job.source 는 확장자에 따라 파일 경로가 아니라 로드된 데이터일 수 있다. 엑셀은
        2차원 셀 값 배열, json 은 dict, md 와 html 은 str 이다. 원본 경로는 job.file_path 를 쓴다.
        훅 메소드에서는 job 을 kwargs["job"] 으로 꺼낸다. 훅 메소드 시그니처에는 없다.
        """
        job = self._start_job(request, file_path, **kwargs)  # 확장자 판별, doc_type 별 설정 적용
        job.source = await self._call_pre_parse(job)         # pre_parse() 호출
        result = await self._call_route(job)                 # ROUTES 에서 라우트 선택, 실행
        return await self._call_post_parse(job, result)      # post_parse() 호출

    async def document_to_response(self, job, doc, clear_coordinates=False):
        """문서를 응답 JSON 으로 변환한다. 문서형 라우트 5개가 공유한다.

        메소드 호출 순서 유지 필수
        """
        doc = await self._call_on_docling_document(job, doc)  # on_docling_document() 호출
        doc = await self.enrich(job, doc)                     # LLM enrichment: 표 설명, 이미지 설명, 항목 추출
        return self.build_response(job, doc, clear_coordinates)   # {"document": ..., "metadata": ...}

    async def records_to_response(self, job, records):
        """엑셀 행이나 JSON 레코드 목록을 응답 JSON 으로 변환한다.

        records 1건이 청크 1개가 된다. 키는 셋이다.
            content   검색 대상 본문(임베딩 텍스트)
            metadata  벡터 DB 메타데이터 컬럼. 임베딩 텍스트에는 포함되지 않는다
            category  청커의 분할 방식을 결정한다. ROW_CATEGORIES 에 있는
                      이름(tabular_row, custom_fields_row)이면 분할하지 않고 1건 = 1청크,
                      그 밖이면 chunk_size 기준으로 분할한다
        id, page, coordinates 는 tb.make_elements() 가 채운다.
        """
        return await self.describe_tables(job, records)   # 레코드 내 표에 설명 생성

    # --- 2. 확장자별 라우팅 ---
    #
    # 확장자로 라우트 메소드를 선택한다.
    # 위에서부터 매칭하며, 라우트가 None 을 반환하면 다음 항목으로 폴백한다.
    # 마지막 항목(None)은 catch-all 이므로 항상 맨 아래에 둔다.
    #
    # 새 확장자 추가 방법은 아래와 같다.
    # 예를 들어 .tsv 를 표로 다루려면
    # ((".tsv",), "route_tabular") 를 맨 위에 넣고 pre_parse 에서 표 형식으로 변환한다.
    #
    # json 은 예외다. 아래 route_json 은 custom_fields 설정이 매칭될 때만 동작한다.
    # 매칭이 없으면 파일을 로드하지 않고 폴백한다. 설정 없이 json 을 코드로 처리하려면
    # pre_parse 가 아니라 ROUTES 에 라우트를 추가한다.
    #
    #   ROUTES = (((".json",), "route_json_ours"),) + DocumentProcessor.ROUTES
    #
    #   async def route_json_ours(self, job):
    #       if job.doc_type != "ins_api":
    #           return None                                  # 다른 doc_type 은 기본 라우트로 폴백
    #       picked = [x for x in job.source["items"] if x["type"] == "product"]
    #       md = tb.json_to_markdown(picked, html_renderer=tb.html_to_text())
    #       doc = self.parse_document(job, md, ext=".md")     # 마크다운으로 파싱
    #       return await self.document_to_response(job, doc)

    ROUTES = (
        ((".wav", ".mp3", ".m4a"),         "route_audio"),    # 음성 전사
        ((".csv", ".xlsx", ".xlsm"),       "route_tabular"),  # 표 파일
        ((".hwp", ".hwpx", ".hml"),        "route_hwp"),
        ((".docx",),                       "route_docx"),
        ((".pdf", ".html", ".htm", ".md"), "route_docling"),  # 레이아웃 분석 대상
        ((".json",),                       "route_json"),
        ((".ppt", ".pptx"),                "route_ppt"),
        (None,                             "route_other"),    # catch-all. 항상 마지막
    )

    async def route_docling(self, job):
        """pdf, html, htm, md. 레이아웃과 표 구조를 분석해 문서로 변환한다."""
        if job.ext == ".md" and not self.md_uses_layout(job):
            return None                              # text 모드면 route_other 로 폴백
        prepared = await self.prepare_input(job)     # HTML 정제, 헤더 메타데이터 분리
        doc = self.parse_document(job, prepared)     # 레이아웃 분석, OCR, 표 구조 인식
        return await self.document_to_response(job, doc)

    async def route_tabular(self, job):
        """csv, xlsx, xlsm. 행 매핑 설정이 있으면 레코드로, 없으면 문서로 변환한다."""
        sheets = await self.read_sheets(job)         # {시트명: 셀 값 배열}
        if self.has_sheet_mapping(job):
            return await self.records_to_response(job, await self.map_sheet_records(job, sheets))
        if self.uses_sheet_as_document(job):         # 시트를 문서로 처리하는 설정
            return await self.document_to_response(job, self.parse_sheets(job, sheets))
        return await self.records_to_response(job, self.sheets_to_records(job, sheets))

    async def route_json(self, job):
        """json. 레코드 매핑 우선, text_fields 설정이 있으면 문서로, 둘 다 없으면 폴백한다."""
        if self.has_json_mapping(job):
            return await self.records_to_response(job, await self.map_records(job))
        if self.has_json_text_fields(job):
            return await self.document_to_response(job, await self.parse_json_text(job))
        return None                                  # route_other 로 폴백

    async def route_hwp(self, job):     # hwp, hwpx, hml
        return await self.document_to_response(job, self.parse_hwp(job))

    async def route_docx(self, job):    # docx
        return await self.document_to_response(job, self.parse_docx(job), clear_coordinates=True)

    async def route_ppt(self, job):
        """ppt, pptx. PDF 변환 후 분석하고, 변환 실패 시 텍스트만 추출한다."""
        doc = self.parse_ppt(job)
        if doc is None:
            return self.parse_plain(job)
        return await self.document_to_response(job, doc)

    async def route_other(self, job):
        """catch-all. 본문이 텍스트면 docling 으로 파싱하고, doc·이미지 등은 텍스트만 추출한다."""
        doc = self.parse_text(job)                   # 텍스트가 아니면 None
        if doc is None:
            return self.parse_plain(job)
        return await self.document_to_response(job, doc)

    # --- 3. doc_type 별 설정 ---
    #
    # 설정 파일(parser_processor_config.yaml)은 모든 문서에 공통으로 적용된다. doc_type 마다
    # 다르게 하려면 아래 표에 적는다. 키는 요청 파라미터 이름이고, 값을 적으면 요청이 그 값을
    # 보낸 것과 같게 동작한다. 설정 파일 경로(점 표기)는 받지 않는다 — 건너뛰고 경고를 남긴다.
    #
    # 파서에서 쓰는 주요 키. 켜기는 1, 끄기는 0 이다.
    #   table_desc   표 설명          img_desc     이미지 설명
    #   chart_desc   차트 설명        doc_summary  문서 요약
    #   toc          목차 보강        keep_pdf     변환 PDF 보존
    # 청크 크기 같은 청킹 설정은 여기가 아니라 chunking_processor.py 의 같은 표에 적는다.
    #
    # 설정 우선순위(뒤가 우선): 설정 파일, CONFIG_BY_DOC_TYPE, config_by_condition(), 요청 파라미터
    # 최종 값은 job.config 와 결과에 기록되어 추적할 수 있다.

    CONFIG_BY_DOC_TYPE = {
        # "press":  {"table_desc": 0},                 # 표가 없어 불필요한 LLM 호출
        # "manual": {"img_desc": 1, "chart_desc": 1},  # 이미지와 차트 설명이 중요
        # "report": {"doc_summary": 1},                # 문서 요약을 붙인다
    }

    def config_by_condition(self, job):
        """[훅 메소드 0] 조건부 설정. 위 표로 안 되는 경우 문서 내용이나 요청 파라미터로 결정한다.

        위 표와 같은 형식의 dict 를 반환하고,
        빈 dict 면 아무것도 바뀌지 않는다.

            if job.params.get("dept") == "IR":
                return {"doc_summary": 1}
        """
        return {}

    # --- 4. 훅 메소드 ---
    #
    # 기본 구현은 입력을 그대로 반환(no-op)하므로 오버라이드하지 않으면
    # 결과가 바뀌지 않는다.
    #
    # 공통 규칙
    #   1. 요청 파라미터가 필요하면 시그니처 마지막에 **kwargs 를 추가한다. job 도 kwargs 로 전달된다.
    #   2. 외부 API 호출은 async def 로 작성한다. 동기 호출은 이벤트 루프를 블로킹해
    #      다른 요청까지 멈춘다.
    #   3. self 에 요청 상태를 저장하지 않는다. 인스턴스 하나가 모든 요청을 공유하므로 동시 요청 간
    #      값이 섞인다. 단계 간 전달은 kwargs["job"].notes 를 쓴다. 클래스 상수는 괜찮다.
    #   4. 오류는 GenosServiceException 을 raise 한다. 부분 실패를 허용하려면 raise 하지 말고
    #      해당 건을 skip 한 뒤 결과에 기록한다.
    #        raise GenosServiceException("1", "건수가 맞지 않아 처리를 중단했습니다")

    def pre_parse(self, ext, doc_type, data, work_dir=None, **kwargs):
        """[훅 메소드 1] 파싱 전 입력 전처리.

        입력 형식이 달라 기본 처리가 안 될 때 지원 형식으로 변환하면 기본 라우트가
        그대로 처리한다. 입력 타입은 확장자마다 다르며 같은 타입으로 반환한다.
            .json        dict 또는 list. JSON 파싱에 실패하면 str
            .md .html    str (파일 내용 전체)
            .xlsx .csv   {시트명: [[셀, ...], ...]}. 시트별 2차원 셀 값 배열이며
                         병합 셀은 각 칸에 같은 값이 채워져 있다
            그 밖         str (파일 경로). 새 파일은 work_dir 에 생성한다

            if ext == ".xlsx" and doc_type == "branch_list":
                return {name: rows[2:] for name, rows in data.items()}   # 상단 2행 제거
        """
        return data

    def on_docling_document(self, job, doc: "DoclingDocument"):
        """[훅 메소드 2] 파싱 후, LLM enrichment 전에 호출된다.

        doc 은 DoclingDocument 객체다. post_parse 의 result["document"] 는 이 객체를
        enrichment 후 JSON 으로 직렬화한 dict 이므로 형태가 다르다.

        enrichment 는 표 설명, 이미지 설명, 문서 요약, custom_fields 항목 추출을 말한다.
        LLM 호출 전 문서 구조를 수정할 때 쓴다.
        헤딩 레벨 보정, 특정 표를 enrichment 대상에서 제외하는 경우가 해당된다.
        주로 doc.texts, doc.tables, doc.iterate_items() 를 다룬다.

            for item in doc.texts:
                if item.text.startswith("부칙"):
                    item.label = "section_header"
            return doc
        """
        return doc

    def post_parse(self, ext, doc_type, result, **kwargs):
        """[훅 메소드 3] 파싱 결과 후처리.

        result 구조
            result["document"]  문서형 결과 dict. 본문은 ["texts"][i]["text"]
            result["elements"]  행형 결과 list[dict]. 1건은 {"content", "metadata"}
            result["metadata"]  문서 단위 메타데이터

        청크 메타데이터는 tb.set_chunk_metadata() 로 설정한다. result["metadata"] 에 직접
        쓰면 이 API 응답에만 포함되고 청크에는 전달되지 않는다. 이 값은 해당 문서의
        모든 청크에 동일하게 적용된다. 청크별 값은 chunking_processor.py 의 on_chunk 에서 info["fields"] 로 넣는다.

            async def post_parse(self, ext, doc_type, result, **kwargs):
                emp_no = (result.get("metadata") or {}).get("EMP_NO")
                if emp_no:
                    tb.set_chunk_metadata(result, {"DEPT_NM": await fetch_dept(emp_no)})
                return result

        부분 실패를 허용할 때는 실패 건만 제외하고 결과에 기록한다.
        pre_parse 에서 kwargs["job"].notes 에 저장한 값을 여기서 읽어 쓸 수 있다.
        """
        return result

    # --- 5. 오버라이드 ---
    #
    # 기본 동작을 바꾸려면 메소드를 오버라이드한다.
    # 기능 비활성화가 목적이면 3 의 설정을 우선 고려한다.
    #
    #   지원     1, 2 에 본문이 보이는 메소드(route_* 등), enrich, parse_document
    #            릴리스가 바뀌어도 이름과 인자를 유지한다
    #   비권장   그 밖의 core 메소드(prepare_input, read_sheets, describe_tables 등)
    #            오버라이드는 가능하지만 릴리스에서 바뀔 수 있다
    #
    #   async def enrich(self, job, doc):            # 표 설명을 자체 기준으로 검증
    #       doc = await super().enrich(job, doc)     # 기본 구현 먼저 호출
    #       for table in doc.tables:
    #           caption = (table.captions or [""])[0]
    #           if not all(k in caption for k in ("기준일", "연 %")):
    #               table.captions = []              # 기준 미달이면 설명만 제거
    #       return doc
    #
    # 2 의 route_* 도 같은 방식으로 오버라이드한다. super() 를 호출하지 않으면 기본 구현은
    # 실행되지 않는다.


if __name__ == "__main__":
    DocumentProcessor.cli()
