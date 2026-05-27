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


# 기능구현 리스트

## Loaders
- attachment_processor loaders → docling output 래핑
  - audio (mp3, wav) ✅ → `AudioLoader` (Whisper STT)
  - tabular (csv, xlsx) ✅ → `TabularLoader`
  - pdf, docx, pptx, hwpx ✅ → `DoclingLoader`

- backend
  - hwpx: [사이냅스, hwp sdk, rhwp, libreoffice, 자유소프트, 한컴데이터로더] (pending)
    - 무료: libreoffice, rhwp
    - 유료: 사이냅스, hwp_sdk

- VLM: dots OCR ✅ → `DoclingLoader` `genos_layout` 옵션 (`LayoutModelType.GENOS_LAYOUT`)
  - yaml: `format_options.pdf.genos_layout.endpoint`

## OCR
- easy ✅
- paddle ✅
- rapid ✅
- tesseract ✅ (tesseract / tesseractcli)
- 업스테이지 ✅ → `UpstageOcrModel` (`UPSTAGE_API_KEY` 또는 `api_key` 옵션)

## Enrichment
- image description ✅ → `ImageDescriptionEnricher`
- ToC ✅ → `TocEnricher`
- metadata ✅ → `MetadataEnricher`
- subject 추출 ✅ → `SubjectExtractor` (postprocessor, `_enrichment_context`로 하위 전달)

## Chunkers
- GenosSmartChunker ✅
- Hybrid ✅
- Hierarchical ✅
- Recursive ✅
- `merge_small_chunks` ✅ — `max_tokens // 3` 미만 청크를 인접 청크에 병합
- `image_option` ✅ — `_build_chunker`에서 postprocessors 보고 자동 감지
  - `enhanced_image_description` 또는 `table_description` 있으면 `image_option=1` 자동 세팅
- `_split_document_by_tokens_image` ✅ — `image_option=1` 시 TableItem을 독립 청크로 분리
- 토크나이저 선택 ✅ (char | miniLM)
- `legal_option` — 미정 (VT5에도 없음, 보류)

## Postprocessing
- `TableRefiner` ✅ — 표 이미지 크롭 → VLM → HTML 복원
- `TableDescriptionPostprocessor` ✅ — TableItem 청크마다 LLM 한 줄 설명 생성
- `SubjectExtractor` ✅
- `EnhancedImageDescriptionPostprocessor` ✅

## 기타
- 기존 전처리기 deprecated warning ✅
- 배포 스크립트 (pending)
- CI/CD 스크립트 수정 (pending)
- python docs / import test (pending)
- docs


# 주요 테스트

- ./genon/preprocessor/tests/conftest.py
- 테스트 실행 스크립트: ./CLAUDE.MD/script.md
  - 모든 스크립트는 여기에 저장해야 함
  
## smoke 테스트
- ./genon/preprocessor/tests/smoke
- hwp, 오디오는 제외
- attachment_processor vs test_attachment_processor 비교: ✅ **완료** (12/12 통과, hwpx 제외)
- test_intelligent_processor vs intelligent_processor 비교: ✅ **완료**

## unit test
- pending

# 클러스터 테스트

- 첨부용 전처리기(구 vs 신)
- 적재용 전처리기(구 vs 신)
  - `data:image/png;base64,{img_str}` 문제 — 로컬에서는 정상, 클러스터 확인 필요
  - ocr: easy, paddle
  - enrich: toc, image_description, extract_metadata
  - dots-ocr (genos_layout)

# yaml
- 첨부용 (parser_config.yaml)
- 적재용 (intelligent_config.yaml)
- 파싱용 (parser_config.yaml)




