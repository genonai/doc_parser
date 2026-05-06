# 전처리기 리팩토링 계획

## 목표

- `intelligent_processor`, `convert_processor`, `attachment_processor`의 중복 코드 제거
- 사용자가 **config만 작성**하면 전처리기를 생성할 수 있도록 모듈화
- 파이프라인 단계별 독립적 수정 가능: `load → chunking → 후처리 → metadata`
- VT5 기능 완전 구현 유지

---

## 현재 문제점

| 문제 | 내용 |
|------|------|
| 코드 중복 | `intelligent_processor`(1735줄), `convert_processor`(2010줄), `attachment_processor`(1525줄)에 동일한 로직 산재 |
| 낮은 확장성 | 새 기능 추가 시 3개 파일 모두 수정 필요 |
| 포맷 변환 미분리 | 비PDF 변환 로직이 processor 내부에 하드코딩 |
| OCR/VLM 강결합 | OCR, VLM 호출이 processor에 직접 인라인 |
| `loaders/`, `enrichment/` 비어있음 | 계획된 모듈 구조가 미완성 상태 |

---

## 목표 아키텍처

```
사용자 config
     │
     ▼
DocumentProcessor(config)   ← BaseProcessor 상속, 코드 수정 불필요
     │
     ├─ load          ← loaders/
     │   ├─ FormatConverter (비PDF → PDF)
     │   └─ DoclingLoader  (PDF/DOCX/MD/HTML → DoclingDocument)
     │
     ├─ chunking      ← chunkers/
     │   └─ GenosBucketChunker (image_option, legal_option 지원)
     │
     ├─ 후처리         ← enrichment/   ← 이 단계만 수정하면 됨
     │   ├─ TableRefiner     (VT5)
     │   ├─ SubjectExtractor (VT5)
     │   ├─ ImageDescriber   (VT5)
     │   └─ ChunkMerger      (VT5 _merge_small_chunks)
     │
     └─ metadata      ← metadata/
         └─ GenOSVectorMetaBuilder
```

---

## 모듈별 구현 계획

### 1. `loaders/` — 포맷 로딩 & 변환

**역할**: 모든 확장자를 받아 `DoclingDocument` 로 변환

```
loaders/
├── __init__.py
├── base_loader.py          # BaseLoader ABC
├── docling_loader.py       # PDF/DOCX/MD/HTML → DoclingDocument (현재 base_processor 로직)
└── converters/
    ├── __init__.py
    ├── base_converter.py   # BaseFormatConverter ABC: convert(src) → pdf_path
    ├── libreoffice.py      # HWP/HWPX/PPTX/DOCX → PDF (convert_processor 로직 추출)
    ├── image.py            # PNG/JPG → PDF
    └── registry.py         # 확장자 → Converter 매핑 레지스트리
```

**config 예시**:
```python
"loaders": {
    "hwpx": {"converter": "libreoffice"},
    "pptx": {"converter": "libreoffice"},
    "png":  {"converter": "image"},
}
```

**동작 흐름**: 비PDF 확장자 → `converter` → PDF → `docling_loader`

---

### 2. `chunkers/` — 청킹 (기존 + VT5 확장)

**변경사항**: `split_documents` 인자 확장 및 VT5 옵션 추가

```python
# 현재 (intelligent_processor)
split_documents(documents, **kwargs)

# 목표 (VT5 호환)
split_documents(documents, subject, legal_option, image_option, **kwargs)
```

```
chunkers/
├── __init__.py             # CHUNKERS 레지스트리
├── GenosBucketChunker.py   # image_option, legal_option, subject 지원
│   ├── _split_document_by_tokens()         # 기본 경로
│   └── _split_document_by_tokens_image()   # VT5: image_option=1, legal_option≠1
├── HierarchicalChunker.py
└── HybridChunker.py
```

---

### 3. `enrichment/` — 후처리 (VT5 핵심 기능)

**역할**: 청크 리스트를 받아 AI 기반으로 보강 후 반환. 각 enricher는 독립적으로 on/off 가능.

