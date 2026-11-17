# 파싱용 전처리기 — 파일 하나를 파싱해 JSON 으로 반환한다. 청킹은 하지 않는다.
# 수정가능: ROUTES, CONFIG_BY_DOC_TYPE, hook 메소드(config_by_condition·edit_input·edit_document·edit_output).
from typing import TYPE_CHECKING
from genon.preprocessor.processing.core import toolbox as tb  # noqa: F401
from genon.preprocessor.processing.core.errors import GenosServiceException  # noqa: F401
from genon.preprocessor.processing.core.parser import ParserCore

if TYPE_CHECKING:
    from docling_core.types.doc.document import DoclingDocument


class DocumentProcessor(ParserCore):
    """파싱 전용 전처리기. main.py 가 /parser 요청을 이 클래스로 보낸다."""

    IS_PARSER = True

    async def __call__(self, request, file_path, **kwargs):
        job = self._start_job(request, file_path, **kwargs)
        job.source = await self._call_edit_input(job)
        result = await self._call_route(job)
        return await self._call_edit_output(job, result)

    async def document_to_response(self, job, doc, clear_coordinates=False):
        # edit_document 는 enrich(LLM 호출)보다 앞에 둔다
        doc = await self._call_edit_document(job, doc)
        doc = await self.enrich(job, doc)
        return self.build_response(job, doc, clear_coordinates)

    async def records_to_response(self, job, records):
        return await self.describe_tables(job, records)

    # 확장자별 route. 위에서부터 맞춰 보고, route 가 None 을 반환하면 다음 줄로 넘어간다.
    # 새 route 는 맨 위에 추가한다. 예: ((".tsv",), "route_tabular")
    ROUTES = (
        ((".csv", ".xlsx", ".xlsm"),       "route_tabular"),
        ((".hwp", ".hwpx", ".hml"),        "route_hwp"),
        ((".docx",),                       "route_docx"),
        ((".pdf", ".html", ".htm", ".md"), "route_docling"),
        ((".json",),                       "route_json"),    # custom_fields 설정이 없으면 다음 route 로 넘김
        ((".ppt", ".pptx"),                "route_ppt"),
        (None,                             "route_other"),   # 나머지 전부. 항상 마지막
    )

    async def route_docling(self, job):
        if job.ext == ".md" and not self.md_uses_layout(job):
            return None
        prepared = await self.prepare_input(job)
        doc = self.parse_document(job, prepared)
        return await self.document_to_response(job, doc)

    async def route_tabular(self, job):
        sheets = await self.read_sheets(job)
        if self.has_sheet_mapping(job):
            return await self.records_to_response(job, await self.map_sheet_records(job, sheets))
        if self.uses_sheet_as_document(job):
            return await self.document_to_response(job, self.parse_sheets(job, sheets))
        return await self.records_to_response(job, self.sheets_to_records(job, sheets))

    async def route_json(self, job):
        if self.has_json_mapping(job):
            return await self.records_to_response(job, await self.map_records(job))
        if self.has_json_text_fields(job):
            return await self.document_to_response(job, await self.parse_json_text(job))
        return None

    async def route_hwp(self, job):
        return await self.document_to_response(job, self.parse_hwp(job))

    async def route_docx(self, job):
        return await self.document_to_response(job, self.parse_docx(job), clear_coordinates=True)

    async def route_ppt(self, job):
        doc = self.parse_ppt(job)
        if doc is None:
            return self.parse_plain(job)
        return await self.document_to_response(job, doc)

    async def route_other(self, job):
        doc = self.parse_text(job)
        if doc is None:
            return self.parse_plain(job)
        return await self.document_to_response(job, doc)

    # doc_type 별 설정. 키는 config.yaml 경로(점 표기) 또는 요청 파라미터 이름.
    # 우선순위: config.yaml < CONFIG_BY_DOC_TYPE < config_by_condition() < 요청 파라미터
    CONFIG_BY_DOC_TYPE = {
        # "press":    {"enrichment.table_description.enable": False},
        # "contract": {"ocr.ocr_mode": "force"},
    }

    def config_by_condition(self, job):
        """표로 정할 수 없는 조건부 설정. 빈 dict 면 바꾸지 않는다.

            if job.params.get("dept") == "IR":
                return {"enrichment.doc_summary.enable": True}
        """
        return {}

    # hook 메소드. 요청 파라미터가 필요하면 **kwargs 로 받고, 외부 API 호출은 async def 로 쓴다.
    def edit_input(self, ext, doc_type, data, work_dir=None, **kwargs):
        """파싱 전 입력 수정. 받은 것과 같은 타입으로 반환한다.

            .json        dict 또는 list (JSON 파싱 실패 시 str)
            .md .html    str (파일 내용)
            .xlsx .csv   {시트명: [[셀, ...], ...]}
            그 밖         str (파일 경로). 새 파일은 work_dir 에 만든다
        """
        return data

    def edit_document(self, job, doc: "DoclingDocument"):
        """파싱 뒤, LLM enrichment 전 문서 수정. document 형식 결과에서만 불린다.

            for item in doc.texts:
                if item.text.startswith("부칙"):
                    item.label = "section_header"
        """
        return doc

    def edit_output(self, ext, doc_type, result, **kwargs):
        """응답 직전 결과 수정. result 에는 "document"(dict) 또는 "elements"(list)와 "metadata" 가 있다.
        chunk 메타데이터는 tb.set_chunk_metadata(result, {...}) 로 넣는다.
        """
        return result


# 단독 실행(저장소 최상위에서):
#   python -m genon.preprocessor.activities.parse 계약서.pdf --doc-type contract -o parsed.json
if __name__ == "__main__":
    DocumentProcessor.cli()
