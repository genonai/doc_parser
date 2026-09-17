# 청킹용 전처리기 — 파서 결과를 받아 청킹한다. 원본 문서를 로드하거나 분석하지 않는다.
#
#   파서 결과 -> _start_job -> edit_input -> 청크 분할 -> vector_meta 생성 -> edit_output -> 목록
#   청크가 생성될 때마다 edit_chunk 를 호출한다.
#
# 용어. chunk 는 분할 결과 1건이고 입력 형식과 관계없이 같은 필드를 갖는다(아래
# chunks_to_vector_metas 참조). vector_meta 는 VECTOR_META(기본 GenOSVectorMeta) 인스턴스
# 1건이고 벡터 DB 1행으로 적재된다.
#
# 구성(뒤로 갈수록 확장 지점). 1 처리 흐름  2 doc_type 별 설정  3 훅 메소드
#                            4 오버라이드  그리고 파일 끝 "파일 단독 실행"
from typing import Optional

from pydantic import BaseModel

from genon.preprocessor.processing.chunking import smart_chunker as sc
from genon.preprocessor.processing.core import toolbox as tb  # noqa: F401
from genon.preprocessor.processing.core.chunker import ChunkerCore
# main.py 의 예외 핸들러가 이 이름으로 잡는다. core 가 던지는 것과 같은 클래스다.
from genon.preprocessor.processing.core.errors import GenosServiceException  # noqa: F401


class GenOSVectorMeta(BaseModel):
    """청크 1건 = 적재 DB 1행. 선언에 없는 키도 그대로 실린다(extra=allow)."""

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
    appendix: str = None ## !! appendix feature (2025-09-30, geonhee kim) !!
    file_path: Optional[str] = None
    guardrail_categories: Optional[list] = None  # #315 민감정보 분류 라벨. 미적용 시 None
    # 표 메타(#360). 표 청크만 골라 검색하거나 나뉜 조각을 원래 순서로 잇는 데 쓴다.
    has_table: bool = False
    table_refs: Optional[str] = None
    table_split_index: Optional[int] = None
    table_split_total: Optional[int] = None


