# 파싱용 전처리기 — 파일 하나를 파싱해 JSON 으로 반환한다. 청킹은 하지 않는다.
#
#   파일 -> _start_job -> edit_input -> ROUTES 라우팅 -> edit_output -> JSON
#   문서형 라우트는 중간에 edit_document 와 LLM enrichment 를 거친다.
#
# 결과 형식은 둘뿐이다. 한 응답에 둘 다 있으면 청커는 document 만 쓰므로 하나로 통일한다.
#   문서형 {"document": {...}}   pdf hwp docx ppt md html, 설정을 갖춘 json 과 엑셀
#   요소형 {"elements": [...]}   엑셀 행, JSON 레코드, 그 밖
#
# 구성(뒤로 갈수록 확장 지점). 1 처리 흐름  2 확장자별 라우팅  3 doc_type 별 설정
#                            4 훅 메소드  5 오버라이드  그리고 파일 끝 "파일 단독 실행"
from typing import TYPE_CHECKING

from genon.preprocessor.processing.core import toolbox as tb  # noqa: F401
# main.py 의 예외 핸들러가 이 이름으로 잡는다. core 가 던지는 것과 같은 클래스다.
from genon.preprocessor.processing.core.errors import GenosServiceException  # noqa: F401
from genon.preprocessor.processing.core.parser import ParserCore

if TYPE_CHECKING:  # 타입 힌트 전용. 실행 시에는 docling 을 불러오지 않는다
    from docling_core.types.doc.document import DoclingDocument


