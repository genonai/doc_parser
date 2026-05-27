# GenOS Document Preprocessor — 리팩토링 가이드

> **대상 버전**: 리팩토링 후 (`feature/refactoring` 브랜치)  
> 구버전(VT5) API와의 차이는 [레거시 참고](#레거시-비교) 섹션 참고

---

## 📋 전처리기 종류

| 전처리기 | 파일 | Config | 용도 |
|---|---|---|---|
| **첨부용** | `attachment_processor.py` | `attachment_config.yaml` | 첨부파일 빠른 처리 (Enrichment 없음) |
| **지능형** | `intelligent_processor.py` | `intelligent_config.yaml` | 문서 적재용 고품질 처리 |
| **파싱용** | `parser_processor.py` | `parser_config.yaml` | 파싱 전용 (Enrichment + Postprocessing) |

---

## 🔄 기본 파이프라인

### 첨부용
```
Load (DoclingLoader) → Chunking → Metadata 생성
```

### 지능형 / 파싱용
```
Load (DoclingLoader) → Enrichers → Chunking → Postprocessors → Metadata 생성
```

---

## 📁 Config 기본 구조

```yaml
format_options:    # 확장자별 로더/파이프라인 설정
  pdf: { ... }
  docx: { ... }

enrichers:         # 청킹 전 문서 레벨 처리 (지능형/파싱용만)
  - toc_enricher: { ... }

chunker:           # 청킹 전략
  name: smart
  max_tokens: 1024

postprocessors:    # 청킹 후 청크 레벨 처리 (지능형/파싱용만)
  - table_refiner: { ... }

output:            # 출력 형식 (파싱용만)
  format: json
  table_format: html

resource_path: /app/resource
log_level: 4
```

---

## 📂 format_options — 확장자별 설정

### 파이프라인 / 백엔드 선택

| `pipeline_options` | 설명 |
|---|---|
| `pdf` | Docling StandardPdfPipeline (레이아웃 분석 포함) |
| `simple` | Docling SimplePipeline (빠른 처리) |

| `backend` | 대상 확장자 | 설명 |
|---|---|---|
| `pypdf` | pdf | PyPDFium 백엔드 (기본) |
| `pymu` | pdf | PyMuPDF 백엔드 |
| `msword` | docx | GenosMsWord 백엔드 |
| `hwpx` | hwpx | HWPx 백엔드 |
| `md` | md | Markdown 백엔드 |
| `html` | html | HTML 백엔드 |
| `langchain_pdf` | pdf | LangChain 백엔드 (Docling 우회) |
| `langchain_docx` | docx | LangChain 백엔드 |
| `langchain_pptx` | pptx | LangChain 백엔드 |
| `langchain_md` | md | LangChain 백엔드 |
| `tabular` | csv, xlsx | 테이블 전용 로더 |
| `audio` | mp3, wav | Whisper STT 로더 |

```yaml
# 예시
format_options:
  pdf:
    pipeline_options: pdf
    backend: pypdf
    generate_picture_images: true  # 이미지 크롭 생성 (TableRefiner, image_description 필요 시 true)
  docx:
    pipeline_options: simple
    backend: msword
  csv:
    backend: tabular
  mp3:
    backend: audio
```

---

### do_ocr — OCR 설정

PDF에서 텍스트 레이어가 없거나 이미지 기반인 경우 OCR 적용.

```yaml
do_ocr:
  paddle:                         # 엔진 선택: paddle | easy | rapid | tesseract | tesseractcli
    force_full_page_ocr: true     # 전체 페이지 OCR 강제 (기본 false)
    lang:
      - korean
    bitmap_area_threshold: 0.05   # 비트맵 영역 비율 임계값 (기본 0.05)
    ocr_endpoint: "http://..."    # PaddleOCR 전용: 원격 서버 주소
    text_score: 0.3               # PaddleOCR 전용: 신뢰도 임계값 (기본 0.5)
    timeout: 60
```

| 엔진 | 특이사항 |
|---|---|
| `paddle` | 원격 서버 방식, `ocr_endpoint` 필수 |
| `easy` | 로컬 실행, GPU 선택 가능 (`use_gpu: true`) |
| `rapid` | 로컬 실행 |
| `tesseract` | 로컬 Tesseract 설치 필요 |
| `tesseractcli` | CLI 방식 Tesseract |

---

### genos_layout — dots OCR 레이아웃 모델

기본 Docling 레이아웃 모델 대신 dots-mocr VLM으로 레이아웃 분석.  
PDF 파이프라인(`pipeline_options: pdf`)에서만 동작.

```yaml
genos_layout:
  endpoint: "http://192.168.75.174:26001/v1/chat/completions"  # 필수
  api_key: ""                  # API 키 (없으면 빈 문자열)
  model: "dots-mocr"           # 기본값, 변경 불필요
  max_completion_tokens: 6000  # 기본값
  timeout: 3600                # 기본값 (초)
  retry_count: 2               # 기본값
  page_batch_size: 32          # 한 번에 처리할 페이지 수 (기본 32)
```

---

### do_picture_description — Docling 파이프라인 내 이미지 설명

Docling이 문서를 로드하는 시점에 이미지를 VLM으로 설명 생성.  
결과는 `item.meta.description.text`에 저장됨.  
(Postprocessor의 `enhanced_image_description`과 별개)

```yaml
do_picture_description:
  api:                          # 종류: api | vlm
    url: "http://..."           # LLM API 엔드포인트
    prompt: "이미지를 설명하세요."
    timeout: 60
    concurrency: 2
```

---

### audio 백엔드 옵션

```yaml
format_options:
  mp3:
    backend: audio
    whisper_url: "http://whisper-server/v1/audio/transcriptions"
    model: "model"
    language: "ko"
    chunk_sec: 600      # 분할 길이 (초)
    timeout: 300
```

---

## 🔬 enrichers — Enrichment 옵션

청킹 **전** 문서 레벨에서 실행. 등록 순서대로 실행됨.

### toc_enricher — 목차 추출

PDF 전체 텍스트를 LLM으로 분석하여 목차(ToC) 생성 후 문서 메타에 주입.

```yaml
enrichers:
  - toc_enricher:
      url: "https://openrouter.ai/api/v1/chat/completions"  # LLM API
      api_key: null
      model: "google/gemini-2.0-flash"
      temperature: 0.0
      top_p: 0.00001
      seed: 33
      max_tokens: 10000
      system_prompt: |         # 커스텀 시스템 프롬프트 (선택)
        You are an expert...
      user_prompt: |           # 커스텀 유저 프롬프트 (선택, {raw_text} 변수 포함)
        {raw_text}
```

### extract_metadata_enricher — 문서 메타데이터 추출

작성일, 작성자, 문서번호 등 메타데이터 추출.

```yaml
enrichers:
  - extract_metadata_enricher:
      url: null
      api_key: null
      model: null
      temperature: 0.0
      max_tokens: 10000
```

### image_description — 이미지 설명 (Enricher)

문서 내 PictureItem에 한 줄 자연어 설명 추가.  
(`do_picture_description`과 달리 청킹 전 DoclingDocument를 직접 수정)

```yaml
enrichers:
  - image_description:
      url: null
      api_key: null
      model: null
```

---

## ✂️ chunker — 청킹 설정

### 청커 종류

| `name` | 클래스 | 특징 |
|---|---|---|
| `smart` | `GenosSmartChunker` | 섹션 헤더 기준 분할 후 토큰 기준 병합 (기본 권장) |
| `hybrid` | `HybridChunker` | Docling 기본 HybridChunker |
| `hierarchical` | `HierarchicalChunker` | 계층 구조 기반 청킹 |
| `recursive` | `RecursiveCharChunker` | 문자 기반 재귀 청킹 |

### GenosSmartChunker (smart) 옵션

```yaml
chunker:
  name: smart
  tokenizer: char          # char (기본, 빠름) | miniLM (정확, 느림)
  max_tokens: 1024         # 청크 최대 토큰 수 (0이면 무제한)
  merge_peers: true        # 동일 섹션 내 청크 병합 (기본 true)
  merge_small_chunks: false  # max_tokens // 3 미만 청크를 인접 청크에 병합 (기본 false)
  # image_option: 1        # 자동 감지됨 — table_description/enhanced_image_description
                           # postprocessor 있으면 자동으로 1 세팅 (TableItem 독립 청크 분리)
```

---

## 🔧 postprocessors — 후처리 옵션

청킹 **후** 청크 레벨에서 실행. 등록 순서대로 실행됨.

### subject_extractor — 문서 주제 추출

PDF 전체 텍스트 → LLM → 300자 이내 문서 주제 추출.  
이후 `table_description`, `enhanced_image_description` 프롬프트에 `{subject}`로 주입됨.

```yaml
postprocessors:
  - subject_extractor:
      url: null
      api_key: null
      model: null
      max_chars: 12000       # LLM에 전달할 최대 텍스트 길이
      max_tokens: 2000
      temperature: 0.0
      timeout: 60
      system_prompt: |       # 커스텀 가능
        당신은 문서 분석 전문가입니다...
```

> ⚠️ `subject_extractor`는 `table_description`, `enhanced_image_description`보다 **앞에** 등록해야 함

### table_refiner — 표 이미지 정제

PDF에서 표 영역을 이미지로 크롭 → VLM으로 HTML 복원 → 청크 텍스트 치환.

```yaml
postprocessors:
  - table_refiner:
      url: null              # VLM API 엔드포인트
      api_key: null
      model: null
      batch_size: 5          # 병렬 처리 배치 크기
      system_prompt: |       # 커스텀 가능
        당신은 HTML 테이블 복원 전문가입니다...
```

### table_description — 표 설명 생성

TableItem 청크마다 LLM으로 한국어 한 줄 설명 생성 후 청크 텍스트 앞에 추가.  
`image_option=1` (자동 감지)일 때 TableItem이 독립 청크로 분리되어 있어 정확히 1:1 적용됨.

```yaml
postprocessors:
  - table_description:
      url: null
      api_key: null
      model: null
      max_tokens: 500
      temperature: 0.0
      timeout: 60
      batch_size: 5
      max_table_chars: 3000  # LLM에 전달할 표 텍스트 최대 길이
      system_prompt: |
        당신은 문서 이해 전문가입니다. 주어진 표를 분석하여 핵심 내용을 한국어로 한 줄(50자 이내)로 설명하세요.
      prompt: |              # {subject}, {table_text} 변수 사용 가능
        다음 표의 핵심 내용을 한국어로 한 줄(50자 이내)로 설명하세요.
        문서 주제: {subject}

        {table_text}
```

### enhanced_image_description — 이미지 상세 설명

PictureItem 청크에 LLM으로 이미지 설명 + 차트/표를 마크다운 테이블로 변환하여 추가.

```yaml
postprocessors:
  - enhanced_image_description:
      url: null
      api_key: null
      model: null
      max_tokens: 5000
      temperature: 0.1
      timeout: 120
      batch_size: 3
      prompt: |              # {subject} 변수 사용 가능
        당신은 RAG 시스템을 위한 이미지 설명 전문가입니다.
        문서의 주제 : {subject}
        ...
```

> **image_description vs enhanced_image_description**  
> - `image_description` (Enricher): 청킹 전, 한 줄 자연어 요약만  
> - `enhanced_image_description` (Postprocessor): 청킹 후, 요약 + 차트·표를 마크다운 테이블로도 추출

---

## 📤 output — 출력 형식 (파싱용)

```yaml
output:
  format: json       # json | docling  (기본 json)
  table_format: html # html | markdown (기본 html)
```

---

## 💡 전처리기별 사용 예시

### 첨부용 — 빠른 첨부파일 처리
```python
from genon.preprocessor.facade.attachment_processor import DocumentProcessor

processor = DocumentProcessor()  # attachment_config.yaml 자동 로드
vectors = await processor(request, "document.pdf")
```

파이프라인: `Load → Chunking → Metadata`  
Enrichment / Postprocessing 없음 → 빠른 처리

### 지능형 — 문서 적재 고품질 처리
```python
from genon.preprocessor.facade.intelligent_processor import DocumentProcessor

processor = DocumentProcessor()  # intelligent_config.yaml 자동 로드
vectors = await processor(request, "document.pdf")
```

파이프라인: `Load → Enrichers → Chunking → Postprocessors → Metadata`

### 파싱용 — 파싱 결과 반환
```python
from genon.preprocessor.facade.parser_processor import DocumentProcessor

processor = DocumentProcessor()  # parser_config.yaml 자동 로드
result = await processor(request, "document.pdf")
```

---

## 🗂️ Config 파일 위치

| 환경 | 경로 |
|---|---|
| 운영 | `genon/preprocessor/resource/` |
| 개발 | `genon/preprocessor/resource_dev/` |

`resource_path` 키로 오버라이드 가능:
```yaml
resource_path: /app/resource
```

---

## ⚠️ 주의사항

1. **postprocessor 순서**: `subject_extractor`는 반드시 `table_description`, `enhanced_image_description`보다 앞에 위치
2. **image_option 자동 감지**: `table_description` 또는 `enhanced_image_description`이 postprocessors에 있으면 청커의 `image_option=1`이 자동으로 세팅되어 TableItem이 독립 청크로 분리됨
3. **genos_layout**: `pipeline_options: pdf`인 경우에만 동작 (`simple` 파이프라인에서는 무시됨)