```
enrichment/
├── __init__.py
├── base_enricher.py            # BaseEnricher ABC: enrich(chunks, document, **kwargs) → chunks
├── table_refiner.py            # VT5 TableRefiner
│   ├── load_documents()
│   ├── cropping()              # 좌표계 변환 + PNG base64 반환
│   ├── _refine_table()         # LLM 호출
│   ├── run_in_batches()        # asyncio 병렬 처리
│   └── refine_vectors()        # vector 내 테이블 구간 치환
├── subject_extractor.py        # VT5 extract_subject (fitz → LLM → subject 300자)
├── image_describer.py          # VT5 image_description / enhanced_image_description
│   ├── image_description_on=1          → 한 줄 이미지 설명
│   └── enhanced_image_description_on=1 → 이미지 설명 + 차트 마크다운 변환
├── chunk_merger.py             # VT5 _merge_small_chunks (max_tokens//3 기준 병합)
├── toc_extractor.py            # TOC 추출 (런타임 프롬프트 오버라이드 지원)
└── registry.py                 # ENRICHERS 레지스트리 (config → enricher 인스턴스)
```

**config 예시**:
```python
"enrichment": {
    "subject_extract_on": 1,
    "table_refine_on": 1,
    "image_description_on": 0,
    "enhanced_image_description_on": 0,
    "chunk_merge_on": 1,
    "toc_on": 1,
    "toc_system_prompt": None,   # 런타임 오버라이드 (VT5)
    "toc_user_prompt": None,
}
```

---

### 4. `ocr/` — OCR & VLM API 모듈 (신규)

**역할**: OCR/VLM 호출을 processor에서 분리하여 API 클라이언트로 추상화

```
ocr/
├── __init__.py
├── base_ocr.py         # BaseOCRClient ABC: run(image_b64) → str
├── paddle_ocr.py       # PaddleOCR API 클라이언트
├── easy_ocr.py         # EasyOCR API 클라이언트
├── dots_ocr.py         # DotsOCR / VLM 클라이언트 (enhanced_image_description에서 사용)
└── registry.py         # OCR_CLIENTS 레지스트리
```

**config 예시**:
```python
"ocr": {
    "model": "paddle",           # paddle | easy | dots_ocr
    "endpoint": "http://...",
    "token": "..."
}
```

---

### 5. `base_processor.py` — 파이프라인 오케스트레이터 (수정)

현재 `base_processor.py`를 확장하여 모든 모듈을 조립.

```python
class BaseProcessor:
    async def __call__(self, request, file_path, **kwargs):
        # 1. load
        raw_doc = self.loader.load(file_path)          # 포맷 변환 포함

        if return_level == "document": return raw_doc

        # 2. chunking
        chunks = self.chunker.chunk(raw_doc, **chunk_kwargs)

        if return_level == "chunk": return chunks

        # 3. 후처리 (enrichment)
        for enricher in self.enrichers:                # config 순서대로 실행
            chunks = await enricher.enrich(chunks, raw_doc, **kwargs)

        # 4. metadata
        vectors = await self.meta_builder(raw_doc, chunks, file_path, request, **kwargs)
        return vectors
```

---

### 6. 사용자 인터페이스 — config만으로 전처리기 생성

```python
from genon.preprocessor.facade import DocumentProcessor

config = {
    "format_options": {
        "pdf": {"pipeline_options": "pdf", "backend": "pypdf", "generate_picture_images": True},
        "hwpx": {"converter": "libreoffice"},   # 비PDF: 변환기 지정
        "pptx": {"converter": "libreoffice"},
    },
    "chunker": "bucket",                         # simple | bucket | hierarchical | hybrid
    "chunker_options": {
        "max_tokens": 1024,
        "image_option": 1,
        "legal_option": 0,
    },
    "enrichment": {
        "subject_extract_on": 1,
        "table_refine_on": 1,
        "image_description_on": 0,
        "enhanced_image_description_on": 0,
        "chunk_merge_on": 1,
        "toc_on": 1,
    },
    "ocr": {
        "model": "paddle",
        "endpoint": "http://ocr-service/api",
        "token": "...",
    },
    "return_level": "vector",                    # document | chunk | vector
    "log_level": 4,
}

processor = DocumentProcessor(config)
vectors = await processor(request, "document.pdf")
```

