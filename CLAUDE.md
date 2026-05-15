# 프로젝트 개요

- ./genon/preprocessor/facade/intelligent_processor, attachment_processor를 base_processor.py로 리팩토링

- base_processor.py를 상속받고 config을 입력하는 방향으로 설계

- 현재 test_attachment_processor.py, intelligent_attachment_processor.py로 리팩토링 진행중

## 설계 원칙

- **모든 확장자는 DoclingDocument로 리턴**: 어떤 포맷이든 출력은 항상 `DoclingDocument`
- **확장자별 config 형식**: `{"pipeline_options": "...", "backend": "..."}` 형태로 통일
- **TabularLoader, AudioLoader 제외**: 테이블/오디오는 추후 DoclingDocument 변환으로 통합 예정
- `BaseProcessor`는 로드 → 청킹 → 벡터화 파이프라인만 담당, 포맷별 처리는 `DoclingLoader`에 위임

# 의존성
- ./.venv-cu126/bin/activate
- 테스트 실행 시 `source .venv-cu126/bin/activate`가 아닌 `.venv-cu126/bin/python3 -m pytest` 로 직접 실행 필요
  - `.venv/bin`이 PATH 우선순위가 높아서 activate만으로는 .venv가 사용됨


# TODO
- attachment_processor loaders -> docling output 으로 래핑
  - audio (mp3, wav)
  - tabuler (csv, xlsx)

- backend
  - hwpx: [사이냅스, hwp sdk] 

- OCR 리팩토링
  - paddle
  - easy
  - rapid
  - tesseract

- Enrichment Refactoring
  - image description
  - ToC

- Chunkers


- 배포 스크립트

- CI/CD 스크립트 수정

## Enrichment 설계 원칙
- load_documents 이후, 동작
- 입력: load_documents 전체 혹은 일부 + 프롬프트
- 출력: structured_llm output
- Image_description 기능도 이곳으로 옮겨져야 함
- ToC 도

# 주요 테스트

- ./genon/preprocessor/tests/conftest.py
- 참고 스크립트: ./CLAUDE.MD/script.md

## smoke 테스트
- ./genon/preprocessor/tests/smoke
- hwp, 오디오, 엑셀 파일은 제외
- attachment_processor vs test_attachment_processor 비교: ✅ **완료** (12/12 통과, hwpx 제외)
- test_intelligent_processor vs intelligent_processor 비교: ✅ **완료**
- intelligent_processor OCR 테스트
  - ✅ **EasyOCR (CPU 모드)**: pdf_sample.pdf 기준 구버전(GenosBucketChunker) vs 신버전(GenosSmartChunker) 청크 수 일치 (4/4)
  - Paddle / Tesseract / Rapid: 추후


## unit test