class GenosSmartChunker(sc.SmartChunkerBase):
    """청킹 본체는 processing/chunking/smart_chunker.py 다. 여기엔 고른 옵션만 둔다."""

    PICTURE_ANNOTATION_TEXT = True          # 그림 annotation 을 청크 본문에 싣는다
    TABLE_DESCRIPTION_MODE = "prefix_only"  # 표 설명은 검색용 접두만

    # 경로 안 구분자(부모 → 자식). heading 에 콤마가 든 경우가 있어(실측 409건 중 20건)
    # 콤마로는 레벨을 되돌릴 수 없다. " > " 는 실측 충돌이 0 이다.
    CHUNK_HEADER_SEP = " > "
    CHUNK_PATH_SEP = " | "        # 형제 경로 사이. 위와 달라야 부모-자식과 형제가 구분된다
    # 다경로 청크의 리프 상한. 초과분은 "… 외 N개"(실측: hwp 71경로 → 3,239자).
    CHUNK_PATH_MAX_LEAVES = 5
    # 경로 앞 라벨. 빈 문자열이면 경로만 붙는다. 크기 산정과 실제 부착이 같은 값을 본다.
    CHUNK_HEADER_PREFIX = "HEADER: "


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
    # 훅 메소드는 _call_* 가 부른다 — async def / def 를 모두 허용하고, **kwargs 를 선언한
    # 훅 메소드에만 요청 파라미터를 넘긴다. _call_* 는 설정 적용 순서도 맡으므로,
    # _ 로 시작하는 메소드는 오버라이드하지 않는다.

    async def __call__(self, request, file_path="", **kwargs):
        """청커 진입점

        job 은 파서의 job 과 다른 객체다. 훅 메소드에서는 kwargs["job"] 으로 꺼낸다.
            job.kind      "docling" 은 문서형, "parse" 는 요소형(엑셀 행, JSON 레코드, 평문)
            job.data      파서 결과          job.doc_type  문서 유형
            job.metadata  문서 단위 메타데이터 job.params    요청 파라미터
            job.config    적용된 설정(2 참조) job.notes     단계 간 공유 dict

        job.kind 와 edit_chunk 의 info["kind"] 는 값이 다르다.
            "docling" -> "docling",  "parse" -> "row"(행, 레코드) 또는 "text"(그 밖)
        """
        job = self._start_job(request, file_path, **kwargs)            # 입력 형식 판별, doc_type 별 설정 적용
        job.data = await self._call_edit_input(job)                    # edit_input() 호출
        chunks = await self.split(job)                                 # 청크 분할
        vector_metas = await self.chunks_to_vector_metas(job, chunks)  # chunk 를 vector_meta 로 변환
        return await self._call_edit_output(job, vector_metas)         # edit_output() 호출

    async def split(self, job):
        """분할 전략을 고르고 결과를 공통 chunk 목록으로 반환한다.

        문서형(docling)은 GenosSmartChunker, 요소형(엑셀 행, JSON 레코드, 평문)은 공통
        분할기를 쓴다. 어느 쪽이든 chunk 필드는 같다 — 아래 chunks_to_vector_metas 참조.
        """
        if job.kind == "docling":
            return self.split_document(job)      # core 의 분할은 동기다
        return self.split_records(job)

    async def chunks_to_vector_metas(self, job, chunks, converted_pdf_path=None):
        """chunk 목록을 vector_meta 목록으로 변환한다. chunk 1개가 벡터 DB 1행이 된다.

        chunk 필드는 입력 형식과 무관하게 같고, 이름은 edit_chunk 의 info 와 같다.
            chunk.text      본문 원문(접두어, 헤딩 경로 적용 전)
            chunk.kind      "docling" | "row"(엑셀 행, JSON 레코드) | "text"(그 밖)
            chunk.page      페이지 번호
            chunk.headings  헤딩 경로(문서 청크)  chunk.metadata  레코드 메타데이터(행 청크)
            chunk.source    원본 객체. bbox, 표 조각 계산용

        호출 순서 유지 필수 — 마스킹이 정제보다 앞이다. 바뀌면 마스킹 전 PII 가 글자 수
        통계나 표 변환본에 남는다. start_chunk_loop / finish_chunk_loop 은 순번·통계·미디어
        업로드 부기를 맡는다 — 반복문을 바꾸더라도 이 둘로 감싼 채 둔다. 빼면 n_chunk_of_doc
        와 청크 순번이 어긋난다.
        """
        vector_metas = []
        for chunk in self.start_chunk_loop(job, chunks, converted_pdf_path):
            text = self.build_chunk_text(job, chunk)         # 문서 접두어 + 헤딩 경로 + 본문
            text, drop = await self._call_edit_chunk(job, chunk, text)  # edit_chunk() 호출
            if drop:
                continue                                     # tb.DROP 이면 제외
            self.collect_chunk_variants(job, chunk, text)    # 표 표기형태 변형(마스킹 전 본문에서)
            text = self.mask_sensitive(job, text)            # PII 라벨링, 마스킹
            text = self.clean_text(job, text)                # 설정의 text_cleanup 적용
            vector_metas.append(self.chunk_to_vector_meta(job, chunk, text))
        return await self.finish_chunk_loop(job, vector_metas)

    # --- 2. doc_type 별 설정 ---
    #
    # 설정 파일(chunking_processor_config.yaml)은 모든 문서에 공통이다. doc_type 마다 다르게
    # 하려면 아래 표에 적는다. 키는 설정 파일 경로(점 표기)나 같은 뜻의 요청 파라미터
    # 이름(괄호)을 쓴다 — 둘 다 같게 동작한다.
    #   chunking.chunk_size               청크 최대 크기  (= chunk_size)
    #   chunking.recursive.chunk_overlap  청크 간 겹침    (= chunk_overlap)
    #   chunking.chunk_mode               split_only(섹션 단위 유지) / resize_all(크기에 맞춰 재분할)
    #
    # 기동 시 한 번 읽혀 굳는 설정(토크나이저 경로, min_chunk_size 등)은 여기 적어도 건너뛰고
    # 경고가 남는다 — 그 값은 설정 파일에서 바꾼다.
    # 우선순위(뒤가 우선). 설정 파일, CONFIG_BY_DOC_TYPE, config_by_condition(), 요청 파라미터.
    # 최종 값은 job.config 와 결과에 기록된다.

    CONFIG_BY_DOC_TYPE = {
        # "faq":    {"chunking.chunk_size": 500},            # 문답 1건이 짧다
        # "manual": {"chunking.chunk_mode": "split_only",    # 섹션 단위 유지
        #            "chunking.chunk_size": 2000},
    }

    def config_by_condition(self, job):
        """[훅 메소드 0] 위 표로 안 되는 조건부 설정. 같은 형식의 dict 를 반환하고, 빈 dict 면 그대로다.

            if job.metadata.get("GROUP_C") == "INS":
                return {"chunking.chunk_size": 800}
        """
        return {}

    # --- 3. 훅 메소드 ---
    #
    # 오버라이드하지 않으면 입력을 그대로 돌려준다(no-op). 공통 규칙은 파서와 같다.
    #   1. 요청 파라미터가 필요하면 **kwargs 를 붙인다. job 은 kwargs["job"] 으로 꺼낸다.
    #   2. 외부 API 호출은 async def 로 쓴다. 동기 호출은 이벤트 루프를 막는다.
    #   3. self 에 요청 상태를 담지 않는다. 단계 간 전달은 job.notes 를 쓴다.
    #   4. 오류는 raise GenosServiceException("1", "메시지"). 부분 실패를 허용하려면 그 건만
    #      건너뛰고 edit_output 에서 결과에 기록한다.

    def edit_input(self, kind, data, **kwargs):
        """[훅 메소드 1] 청킹 전 전처리.

            kind == "parse"    data 는 list[dict] (엑셀 행, JSON 레코드)
            kind == "docling"  data 는 dict. DoclingDocument 가 아니라 직렬화된 JSON 이므로
                               본문은 data["texts"][i]["text"] 로 접근한다
        """
        return data

    def edit_chunk(self, text, info, **kwargs):
        """[훅 메소드 2] 청크 생성 직후. 요청당 한 번이 아니라 청크마다 호출된다.

        반환값은 셋 중 하나다. str 이면 청크 텍스트를 그 값으로 교체하고, None 이면 변경
        없음(return 을 생략해도 청크는 유지된다), tb.DROP 이면 제외하고 인덱스를 재계산한다.

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

    def edit_output(self, vector_metas, **kwargs):
        """[훅 메소드 3] 응답 반환 직전. vector_metas 는 VECTOR_META 인스턴스 목록이다.

        청크 분할·병합, 필드 값 일괄 변환·삭제에 쓴다. 텍스트 수정이나 청크 제외만 필요하면
        edit_chunk 쪽이 낫다 — 통계가 자동으로 갱신된다.

            vector_metas = tb.split_chunk(vector_metas, when=lambda vm: len(vm.text) > 4000)
            vector_metas = tb.merge_small_chunks(vector_metas, min_chars=80)
            for vm in vector_metas:
                vm.AMOUNT = tb.to_int(tb.regex_sub(vm.AMOUNT, pattern=r"\\D", repl=""))
                tb.drop_fields(vm, "INTERNAL_URL")
            tb.refresh_stats(vector_metas)

        여기서 텍스트를 고쳤거나 vector_meta 를 지웠으면 통계를 다시 맞춘다.
        개수가 바뀌었으면 tb.refresh_stats(vector_metas), 텍스트만 고쳤으면 reindex=False.
        """
        return vector_metas

    # --- 4. 오버라이드 ---
    #
    # 기본 동작 자체를 바꿀 때 쓴다. 청크 크기나 분할 방식만 바꾸려면 2 의 설정을 먼저 본다.
    #   지원     1 에 본문이 보이는 메소드(split, chunks_to_vector_metas, build_chunk_text,
    #            mask_sensitive, clean_text, chunk_to_vector_meta)
    #            릴리스가 바뀌어도 이름과 인자를 유지한다
    #   비권장   그 밖의 core 메소드(split_document, split_records, collect_chunk_variants,
    #            refresh_stats 등) 오버라이드는 되지만 릴리스에서 바뀔 수 있다
    #
    #   def build_chunk_text(self, job, chunk):          # 청크 텍스트 조립을 바꾼다
    #       return f"[{job.metadata.get('title', '')}] " + chunk.text


# --- 파일 단독 실행 ---
#
# cli() 는 서버를 띄우지 않고 이 파일 하나를 돌린다. 입력은 **파서가 만든 결과 JSON** 이고
# (원본 문서가 아니다) 산출은 /chunker 응답과 같은 vector_meta 목록이다. 저장 위치와 청크
# 건수, 걸린 시간은 stderr 로 알린다. 경로 실행(python chunking_processor.py)은 import 가
# 풀리지 않는다 — 저장소 최상위에서 -m 으로 부른다.
#
#   python -m genon.preprocessor.facade.parser_processor 계약서.pdf --doc-type contract -o parsed.json
#   python -m genon.preprocessor.facade.chunking_processor parsed.json --doc-type contract -o chunks.json
#
#   --doc-type    doc_type 별 설정(2)과 훅 메소드 게이팅에 쓰인다. 파서에 넘긴 값과 같게 준다
#   --config      프로세서 설정 yaml 경로. 미지정 시 기본 경로를 찾는다
#   -o, --out     결과 JSON 경로. 생략하면 stdout 으로 나온다
#   --log-level   5 DEBUG / 4 INFO / 3 WARNING / 2 ERROR / 1 CRITICAL / 0 끔
#
# cli() 를 거치지 않고 클래스를 직접 불러도 된다. 파서와 다른 곳이 둘이다. 첫 인자에 None 을
# 주면 미디어 업로드에서 에러가 나므로 tb.mock_request() 를 넘기고, 산출이 vector_meta 객체
# 목록이라 json.dump 가 바로 받지 못해 model_dump() 를 거친다.
#
#   metas = asyncio.run(DocumentProcessor()(tb.mock_request(), "parsed.json", doc_type="contract"))
#   json.dump([m.model_dump() for m in metas], open("chunks.json", "w", encoding="utf-8"),
#             ensure_ascii=False, indent=2)
if __name__ == "__main__":
    DocumentProcessor.cli()