class DocumentProcessor(ParserCore):
    """파싱 전용 전처리기. main.py 가 /parser 요청을 이 클래스로 라우팅한다."""

    IS_PARSER = True   # True 여야 /parser 엔드포인트가 활성화된다

    # --- 1. 처리 흐름 ---
    #
    # 훅 메소드는 _call_* 가 부른다 — async def / def 를 모두 허용하고, **kwargs 를 선언한
    # 훅 메소드에만 요청 파라미터를 넘긴다. _call_* 는 설정 적용 순서도 맡으므로,
    # _ 로 시작하는 메소드는 오버라이드하지 않는다.

    async def __call__(self, request, file_path, **kwargs):
        """파서 진입점

        job 은 요청 컨텍스트다. 시그니처에 job 이 있는 메소드에서만 쓸 수 있다 —
        config_by_condition(job), edit_document(job, doc), 직접 구현한 route_*(job).
        edit_input·edit_output 의 **kwargs 에는 job 이 들어오지 않는다.
            job.ext        확장자(소문자)          job.doc_type  문서 유형(소문자)
            job.file_path  원본 파일 경로          job.source    실제로 파싱할 파일 경로
            job.params     요청 파라미터           job.config    요청 인자로 덧씌운 값(3 참조)
            job.notes      단계 간 공유 dict       job.temp_dir("접두")  임시 디렉터리(자동 삭제)

        job.source 는 언제나 파일 경로다. 확장자 별칭 사본이나 edit_input 이 만든 파생 파일이면
        원본과 다르고, 그때 원본 경로는 job.file_path 에 남는다. 훅이 dict 나 str 로 받는
        로드된 데이터는 훅의 data 인자이지 job.source 가 아니다.
        """
        job = self._start_job(request, file_path, **kwargs)  # 확장자 판별, doc_type 별 설정 적용
        job.source = await self._call_edit_input(job)        # edit_input() 호출
        result = await self._call_route(job)                 # ROUTES 에서 라우트 선택, 실행
        return await self._call_edit_output(job, result)     # edit_output() 호출

    async def document_to_response(self, job, doc, clear_coordinates=False):
        """문서를 응답 JSON 으로 변환한다. 문서형 라우트 5개가 공유한다.

        호출 순서 유지 필수 — edit_document 가 enrich 앞이라 LLM 호출 전에 표를 대상에서
        뺄 수 있다. 뒤에서 빼면 비용은 이미 치른 뒤다.
        """
        doc = await self._call_edit_document(job, doc)           # edit_document() 호출
        doc = await self.enrich(job, doc)                        # LLM enrichment: 표 설명, 이미지 설명, 항목 추출
        return self.build_response(job, doc, clear_coordinates)  # {"document": ..., "metadata": ...}

    async def records_to_response(self, job, records):
        """엑셀 행이나 JSON 레코드 목록을 응답 JSON 으로 변환한다. 1건이 청크 1개가 된다.

            content   검색 대상 본문(임베딩 텍스트)
            metadata  벡터 DB 메타데이터 컬럼. 임베딩 텍스트에는 포함되지 않는다
            category  ROW_CATEGORIES 에 있는 이름(tabular_row, custom_fields_row)이면
                      분할하지 않고 1건 = 1청크, 그 밖이면 chunk_size 기준으로 분할
        id, page, coordinates 는 tb.make_elements() 가 채운다.
        """
        return await self.describe_tables(job, records)   # 레코드 내 표에 설명 생성

    # --- 2. 확장자별 라우팅 ---
    #
    # 위에서부터 확장자를 맞춰 보고, 라우트가 None 을 반환하면 다음 줄로 폴백한다.
    # 새 확장자는 한 줄 추가한다 — .tsv 를 표로 다루려면 ((".tsv",), "route_tabular") 를
    # 맨 위에 넣고 edit_input 에서 표 형식으로 바꾼다.
    #
    # json 만 예외다. route_json 은 custom_fields 설정이 매칭될 때만 동작하고, 매칭이 없으면
    # 파일을 로드하지 않고 폴백한다. 설정 없이 코드로 처리하려면 자기 라우트를 맨 위에 둔다.
    #
    #   아래 ROUTES 표의 첫 항목으로 ((".json",), "route_json_ours") 를 추가한다.
    #   클래스 안에 ROUTES 를 중복 정의하지 않는다. 메소드는 아래 예제처럼 추가한다.
    #
    #   async def route_json_ours(self, job):
    #       import json
    #       from pathlib import Path
    #
    #       if job.doc_type != "ins_api":
    #           return None                         # 다른 문서 유형은 기본 라우트로 폴백
    #       payload = json.loads(tb.read_text_with_fallback(job.source))
    #       if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
    #           raise ValueError("items 배열이 필요합니다")
    #       picked = [x for x in payload["items"]
    #                 if isinstance(x, dict) and x.get("type") == "product"]
    #       md = tb.json_to_markdown(picked, html_renderer=tb.html_to_text())
    #       path = Path(job.temp_dir("route_json_ours")) / "products.md"
    #       path.write_text(md, encoding="utf-8")   # 요청 종료 시 임시 파일 자동 삭제
    #       prepared = {"path": str(path), "artifacts_from": None, "origin": job.source}
    #       doc = self.parse_document(job, prepared)
    #       return await self.document_to_response(job, doc)

    ROUTES = (
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
    # 설정 파일(parser_processor_config.yaml)은 모든 문서에 공통이다. doc_type 마다 다르게
    # 하려면 아래 표에 적는다. 키는 설정 파일 경로(점 표기)나 같은 뜻의 요청 파라미터
    # 이름(괄호)을 쓴다 — 둘 다 같게 동작한다. 켜기는 1(또는 True), 끄기는 0 이다.
    #   enrichment.table_description.enable  표 설명       (= table_desc)
    #   enrichment.image_description.enable  이미지 설명   (= img_desc)
    #   enrichment.doc_summary.enable        문서 요약     (= doc_summary)
    #   enrichment.toc.enable                목차 보강     (= toc)
    #   ocr.ocr_mode                         auto / force / disable
    #   pdf_output.keep                      변환 PDF 보존 (= keep_pdf)
    # 청크 크기 같은 청킹 설정은 chunking_processor.py 의 같은 표에 적는다.
    #
    # 기동 시 한 번 읽혀 굳는 설정(엔드포인트 주소, 프롬프트 등)은 여기 적어도 건너뛰고
    # 경고가 남는다 — 그 값은 설정 파일에서 바꾼다.
    # 우선순위(뒤가 우선). 설정 파일, CONFIG_BY_DOC_TYPE, config_by_condition(), 요청 파라미터.
    # 최종 값은 job.config 와 결과에 기록되어 추적할 수 있다.

    CONFIG_BY_DOC_TYPE = {
        # "press":    {"enrichment.table_description.enable": False},  # 표가 없어 불필요한 LLM 호출
        # "manual":   {"enrichment.image_description.enable": True},   # 이미지 설명이 중요
        # "contract": {"ocr.ocr_mode": "force"},                       # 스캔본이 많다
    }

    def config_by_condition(self, job):
        """[훅 메소드 0] 위 표로 안 되는 조건부 설정. 같은 형식의 dict 를 반환하고, 빈 dict 면 그대로다.

            if job.params.get("dept") == "IR":
                return {"enrichment.doc_summary.enable": True}
        """
        return {}

    # --- 4. 훅 메소드 ---
    #
    # 오버라이드하지 않으면 입력을 그대로 돌려준다(no-op). 공통 규칙 넷.
    #   1. 요청 파라미터가 필요하면 시그니처 끝에 **kwargs 를 붙인다. 자리 인자와 이름이 겹치는
    #      키는 빠진다. job 은 여기로 오지 않는다 — 1 의 job 설명을 본다.
    #   2. 외부 API 호출은 async def 로 쓴다. 동기 호출은 이벤트 루프를 막아 다른 요청까지 멈춘다.
    #   3. self 에 요청 상태를 담지 않는다 — 인스턴스 하나가 모든 요청을 받아 값이 섞인다
    #      (클래스 상수는 괜찮다). 훅 사이로 값을 넘겨야 하면 한 훅 안에서 끝낼 수 있는지 먼저 본다.
    #   4. 오류는 raise GenosServiceException("1", "건수가 맞지 않아 처리를 중단했습니다").
    #      부분 실패를 허용하려면 raise 하지 말고 그 건만 건너뛴 뒤 결과에 기록한다.

    def edit_input(self, ext, doc_type, data, work_dir=None, **kwargs):
        """[훅 메소드 1] 파싱 전 입력 전처리. 지원 형식으로 바꿔 두면 기본 라우트가 그대로 처리한다.

        확장자마다 들어오는 타입이 다르고, 같은 타입으로 반환한다.
            .json        dict 또는 list. JSON 파싱에 실패하면 str
            .md .html    str (파일 내용 전체)
            .xlsx .csv   {시트명: [[셀, ...], ...]}. 병합 셀은 각 칸에 같은 값이 채워져 있다
            그 밖         str (파일 경로). 새 파일은 work_dir 에 생성한다

            if ext == ".xlsx" and doc_type == "branch_list":
                return {name: rows[2:] for name, rows in data.items()}   # 상단 2행 제거
        """
        return data

    def edit_document(self, job, doc: "DoclingDocument"):
        """[훅 메소드 2] 파싱 후, LLM enrichment(표·이미지 설명, 문서 요약, custom_fields 추출) 전.

        문서형 라우트에서만 불린다. 엑셀 행이나 JSON 레코드처럼 요소형으로 나가는 경로에는
        DoclingDocument 가 없어 이 훅 메소드를 거치지 않는다 — 그쪽은 edit_output 에서 손본다.
        doc 은 DoclingDocument 객체다. edit_output 의 result["document"] 는 이 객체를
        enrichment 후 JSON 으로 직렬화한 dict 이므로 형태가 다르다.

        LLM 호출 전 문서 구조를 고칠 때 쓴다 — 헤딩 레벨 보정, 특정 표를 enrichment 대상에서
        제외. 주로 doc.texts, doc.tables, doc.iterate_items() 를 다룬다.

            for item in doc.texts:
                if item.text.startswith("부칙"):
                    item.label = "section_header"
            return doc
        """
        return doc

    def edit_output(self, ext, doc_type, result, **kwargs):
        """[훅 메소드 3] 응답 반환 직전. result 는 아래 중 해당하는 것을 담는다.
            result["document"]  문서형 결과 dict. 본문은 ["texts"][i]["text"]
            result["elements"]  요소형 결과 list[dict]. 1건은 {"content", "metadata"}
            result["metadata"]  문서 단위 메타데이터

        청크에 실을 메타는 tb.set_chunk_metadata() 로 넣는다. result["metadata"] 에 직접 쓰면
        이 API 응답에만 남고 청크에는 전달되지 않는다. 이 값은 그 문서의 모든 청크에 똑같이
        붙는다(청크마다 다른 값은 chunking_processor.py 의 edit_chunk 에서 info["fields"] 로).

            async def edit_output(self, ext, doc_type, result, **kwargs):
                emp_no = (result.get("metadata") or {}).get("EMP_NO")
                if emp_no:
                    tb.set_chunk_metadata(result, {"DEPT_NM": await fetch_dept(emp_no)})
                return result

        부분 실패는 실패 건만 빼고 결과에 기록한다. 이 훅에는 job 이 오지 않으므로 edit_input
        에서 넘긴 값을 여기서 읽을 수 없다 — 필요한 값은 result 안에서 찾는다.
        """
        return result

    # --- 5. 오버라이드 ---
    #
    # 기본 동작 자체를 바꿀 때 쓴다. 기능을 끄는 것이 목적이면 3 의 설정을 먼저 본다.
    # super() 를 부르지 않으면 기본 구현은 실행되지 않는다. 2 의 route_* 도 같은 방식이다.
    #   지원     1, 2 에 본문이 보이는 메소드(route_* 등), enrich, parse_document
    #            릴리스가 바뀌어도 이름과 인자를 유지한다
    #   비권장   그 밖의 core 메소드(prepare_input, read_sheets, describe_tables 등)
    #            오버라이드는 되지만 릴리스에서 바뀔 수 있다
    #
    #   async def enrich(self, job, doc):            # 표 설명을 자체 기준으로 검증
    #       doc = await super().enrich(job, doc)     # 기본 구현 먼저 호출
    #       for table in doc.tables:
    #           caption = (table.captions or [""])[0]
    #           if not all(k in caption for k in ("기준일", "연 %")):
    #               table.captions = []              # 기준 미달이면 설명만 제거
    #       return doc


# --- 파일 단독 실행 ---
#
# cli() 는 서버를 띄우지 않고 이 파일 하나를 돌린다. 고친 훅 메소드가 의도대로 도는지 문서
# 한 건으로 바로 볼 때 쓴다. 산출은 /parser 응답과 같은 JSON 이고, 저장 위치와 걸린 시간은
# stderr 로 알린다. 여기서 만든 parsed.json 을 chunking_processor 에 넘기면 청킹까지 이어진다.
# 경로 실행(python parser_processor.py)은 import 가 풀리지 않는다 — 저장소 최상위에서 -m 으로.
#
#   python -m genon.preprocessor.facade.parser_processor 계약서.pdf --doc-type contract -o parsed.json
#
#   --doc-type    custom_fields 설정과 훅 메소드 게이팅에 쓰인다. 훅 메소드를 doc_type 으로
#                 가르고 있으면 이것을 빼는 순간 그 코드가 통째로 안 돈다 — 사실상 필수다
#   --config      프로세서 설정 yaml 경로. 미지정 시 기본 경로를 찾는다
#   -o, --out     결과 JSON 경로. 생략하면 stdout 으로 나온다
#   --log-level   5 DEBUG / 4 INFO / 3 WARNING / 2 ERROR / 1 CRITICAL / 0 끔
#
# cli() 를 거치지 않고 클래스를 직접 불러도 된다. 첫 인자는 FastAPI Request 자리다. 파서는
# 쓰지 않아 None 이어도 되지만 청커로 옮기면 미디어 업로드에서 깨지므로 양쪽 다
# tb.mock_request() 로 적어 둔다. 설정 yaml 은 DocumentProcessor(config_path="...yaml").
#
#   result = asyncio.run(DocumentProcessor()(tb.mock_request(), "계약서.pdf", doc_type="contract"))
#   json.dump(result, open("parsed.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
if __name__ == "__main__":
    DocumentProcessor.cli()
