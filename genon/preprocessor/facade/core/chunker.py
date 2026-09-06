"""청킹 처리 본체 (#363 08).

`facade/chunking_processor.py` 에서 통째로 옮겨 왔다. 고객이 여는 파일은 그쪽이고
여기는 열 일이 없다. 08-2 는 순수 이동이며 벡터 스키마·청커 옵션을 facade 로
되돌리는 것과 훅은 08-3 이다.
"""
from __future__ import annotations

import json
import os
import logging
import re
from pathlib import Path

from collections import defaultdict
from datetime import datetime
from dataclasses import dataclass
from typing import Optional, Any, List, Tuple

from fastapi import Request

_log = logging.getLogger(__name__)

# ── 공용 하위 모듈로 옮긴 헬퍼들의 별칭 ──────────────────────────────
# 구현은 facade/common/, facade/chunking/ 에 한 벌만 둔다. 여기서는 기존 이름을
# 그대로 유지해 호출부를 건드리지 않는다. 사이트별 조정 대상 상수(구분자, 최소
# 청크 크기, 토크나이저 경로)는 이 파일에 남아 있으므로 래퍼가 넘겨준다.
from genon.preprocessor.facade.common import config_parse as cp
from genon.preprocessor.facade.common import pipeline_setup as ps
from genon.preprocessor.facade.common import appendix as apx
from genon.preprocessor.facade.chunking import smart_chunker as sc
from genon.preprocessor.facade.common import vector_meta as vm
from genon.preprocessor.facade.common import docling_ops as dops
from genon.preprocessor.facade.common import runtime as rt
from genon.preprocessor.facade.chunking import doc_prefix as dpx
from genon.preprocessor.facade.chunking import header_path as hp
from genon.preprocessor.facade.chunking import table_blocks as tbk
from genon.preprocessor.facade.chunking import table_variants as tv

_as_dict = cp.as_dict
_parse_optional_bool = cp.parse_optional_bool
_parse_optional_int = cp.parse_optional_int
_resolve_include_chunk_header = cp.resolve_include_chunk_header


def _build_header_line(headings, include_header: bool, chunker_cls) -> str:
    """구분자는 청커 클래스가 든 값을 그대로 쓴다.

    청크 크기 산정(청커 안)과 실제 부착(compose_vectors)이 반드시 같은 문자열을 봐야
    한다. 한 곳에서만 읽으면 그 조건이 구조적으로 보장된다.
    """
    return hp.build_header_line(
        headings, include_header,
        chunker_cls.CHUNK_HEADER_SEP, chunker_cls.CHUNK_PATH_SEP,
        chunker_cls.CHUNK_PATH_MAX_LEAVES)

def _clamp_chunk_size(size):
    return cp.clamp_chunk_size(size, _MIN_CHUNK_SIZE)

def _load_config(config_path: str) -> dict:
    return cp.load_config(config_path, strict=True)

def _resolve_tokenizer(chunking_cfg: dict):
    return cp.resolve_tokenizer(
        chunking_cfg, local_path=_DEFAULT_TOKENIZER_LOCAL_PATH, hf_id=_DEFAULT_TOKENIZER_ID)


from docling.utils.llm_cache import (
    log_summary as _log_cache_summary,
    parse_interim_ref as _parse_interim_ref,
    reset_context as _reset_cache_context,
    resolve_context as _resolve_cache_context,
    set_context as _set_cache_context,
)
from docling_core.transforms.chunker import (
    DocChunk,
)

import asyncio
from docling_core.types.doc.document import (
    ListItem,
    CodeItem,
)
from docling_core.types.doc import (
    BoundingBox,
    DocItemLabel,
    DoclingDocument,
    SectionHeaderItem,
    TableItem,
    TextItem,
    ProvenanceItem
)
from docling.datamodel.settings import settings


from pydantic import BaseModel

from genon.preprocessor.facade.enrichment.enrichment_config import EnrichmentConfig
from genon.preprocessor.facade.enrichment.field_transforms import (
    DEFAULT_METADATA_FIELD_TRANSFORMS,
    apply_field_transforms,
    extract_metadata_from_document,
    serialize_metadata_value_for_output,
)
from genon.preprocessor.facade.chunking import text_norm as tn

try:
    import semchunk
    from transformers import AutoTokenizer, PreTrainedTokenizerBase
except ImportError:
    raise RuntimeError(
        "Module requires 'chunking' extra; to install, run: "
        "`pip install 'docling-core[chunking]'`"
    )

try:
    from genos_utils import upload_files
except ImportError:
    upload_files = None


# 사이트가 바꾸는 값(적재 컬럼·청커 옵션·헤더 구분자)은 이 파일에 없다.
# 배포되는 facade 가 VECTOR_META / CHUNKER 로 정한다. 아래 둘은 그 축이 아니라
# 배관의 하한값이라 여기 둔다.

_MIN_CHUNK_SIZE = 1024

# 청킹용 토크나이저 기본 경로 (config 의 chunking.tokenizer 미지정 시 이 값을 쓴다)
_DEFAULT_TOKENIZER_LOCAL_PATH = "/models/doc_parser_models/sentence-transformers-all-MiniLM-L6-v2"
_DEFAULT_TOKENIZER_ID = "sentence-transformers/all-MiniLM-L6-v2"


# ============================================================
# 여기부터 배관 — 설정 로딩, 벡터 스키마, 청킹 실행. 고칠 일이 없다.
# ============================================================

# 민감정보 분류/마스킹(#315)은 facade/guardrail 모듈로 분리 — gr.* 로 사용.
# chunking 은 워크플로우를 직접 호출하지 않고, parser 가 넘긴 sensitive_infos 를 청크에 적용만 한다.
from genon.preprocessor.facade import guardrail as gr
from genon.preprocessor.facade.core.errors import GenosServiceException


def _resolve_default_chunking_config_path() -> str:
    # facade/core/ 로 한 단계 깊어졌으므로 facade/ 를 기준으로 잡는다 —
    # 옮기기 전 이 헬퍼는 facade/ 에 있었고 아래 상대 경로가 그것을 전제한다.
    base_dir = Path(__file__).resolve().parents[1]
    local_config = (base_dir / "../resource_dev/chunking_processor_config.yaml").resolve()
    default_config = (base_dir / "../resource/chunking_processor_config.yaml").resolve()

    if local_config.exists():
        return str(local_config)
    return str(default_config)


# 조각에 섹션 문맥을 물려주는 규칙은 공용 모듈 한 벌이다(표 기준 분리도 같은 함수를 쓴다).
_carry_over_section_headings = hp.carry_over_section_headings


class GenOSVectorMetaBuilder(vm.VectorMetaBuilderBase):
    """공통 세터(텍스트 통계·페이지·bbox·미디어·글로벌 메타데이터)는
    facade/common/vector_meta.py 에 있다. 여기에는 이 facade 고유 필드만 둔다."""

    def __init__(self):
        """빌더 초기화"""
        super().__init__()
        self.title: Optional[str] = None
        self.created_date: Optional[int] = None
        self.appendix: Optional[str] = None # !! appendix feature (2025-09-30, geonhee kim) !!
        self.file_path: Optional[str] = None

    def build(self, model=None):
        """설정된 데이터로 벡터 메타 객체를 만든다.

        model 은 배포되는 facade 가 정한 스키마다(ChunkerCore.VECTOR_META).
        미지정이면 core 기본 스키마를 쓴다.
        """
        payload = {
            **self.core_payload(),
            "title": self.title,
            "created_date": self.created_date,
            "appendix": self.appendix or "", # !! appendix feature (2025-09-30, geonhee kim) !!
            "file_path": self.file_path,
            **self.extra_metadata,
        }
        return model.model_validate(payload)


