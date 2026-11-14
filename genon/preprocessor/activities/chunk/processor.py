# 청킹용 전처리기 — 파서 결과를 받아 청크로 나눈다. 원본 문서를 다시 읽거나 분석하지 않는다.
#
#   파서 결과 -> _start_job -> edit_input -> 청크 분할 -> vector_meta 만들기 -> edit_output -> 결과 목록
#   청크가 하나 만들어질 때마다 edit_chunk 를 부른다.
#
# 용어
#   chunk        분할 결과 한 건. 입력 형식과 관계없이 같은 필드를 갖는다(아래 chunks_to_vector_metas 참조).
#   vector_meta  VECTOR_META 클래스(기본 GenOSVectorMeta)의 인스턴스 한 건. 벡터 DB 의 한 행으로 적재된다.
#   doc_type     요청이 주는 문서 유형 이름(예: "contract"). 유형별 설정과 훅 적용 여부를 가르는 데 쓴다.
#   훅 메소드    사이트별 처리를 끼워 넣도록 비워 둔 메소드(edit_input·edit_chunk·edit_output 등).
#
# 파일 구성(뒤로 갈수록 직접 고쳐 쓰는 곳). 1 처리 흐름  2 doc_type 별 설정  3 훅 메소드
#                                          4 오버라이드  그리고 파일 끝 "파일 단독 실행"
from typing import Optional

from pydantic import BaseModel

from genon.preprocessor.processing.chunking import smart_chunker as sc
from genon.preprocessor.processing.core import toolbox as tb  # noqa: F401
from genon.preprocessor.processing.core.chunker import ChunkerCore
# main.py 의 예외 핸들러가 이 이름으로 잡는다. core 가 던지는 것과 같은 클래스다.
from genon.preprocessor.processing.core.errors import GenosServiceException  # noqa: F401


class GenOSVectorMeta(BaseModel):
    """청크 한 건이 적재 DB 의 한 행이 된다. 아래에 선언하지 않은 키도 그대로 실린다(extra=allow)."""

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
    appendix: str = None  # 부록(appendix) 기능용 필드
    file_path: Optional[str] = None
    guardrail_categories: Optional[list] = None  # 민감정보 분류 라벨. 분류를 하지 않으면 None
    # 표 관련 필드. 표 청크만 골라 검색하거나, 여러 청크로 나뉜 표를 원래 순서로 잇는 데 쓴다.
    has_table: bool = False
    table_refs: Optional[str] = None
    table_split_index: Optional[int] = None
    table_split_total: Optional[int] = None


class GenosSmartChunker(sc.SmartChunkerBase):
    """청킹 로직 본체는 processing/chunking/smart_chunker.py 에 있다. 여기에는 고른 옵션만 둔다."""

    PICTURE_ANNOTATION_TEXT = True          # 그림 설명(annotation)을 청크 본문에 넣는다
    TABLE_DESCRIPTION_MODE = "prefix_only"  # 표 설명은 검색용으로 표 앞에만 붙인다

    # 헤딩 경로에서 부모와 자식 사이 구분자. 실제 문서에서 heading 에 콤마가 든 경우가 있어
    # (409건 중 20건) 콤마로는 레벨을 구분할 수 없다. " > " 는 실제 문서에서 겹친 적이 없다.
    CHUNK_HEADER_SEP = " > "
    CHUNK_PATH_SEP = " | "        # 형제 경로 사이 구분자. 위와 달라야 부모-자식과 형제가 구분된다
    # 한 청크에 경로가 여러 개일 때 보여 줄 최대 개수. 넘는 것은 "… 외 N개"로 줄인다
    # (hwp 문서에서 경로 71개가 3,239자를 차지한 사례가 있다).
    CHUNK_PATH_MAX_LEAVES = 5
    # 경로 앞에 붙는 라벨. 빈 문자열이면 경로만 붙는다. 청크 크기 계산과 실제로 붙이는 쪽이 같은 값을 쓴다.
    CHUNK_HEADER_PREFIX = "HEADER: "


