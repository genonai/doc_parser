# 프로젝트 개요

- ./genon/preprocessor/facade/intelligent_processor, attachment_processor를 base_processor.py로 리팩토링

- base_processor.py를 상속받고 config을 입력하는 방향으로 설계

- 현재 test_attachment_processor.py, intelligent_attachment_processor.py로 리팩토링 진행중

# 설계 원칙

- **모든 확장자는 DoclingDocument로 리턴**: 어떤 포맷이든 출력은 항상 `DoclingDocument`
- **확장자별 config 형식**: `{"pipeline_options": "...", "backend": "..."}` 형태로 통일
- `BaseProcessor`는 로드 → 청킹 → 벡터화 파이프라인만 담당, 포맷별 처리는 `DoclingLoader`에 위임
- 전체 파이프라인: Load(+enrich) -> chunking -> postprocessing -> metadata

# 의존성
- ./.venv-cu126/bin/activate
- 테스트 실행 시 `source .venv-cu126/bin/activate`가 아닌 `.venv-cu126/bin/python3 -m pytest` 로 직접 실행 필요
  - `.venv/bin`이 PATH 우선순위가 높아서 activate만으로는 .venv가 사용됨


# TODO
- attachment_processor loaders -> docling output 으로 래핑
  - audio (mp3, wav)
  - tabuler (csv, xlsx) ✅
  - pdf, docx, pptx, hwpx  ✅

- backend
  - hwpx: [사이냅스, hwp sdk, rhwp, libreoffice, 자유소프트, 한컴데이터로더] (pending)
    - 무료: libreoffice, rhwp
    - 유료: 사이냅스, hwp_sdk

- OCR 리팩토링
  - paddle
  - easy ✅
  - rapid
  - tesseract
  - 업스테이지(pending)

- Enrichment Refactoring
  - image description ✅
  - ToC

- Chunkers
  - GenosSmartChunker ✅
  - Hybrid ✅
  - Hierarchical ✅
  - Recursive ✅
  - `_merge_small_chunks`: ✅ 청크 후처리 — `max_tokens // 3` 미만 청크를 인접 청크에 병합 ✅
     - `"chunker": {"name": "smart", "max_tokens": 1024, "merge_small_chunks": True}`
     - `merge_small_chunks=False`(기본)이면 병합 안 함
  - `_split_document_by_tokens_image`: image_option=1 & legal_option!=1 일 때 전용 경로. TableItem을 독립 청크로 분리 + LLM description 부착 (vT5:1450)
  - `split_documents` 인자 확장: `subject`, `legal_option`, `image_option` 추가 필요 (vT5:2089)
  - chunker meta 필드 추가: `image_option`, `legal_option`, `subject` → GenosBucketChunker 내부 분기용 (vT5:530)
  - 내부 토크나이저 선택할 수 있게. (char | miniLM)

- postprocessing(VT5)
  - **TableRefiner**: PDF에서 표 영역을 이미지로 크롭 → VLM → 마크다운 복원 → vector text 치환 (vT5:115)
    - `cropping()`: 좌표계 변환(BOTTOMLEFT↔TOPLEFT) + 여백/클램핑/zoom → PNG base64
    - `_refine_table()`: LLM 호출, `|` 문자 충돌 방지 프롬프트 포함
    - `run_in_batches()`: asyncio 병렬 처리 (batch_size=5)
    - `refine_vectors()`: vector 리스트에서 마크다운 표 블록 탐지 후 치환
  - **테이블 description**: 문서 내 각 TableItem마다 LLM으로 한국어 한 줄 description 생성, 테이블 마크다운 앞에 추가 (vT5:713)

- Enrichment 추가 항목 (vT5에서 이동 예정)
  - **subject 추출**: fitz로 PDF 전체 텍스트 추출 → LLM → 300자 이내 문서 주제. 이후 image_description 프롬프트·vector.subject에 주입 (vT5:2475)
  - **image_description** (image_description_on=1): 이미지를 한 줄 자연어로 요약 (vT5:2897)
  - **enhanced_image_description** (enhanced_image_description_on=1): 요약 + 차트/표를 마크다운 테이블로 변환까지 (vT5:2918)
  - TOC 런타임 오버라이드: kwargs로 `toc_system_prompt`, `toc_user_prompt` 주입 가능하도록 (vT5:2537). 현재 intelligent_processor는 전역변수만 사용, 런타임 교체 불가