def _extract_sensitive_infos(raw_payload) -> list:
    """parser 가 파스 출력에 실어 보낸 sensitive_infos 를 꺼낸다(#315).
    봉투 {"code":0,"data":{...}} 또는 평평한 dict 어느 쪽이든 sensitive_infos 를 찾는다."""
    if not isinstance(raw_payload, dict):
        return []
    for container in (raw_payload, raw_payload.get("data")):
        if isinstance(container, dict):
            si = container.get("sensitive_infos")
            if isinstance(si, list):
                return si
    return []


def _classify_payload(obj) -> Tuple[str, Any]:
    """parser 결과(dict)를 (kind, data) 로 분류한다.

    chunker 입력은 두 채널(인라인 document / file_path .json)로 들어오며, 두 채널 모두
    docling 또는 parse-format(legacy) 어느 형태든 담길 수 있다. file_path 는 parser 결과
    JSON 경로이므로 확장자가 아니라 payload 형태로 판별한다.

    반환:
      ("docling", <DoclingDocument dict>) - 아래 허용 형태
        1) raw docling dict (DoclingDocument.model_dump 결과; schema_name/body/texts 보유)
        2) {"document": {...}}                 (parser docling 응답)
        3) {"data": {"document": {...}}}       (전체 envelope)
      ("parse", <elements list>) - parse-format(비-docling)
        4) {"elements": [...]}                 (parser parse 응답; output.format=json/html/markdown)
        5) {"data": {"elements": [...]}}       (전체 envelope)

    주의: parser 의 docling 응답은 _normalize_response 로 인해 빈 "elements": [] 키도 함께
    가질 수 있으므로 반드시 "document" 를 "elements" 보다 먼저 검사한다.
    """
    if not isinstance(obj, dict):
        raise GenosServiceException(1, "chunker 입력 형식을 인식할 수 없습니다.")

    candidates = [obj]
    data = obj.get("data")
    if isinstance(data, dict):
        candidates.append(data)

    # docling 우선 (document 키)
    for node in candidates:
        if isinstance(node.get("document"), dict):
            return "docling", node["document"]
    # parse-format (elements 키)
    for node in candidates:
        if isinstance(node.get("elements"), list):
            return "parse", node["elements"]
    # raw docling dict (DoclingDocument 직렬화 결과로 보이면 그대로)
    if "body" in obj or "schema_name" in obj or "texts" in obj:
        return "docling", obj
    raise GenosServiceException(
        1, "chunker 입력 형식을 인식할 수 없습니다(docling/parse-format 아님)."
    )


@dataclass
class ChunkInput:
    """load_input 산출. 청킹 훅이 다루는 것은 kind 와 data 뿐이다."""

    kind: str            # "docling" | "parse"
    data: object         # DoclingDocument(또는 dict) | list[dict]
    guardrail: dict      # #315 민감정보 컨텍스트. core 가 그대로 넘긴다