---

## 구현 순서

```
Phase 1 — 기반 (1주)
  ├─ loaders/ 구현 (docling_loader, converter registry)
  ├─ FormatConverter 추출 (convert_processor → libreoffice.py)
  └─ base_processor 파이프라인 정비

Phase 2 — 청커 (0.5주)
  └─ GenosBucketChunker VT5 옵션 통합 (image_option, legal_option, subject)

Phase 3 — OCR/VLM API화 (0.5주)
  └─ ocr/ 모듈 구현 (paddle, easy, dots_ocr 클라이언트)

Phase 4 — Enrichment (1.5주)
  ├─ SubjectExtractor
  ├─ TableRefiner (VT5 — 가장 복잡)
  ├─ ImageDescriber
  ├─ ChunkMerger
  └─ TocExtractor (런타임 프롬프트 오버라이드 포함)

Phase 5 — 통합 & 테스트 (0.5주)
  ├─ intelligent_processor / convert_processor 로직을 새 모듈로 완전 대체
  ├─ 기존 smoke/unit/regression 테스트 통과 확인
  └─ attachment_processor 정리
```

---

## 파일 구조 최종 목표

```
facade/
├── base_processor.py       # 파이프라인 오케스트레이터 (유지·수정)
├── test_processor.py       # 사용자 config 예시 (유지)
├── loaders/
│   ├── docling_loader.py       # PDF/DOCX/MD/HTML → DoclingDocument (intelligent_processor 로직)
│   ├── tabular_loader.py       # CSV/XLSX → Document (attachment_processor 로직)
│   ├── audio_loader.py         # MP3/WAV → Document (attachment_processor 로직)
│   ├── registry.py             # 확장자 → Loader 매핑 (format_options config로 선택)
│   └── converters/
│       ├── libreoffice.py      # HWP/HWPX/PPTX/DOCX → PDF (convert_processor 로직)
│       ├── image.py            # PNG/JPG → PDF
│       └── registry.py        # 확장자 → Converter 매핑
├── chunkers/
│   ├── GenosBucketChunker.py   (VT5 옵션 추가)
│   ├── HierarchicalChunker.py
│   └── HybridChunker.py
├── enrichment/
│   ├── table_refiner.py
│   ├── subject_extractor.py
│   ├── image_describer.py
│   ├── chunk_merger.py
│   ├── toc_extractor.py
│   └── registry.py
├── ocr/
│   ├── paddle_ocr.py
│   ├── easy_ocr.py
│   └── dots_ocr.py
├── metadata/
│   └── builder.py          (유지)
├── utils/
│   └── ...                 (유지)
│
│ [삭제 예정]
├── intelligent_processor.py  → 각 모듈로 분해
├── convert_processor.py      → loaders/converters로 분해
└── attachment_processor.py   → 정리
```

---

## VT5 기능 → 모듈 매핑

| VT5 기능 | 위치 | 담당 모듈 |
|----------|------|-----------|
| TableRefiner (PDF 크롭 + LLM) | `intelligent_vT5.py:115-367` | `enrichment/table_refiner.py` |
| 테이블 description 생성 | `intelligent_vT5.py:713-758` | `enrichment/table_refiner.py` |
| extract_subject | `intelligent_vT5.py:2475-2504` | `enrichment/subject_extractor.py` |
| image_description | `intelligent_vT5.py:2572-2584` | `enrichment/image_describer.py` |
| enhanced_image_description | `intelligent_vT5.py:2897-2952` | `enrichment/image_describer.py` |
| _split_document_by_tokens_image | `intelligent_vT5.py:1450-1647` | `chunkers/GenosBucketChunker.py` |
| _merge_small_chunks | `intelligent_vT5.py:1649-1743` | `enrichment/chunk_merger.py` |
| 런타임 프롬프트 오버라이드 | `intelligent_vT5.py:2537-2542` | `enrichment/toc_extractor.py` |
| split_documents 인자 확장 | `intelligent_vT5.py:2089-2098` | `base_processor.py` + chunker |
