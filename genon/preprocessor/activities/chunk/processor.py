# 청킹용 전처리기 — parse 단계 결과를 받아 chunk 로 나눈다.
# 수정가능: GenOSVectorMeta, GenosSmartChunker 상수, ROW_CATEGORIES, CONFIG_BY_DOC_TYPE,
# hook 메소드(config_by_condition·edit_input·edit_chunk·edit_output).
from typing import Optional
from pydantic import BaseModel
from genon.preprocessor.processing.chunking import smart_chunker as sc
from genon.preprocessor.processing.core import toolbox as tb  # noqa: F401
from genon.preprocessor.processing.core.chunker import ChunkerCore
from genon.preprocessor.processing.core.errors import GenosServiceException  # noqa: F401


class GenOSVectorMeta(BaseModel):
    """chunk 한 건 = vector DB 한 행. 선언하지 않은 키도 그대로 실린다."""

    class Config:
        extra = 'allow'
    text: str = None
    n_char: int = None
    n_word: int = None
    n_line: int = None
    e_page: int = None
    i_page: int = None
    i_chunk_on_page: int = None
    n_chunk_of_page: int = None
    i_chunk_on_doc: int = None
    n_chunk_of_doc: int = None
    n_page: int = None
    reg_date: str = None
    chunk_bboxes: str = None
    media_files: str = None
    title: str = None
    created_date: int = None
    appendix: str = None
    file_path: Optional[str] = None
    guardrail_categories: Optional[list] = None
    has_table: bool = False
    table_refs: Optional[str] = None
    table_split_index: Optional[int] = None
    table_split_total: Optional[int] = None


class GenosSmartChunker(sc.SmartChunkerBase):
    PICTURE_ANNOTATION_TEXT = True          # 그림 설명을 chunk 본문에 넣음
    TABLE_DESCRIPTION_MODE = "prefix_only"  # 표 설명을 표 앞에만 붙임
    CHUNK_HEADER_SEP = " > "                # heading 경로의 부모-자식 구분자
    CHUNK_PATH_SEP = " | "                  # 형제 경로 구분자
    CHUNK_PATH_MAX_LEAVES = 5               # 경로 최대 개수. 넘으면 "… 외 N개"
    CHUNK_HEADER_PREFIX = "HEADER: "        # 경로 앞 라벨. 빈 문자열이면 경로만


class DocumentProcessor(ChunkerCore):
    """청킹 전용 전처리기. main.py 가 /chunker 요청을 이 클래스로 보낸다."""

    IS_CHUNKER = True
    VECTOR_META = GenOSVectorMeta
    CHUNKER = GenosSmartChunker

    # 나누지 않고 1건 = chunk 1개로 만드는 element category. 예: | {"my_row"}
    ROW_CATEGORIES = ChunkerCore.ROW_CATEGORIES

    async def __call__(self, request, file_path="", **kwargs):
        job = self._start_job(request, file_path, **kwargs)
        job.data = await self._call_edit_input(job)
        chunks = await self.split(job)
        vector_metas = await self.chunks_to_vector_metas(job, chunks)
        return await self._call_edit_output(job, vector_metas)

    async def split(self, job):
        if job.kind == "docling":
            return self.split_document(job)
        return self.split_records(job)

    async def chunks_to_vector_metas(self, job, chunks, converted_pdf_path=None):
        # 순서 유지: 마스킹은 정제보다 먼저, 반복문은 start_chunk_loop/finish_chunk_loop 로 감싼 채 둔다
        vector_metas = []
        for chunk in self.start_chunk_loop(job, chunks, converted_pdf_path):
            text = self.build_chunk_text(job, chunk)
            text, drop = await self._call_edit_chunk(job, chunk, text)
            if drop:
                continue
            self.collect_chunk_variants(job, chunk, text)
            text = self.mask_sensitive(job, text)
            text = self.clean_text(job, text)
            vector_metas.append(self.chunk_to_vector_meta(job, chunk, text))
        return await self.finish_chunk_loop(job, vector_metas)

    # doc_type 별 설정. 키는 config.yaml 경로(점 표기) 또는 요청 파라미터 이름.
    # 우선순위: config.yaml < CONFIG_BY_DOC_TYPE < config_by_condition() < 요청 파라미터
    CONFIG_BY_DOC_TYPE = {
        # "faq":    {"chunking.chunk_size": 500},
        # "manual": {"chunking.chunk_mode": "split_only", "chunking.chunk_size": 2000},
    }

    def config_by_condition(self, job):
        """표로 정할 수 없는 조건부 설정. 빈 dict 면 바꾸지 않는다.

            if job.params.get("dept") == "INS":
                return {"chunking.chunk_size": 800}
        """
        return {}

    # hook 메소드. 요청 파라미터가 필요하면 **kwargs 로 받고, 외부 API 호출은 async def 로 쓴다.
    def edit_input(self, kind, data, **kwargs):
        """청킹 전 입력 수정. kind="parse" 면 list[dict], kind="docling" 이면 dict(본문은 data["texts"][i]["text"])."""
        return data

    def edit_chunk(self, text, info, **kwargs):
        """chunk 마다 불린다. str 이면 본문 교체, None 이면 그대로, tb.DROP 이면 제외.

        info: kind("docling"|"row"|"text"), page, index, headings, metadata(읽기 전용),
              fields(넣은 값이 vector_meta 필드로 실림)

            if "상담직원용" in text:
                return tb.DROP
            info["fields"]["RISK"] = "high"
        """
        return None

    def edit_output(self, vector_metas, **kwargs):
        """응답 직전 vector_meta 목록 수정. 개수나 본문을 바꿨으면 tb.refresh_stats(vector_metas) 를 부른다."""
        return vector_metas


# 단독 실행(저장소 최상위에서). 입력은 parse 단계 결과 JSON 이다.
#   python -m genon.preprocessor.activities.chunk parsed.json --doc-type contract -o chunks.json
if __name__ == "__main__":
    DocumentProcessor.cli()