class DocumentProcessor(ChunkerCore):
    """청킹 전용 전처리기. main.py 가 /chunker 요청을 이 클래스로 보낸다."""

    IS_CHUNKER = True                 # True 여야 /chunker 엔드포인트가 열린다
    VECTOR_META = GenOSVectorMeta
    CHUNKER = GenosSmartChunker

    # 나누지 않고 한 건을 그대로 청크 하나로 만드는 element category. 엑셀 행과 JSON 레코드가
    # 여기에 해당한다. 자체 파서가 다른 category 이름을 쓰면 여기에 추가한다. 예: | {"my_row"}
    ROW_CATEGORIES = ChunkerCore.ROW_CATEGORIES

    # --- 1. 처리 흐름 ---
    #
    # 훅 메소드는 _call_* 메소드가 부른다. async def 와 def 를 모두 받고, **kwargs 를 선언한
    # 훅 메소드에만 요청 파라미터를 넘긴다. _call_* 는 설정을 적용하는 순서도 책임지므로
    # _ 로 시작하는 메소드는 오버라이드하지 않는다.

    async def __call__(self, request, file_path="", **kwargs):
        """청커 진입점.

        여기의 job 은 파서의 job 과 다른 객체다. 인자에 job 이 있는 메소드에서만 쓸 수 있다 —
        config_by_condition(job) 과, 오버라이드하는 core 메소드(4 참조).
        edit_input·edit_chunk·edit_output 의 **kwargs 에는 job 이 들어오지 않는다.
            job.kind      "docling" 은 문서형, "parse" 는 요소형(엑셀 행, JSON 레코드, 일반 텍스트)
            job.data      파서 결과          job.doc_type  요청이 준 문자열 그대로(정규화하지 않음)
            job.params    요청 파라미터      job.config    doc_type 별 설정으로 요청에 더한 값(2 참조)
            job.notes     단계끼리 값을 주고받는 dict

        job.metadata 는 문서형 청크를 vector_meta 로 바꾸는 준비 단계에서만 만들어진다.
        행의 메타데이터는 chunk.metadata 에 있다. config_by_condition 이 불릴 때는 job.metadata 가 아직 없다.

        job.kind 와 edit_chunk 의 info["kind"] 는 값이 다르다.
            "docling" -> "docling",  "parse" -> "row"(행, 레코드) 또는 "text"(그 밖)
        """
        job = self._start_job(request, file_path, **kwargs)            # 입력 형식 판별, doc_type 별 설정 적용
        job.data = await self._call_edit_input(job)                    # edit_input() 호출
        chunks = await self.split(job)                                 # 청크 분할
        vector_metas = await self.chunks_to_vector_metas(job, chunks)  # chunk 를 vector_meta 로 변환
        return await self._call_edit_output(job, vector_metas)         # edit_output() 호출

    async def split(self, job):
        """분할 방식을 고르고, 결과를 공통 형식의 chunk 목록으로 돌려준다.

        문서형(docling)은 GenosSmartChunker 로, 요소형(엑셀 행, JSON 레코드, 일반 텍스트)은 공통
        분할기로 나눈다. 어느 쪽이든 chunk 필드는 같다 — 아래 chunks_to_vector_metas 참조.
        """
        if job.kind == "docling":
            return self.split_document(job)      # core 의 분할 메소드는 동기 함수다
        return self.split_records(job)

    async def chunks_to_vector_metas(self, job, chunks, converted_pdf_path=None):
        """chunk 목록을 vector_meta 목록으로 바꾼다. chunk 하나가 벡터 DB 의 한 행이 된다.

        chunk 필드는 입력 형식과 관계없이 같고, 이름은 edit_chunk 의 info 와 같다.
            chunk.text      본문 원문(문서 접두어·헤딩 경로를 붙이기 전)
            chunk.kind      "docling" | "row"(엑셀 행, JSON 레코드) | "text"(그 밖)
            chunk.page      페이지 번호
            chunk.headings  헤딩 경로(문서 청크)  chunk.metadata  레코드 메타데이터(행 청크)
            chunk.source    원본 객체. bbox 와 표 조각 계산에 쓴다

        호출 순서를 바꾸지 않는다 — 마스킹이 정제보다 먼저여야 한다. 순서가 바뀌면 마스킹 전의
        개인정보(PII)가 글자 수 통계나 표 변환본에 남는다. start_chunk_loop / finish_chunk_loop 은
        청크 순번·통계·미디어 업로드 기록을 맡는다. 반복문을 고치더라도 이 둘로 감싼 채 둔다.
        빼면 n_chunk_of_doc 와 청크 순번이 어긋난다.
        """
        vector_metas = []
        for chunk in self.start_chunk_loop(job, chunks, converted_pdf_path):
            text = self.build_chunk_text(job, chunk)         # 문서 접두어 + 헤딩 경로 + 본문
            text, drop = await self._call_edit_chunk(job, chunk, text)  # edit_chunk() 호출
            if drop:
                continue                                     # tb.DROP 을 돌려받은 청크는 뺀다
            self.collect_chunk_variants(job, chunk, text)    # 표를 다른 형식으로 바꾼 본문 수집(마스킹 전 본문 기준)
            text = self.mask_sensitive(job, text)            # 민감정보 라벨 달기, 마스킹
            text = self.clean_text(job, text)                # 설정의 text_cleanup 적용
            vector_metas.append(self.chunk_to_vector_meta(job, chunk, text))
        return await self.finish_chunk_loop(job, vector_metas)

    # --- 2. doc_type 별 설정 ---
    #
    # 설정 파일(같은 폴더의 config.yaml)은 모든 문서에 똑같이 적용된다. doc_type 마다 다르게
    # 하려면 아래 CONFIG_BY_DOC_TYPE 에 적는다. 키는 설정 파일 경로(점 표기)나 같은 뜻의 요청
    # 파라미터 이름(괄호 안)을 쓴다 — 어느 쪽을 써도 같게 동작한다.
    #   chunking.chunk_size               청크 최대 크기  (= chunk_size)
    #   chunking.recursive.chunk_overlap  청크끼리 겹치는 길이  (= chunk_overlap)
    #   chunking.chunk_mode               split_only(섹션 단위 유지) / resize_all(크기에 맞춰 다시 나눔)
    #   chunking.short_chunk_size         본문이 이보다 짧은 청크를 이웃 청크에 합친다 (= short_chunk_size)
    #
    # 서버가 뜰 때 한 번 읽고 고정되는 설정(토크나이저 경로, min_chunk_size 등)은 여기에 적어도
    # 건너뛰고 경고만 남긴다 — 그런 값은 설정 파일에서 바꾼다.
    # 우선순위(뒤로 갈수록 우선). 설정 파일, CONFIG_BY_DOC_TYPE, config_by_condition(), 요청 파라미터.
    # job.config 에는 여기서 더한 값만 담긴다(최종 설정 전체도, 결과에 기록되는 값도 아니다).

    CONFIG_BY_DOC_TYPE = {
        # "faq":    {"chunking.chunk_size": 500},            # 문답 한 건이 짧다
        # "manual": {"chunking.chunk_mode": "split_only",    # 섹션 단위 유지
        #            "chunking.chunk_size": 2000},
    }

    def config_by_condition(self, job):
        """[훅 메소드 0] 위 표로 표현할 수 없는 조건부 설정. 같은 형식의 dict 를 돌려주고, 빈 dict 면 바꾸지 않는다.

            if job.params.get("dept") == "INS":
                return {"chunking.chunk_size": 800}

        분할 전에 불리므로 job.metadata 는 아직 없다. 요청 조건은 job.params 로 판단하고,
        문서·행 메타데이터에 따라 달라져야 하면 edit_chunk 의 info["metadata"] 에서 읽는다.
        """
        return {}

    # --- 3. 훅 메소드 ---
    #
    # 오버라이드하지 않으면 입력을 그대로 돌려준다(아무것도 하지 않음). 공통 규칙은 파서와 같다.
    #   1. 요청 파라미터가 필요하면 **kwargs 를 붙인다. 위치 인자와 이름이 겹치는 키는 빠지고,
    #      job 은 여기로 오지 않는다. 문서 유형은 tb.normalize_doc_type(kwargs.get("doc_type")) 로 읽는다.
    #   2. 외부 API 호출은 async def 로 쓴다. 동기 호출은 이벤트 루프를 막는다.
    #   3. 요청별 상태를 self 에 담지 않는다. 훅 사이에 값을 넘겨야 하면 한 훅 안에서 끝낼 수
    #      있는지 먼저 본다.
    #   4. 오류는 raise GenosServiceException("1", "메시지") 로 낸다. 일부만 실패해도 되면 그 건만
    #      건너뛰고 edit_output 에서 결과에 기록한다.

    def edit_input(self, kind, data, **kwargs):
        """[훅 메소드 1] 청킹 전에 입력을 손본다.

            kind == "parse"    data 는 list[dict] (엑셀 행, JSON 레코드)
            kind == "docling"  data 는 dict. DoclingDocument 객체가 아니라 JSON 으로 바꾼 값이므로
                               본문은 data["texts"][i]["text"] 로 읽는다
        """
        return data

    def edit_chunk(self, text, info, **kwargs):
        """[훅 메소드 2] 청크를 만든 직후. 요청마다 한 번이 아니라 청크마다 불린다.

        반환값은 셋 중 하나다. str 이면 청크 텍스트를 그 값으로 바꾸고, None 이면 그대로 둔다
        (return 을 생략해도 청크는 남는다). tb.DROP 이면 그 청크를 빼고 순번을 다시 매긴다.

        info 구조
            kind      "docling"(문서형), "row"(행, 레코드), "text"(그 밖)
            page      페이지 번호(1부터)    index  지금까지의 순번(참고용)
            headings  헤딩 경로. 문서형 청크에만 있다
            metadata  문서 또는 레코드 메타데이터. 복사본이라 고쳐도 저장되지 않는다
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
        """[훅 메소드 3] 응답을 돌려주기 직전. vector_metas 는 VECTOR_META 인스턴스 목록이다.

        청크를 나누거나 합치고, 필드 값을 한꺼번에 바꾸거나 지울 때 쓴다. 텍스트를 고치거나
        청크를 빼기만 하면 edit_chunk 가 낫다 — 통계가 자동으로 맞춰진다.

            vector_metas = tb.split_chunk(vector_metas, when=lambda vm: len(vm.text) > 4000)
            vector_metas = tb.merge_small_chunks(vector_metas, min_chars=80)
            for vm in vector_metas:
                vm.AMOUNT = tb.to_int(tb.regex_sub(vm.AMOUNT, pattern=r"\\D", repl=""))
                tb.drop_fields(vm, "INTERNAL_URL")
            tb.refresh_stats(vector_metas)

        여기서 텍스트를 고쳤거나 vector_meta 를 지웠으면 통계를 다시 맞춘다.
        개수가 바뀌었으면 tb.refresh_stats(vector_metas), 텍스트만 고쳤으면 reindex=False 를 준다.
        """
        return vector_metas

    # --- 4. 오버라이드 ---
    #
    # 기본 동작 자체를 바꿀 때 쓴다. 청크 크기나 분할 방식만 바꾸려면 2 의 설정을 먼저 본다.
    #   지원     1 에 본문이 보이는 메소드(split, chunks_to_vector_metas, build_chunk_text,
    #            mask_sensitive, clean_text, chunk_to_vector_meta).
    #            새 릴리스에서도 이름과 인자를 유지한다
    #   비권장   그 밖의 core 메소드(split_document, split_records, collect_chunk_variants,
    #            refresh_stats 등). 오버라이드는 되지만 릴리스에 따라 바뀔 수 있다
    #
    #   def build_chunk_text(self, job, chunk):          # 청크 텍스트 조립을 바꾼다
    #       metadata = job.metadata if chunk.kind == "docling" else chunk.metadata
    #       text = super().build_chunk_text(job, chunk)  # 기본 헤딩·접두어 조립은 그대로 쓴다
    #       title = metadata.get("title")
    #       return f"[{title}] {text}" if title else text


# --- 파일 단독 실행 ---
#
# cli() 는 서버를 띄우지 않고 이 파일 하나만 돌린다. 입력은 **파서가 만든 결과 JSON** 이고
# (원본 문서가 아니다) 결과는 /chunker 응답과 같은 vector_meta 목록이다. 저장 위치, 청크
# 개수, 걸린 시간은 stderr 로 알린다. 파일 경로로 실행하면(python chunk.py) import 가
# 실패한다 — 저장소 최상위에서 -m 으로 부른다.
#
#   python -m genon.preprocessor.activities.parse 계약서.pdf --doc-type contract -o parsed.json
#   python -m genon.preprocessor.activities.chunk parsed.json --doc-type contract -o chunks.json
#
#   --doc-type    doc_type 별 설정(2)과 훅 적용 여부를 정한다. 파서에 넘긴 값과 같게 준다
#   --config      처리기 설정 yaml 경로. 주지 않으면 기본 경로에서 찾는다
#   -o, --out     결과 JSON 경로. 생략하면 stdout 으로 나온다
#   --log-level   5 DEBUG / 4 INFO / 3 WARNING / 2 ERROR / 1 CRITICAL / 0 끔
#
# cli() 를 거치지 않고 클래스를 직접 불러도 된다. 파서와 다른 점이 둘 있다. 첫 인자에 None 을
# 주면 미디어 업로드에서 오류가 나므로 tb.mock_request() 를 넘긴다. 결과가 vector_meta 객체
# 목록이라 json.dump 에 바로 넘길 수 없으므로 model_dump() 를 거친다.
#
#   metas = asyncio.run(DocumentProcessor()(tb.mock_request(), "parsed.json", doc_type="contract"))
#   json.dump([m.model_dump() for m in metas], open("chunks.json", "w", encoding="utf-8"),
#             ensure_ascii=False, indent=2)
if __name__ == "__main__":
    DocumentProcessor.cli()
