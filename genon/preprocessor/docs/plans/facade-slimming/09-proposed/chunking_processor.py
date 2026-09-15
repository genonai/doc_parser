# 청킹용 전처리기
#
# 파서 결과를 받아 청킹한다. 원본 문서를 로드하거나 분석하지 않는다.
#
# 처리 순서. 아래 __call__ 의 메소드 호출 순서와 같다.
#   파서 결과 -> _start_job -> pre_chunk -> 청크 분할 -> vector_meta 생성 -> post_chunk -> vector_meta 목록
#   청크가 생성될 때마다 on_chunk 를 호출한다.
#
# 용어
#   chunk        분할 결과 1건. 입력 형식과 관계없이 같은 필드를 갖는다(chunk_to_vector_meta 참조)
#   vector_meta  VECTOR_META(기본 GenOSVectorMeta) 인스턴스 1건. 벡터 DB 1행으로 적재된다
#
# 구성
#   1 처리 흐름          파이프라인 전체 호출 순서
#   2 doc_type 별 설정   청크 크기 등 doc_type 별 설정. 코드 수정보다 우선
#   3 훅 메소드          no-op 기본 구현을 채우는 메소드 3개
#   4 오버라이드          기본 구현 자체를 바꿀 때
#
# CLI 실행 예시: python chunking_processor.py parsed.json -o chunks.json
from pydantic import BaseModel

from genon.preprocessor.facade.chunking import smart_chunker as sc
from genon.preprocessor.facade.core import toolbox as tb
from genon.preprocessor.facade.core.chunker import ChunkerCore
from genon.preprocessor.facade.core.errors import GenosServiceException


class GenOSVectorMeta(BaseModel):
    """청크 스키마. 인스턴스 1건(vector_meta)이 벡터 DB 1행이 된다.

    텍스트와 메타데이터를 함께 담는다.
    메타데이터 컬럼을 추가하려면 필드를 선언하고 값은 on_chunk 의 info["fields"] 로 넣는다.
    extra="allow" 라 미선언 필드도 저장되지만, 가독성을 위해 여기에 선언하는 것을 권장한다.
    """

    class Config:
        extra = "allow"

    text: str = None
    # 기존 필드 그대로 (n_char, i_page, title, appendix, has_table 등)


class GenosSmartChunker(sc.SmartChunkerBase):
    """청킹 옵션. 구현은 facade/chunking/smart_chunker.py 에 있다."""

    PICTURE_ANNOTATION_TEXT = True          # 이미지 설명을 청크 텍스트에 포함
    TABLE_DESCRIPTION_MODE = "prefix_only"  # 표 설명은 표 청크 앞에만 추가. full 이면 본문에도 추가
    CHUNK_HEADER_PREFIX = "HEADER: "        # 청크 헤더 접두어. 빈 문자열이면 헤딩 경로만 추가
    CHUNK_HEADER_SEP = " > "                # 헤딩 계층 구분자
    CHUNK_PATH_SEP = " | "                  # 같은 레벨 헤딩 구분자
    CHUNK_PATH_MAX_LEAVES = 5               # 헤딩 경로 최대 개수. 초과분은 생략