class ChunkerCore:

    # IS_CHUNKER 는 배포되는 facade 가 선언한다 — main.py 가 그 속성으로 /chunker
    # 전용임을 식별한다. core 는 그 표식을 갖지 않는다.

    # 사이트마다 달라지는 두 가지. 배포되는 facade 가 **반드시** 정한다(#363 08).
    # core 에 기본값을 두지 않는 이유는 사본이 둘이 되면 조용히 어긋나기 때문이다.
    #   VECTOR_META  적재 DB 컬럼 스키마
    #   CHUNKER      청킹 동작 옵션·헤더 구분자
    VECTOR_META: type = None
    CHUNKER: type = None

    def __init__(self, config_path: str | None = None):
        '''
        initialize Document Converter (config 기반)

        config_path 가 None 이면 resource_dev/chunking_processor_config.yaml
        (없으면 resource/chunking_processor_config.yaml) 을 사용한다.
        GenOS 는 DocumentProcessor() 무인자로 호출하므로 기본 경로 resolve 필수.
        '''
        if config_path is None:
            config_path = _resolve_default_chunking_config_path()

        cfg = _load_config(config_path)
        self._config_dir = Path(config_path).resolve().parent

        defaults_cfg = _as_dict(cfg.get("defaults"))
        log_level = _parse_optional_int(defaults_cfg.get("log_level"), "defaults.log_level")
        if log_level is None:
            log_level = 4
        self._log_level = log_level

        # layout 은 아래 page_batch_size 전역 설정에만 쓴다(청킹은 docling 파싱을 하지 않는다).
        layout_cfg = _as_dict(cfg.get("layout"))
        chunking_cfg = _as_dict(cfg.get("chunking"))
        ec = EnrichmentConfig.from_raw(cfg.get("enrichment"), self._config_dir, parent_cfg=cfg)

        # 청킹용 토크나이저 (chunking config 기반; 미지정 시 현행 기본값)
        self._tokenizer = _resolve_tokenizer(chunking_cfg)

        # 토큰 수 계산 방식 (chunking 섹션). "char"(default)=문자 수 기준 | "huggingface"=HF 토크나이저 기준
        self._tokenizer_type = str(chunking_cfg.get("tokenizer_type", "char")).strip().lower()
        if self._tokenizer_type not in {"char", "huggingface"}:
            _log.warning(
                f"[DocumentProcessor] Unknown chunking.tokenizer_type '{self._tokenizer_type}', fallback to 'char'."
            )
            self._tokenizer_type = "char"

        # 청크 최대 크기(GenosSmartChunker.max_tokens) 기본값. kwargs 의 chunk_size 가 우선.
        self._chunk_size = _parse_optional_int(chunking_cfg.get("chunk_size"), "chunking.chunk_size")

        # 청킹 모드: "split_only"(기본, chunk_size 초과 청크만 분할) | "resize_all"(모든 청크를 chunk_size 에 맞게 병합/분할)
        self._chunk_mode = str(chunking_cfg.get("chunk_mode", "split_only")).strip().lower()
        if self._chunk_mode not in {"split_only", "resize_all"}:
            _log.warning(f"[DocumentProcessor] Unknown chunking.chunk_mode '{self._chunk_mode}', fallback to 'split_only'.")
            self._chunk_mode = "split_only"

        # 청크 선두 "HEADER: <섹션 경로>" 라인 부착 여부(기본 True). kwargs 의 include_chunk_header 가 우선.
        _ich = _parse_optional_bool(chunking_cfg.get("include_chunk_header"), "chunking.include_chunk_header")
        self._include_chunk_header = True if _ich is None else _ich

        # 표를 본문과 섞지 않고 독자 청크로 낼지(chunking.table_as_chunk, 기본 true).
        # kwargs 의 table_as_chunk 가 우선.
        _tac = _parse_optional_bool(chunking_cfg.get("table_as_chunk"), "chunking.table_as_chunk")
        self._table_as_chunk = True if _tac is None else _tac

        # 청크 텍스트 정규화(chunking.text_cleanup): "off"(기본) | "safe".
        # safe 면 청킹 입력에 문자 위생(tn.sanitize)을, 벡터 생성 직전에 표현 정리(tn.tidy)를 적용한다.
        # 우선순위: kwargs.text_cleanup > 아래 > "off".
        self._text_cleanup = tn.mode_from_cfg(chunking_cfg)
        # 사이트별 노이즈 삭제 규칙(text_cleanup.rules). 기동 시 정규식을 컴파일해
        # 잘못된 설정을 요청 전에 드러낸다. 규칙이 없으면 빈 튜플이라 무비용이다.
        self._text_cleanup_rules = tn.rules_from_cfg(chunking_cfg)

        # 민감정보 분류(#315): chunking 은 워크플로우를 직접 호출하지 않는다(parser 가 호출).
        # parser 가 넘긴 sensitive_infos 를 청크에 적용만 하며, 치환 여부는 masking_enabled 로 결정.
        self._gr_cfg = gr.GuardrailConfig.from_cfg(cfg)

        # parse-format(비-docling) 문자 splitter overlap 기본값 (docling 무관, parse-format 전용).
        # 크기는 공통 chunking.chunk_size(self._chunk_size)를 사용한다(_chunk_text_elements).
        recursive_cfg = _as_dict(chunking_cfg.get("recursive"))
        rco = _parse_optional_int(recursive_cfg.get("chunk_overlap"), "chunking.recursive.chunk_overlap")
        self._recursive_chunk_overlap = rco if rco is not None and rco >= 0 else 100

        self.page_chunk_counts = defaultdict(int)

        # 표 이미지(table_image) 옵션: 표를 picture 와 동일하게 이미지로 잘라 저장하고,
        # media_files 에 type='table_image' 로 기록한다(검색=청크 텍스트 / 답변=표 이미지).
        # 기본 False 라 미설정 시 기존 동작과 동일(하위 호환).
        table_image_cfg = _as_dict(cfg.get("table_image"))
        self.table_image_enabled = bool(
            _parse_optional_bool(table_image_cfg.get("enable"), "table_image.enable")
        )

        output_cfg = _as_dict(cfg.get("output"))
        # 표 직렬화 형식. auto 는 여기서 확정하지 않는다 - 표마다 grid 구조를 봐야 정해진다.
        # 파서와 청커는 별개 호출이라 이 값이 kwargs 로 넘어오지 않는다. 요청이 명시하지
        # 않으면 이 설정이 유일한 경로다.
        self._table_format = cp.resolve_table_format_setting(output_cfg)
        # markdown 표 compact(컬럼 정렬 패딩 제거) 여부. 기본 True. html 포맷엔 무관.
        self._compact_tables = cp.resolve_compact_tables(output_cfg)
        # 병합 셀 표에 행 문장을 덧붙일지. 기본 off(청크가 커진다).
        self._table_row_serialization = cp.resolve_table_row_serialization(output_cfg)
        # 같은 청크 전문을 표만 다른 표기형태로 렌더한 텍스트를 추가 필드로 실을지.
        # 기본은 빈 목록(추가 필드 없음) — 켜면 본문이 형식 수만큼 복제되어 페이로드가 커진다.
        self._table_text_formats = cp.resolve_table_text_formats(output_cfg)

        # layout.page_batch_size 는 docling 의 프로세스 전역 싱글턴이라 청커도 그대로 세운다.
        # 통합 실행(main.py)은 attachment → intelligent → convert → parser → chunking 순으로
        # 생성하므로 이 줄을 지우면 전역값이 달라져 /parser 와 /preprocess* 의 페이지 배치
        # 동작이 바뀐다. 전역 오염 자체는 이번 작업 범위 밖이다.
        _layout = ps.resolve_layout_settings(cfg, layout_cfg)
        settings.perf.page_batch_size = _layout.page_batch_size

        # 추출 메타데이터 → typed 벡터 필드 매핑(설정 기반). 설정이 비어있으면
        # 기존 created_date 동작을 그대로 재현한다(하위 호환).
        self._metadata_field_transforms = (
            ec.metadata.field_transforms or DEFAULT_METADATA_FIELD_TRANSFORMS
        )

    def split_documents(self, documents: DoclingDocument, **kwargs: dict) -> List[DocChunk]:
        # chunk_size 우선순위: kwargs > yaml(chunking.chunk_size) > 0
        chunk_size = _parse_optional_int(kwargs.get('chunk_size'), 'chunk_size')
        if chunk_size is None:
            chunk_size = self._chunk_size
        chunk_size = _clamp_chunk_size(chunk_size)
        # chunk_mode 우선순위: kwargs > yaml(chunking.chunk_mode) > "split_only"
        chunk_mode = str(kwargs.get('chunk_mode') or self._chunk_mode).strip().lower()
        if chunk_mode not in {"split_only", "resize_all"}:
            chunk_mode = "split_only"
        chunker = self.CHUNKER(
            max_tokens = chunk_size if chunk_size is not None else 0,
            merge_peers = True,
            tokenizer = self._tokenizer,
            tokenizer_type = self._tokenizer_type,
            chunk_mode = chunk_mode,
            # 크기 산정(_size)이 compose_vectors 의 실제 부착 여부와 같은 값을 보게 한다.
            include_chunk_header = _resolve_include_chunk_header(kwargs, self._include_chunk_header),
            table_as_chunk = cp.resolve_table_as_chunk(kwargs, self._table_as_chunk),
            # 문서 접두(chunk_prefix_fields / first_chunk_fields) 몫 예약. compose_vectors 가
            # 같은 metadata 로 같은 문자열을 만들어 실제로 붙인다.
            chunk_prefix_text = dpx.reserved_prefix_text(
                kwargs,
                dpx.merge_context_metadata(extract_metadata_from_document(documents), kwargs)),
        )

        cp.apply_table_output_defaults(kwargs, self)
        # 청크 텍스트 정규화(text_cleanup=safe): 문자 위생을 청킹 입력에 먼저 적용한다.
        # 출력에서만 정규화하면 청크 경계가 노이즈 문자를 센 채로 잡힌다.
        _cleanup = tn.prepare_document(documents, kwargs, self)
        chunks: List[DocChunk] = list(chunker.chunk(dl_doc=documents, **kwargs))
        # 표별 분할 조각 수는 청커만 안다. compose_vectors 가 조각 순서 메타를 매길 때 읽는다.
        self._table_split_totals = getattr(chunker, "_table_split_totals", {})
        # 표 표기형태별 변형 텍스트도 같은 방식으로 청커에서 받는다.
        self._table_variants = getattr(chunker, "_table_variants", None)
        if _cleanup:
            chunks = tn.drop_blank_chunks(chunks, rules=tn.rules_of(self))
        for chunk in chunks:
            if chunk.meta.doc_items[0].prov:
                self.page_chunk_counts[chunk.meta.doc_items[0].prov[0].page_no] += 1
        return chunks

    async def compose_vectors(self, document: DoclingDocument, chunks: List[DocChunk], file_path: str, request: Request, converted_pdf_path: Optional[str] = None, **kwargs: dict) -> \
            list[dict]:
        title = ""
        _sensitive_infos: list = kwargs.get("_sensitive_infos") or []      # #315 분류 결과
        _gr_masking: bool = bool(kwargs.get("_guardrail_masking", False))   # #315 마스킹 치환 on/off
        # 벡터 생성 직전 표현 정리(text_cleanup=safe). 마스킹 뒤에 적용해야
        # 임베딩 텍스트와 n_char/n_word/n_line 통계가 일치한다.
        _cleanup_out: bool = tn.enabled_for(kwargs, self)
        # 청크 선두 "HEADER: <섹션 경로>" 부착 여부. split_documents 와 kwargs 를 각기 언패킹해서 받으므로
        # setdefault 로 전달할 수 없어 양쪽이 같은 resolver 를 호출한다.
        _include_header: bool = _resolve_include_chunk_header(kwargs, self._include_chunk_header)
        # 표 표기형태별 변형 텍스트 기록부(청커가 남긴다). off 면 None 이라 필드가 아예 생기지 않는다.
        _table_variants = getattr(self, "_table_variants", None)
        if _table_variants is not None and not _table_variants.enabled():
            _table_variants = None
        merged_metadata = dpx.merge_context_metadata(
            extract_metadata_from_document(document), kwargs)
        # 설정 기반 typed 필드 변환 (created_date 등). source/target 키는 passthrough 에서 제외.
        typed_values, consumed_keys = apply_field_transforms(
            self._metadata_field_transforms, merged_metadata, document)
        # 본문과 동일한 값을 실을 메타 필드. 문서형 custom_fields yaml 이 정하고 파서가
        # 문서 metadata 로 실어 보낸다.
        _body_fields: list = cp.resolve_body_fields(kwargs, merged_metadata)
        # 청크 본문 앞에 얹을 문서 단위 메타 값. 청커가 크기 산정에서 예약한 것과 같은 문자열이다.
        _prefix_text, _first_prefix_text = dpx.resolve_prefix_texts(kwargs, merged_metadata)

        for item, _ in document.iterate_items():
            if hasattr(item, 'label'):
                if item.label == DocItemLabel.TITLE:
                    title = item.text.strip() if item.text else ""
                    break

        # kwargs에서 부록 정보 추출 !! appendix feature (2025-09-30, geonhee kim) !!
        appendix_info = kwargs.get('appendix', '')
        appendix_list = []
        if isinstance(appendix_info, str):
            if appendix_info:
                try:
                    parsed = json.loads(appendix_info)
                    if isinstance(parsed, list):
                        appendix_list = [item.strip() for item in parsed if isinstance(item, str) and item.strip()]
                    elif isinstance(parsed, str):
                        appendix_list = [parsed.strip()] if parsed.strip() else []
                    else:
                        appendix_list = []
                except json.JSONDecodeError:
                    appendix_list = [appendix_info.strip()] if appendix_info.strip() else []
            else:
                appendix_list = []
        elif isinstance(appendix_info, list):
            appendix_list = appendix_info
        else:
            appendix_list = []

        passthrough_metadata = dict(merged_metadata)
        # GenOSVectorMeta 스키마 예약 필드 + transform 이 소비한 source/target 키는 passthrough 제외.
        reserved_keys = {
            "text", "n_char", "n_word", "n_line", "e_page", "i_page",
            "i_chunk_on_page", "n_chunk_of_page", "i_chunk_on_doc", "n_chunk_of_doc",
            "n_page", "reg_date", "chunk_bboxes", "media_files", "title",
            "created_date", "appendix", "file_path", "metadata", "guardrail_categories",
            cp.BODY_FIELDS_KEY, cp.CHUNK_PREFIX_FIELDS_KEY, cp.FIRST_CHUNK_FIELDS_KEY,
            cp.FIELD_LABELS_KEY,
        } | set(tv.field_names()) | consumed_keys
        for reserved_key in reserved_keys:
            passthrough_metadata.pop(reserved_key, None)
        passthrough_metadata = {
            key: serialize_metadata_value_for_output(value)
            for key, value in passthrough_metadata.items()
        }

        global_metadata = dict(
            n_chunk_of_doc=len(chunks),
            n_page=document.num_pages(),
            reg_date=datetime.now().isoformat(timespec='seconds') + 'Z',
            title=title,
        )
        global_metadata.update(typed_values)  # 설정 기반 typed 필드 (created_date 등)
        global_metadata.update(passthrough_metadata)
        # 비-PDF 입력이 변환된 경우 vector 의 file_path 를 변환 PDF 경로로 set.
        if converted_pdf_path:
            global_metadata['file_path'] = converted_pdf_path

        # 같은 표의 조각이 연속해서 나오는 순서가 곧 조각 번호다.
        table_piece_seen: dict = {}
        current_page = None
        chunk_index_on_page = 0
        vectors = []
        upload_tasks = []
        for chunk_idx, chunk in enumerate(chunks):
            chunk_page = chunk.meta.doc_items[0].prov[0].page_no if chunk.meta.doc_items[0].prov else 0
            # 청크 선두에 섹션 경로 부착 (HEADER: ). 여기가 유일한 부착 지점이며,
            # 청커의 크기 산정도 같은 _build_header_line 을 쓴다(한도 초과 방지).
            headers_text = _build_header_line(chunk.meta.headings, _include_header, self.CHUNKER)
            # 접두는 헤더 앞이다 — 문서 식별(카드명·문의유형)이 섹션 경로보다 앞에 와야
            # 청크만 떼어 봤을 때 "무엇에 대한 글인지" 가 먼저 읽힌다.
            # 첫 청크 전용 접두는 chunk_idx 0 에서만 붙는다(문서당 1회 계약).
            content = (_prefix_text
                       + (_first_prefix_text if chunk_idx == 0 else "")
                       + headers_text + chunk.text)

            # appendix 추출 !! appendix feature (2025-09-30, geonhee kim) !!
            matched_appendices = self.check_appendix_keywords(content, appendix_list)
            # print(appendix_list, matched_appendices)
            chunk_global_metadata = global_metadata.copy()
            chunk_global_metadata['appendix'] = matched_appendices  # Only matched ones
            ###

            if chunk_page != current_page:
                current_page = chunk_page
                chunk_index_on_page = 0

            # 표 표기형태별 변형은 마스킹·정제 이전 텍스트에서 치환하고, 변형에도 같은
            # 후처리를 적용한다. 순서를 바꾸면 가드레일로 가린 값이 변형 필드로 평문 유출된다.
            variant_values = _table_variants.field_values(
                content, [getattr(item, "self_ref", "") for item in chunk.meta.doc_items],
            ) if _table_variants else {}

            # #315 가드레일 분류 후처리: quote 매칭 → guardrail_categories 부착(항상) + 마스킹 치환(옵션)
            content, chunk_cats = gr.apply_to_text(content, _sensitive_infos, _gr_masking)
            if _cleanup_out:
                content = tn.tidy(content)
            for field_name, variant_text in variant_values.items():
                variant_text, _ = gr.apply_to_text(variant_text, _sensitive_infos, _gr_masking)
                chunk_global_metadata[field_name] = (
                    tn.tidy(variant_text) if _cleanup_out else variant_text)
            # 본문이 확정된 뒤에 넣어야 헤더 접두·가드레일 마스킹·정제까지 반영된 값이
            # 그대로 실린다. 문서 단위로 뽑힌 같은 이름의 값은 여기서 덮인다.
            for field_name in _body_fields:
                chunk_global_metadata[field_name] = content

            vector = (GenOSVectorMetaBuilder()
                      .set_text(content)
                      .set_page_info(chunk_page, chunk_index_on_page, self.page_chunk_counts[chunk_page])
                      .set_chunk_index(chunk_idx)
                      .set_global_metadata(**chunk_global_metadata) #!! appendix feature (2025-09-30, geonhee kim) !!
                      .set_chunk_bboxes(chunk.meta.doc_items, document)
                      .set_media_files(chunk.meta.doc_items, include_tables=self.table_image_enabled)
                      .set_table_info(chunk.meta.doc_items,
                                      getattr(self, "_table_split_totals", {}),
                                      table_piece_seen)
                      .set_guardrail_categories(sorted(chunk_cats) if chunk_cats else None)
                      ).build(self.VECTOR_META)
            vectors.append(vector)

            chunk_index_on_page += 1
            if upload_files:
                file_list = self.get_media_files(chunk.meta.doc_items, include_tables=self.table_image_enabled)
                upload_tasks.append(asyncio.create_task(
                    upload_files(file_list, request=request)
                ))

        if upload_tasks:
            await asyncio.gather(*upload_tasks)

        return vectors

    def get_media_files(self, doc_items: list, include_tables: bool = False):
        return dops.get_media_files(doc_items, include_tables)

    def check_appendix_keywords(self, content: str, appendix_list: list) -> str:
        return apx.check_appendix_keywords(content, appendix_list)

    @classmethod
    def cli(cls, argv=None) -> int:
        """파일 단독 실행. 자세한 사용법은 facade/core/cli.py 참조."""
        from genon.preprocessor.facade.core.cli import run_cli

        return run_cli(cls, argv)

    def setup_logging(self, level_num: int):
        rt.setup_logging(level_num)

    # ------------------------------------------------------------------
    # parse-format(비-docling) 공통 청킹
    #   parser 가 docling 을 만들지 못하는 포맷(audio, csv/xlsx, ppt/pptx/doc,
    #   txt/json/md, 이미지)은 {"elements":[...]} parse-format 을 반환한다. 이를
    #   legacy(attachment_processor) 와 동일하게 청킹한다. 포맷은 file_path 확장자가
    #   아니라 element 내용(마커/카테고리)으로 식별한다.
    # ------------------------------------------------------------------

    @classmethod
    def _single_marker_vector(cls, text: str, cleanup: bool = False,
                              **variant_kwargs):
        """legacy return_vectormeta_format 과 동일한 단일(미분할) 벡터.

        audio([AUDIO]) / tabular([DA]) 처럼 분할하지 않고 통째로 1개 벡터로 반환한다.
        (attachment_processor.AudioLoader/TabularLoader.return_vectormeta_format 동일 형태)

        legacy 는 n_char/n_word/n_line 을 1 로 고정해 통계가 실제와 달랐다. 다른 경로와
        동일하게 실제 값을 계산한다.
        """
        variant_values = tv.field_values_for_text(
            text, tidy=tn.tidy if cleanup else None, **variant_kwargs)
        if cleanup:
            text = tn.tidy(text)
        return cls.VECTOR_META.model_validate({
            **variant_values,
            'text': text,
            'n_char': len(text),
            'n_word': len(text.split()),
            'n_line': len(text.splitlines()),
            'i_page': 1,
            'e_page': 1,
            'n_page': 1,
            # 순번은 0 부터 센다 — 다른 세 경로(_chunk_text_elements 등)가 모두 그렇다.
            # legacy 는 1 로 넣었는데, 그러면 같은 적재 테이블에 두 규약이 섞이고
            # post_chunk 훅이 toolbox.refresh_stats 를 부를 때 값이 1 에서 0 으로
            # 바뀌어 버린다. n_chunk_of_doc 도 이 경로만 비어 있었다.
            'i_chunk_on_page': 0,
            'n_chunk_of_page': 1,
            'i_chunk_on_doc': 0,
            'n_chunk_of_doc': 1,
            'reg_date': datetime.now().isoformat(timespec='seconds') + 'Z',
            'chunk_bboxes': ".",
            'media_files': ".",
        })

    def _text_variant_options(self, **kwargs: dict) -> dict:
        """비-docling 경로에서 표기형태 필드를 만들 때 쓸 인자.

        `_chunk_parse_format` 이 `apply_table_output_defaults` 로 kwargs 에 채워 둔 값을
        읽는다. 설정 해석은 공용 모듈에만 둔다(facade 마다 헬퍼를 복제하지 않는다).
        """
        return {
            "formats": cp.resolve_table_text_formats(kwargs),
            "compact_tables": cp.resolve_compact_tables(kwargs),
        }

    def _isolate_tables_enabled(self, **kwargs: dict) -> bool:
        """표를 독립 청크로 분리할지. docling 경로의 table_as_chunk 와 같은 스위치다."""
        return cp.resolve_table_as_chunk(kwargs, getattr(self, "_table_as_chunk", True))

    def _resolve_recursive_split_params(self, **kwargs: dict) -> "tuple[int, int]":
        """RecursiveCharacterTextSplitter 용 (chunk_size, chunk_overlap) 결정.

        chunk_size: 명시 kwargs(0 포함) 우선. 0/음수는 docling '분할 안 함' 관례에 맞춰 char splitter
          에서 사실상 미분할(1000000)로 해석. 키가 없거나 파싱 불가면 공통 chunking.chunk_size 사용.
        chunk_overlap: 호출 kwargs(chunk_overlap/recursive_chunk_overlap) > config(recursive.chunk_overlap).
          명시적 null 도 default 로 폴백해 int(None) 크래시를 막는다.

        parse-format 텍스트 경로와 행 기반 경로(splittable element)가 같은 규칙을 쓰도록 공유한다.
        """
        _NO_SPLIT = 1000000
        common_size = getattr(self, "_chunk_size", None)
        overlap_default = getattr(self, "_recursive_chunk_overlap", 100)

        raw_size = kwargs.get('chunk_size')
        if raw_size is None:                       # 키 없음/명시 null → 공통 config
            chunk_size = common_size
        else:
            try:
                chunk_size = int(raw_size)         # 명시값(0 포함) 보존
            except (TypeError, ValueError):
                chunk_size = common_size           # 파싱 불가 → 공통 config (기존 동작 유지)
        if not chunk_size or chunk_size <= 0:      # 명시적 0/음수 또는 공통값 부재 → 미분할
            chunk_size = _NO_SPLIT

        overlap = kwargs.get('chunk_overlap')
        if overlap is None:
            overlap = kwargs.get('recursive_chunk_overlap')
        if overlap is None:                        # 부재 또는 명시 null 모두 default 로
            overlap = overlap_default

        chunk_size = max(int(chunk_size), 1)
        # overlap >= size 면 RecursiveCharacterTextSplitter 가 ValueError 로 크래시하므로 size-1 이하로 클램프.
        chunk_overlap = min(max(int(overlap), 0), chunk_size - 1)
        return chunk_size, chunk_overlap

    def _chunk_text_elements(self, elements: list, **kwargs: dict) -> list:
        """parse-format element 들을 RecursiveCharacterTextSplitter 로 청킹한다.

        legacy attachment_processor.split_documents/compose_vectors 와 동일한 동작.
        parser 의 element page 는 이미 1-based 이므로 attachment 처럼 +1 하지 않는다.
        """
        from langchain_text_splitters import RecursiveCharacterTextSplitter
        from langchain_core.documents import Document

        chunk_size, chunk_overlap = self._resolve_recursive_split_params(**kwargs)

        # #315 민감정보 분류: __call__ 에서 문서 전체 1회 분류한 결과를 청크별 quote 매칭에 사용.
        _sensitive_infos: list = kwargs.get("_sensitive_infos") or []
        _gr_masking: bool = bool(kwargs.get("_guardrail_masking", False))
        # 벡터 생성 직전 표현 정리(text_cleanup=safe). 마스킹 뒤에 적용해야
        # 임베딩 텍스트와 n_char/n_word/n_line 통계가 일치한다.
        _cleanup_out: bool = tn.enabled_for(kwargs, self)

        # 표를 독립 청크로 분리한다(table_as_chunk). 표 조각을 별 Document 로 두면
        # splitter 가 표와 앞뒤 본문을 한 청크로 다시 묶지 못한다.
        _isolate_tables: bool = self._isolate_tables_enabled(**kwargs)
        _variant_options = self._text_variant_options(**kwargs)

        # element → page 단위 Document 재구성 (빈 내용 제외)
        docs: list = []
        for el in elements:
            content = str((el or {}).get("content", "") or "")
            if not content.strip():
                continue
            page = (el or {}).get("page", 1)
            try:
                page = int(page)
            except (TypeError, ValueError):
                page = 1
            parts = tbk.split_at_tables(content) if _isolate_tables else [content]
            docs.extend(
                Document(page_content=part, metadata={"page": page}) for part in parts
            )

        if not docs:
            raise GenosServiceException(1, "chunk length is 0")

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size, chunk_overlap=chunk_overlap,
        )
        chunks = splitter.split_documents(docs)
        # 정규화 시 공백만 남는 청크도 제거한다(페이지 카운트 집계 전이어야 한다).
        if _cleanup_out:
            chunks = tn.drop_blank_chunks(chunks, "page_content", rules=tn.rules_of(self))
        else:
            chunks = [c for c in chunks if c.page_content]
        if not chunks:
            raise GenosServiceException(1, "chunk length is 0")

        page_chunk_counts: dict = defaultdict(int)
        for c in chunks:
            page_chunk_counts[c.metadata.get("page", 1)] += 1

        global_metadata = dict(
            n_chunk_of_doc=len(chunks),
            n_page=max((c.metadata.get("page", 1) for c in chunks), default=1),
            reg_date=datetime.now().isoformat(timespec='seconds') + 'Z',
        )

        vectors = []
        current_page = None
        chunk_index_on_page = 0
        for idx, c in enumerate(chunks):
            page = c.metadata.get("page", 1)
            text = c.page_content
            if page != current_page:
                current_page = page
                chunk_index_on_page = 0
            # 표기형태 변형은 마스킹·정제 이전 텍스트에서 만들고 같은 후처리를 거친다
            # (순서를 바꾸면 가드레일로 가린 값이 변형 필드로 평문 유출된다).
            variant_values = tv.field_values_for_text(
                text,
                mask=lambda value: gr.apply_to_text(
                    value, _sensitive_infos, _gr_masking)[0],
                tidy=tn.tidy if _cleanup_out else None,
                **_variant_options,
            )
            has_table = tbk.has_table(text)
            # #315 가드레일 분류 후처리: quote 매칭 → guardrail_categories 부착(항상) + 마스킹 치환(옵션)
            text, chunk_cats = gr.apply_to_text(text, _sensitive_infos, _gr_masking)
            if _cleanup_out:
                text = tn.tidy(text)
            vectors.append(self.VECTOR_META.model_validate({
                **variant_values,
                'has_table': has_table,
                'text': text,
                'n_char': len(text),
                'n_word': len(text.split()),
                'n_line': len(text.splitlines()),
                'i_page': page,
                'e_page': page,
                'i_chunk_on_page': chunk_index_on_page,
                'n_chunk_of_page': page_chunk_counts[page],
                'i_chunk_on_doc': idx,
                'guardrail_categories': sorted(chunk_cats) if chunk_cats else None,  # #315 민감정보 분류 라벨
                **global_metadata,
            }))
            chunk_index_on_page += 1
        return vectors

    def _expand_table_rows(self, rows: list, **kwargs: dict) -> list:
        """표를 담은 행을 표 조각과 본문 조각으로 나눈다(table_as_chunk).

        분리 규칙은 공용 모듈 한 벌이다 — xlsx 직접 경로(converters/xlsx_processor)도
        같은 함수를 쓴다.
        """
        if not self._isolate_tables_enabled(**kwargs):
            return rows
        return tbk.expand_elements(rows)

    def _expand_splittable_rows(self, rows: list, **kwargs: dict) -> list:
        """`splittable` 표시가 있고 chunk_size 를 넘는 행을 여러 행으로 펼친다.

        레코드 1건이 chunk_size 를 넘으면 청크를 나누되 **레코드 metadata 는 모든 조각에 그대로**
        유지한다(적재 측에서 같은 레코드의 조각임을 metadata 로 식별). 플래그가 없는 기존
        tabular_row/faq_row 는 손대지 않으므로 회귀가 없다.

        element 에 `chunk_prefix`(섹션 제목 또는 JSON/Excel 레코드 식별 필드)가 실려 있으면 접두를
        뗀 본문만 분할하고 조각마다 접두를 다시 붙인다 — 안 그러면 두 번째 조각부터
        "어느 카드/어느 섹션인지"가 사라진다(docling 경로가 헤더 몫을 분할 예산에서 미리 빼는
        논리, `_header_line_for`/`:1300-1306` 와 같은 발상).
        """
        if not any(el.get("splittable") for el in rows):
            return rows

        from langchain_text_splitters import RecursiveCharacterTextSplitter

        chunk_size, chunk_overlap = self._resolve_recursive_split_params(**kwargs)

        expanded: list = []
        split_records = 0
        for el in rows:
            content = str(el.get("content", "") or "")
            if not el.get("splittable") or len(content) <= chunk_size:
                expanded.append(el)
                continue

            prefix = str(el.get("chunk_prefix") or "")
            body = content
            budget = chunk_size
            if prefix:
                if content.startswith(prefix):
                    body = content[len(prefix):].lstrip("\n")
                    budget = chunk_size - len(prefix) - 1  # 접두와 본문 사이 개행 1자
                    if budget <= 0:
                        # 접두 하나가 chunk_size 이상인 병리 케이스 — 본문 기준으로 폴백(경고).
                        _log.warning(
                            "[chunker] chunk_prefix(%d자)가 chunk_size(%d) 이상 — 접두 몫 예약 "
                            "생략, 청크가 한도를 초과할 수 있음", len(prefix), chunk_size,
                        )
                        budget = chunk_size
                        prefix = ""
                        body = content
                else:
                    # 접두와 본문이 어긋나면(설정 변경 등) 접두 재부착을 포기하고 전체를 분할한다.
                    prefix = ""

            # chunk_overlap 은 원래 chunk_size 기준으로 이미 클램프돼 있다(_resolve_recursive_
            # split_params) — 접두를 뺀 budget 이 더 작으면 그 기준으로 다시 클램프해야
            # RecursiveCharacterTextSplitter 가 "overlap > chunk_size" 로 죽지 않는다.
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=budget,
                chunk_overlap=min(chunk_overlap, max(budget - 1, 0)),
                # 마크다운 헤딩을 최우선 분리자로 둔다. 본문이 섹션으로 나뉘어 있으면 문장
                # 한가운데가 아니라 섹션 경계에서 잘린다(custom_fields 의 text/html_text 렌더링,
                # HTML/markdown 원천 모두 해당). 헤딩이 없는 평문은 종전 문단/문장 분리자로
                # 조용히 폴백하므로 회귀가 없다.
                separators=["\n## ", "\n### ", "\n#### ", "\n\n", "\n", " ", ""],
                keep_separator=True,
            )
            pieces = [piece for piece in splitter.split_text(body if prefix else content) if piece.strip()]
            if len(pieces) <= 1:
                expanded.append(el)
                continue
            pieces = _carry_over_section_headings(pieces)
            split_records += 1
            if prefix:
                expanded.extend({**el, "content": f"{prefix}\n{piece}"} for piece in pieces)
            else:
                expanded.extend({**el, "content": piece} for piece in pieces)

        if split_records:
            _log.info(
                f"[chunker] splittable 레코드 {split_records}건을 chunk_size({chunk_size}) 기준으로 "
                f"분할했습니다: {len(rows)} → {len(expanded)} 청크"
            )
        return expanded

    def _chunk_custom_fields_rows(self, elements: list, **kwargs: dict) -> list:
        """행별 tabular/custom_fields element → 행마다 청크 1개.

        일반 tabular_row는 원본 컬럼 metadata를, custom_fields_row는 목표필드 + doc_type metadata를
        가진다. 이를 청크 extra 필드로 부착하고 text/인덱스/reg_date 등 표준 필드를 채운다.
        intelligent 의 tabular(build_tabular_vectors)와 동일한 "행=청크" 의미다.
        """
        # faq_row는 기존 parser 산출물 JSON을 다시 청킹할 수 있도록 계속 허용한다.
        row_categories = {"tabular_row", "custom_fields_row", "faq_row"}
        rows = [el for el in elements if el.get("category") in row_categories]
        if not rows:
            raise GenosServiceException(1, "chunk length is 0")
        # 행 element 가 하나라도 있으면 이 경로로 오므로, 섞여 온 비-행 element 는 버려진다.
        # tabular_row 는 외부 파서도 만들 수 있는 일반 이름이라 조용한 축소를 로그로 드러낸다.
        dropped = len(elements) - len(rows)
        if dropped:
            _log.warning(
                f"[chunker] 행 기반 청킹 경로에서 비-행 element {dropped}개를 버렸습니다 "
                f"(rows={len(rows)}, total={len(elements)})"
            )

        # 표를 독립 청크로 분리한다(table_as_chunk). 행 경로에는 TableItem 이 없으므로
        # 청크 텍스트 안의 표 블록을 경계로 삼는다.
        rows = self._expand_table_rows(rows, **kwargs)

        # splittable=True element(json_mapping 레코드)만 chunk_size 기준으로 나눈다.
        # 플래그가 없는 tabular_row/faq_row 는 종전대로 "행 1개 = 청크 1개" 다.
        rows = self._expand_splittable_rows(rows, **kwargs)

        _variant_options = self._text_variant_options(**kwargs)

        # #315 민감정보 분류 결과(있으면 text 에 quote 매칭·라벨·마스킹 적용).
        _sensitive_infos: list = kwargs.get("_sensitive_infos") or []
        _gr_masking: bool = bool(kwargs.get("_guardrail_masking", False))
        # 벡터 생성 직전 표현 정리(text_cleanup=safe). 마스킹 뒤에 적용해야
        # 임베딩 텍스트와 n_char/n_word/n_line 통계가 일치한다.
        _cleanup_out: bool = tn.enabled_for(kwargs, self)

        def _page_of(el: dict) -> int:
            # /chunk 는 호출자 인라인 payload 를 받으므로 손상/외부 JSON 의 비숫자 page 가 도달 가능.
            # _chunk_text_elements 와 동일하게 실패 시 1 로 폴백한다.
            try:
                return int(el.get("page", 1) or 1)
            except (TypeError, ValueError):
                return 1

        reg_date = datetime.now().isoformat(timespec='seconds') + 'Z'
        n_chunk_of_doc = len(rows)
        n_page = max((_page_of(el) for el in rows), default=1)

        page_chunk_counts: dict = defaultdict(int)
        for el in rows:
            page_chunk_counts[_page_of(el)] += 1

        vectors: list = []
        current_page = None
        chunk_index_on_page = 0
        for idx, el in enumerate(rows):
            page = _page_of(el)
            if page != current_page:
                current_page = page
                chunk_index_on_page = 0
            text = str(el.get("content", "") or "")
            # 표기형태 변형은 마스킹·정제 이전 텍스트에서 만들고 같은 후처리를 거친다.
            # 순서를 바꾸면 가드레일로 가린 값이 변형 필드로 평문 유출된다.
            variant_values = tv.field_values_for_text(
                text,
                mask=lambda value: gr.apply_to_text(
                    value, _sensitive_infos, _gr_masking)[0],
                tidy=tn.tidy if _cleanup_out else None,
                **_variant_options,
            )
            has_table = tbk.has_table(text)
            text, chunk_cats = gr.apply_to_text(text, _sensitive_infos, _gr_masking)
            if _cleanup_out:
                text = tn.tidy(text)
            row_meta = dict(el.get("metadata") or {})
            # docling 경로와 같은 계약: body_fields 에 오른 필드는 청크 본문과 같은 값을 갖는다.
            for field_name in cp.resolve_body_fields(kwargs, row_meta):
                row_meta[field_name] = text
            row_meta.pop(cp.BODY_FIELDS_KEY, None)  # 제어값은 청크 필드로 내보내지 않는다
            try:
                vectors.append(self.VECTOR_META.model_validate({
                    **row_meta,  # 목표 필드(question/answer_text/...) + doc_type. extra=allow 로 보존.
                    **variant_values,
                    'has_table': has_table,
                    'text': text,
                    'n_char': len(text),
                    'n_word': len(text.split()),
                    'n_line': len(text.splitlines()),
                    'i_page': page,
                    'e_page': page,
                    'i_chunk_on_page': chunk_index_on_page,
                    'n_chunk_of_page': page_chunk_counts[page],
                    'i_chunk_on_doc': idx,
                    'n_chunk_of_doc': n_chunk_of_doc,
                    'n_page': n_page,
                    'reg_date': reg_date,
                    'chunk_bboxes': ".",
                    'media_files': ".",
                    'guardrail_categories': sorted(chunk_cats) if chunk_cats else None,
                }))
            except Exception as exc:
                # 목표필드명이 예약 필드(title/created_date/appendix)와 겹치면 타입 검증에 걸린다.
                # 그대로 두면 pydantic ValidationError 가 raw 로 올라가 stage 도 없고, ValueError
                # 하위라 업로드 파일 문제(INPUT_ERROR)로 오분류된다 — 원인을 메시지에 담아 바꾼다.
                collided = sorted(set(row_meta) & set(self.VECTOR_META.model_fields))
                hint = f" 예약 필드와 겹치는 목표필드: {collided}." if collided else ""
                raise GenosServiceException(
                    "1",
                    f"행 metadata 를 청크 property 로 변환하지 못했습니다(element #{idx}).{hint} {exc}",
                    stage="custom_fields",
                ) from exc
            chunk_index_on_page += 1
        return vectors

    def _chunk_parse_format(self, elements: list, **kwargs: dict) -> list:
        """parse-format( {"elements":[...]} ) 출력을 legacy 동작으로 청킹한다.

        포맷은 element 내용으로 식별(파일 확장자 불필요):
          0) tabular_row/custom_fields_row: 행마다 벡터 1개.
          1) audio: content 가 "[AUDIO]" 로 시작하는 element 가 있으면 → 단일 벡터.
          2) legacy tabular([DA]): 비어있지 않은 element가 전부 category=="table"이면 → 단일 벡터.
          3) 그 외: RecursiveCharacterTextSplitter 로 텍스트 청킹.
        """
        elements = elements or []

        # 파서와 청커가 별개 요청이라 표 출력 설정은 프로세서 속성에만 있다. docling 경로
        # (split_documents)와 마찬가지로 kwargs 로 옮겨, 이 아래 경로들도 같은 설정을 본다.
        cp.apply_table_output_defaults(kwargs, self)

        # 청크 텍스트 정규화(text_cleanup=safe): 분할 전에 문자 위생을 적용한다.
        # 행 기반 경로는 metadata 가 그대로 청크 property 로 나가므로 함께 정규화한다
        # (text 만 정규화하면 같은 내용이 두 표현으로 저장된다).
        _cleanup_in = tn.enabled_for(kwargs, self)
        if _cleanup_in:
            elements = tn.sanitize_elements(elements, tn.rules_of(self))

        # 0) 행 기반 tabular/custom_fields 가드. faq_row는 이전 산출물 하위 호환용이다.
        non_empty_all = [el for el in elements if isinstance(el, dict)]
        row_categories = {"tabular_row", "custom_fields_row", "faq_row"}
        if non_empty_all and any(el.get("category") in row_categories for el in non_empty_all):
            return self._chunk_custom_fields_rows(non_empty_all, **kwargs)

        # 1) audio 가드 — parser 전사 결과는 content 가 "[AUDIO]" 접두사로 시작한다.
        for el in elements:
            content = str((el or {}).get("content", "") or "")
            if content.startswith("[AUDIO]"):
                return [self._single_marker_vector(
                    content, _cleanup_in, **self._text_variant_options(**kwargs))]

        # 2) legacy tabular([DA]) 가드 — 이전 csv/xlsx parse payload 호환용.
        non_empty = [
            el for el in elements
            if str((el or {}).get("content", "") or "").strip()
        ]
        if non_empty and all((el or {}).get("category") == "table" for el in non_empty):
            joined = "\n".join(str(el.get("content", "")) for el in non_empty)
            return [self._single_marker_vector(
                "[DA] " + joined, _cleanup_in, **self._text_variant_options(**kwargs))]

        # 3) 공통 텍스트 경로
        return self._chunk_text_elements(elements, **kwargs)

    async def __call__(self, request: Request, file_path: str = "", **kwargs: dict):
        """파싱 결과를 입력받아 청킹만 수행한다 (Chunk API, #284).

        입력 채널(우선순위) — 두 채널 모두 docling/parse-format 어느 형태든 허용:
          1) kwargs["document"] (또는 "docling_document") = parser 결과를 요청 JSON 에 인라인 전달.
          2) 인라인이 없으면 file_path 가 가리키는 .json 파일에서 parser 결과를 로드(폴백).
        허용 형태:
          - docling: raw docling dict / {"document":...} / {"code":0,"data":{"document":...}}
          - parse-format(비-docling): {"elements":[...]} / {"code":0,"data":{"elements":[...]}}
        형태 판별은 file_path 확장자가 아니라 payload 내용으로 한다(file_path 는 parser 결과 JSON 경로).
        출력: list[GenOSVectorMeta] (적재 인제스션(/run)과 동일 스키마).

        docling 은 GenosSmartChunker 로, parse-format 은 legacy(attachment) 와 동일한 공통
        청킹(_chunk_parse_format)으로 처리한다. 파싱/로딩/OCR/레이아웃/enrichment 은 앞단계
        (파싱 Activity)에서 이미 수행됐으므로 여기서는 호출하지 않는다.
        file_path 는 벡터 메타(file_path)로도 사용된다.
        """
        src = self.load_input(file_path, **kwargs)
        return await self.chunk(request, file_path, src, **kwargs)

    def load_input(self, file_path: str = "", **kwargs) -> "ChunkInput":
        """파서 결과를 읽어 (형태, 데이터, 가드레일 컨텍스트) 로 돌려준다.

        인라인 document 가 우선이고 없으면 file_path 의 .json 을 읽는다. 형태 판별은
        확장자가 아니라 payload 내용으로 한다(file_path 는 parser 결과 JSON 경로다).
        """
        runtime_level = kwargs.get('log_level')
        self.setup_logging(runtime_level if runtime_level is not None else self._log_level)
        _log.info(f"[chunker] file_path: {file_path}")

        raw_payload = kwargs.get("document")
        if raw_payload is None:
            raw_payload = kwargs.get("docling_document")
        if not raw_payload and file_path and file_path.lower().endswith(".json") and os.path.isfile(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    raw_payload = json.load(f)
            except Exception as exc:
                raise GenosServiceException(1, f"chunker 입력 파일 로드 실패({file_path}): {exc}") from exc
        if not raw_payload:
            raise GenosServiceException(
                1, "chunker API: 'document'(인라인 JSON) 또는 file_path(.json) 입력이 필요합니다.")

        if isinstance(raw_payload, DoclingDocument):
            kind, data = "docling", raw_payload
        else:
            kind, data = _classify_payload(raw_payload)
        # 민감정보 분류(#315): chunking 은 워크플로우를 호출하지 않는다. parser 가 분류해
        #   파스 출력에 실어 보낸 sensitive_infos 를 청크에 적용만 한다(병합).
        return ChunkInput(kind, data, dict(
            _sensitive_infos=_extract_sensitive_infos(raw_payload),
            _guardrail_masking=self._gr_cfg.masking_enabled,
        ))

    def pre_chunk(self, kind, data, **kwargs):
        """[전처리] 분할 직전. 받은 형 그대로 돌려준다.

          kind == "docling"   data = DoclingDocument (또는 그 dict)
          kind == "parse"     data = list[dict]  (레코드/표 경로 elements)
        """
        return data

    def post_chunk(self, vectors, **kwargs):
        """[후처리] 응답 직전. list[VECTOR_META] 를 손본다."""
        return vectors

    async def chunk(self, request: Request, file_path: str, src: "ChunkInput", **kwargs):
        """분할과 벡터 조합. 입력 판별은 load_input 이 이미 끝냈다."""

        # 인라인 payload 는 load_input 이 이미 읽었다. 여기 남아 있으면 split/compose 로
        # 흘러가므로 제거한다.
        kwargs.pop("document", None)
        kwargs.pop("docling_document", None)

        # #329: /chunk 는 interim_ref(=workflow_id/run_id)로 캐시 스코프를 유도한다.
        #   현재 청킹 경로엔 LLM 호출이 없어 캐시는 실질 no-op 이지만, Temporal 호출부와
        #   API 표면을 일관되게 맞추고(파싱 Activity 와 동일 스코프) 미래 확장에 대비해
        #   컨텍스트를 설정한다. 아래 pop 으로 청킹 내부에는 누출되지 않게 한다.
        _wf, _run = _parse_interim_ref(kwargs.get("interim_ref"))
        _cache_token = _set_cache_context(
            _resolve_cache_context(kwargs, workflow_id=_wf, run_id=_run)
        )
        # 캐시/정책 키가 split_documents/compose_vectors 로 누출되지 않도록 제거.
        for _k in ("interim_ref", "interim_root", "llm_cache", "error_policy", "request_deadline", "workflow_id", "run_id"):
            kwargs.pop(_k, None)
        try:
            kind, data, _gr_kwargs = src.kind, src.data, src.guardrail
            if kind == "parse":
                # parse-format(비-docling): legacy(attachment) 와 동일하게 공통 청킹.
                vectors = self._chunk_parse_format(data, **_gr_kwargs, **kwargs)
                if not vectors:
                    raise GenosServiceException(1, "chunk length is 0")
            else:
                # docling 원본 JSON → DoclingDocument 복원 (parser output.format='docling' 와 round-trip).
                try:
                    if isinstance(data, DoclingDocument):
                        document: DoclingDocument = data
                    else:
                        document: DoclingDocument = DoclingDocument.model_validate(data)
                except Exception as exc:
                    raise GenosServiceException(1, f"docling document 복원 실패: {exc}") from exc

                # 요청별 상태 초기화 (싱글턴 프로세서 재사용 간 page_chunk_counts 누적 방지).
                self.page_chunk_counts = defaultdict(int)

                has_text_items = False
                for item, _ in document.iterate_items():
                    if (isinstance(item, (TextItem, ListItem, CodeItem, SectionHeaderItem)) and item.text and item.text.strip()) or (isinstance(item, TableItem) and item.data and len(item.data.table_cells) == 0):
                        has_text_items = True
                        break

                if not has_text_items:
                    # text item 이 없으면 split 결과가 비므로 최소 text item 을 추가 (intelligent 와 동일 로직).
                    prov = ProvenanceItem(
                        page_no=1,
                        bbox=BoundingBox(l=0, t=0, r=1, b=1),  # 최소 bbox
                        charspan=(0, 1),
                    )
                    document.add_text(label=DocItemLabel.TEXT, text=".", prov=prov)

                chunks: List[DocChunk] = self.split_documents(document, **kwargs)
                if len(chunks) < 1:
                    raise GenosServiceException(1, "chunk length is 0")

                vectors: list[dict] = await self.compose_vectors(
                    document, chunks, file_path, request, **_gr_kwargs, **kwargs,
                )

            # 벡터 file_path 메타를 입력 file_path 로 채운다(compose_vectors 는 변환 PDF 경우에만
            # 세팅하므로, chunker 입력 경로(인라인 시 메타용 경로 / 파일 입력 시 .json 경로)를 반영).
            if file_path:
                for v in vectors:
                    if not getattr(v, "file_path", None):
                        v.file_path = file_path
            return vectors
        finally:
            _log_cache_summary()
            _reset_cache_context(_cache_token)


# GenOS 와의 의존성 제거를 위해 추가
async def assert_cancelled(request: Request):
    if await request.is_disconnected():
        raise GenosServiceException(1, f"Cancelled")