- VLM: dots ocr

- 배포 스크립트

- CI/CD 스크립트 수정

- 기존 전처리기 deprecated warning ✅

## 메모

- **image_description vs enhanced_image_description 차이**
  - `image_description`: 이미지를 한 줄 자연어 요약만
  - `enhanced_image_description`: 요약 + 차트·표를 마크다운 테이블로도 추출
  

- **TableRefiner가 postprocessing인가 enrichment인가**
  - vT5에서는 chunking 이후 vector 치환으로 동작(postprocessing)
  - 하지만 DoclingDocument 기반 파이프라인에서는 load 직후 DoclingDocument를 수정하는 enrichment로 재설계하는 게 자연스러울 수도 있음
  - → 어느 단계에 배치할지 미결

- **subject 추출을 Enricher로 분리할 경우 실행 순서**
  - subject가 image_description 프롬프트에 주입되므로 subject enricher → image_description enricher 순서 보장 필요
  - → enricher 체인 순서 관리 방식 미결 (리스트 순서로 보장? 의존성 명시?)

- **TabularLoader DoclingDocument 래핑 후 downstream 호환성**
  - 기존 attachment_processor는 `dict` 반환값을 직접 vector 변환에 사용
  - 새 TabularLoader는 DoclingDocument 반환 → chunker/vectorizer가 테이블을 어떻게 처리할지 확인 필요

## Enrichment 설계 원칙
- load_documents 이후, 동작
- 입력: load_documents 전체 혹은 일부 + 프롬프트
- 출력: structured_llm output
- Image_description 기능도 이곳으로 옮겨져야 함
- ToC 도

# 주요 테스트

- ./genon/preprocessor/tests/conftest.py
- 테스트 실행 스크립트: ./CLAUDE.MD/script.md
  - 모든 스크립트는 여기에 저장해야 함
  
## smoke 테스트
- ./genon/preprocessor/tests/smoke
- hwp, 오디오는 제외
- attachment_processor vs test_attachment_processor 비교: ✅ **완료** (12/12 통과, hwpx 제외)
- test_intelligent_processor vs intelligent_processor 비교: ✅ **완료**
- intelligent_processor OCR 테스트
  - **EasyOCR 청커 설정 주의**: OLD `intelligent_processor.split_documents()`는 `max_chunk_size` kwarg로 토큰 제한 설정 (`default=0 → 병합 없음`). 비교 시 반드시 `split_documents(doc, max_chunk_size=1024)` 명시해야 NEW(GenosSmartChunker, max_tokens=1024)와 공정 비교 가능
  - **EasyOCR (CPU 모드)**: pdf_sample.pdf 기준 — `max_tokens=1024` 동일 조건, OLD 5청크 vs NEW 4청크, 3/6 페이지 일치. page 7↔8 미스매치는 페이지 경계 걸친 청크의 귀속 차이(내용 소실 아님), page 11 병합 경계 차이 1건. 실질적 동등 ✅
  - Paddle / Tesseract / Rapid: 추후
- intelligent_processor ImageDescriptionEnricher 테스트
  - **picture_area_threshold=0.001 필수**: 기본값 0.05면 작은 이미지 전부 필터링됨 (레거시 OKDS_pdf.py 참고)
  - **OLD (PictureDescriptionApiOptions)**: "다음은 이미지에 대한 설명입니다." 전치 문장이 자동 추가됨 (docling pipeline 동작)
  - **NEW (ImageDescriptionEnricher)**: 설명만 깔끔하게 출력
  - **결과**: OLD 13/13 ✅, NEW 13/13 ✅ — 동등
  - **config 구조**: `configs/enrich/image_description/gemini-2.0-flash.yaml` (url + model + prompt 통합)
  - **enricher 사용법**: `{"name": "image_description", "api_key": "...", "config_file": "gemini-2.0-flash"}`


## unit test


이거 구조 변경좀 하자
configs/enrich/image_description/모델 이름.yaml 뭐 이런 식으로 두고 프롬프트도 여기에 두자