class DocumentProcessor(ChunkerCore):
    """청킹 전용 전처리기. main.py 가 /chunker 요청을 이 클래스로 라우팅한다."""

    IS_CHUNKER = True                 # True 여야 /chunker 엔드포인트가 활성화된다
    VECTOR_META = GenOSVectorMeta
    CHUNKER = GenosSmartChunker

    # 분할하지 않고 1건 = 1청크로 처리할 element category. 엑셀 행과 JSON 레코드가
    # 해당된다. 자체 파서가 다른 category 를 쓰면 여기에 추가한다. 예: | {"my_row"}
    ROW_CATEGORIES = ChunkerCore.ROW_CATEGORIES

    # --- 1. 처리 흐름 ---
    #
    # 훅 메소드는 _call_pre_chunk, _call_on_chunk, _call_post_chunk 로 호출한다. _call_* 는
    # async def / def 를 모두 허용하고, **kwargs 를 선언한 경우에만 요청 파라미터를 넘긴다.
    # 그래서 훅 메소드는 필요한 만큼만 선언해 쓰면 된다.
    # _ 로 시작하는 메소드는 호출 규약과 설정 적용 순서를 담당하므로 오버라이드하지 않는다.

    async def __call__(self, request, file_path="", **kwargs):
        """청커 진입점

        job 은 파서의 job 과 다른 객체이며 필드도 다르다.
            job.kind      "docling" 은 문서형, "parse" 는 요소형(엑셀 행, JSON 레코드, 평문)
            job.data      파서 결과          job.doc_type  문서 유형
            job.metadata  문서 단위 메타데이터 job.params    요청 파라미터
            job.config    적용된 설정(2 참조) job.notes     단계 간 공유 dict

        훅 메소드에서는 job 을 kwargs["job"] 으로 꺼낸다.
        job.kind 와 on_chunk 의 info["kind"] 는 값이 다르다.
            "docling" -> "docling",  "parse" -> "row"(행, 레코드) 또는 "text"(그 밖)
        """
        job = self._start_job(request, file_path, **kwargs)            # 입력 형식 판별, doc_type 별 설정 적용
        job.data = await self._call_pre_chunk(job)                     # pre_chunk() 호출
        chunks = await self.split(job)                                 # 청크 분할
        vector_metas = await self.chunks_to_vector_metas(job, chunks)  # chunk 를 vector_meta 로 변환
        return await self._call_post_chunk(job, vector_metas)          # post_chunk() 호출

    async def split(self, job):
        """입력 형식별 분할 전략을 선택하고, 결과를 공통 chunk 목록으로 반환한다.

        - 문서형(docling): GenosSmartChunker
        - 요소형(엑셀 행, JSON 레코드, 평문): 공통 분할기
        chunk 필드는 chunk_to_vector_meta 의 docstring 을 참조한다.
        """
        if job.kind == "docling":
            return self.split_document(job)      # core 의 분할은 동기다
        return self.split_records(job)

    async def chunks_to_vector_metas(self, job, chunks):
        """chunk 목록을 vector_meta 목록으로 변환한다."""
        vector_metas = []
        for chunk in chunks:
            text = self.build_chunk_text(job, chunk)            # 문서 접두어 + 헤딩 경로 + 본문
            text = await self._call_on_chunk(job, chunk, text)  # on_chunk() 호출
            if text is None:
                continue                                        # tb.DROP 이면 제외
            text = self.mask_sensitive(job, text)               # PII 라벨링, 마스킹
            text = self.clean_text(job, text)                   # 설정의 text_cleanup 적용
            vector_metas.append(self.chunk_to_vector_meta(job, chunk, text))
        self.refresh_stats(job, vector_metas)                   # 인덱스와 통계 갱신
        return vector_metas

    def build_chunk_text(self, job, chunk):
        """청크 텍스트를 조립한다.

        문서 식별 값(카드 이름, 문의 유형 등)이 헤딩 경로보다 앞에 온다.
        """
        return self.build_doc_prefix(job, chunk) + self.build_header_line(job, chunk) + chunk.text

    def chunk_to_vector_meta(self, job, chunk, text):
        """chunk 1건을 vector_meta 1건으로 변환한다.

        청크별 메타데이터는 on_chunk 의 info["fields"] 로 넣는다. 이 메소드는 vector_meta 를
        조립하는 방식 자체를 바꿀 때만 오버라이드한다.

        chunk 는 입력 형식과 관계없이 같은 필드를 갖는다. 필드 이름은 on_chunk 의 info 와 같다.
            chunk.text      본문 원문(접두어, 헤딩 경로 적용 전)
            chunk.kind      "docling"(문서형), "row"(행, 레코드), "text"(그 밖)
            chunk.page      페이지 번호(1부터)
            chunk.headings  헤딩 경로. 문서형만 채워진다
            chunk.metadata  레코드 메타데이터. 행 청크만 채워진다
            chunk.source    원본 객체(DocChunk, langchain Document, element dict). bbox 등 세부 정보용

        text 는 접두어, 헤딩 경로, 마스킹, 정제까지 적용된 값이다.
        원문 기준으로 계산하려면 chunk.text 를 쓴다.
        """
        return (self.vector_meta_builder(job)
                .set_text(text)
                .set_page_info(chunk)          # 페이지 번호, 페이지 내 인덱스
                .set_chunk_bboxes(chunk)       # 원본 bbox 좌표
                .set_media_files(chunk)        # 표, 이미지 파일 경로
                .set_table_info(chunk)         # 표 청크 여부, 분할된 표 조각 번호
                .set_metadata(job.metadata)    # 문서 단위 메타데이터. 모든 청크에 동일
                .build(self.VECTOR_META))

    # --- 2. doc_type 별 설정 ---
    #
    # 설정 파일(chunking_processor_config.yaml)은 모든 문서에 공통으로 적용된다.
    # doc_type 마다 다르게 하려면 아래 표에 적는다. 키는 설정 파일 경로를 dot notation 으로 쓴다.
    #
    # 설정 우선순위(뒤가 우선): 설정 파일, CONFIG_BY_DOC_TYPE, config_by_condition(), 요청 파라미터
    # 최종 값은 job.config 와 결과에 기록된다.

    CONFIG_BY_DOC_TYPE = {
        # "faq":    {"chunking.chunk_size": 500,            # 문답 1건이 짧다
        #            "chunking.min_chunk_size": 0},         # 0 이면 최소 크기 보정 비활성화
        # "manual": {"chunking.chunk_mode": "split_only",   # 섹션 단위 유지
        #            "chunking.chunk_size": 2000},
    }

    def config_by_condition(self, job):
        """[훅 메소드 0] 조건부 설정. 위 표로 안 되는 경우 문서 내용이나 요청 파라미터로 결정한다.

        위 표와 같은 형식의 dict 를 반환하고, 빈 dict 면 아무것도 바뀌지 않는다.

            if job.metadata.get("GROUP_C") == "INS":
                return {"guardrail.masking_enabled": True}
        """
        return {}

    # --- 3. 훅 메소드 ---
    #
    # 공통 규칙은 파서와 같다.
    #   1. 요청 파라미터가 필요하면 **kwargs 를 추가한다. job 은 kwargs["job"] 으로 꺼낸다.
    #   2. 외부 API 호출은 async def 로 작성한다.
    #   3. self 에 요청 상태를 저장하지 않는다. 단계 간 전달은 job.notes 를 쓴다.
    #   4. 오류는 GenosServiceException 을 raise 한다. 부분 실패를 허용하려면 해당 건만
    #      skip 하고 post_chunk 에서 결과에 기록한다.

    def pre_chunk(self, kind, data, **kwargs):
        """[훅 메소드 1] 청킹 전 전처리.

            kind == "parse"    data 는 list[dict] (엑셀 행, JSON 레코드)
            kind == "docling"  data 는 dict. DoclingDocument 가 아니라 직렬화된 JSON 이므로
                               본문은 data["texts"][i]["text"] 로 접근한다
        """
        return data

    def on_chunk(self, text, info, **kwargs):
        """[훅 메소드 2] 청크 생성 직후 호출된다.

        반환값은 셋 중 하나다.
            str      청크 텍스트를 이 값으로 교체한다
            None     변경 없음. return 을 생략해도 청크는 유지된다
            tb.DROP  청크를 제외한다. 인덱스는 재계산된다

        info 구조
            kind      "docling"(문서형), "row"(행, 레코드), "text"(그 밖)
            page      페이지 번호(1부터)    index  현재까지의 인덱스(참고용)
            headings  헤딩 경로. 문서형 청크만 채워진다
            metadata  문서 또는 레코드 메타데이터. 복사본이라 수정해도 저장되지 않는다
            fields    청크별 값을 넣는 dict. 넣은 값은 vector_meta 필드로 실린다.
                      본문(text)과 통계·순번 필드는 넣을 수 없다

            if "상담직원용" in text:
                return tb.DROP
            if "손실" in text:
                info["fields"]["RISK"] = "high"       # vector_meta 필드로 실린다
            return text.replace("[내부]", "")
        """
        return None

    def post_chunk(self, vector_metas, **kwargs):
        """[훅 메소드 3] 청킹 결과 후처리.

        vector_metas 는 vector_meta(VECTOR_META 인스턴스) 목록이다.

        용도
        - 청크 분할, 병합
        - 필드 값 일괄 변환, 삭제

            vector_metas = tb.split_chunk(vector_metas, when=lambda vm: len(vm.text) > 4000)
            vector_metas = tb.merge_small_chunks(vector_metas, min_chars=80)
            for vm in vector_metas:
                vm.AMOUNT = tb.to_int(tb.regex_sub(vm.AMOUNT, pattern=r"\\D", repl=""))
                tb.drop_fields(vm, "INTERNAL_URL")
            tb.refresh_stats(vector_metas)

        텍스트를 수정했거나 vector_meta 를 삭제했으면 통계를 재계산한다.
        - 개수 변경: tb.refresh_stats(vector_metas)
        - 텍스트만 수정: tb.refresh_stats(vector_metas, reindex=False)

        참고: 텍스트 수정이나 청크 제외만 필요하면 on_chunk 를 쓴다. 통계가 자동 갱신된다.
        """
        return vector_metas

    # --- 4. 오버라이드 ---
    #
    # 기본 동작을 바꾸려면 메소드를 오버라이드한다.
    # 청크 크기나 분할 방식만 바꾸려면 2 의 설정을 우선 고려한다.
    #
    #   지원     1 에 본문이 보이는 메소드(split, build_chunk_text, chunk_to_vector_meta 등)
    #            릴리스가 바뀌어도 이름과 인자를 유지한다
    #   비권장   그 밖의 core 메소드(split_document, mask_sensitive, clean_text, refresh_stats 등)
    #            오버라이드는 가능하지만 릴리스에서 바뀔 수 있다
    #
    #   def build_chunk_text(self, job, chunk):
    #       return f"[{job.metadata.get('title', '')}] " + chunk.text


if __name__ == "__main__":
    DocumentProcessor.cli()
